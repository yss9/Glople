"""카세트(design.md 6.6, M2): 실제 모델 서버의 응답을 녹화해 두고 GPU·서버 없이 그대로 다시 돌린다.

- 키 = 정규화한 요청의 sha256. 정규화: 메시지(role·content·tool_calls 이름과 인자·tool_call_id) + 관련 kwargs
  (tools는 JSON의 sha256, max_tokens·temperature·top_p·stop·logit_bias 등 호출 때 덮어쓴 값).
  tool_call id는 서버가 매번 새 uuid를 주므로 나온 순서대로 call_1, call_2 …로 바꿔 키가 흔들리지 않게 한다.
- 모델 객체 자체의 설정(답변 temperature 0.3·logit_bias 같은 생성자 값)은 키에 넣지 않는다. 녹화할 때의 값은 meta에
  남기므로 설정을 바꾸면 카세트를 다시 녹화한다(오너).
- 녹화: RecordingChatModel(inner=ChatOpenAI, cassette)로 감싸 그래프에 넣는다(graph.build.real_deps(record=…),
  cli chat --llm real --record PATH). 연결 끊김·타임아웃·5xx·빈 스트림도 오류 기록으로 남겨 재생 때 같은 예외를 낸다.
- 재생: llm/fake.py의 CassetteScript를 ScriptedChatModel에 끼운다. 녹화에 없는 요청은 CassetteMiss(조용히 넘어가지 않음).
- 카세트에는 모델 요청·응답만 있다. 토큰은 모델 요청에 들어가지 않으므로(design.md 10.1) 카세트에도 없다.
  사용자 발화는 들어 있으니 커밋할 카세트는 오너 본인 데모 문장으로만 녹화한다.
"""
import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import reduce
from operator import add
from pathlib import Path
from typing import Any

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ConfigDict

from glople_agent.llm.normalize import is_model_connection_error, is_model_timeout_error

CASSETTE_VERSION = 1
KEY_KWARGS = ("max_tokens", "max_completion_tokens", "temperature", "top_p", "stop", "logit_bias", "tool_choice",
              "parallel_tool_calls", "response_format", "extra_body")
ROLES = {"system": "system", "human": "user", "ai": "assistant", "tool": "tool"}
MODEL_META_KEYS = ("model_name", "temperature", "top_p", "max_tokens", "logit_bias", "extra_body", "streaming",
                   "disable_streaming", "openai_api_base")


class CassetteMiss(KeyError):
    """녹화에 없는 요청. 키(정규화 요청 sha256)와 마지막 사용자 말을 알려 준다."""


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b if isinstance(b, str) else str(b.get("text", "")) for b in content
                       if isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text"))
    return "" if content is None else str(content)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def normalize_messages(messages: Sequence[BaseMessage]) -> list[dict]:
    """키에 쓰는 메시지 모양. ChatOpenAI가 보내는 것(role·content·tool_calls·tool_call_id)만 남긴다(artifact 없음)."""
    ids: dict[str, str] = {}

    def alias(call_id: str | None) -> str:
        return ids.setdefault(call_id or f"none-{len(ids)}", f"call_{len(ids) + 1}")

    out: list[dict] = []
    for m in messages:
        row: dict[str, Any] = {"role": ROLES.get(m.type, m.type), "content": _text(m.content)}
        if isinstance(m, AIMessage) and m.tool_calls:
            row["tool_calls"] = [{"id": alias(tc.get("id")), "name": tc["name"], "args": _jsonable(tc.get("args"))}
                                 for tc in m.tool_calls]
        if isinstance(m, ToolMessage):
            row["tool_call_id"] = alias(m.tool_call_id)
        out.append(row)
    return out


def normalize_request(messages: Sequence[BaseMessage], kwargs: Mapping[str, Any]) -> dict:
    kw: dict[str, Any] = {}
    tools = kwargs.get("tools")
    if tools is not None:
        kw["tools_sha256"] = hashlib.sha256(json.dumps(tools, ensure_ascii=False).encode()).hexdigest()
    for key in KEY_KWARGS:
        if kwargs.get(key) is not None:
            kw[key] = _jsonable(kwargs[key])
    return {"messages": normalize_messages(messages), "kwargs": kw}


def request_key(messages: Sequence[BaseMessage], kwargs: Mapping[str, Any]) -> str:
    raw = json.dumps(normalize_request(messages, kwargs), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def response_record(msg: AIMessage) -> dict:
    """AIMessage → 서버 응답 모양 기록(content·tool_calls arguments 문자열·finish_reason). 재생은 fake.from_server."""
    return {
        "content": _text(msg.content),
        "tool_calls": [{"name": tc["name"], "arguments": json.dumps(tc.get("args") or {}, ensure_ascii=False)}
                       for tc in msg.tool_calls],
        "invalid_tool_calls": [{"name": itc.get("name"), "arguments": itc.get("args")}
                               for itc in msg.invalid_tool_calls],
        "finish_reason": (msg.response_metadata or {}).get("finish_reason"),
    }


def error_record(exc: BaseException) -> dict | None:
    """재생할 수 있는 모델 오류만 기록한다(우리 코드의 버그는 기록하지 않음)."""
    if is_model_timeout_error(exc):
        return {"error": "timeout"}
    if is_model_connection_error(exc):
        return {"error": "connection"}
    if isinstance(exc, openai.APIStatusError):
        return {"error": "server", "status": exc.status_code}
    if isinstance(exc, json.JSONDecodeError):
        return {"error": "bad_response"}
    if isinstance(exc, ValueError) and "No generation chunks" in str(exc):
        return {"error": "empty_stream"}
    return None


def _last_user(messages: Sequence[BaseMessage]) -> str:
    for m in reversed(messages):
        if m.type == "human":
            return _text(m.content)[:60]
    return ""


@dataclass
class Cassette:
    """녹화 묶음: {key: {"request": 정규화 요청, "response": 응답 또는 오류 기록}}. JSON 파일 하나."""

    entries: dict[str, dict] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Cassette":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != CASSETTE_VERSION:
            raise ValueError(f"카세트 버전이 다릅니다: {data.get('version')!r} (지금 {CASSETTE_VERSION})")
        return cls(entries=dict(data.get("entries") or {}), meta=dict(data.get("meta") or {}), path=path)

    @classmethod
    def open(cls, path: str | Path) -> "Cassette":
        """녹화용: 파일이 있으면 이어서, 없으면 새로."""
        path = Path(path)
        return cls.load(path) if path.is_file() else cls(path=path)

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("저장할 경로가 없습니다")
        target.parent.mkdir(parents=True, exist_ok=True)
        body = {"version": CASSETTE_VERSION, "meta": self.meta, "entries": self.entries}
        target.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        self.path = target
        return target

    def __len__(self) -> int:
        return len(self.entries)

    def put(self, messages: Sequence[BaseMessage], kwargs: Mapping[str, Any], response: dict) -> str:
        key = request_key(messages, kwargs)
        self.entries[key] = {"request": normalize_request(messages, kwargs), "response": response}
        return key

    def lookup(self, messages: Sequence[BaseMessage], kwargs: Mapping[str, Any]) -> dict:
        key = request_key(messages, kwargs)
        entry = self.entries.get(key)
        if entry is None:
            raise CassetteMiss(f"카세트에 없는 요청입니다(key={key[:12]}…, 사용자 말 {_last_user(messages)!r}). "
                               "모델 설정·프롬프트·도구 결과가 바뀌었으면 다시 녹화하세요")
        return entry["response"]


def _model_meta(model: BaseChatModel) -> dict:
    out = {}
    for key in MODEL_META_KEYS:
        value = getattr(model, key, None)
        if value is not None:
            out[key] = _jsonable(value)
    return out


class RecordingChatModel(BaseChatModel):
    """inner(보통 ChatOpenAI)를 그대로 부르고 (정규화 요청 → 응답)을 카세트에 남긴다. 오너가 실제 서버로 녹화할 때 쓴다.

    bind_tools는 ScriptedChatModel처럼 받은 dict를 kwargs로 넘기고, 부를 때 inner.bind_tools로 감싼다(요청 JSON은
    route 노드가 ChatOpenAI를 직접 묶을 때와 같음). autosave면 응답마다 파일에 쓴다."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseChatModel
    cassette: Cassette
    autosave: bool = True
    profile: str = "model"

    @property
    def _llm_type(self) -> str:
        return "recording"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=tools, **kwargs)

    def _target(self, kwargs: Mapping[str, Any]) -> tuple[Any, dict]:
        rest = {k: v for k, v in kwargs.items() if k != "tools"}
        tools = kwargs.get("tools")
        return (self.inner.bind_tools(tools) if tools is not None else self.inner), rest

    def _record(self, messages, kwargs, response: dict) -> None:
        self.cassette.meta.setdefault("models", {}).setdefault(self.profile, _model_meta(self.inner))
        self.cassette.meta["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.cassette.put(messages, kwargs, response)
        if self.autosave and self.cassette.path is not None:
            self.cassette.save()

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        target, rest = self._target(kwargs)
        try:
            msg = target.invoke(messages, stop=stop, **rest)
        except Exception as e:
            if (rec := error_record(e)) is not None:
                self._record(messages, kwargs, rec)
            raise
        self._record(messages, kwargs, response_record(msg))
        finish = (msg.response_metadata or {}).get("finish_reason")
        return ChatResult(generations=[ChatGeneration(message=msg, generation_info={"finish_reason": finish})])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs) -> Iterator[ChatGenerationChunk]:
        target, rest = self._target(kwargs)
        chunks = []
        try:
            for chunk in target.stream(messages, stop=stop, **rest):
                chunks.append(chunk)
                yield ChatGenerationChunk(message=chunk)
        except Exception as e:
            if (rec := error_record(e)) is not None:
                self._record(messages, kwargs, rec)
            raise
        if chunks:
            self._record(messages, kwargs, response_record(reduce(add, chunks)))


def recording_model(inner: BaseChatModel, cassette: Cassette, *, profile: str = "model") -> RecordingChatModel:
    """inner의 스트리밍 설정(disable_streaming·tags)을 그대로 따르는 녹화 래퍼."""
    return RecordingChatModel(inner=inner, cassette=cassette, profile=profile,
                              disable_streaming=getattr(inner, "disable_streaming", False),
                              tags=list(getattr(inner, "tags", None) or []))


__all__ = ["CASSETTE_VERSION", "Cassette", "CassetteMiss", "KEY_KWARGS", "RecordingChatModel", "error_record",
           "normalize_messages", "normalize_request", "recording_model", "request_key", "response_record"]

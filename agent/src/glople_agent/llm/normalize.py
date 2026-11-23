"""mlx_lm.server 응답 quirk → RouterOutcome (design.md 6.3).

라우터 모델의 응답(AIMessage)이나 예외를 받아 `router_kind`와 messages에 넣을 정규화한 AIMessage를 정한다.

| 서버 응답 | AIMessage 모습 | kind |
|---|---|---|
| 호출 1개 / 병렬 호출 | tool_calls (병렬이면 content "\\n") | tool (content는 "") |
| JSON 깨짐 | tool_calls 없음 + finish_reason=tool_calls | dropped |
| ChatOpenAI 파싱 실패 | invalid_tool_calls | dropped (정상 호출이 있으면 그것만 남기고 tool) |
| max_tokens에서 끊김 | finish_reason=length | truncated |
| content에 <tool_call> 텍스트 | 텍스트 | 직접 파싱 성공 시 tool, 실패면 unparsed |
| 연결 끊김·타임아웃 | APIConnectionError·APITimeoutError | error |
| 텍스트(되묻기·거절·인사) | content | text |

detail은 trace(체크포인트)에 그대로 넣을 수 있게 JSON 값만 담는다(예외 객체·토큰 없음).
"""
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2
import openai
import pydantic
from langchain_core.messages import AIMessage
from langchain_core.messages.tool import tool_call as make_tool_call

from glople_agent.state import RouterKind

# 재시도 때 route 노드가 invoke에 덧붙일 값(design.md 6.2). 텍스트·호출·오류는 재시도 없음
RETRY_OVERRIDES: dict[str, dict[str, Any]] = {
    "dropped": {"temperature": 0.2},   # 깨진 JSON·빈 호출
    "unparsed": {"temperature": 0.2},  # <tool_call> 텍스트를 직접 읽지도 못함
    "truncated": {"max_tokens": 768},  # 서버·요청의 max_tokens에 걸림
}

# evaluate.py·check_server.py와 같은 대비책: 본문의 <tool_call> 블록을 직접 읽는다
TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_DETAIL_TEXT_MAX = 300


@dataclass(frozen=True)
class RouterOutcome:
    kind: RouterKind
    message: AIMessage | None = None  # tool: messages에 넣을 정규화 메시지, text: 라우터 문장. 그 밖은 None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def tool_calls(self) -> list[dict]:
        return list(self.message.tool_calls) if self.kind == "tool" and self.message is not None else []

    @property
    def text(self) -> str | None:
        return self.message.content if self.kind == "text" and self.message is not None else None

    @property
    def retry_overrides(self) -> dict[str, Any] | None:
        return retry_overrides(self.kind)


def retry_overrides(kind: str) -> dict[str, Any] | None:
    """그 kind를 한 번 더 시도할 때 쓸 invoke 인자. 재시도 대상이 아니면 None."""
    found = RETRY_OVERRIDES.get(kind)
    return dict(found) if found is not None else None


def new_call_id() -> str:
    return str(uuid.uuid4())  # mlx_lm.server와 같은 모양(server.py:72)


def _short(value: Any, limit: int = _DETAIL_TEXT_MAX) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content 블록 목록이면 텍스트만 잇는다
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return "" if content is None else str(content)


def _invalid_entry(name: Any, args: Any, error: Any) -> dict[str, str]:
    first_line = str(error or "").strip().splitlines()[0] if error else ""
    return {"name": str(name or ""), "args": _short(args if args is not None else ""), "error": _short(first_line, 160)}


def _split_calls(msg: AIMessage) -> tuple[list[dict], list[dict]]:
    """(정상 호출, 탈락 호출 요약). 이름이 비었거나 인자가 객체가 아니면 탈락으로 돌린다."""
    valid, invalid = [], []
    for tc in msg.tool_calls or []:
        name, args = tc.get("name"), tc.get("args")
        if not isinstance(name, str) or not name.strip():
            invalid.append(_invalid_entry(name, args, "도구 이름이 없음"))
        elif not isinstance(args, dict):
            invalid.append(_invalid_entry(name, args, "인자가 객체가 아님"))
        else:
            valid.append(tc)
    for itc in msg.invalid_tool_calls or []:
        invalid.append(_invalid_entry(itc.get("name"), itc.get("args"), itc.get("error")))
    return valid, invalid


def parse_tool_call_text(text: str) -> tuple[list[dict], int]:
    """본문의 <tool_call> 블록 → ([{"name", "arguments"}], 깨진 블록 수). evaluate.parse_output과 같은 규칙."""
    calls, invalid = [], 0
    for block in TOOL_CALL_BLOCK_RE.findall(text):
        try:
            obj = json.loads(block)
            if not isinstance(obj, dict):
                raise ValueError("호출이 객체가 아님")
            arguments = obj.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            name = obj.get("name")
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ValueError("이름·인자 모양이 틀림")
            calls.append({"name": name, "arguments": arguments})
        except (ValueError, TypeError):  # json.JSONDecodeError는 ValueError
            invalid += 1
    if text.count("<tool_call>") > text.count("</tool_call>"):  # 닫는 태그 없이 끝남
        invalid += 1
    return calls, invalid


def _tool_message(source: AIMessage, calls: list[dict]) -> AIMessage:
    # 학습 분포대로 content는 "". invalid_tool_calls·additional_kwargs는 다음 요청에 섞이지 않게 뺀다
    return AIMessage(
        content="",
        tool_calls=calls,
        id=source.id,
        name=source.name,
        response_metadata=dict(source.response_metadata or {}),
        usage_metadata=source.usage_metadata,
    )


def _raw_summary(msg: AIMessage, content: str, finish_reason: str | None) -> dict[str, Any]:
    return {
        "finish_reason": finish_reason,
        "content": _short(content),
        "tool_calls": [{"name": tc.get("name"), "args": tc.get("args")} for tc in msg.tool_calls or []],
        "invalid_tool_calls": len(msg.invalid_tool_calls or []),
    }


def normalize_router(msg: AIMessage | BaseException, *,
                     new_id: Callable[[], str] = new_call_id) -> RouterOutcome:
    """라우터 응답을 6.3 표대로 분류한다. 예외를 넘기면 normalize_router_error와 같다."""
    if isinstance(msg, BaseException):
        return normalize_router_error(msg)
    if not isinstance(msg, AIMessage):
        raise TypeError(f"AIMessage나 예외만 받습니다: {type(msg).__name__}")

    content = _content_text(msg.content)
    finish_reason = (msg.response_metadata or {}).get("finish_reason")
    valid, invalid = _split_calls(msg)
    detail: dict[str, Any] = {"raw": _raw_summary(msg, content, finish_reason)}
    if invalid:
        detail["invalid"] = invalid

    if valid:
        calls = [tc if tc.get("id") else {**tc, "id": new_id()} for tc in valid]
        if finish_reason == "length":  # 병렬 중 뒤 호출이 잘렸을 수 있다 → 다시 받는다
            detail["partial_tool_calls"] = [{"name": c["name"], "args": c["args"]} for c in calls]
            return RouterOutcome("truncated", None, detail)
        if content:
            detail["content_dropped"] = _short(content)  # 병렬 "\n", 호출 앞 설명 문장
        return RouterOutcome("tool", _tool_message(msg, calls), detail)

    if invalid:
        return RouterOutcome("dropped", None, {**detail, "reason": "invalid_tool_calls"})

    if "<tool_call>" in content:
        parsed, broken = parse_tool_call_text(content)
        detail["text_parse"] = {"calls": len(parsed), "invalid": broken}
        if parsed and finish_reason != "length":
            calls = [make_tool_call(name=c["name"], args=c["arguments"], id=new_id()) for c in parsed]
            leftover = TOOL_CALL_BLOCK_RE.sub("", content).strip()
            if leftover:
                detail["content_dropped"] = _short(leftover)
            return RouterOutcome("tool", _tool_message(msg, calls), detail)
        if finish_reason == "length":
            return RouterOutcome("truncated", None, detail)
        return RouterOutcome("unparsed", None, detail)

    if finish_reason == "length":
        return RouterOutcome("truncated", None, detail)
    if finish_reason == "tool_calls":  # 서버가 깨진 호출을 경고만 남기고 버림(server.py:92-97)
        return RouterOutcome("dropped", None, {**detail, "reason": "empty_tool_calls"})
    text = content.strip()
    if not text:  # 빈 답(<|im_end|>만): 답할 것도 부를 것도 없음
        return RouterOutcome("dropped", None, {**detail, "reason": "empty_text"})
    return RouterOutcome(
        "text",
        AIMessage(content=text, id=msg.id, name=msg.name,
                  response_metadata=dict(msg.response_metadata or {}), usage_metadata=msg.usage_metadata),
        detail,
    )


# --- 예외 분류 -------------------------------------------------------------

def is_model_timeout_error(exc: BaseException) -> bool:
    return isinstance(exc, (openai.APITimeoutError, TimeoutError))


def is_model_connection_error(exc: BaseException) -> bool:
    """RetryPolicy(retry_on=...)용: 연결 끊김만 True. 타임아웃은 APIConnectionError의 하위 클래스라 따로 뺀다."""
    if is_model_timeout_error(exc):
        return False
    return isinstance(exc, (openai.APIConnectionError, ConnectionError))


def _is_bad_tool_call_shape(exc: BaseException) -> bool:
    # arguments가 JSON이지만 객체가 아니면 langchain-openai가 AIMessage를 만들다 검증 오류를 낸다
    return isinstance(exc, pydantic.ValidationError) and exc.title in ("AIMessage", "AIMessageChunk")


def _is_unreadable_response(exc: BaseException) -> bool:
    # 200 헤더 뒤 본문 없이 끊기면(HTTP/1.0) openai 3.24.0이 응답 JSON을 읽다 JSONDecodeError를 그대로 올린다
    # (test_pins_openai_http.py::test_headers_then_close_is_json_error_not_connection_error)
    return isinstance(exc, json.JSONDecodeError)


def is_model_error(exc: BaseException) -> bool:
    """route 노드가 잡아서 RouterOutcome으로 바꿀 예외인지(우리 코드의 버그는 잡지 않는다)."""
    return (is_model_connection_error(exc) or is_model_timeout_error(exc)
            or isinstance(exc, openai.APIError) or _is_bad_tool_call_shape(exc) or _is_unreadable_response(exc))


def _is_empty_stream(exc: BaseException) -> bool:
    # 스트리밍에서 헤더 뒤 끊기면 [DONE] 없이 청크 0개 → langchain-core가 ValueError를 낸다
    # (test_pins_openai_http.py::test_stream_cut_after_headers_raises_no_chunks)
    return isinstance(exc, ValueError) and "No generation chunks" in str(exc)


def is_answer_model_error(exc: BaseException) -> bool:
    """answer_llm 노드가 잡아서 템플릿으로 보낼 예외: 라우터와 같은 모델 오류 + 스트리밍 중 끊김.
    스트림을 읽는 도중의 전송 오류는 openai가 감싸지 않고 httpx2 예외 그대로 올라온다."""
    return is_model_error(exc) or _is_empty_stream(exc) or isinstance(exc, httpx2.TransportError)


def normalize_router_error(exc: BaseException) -> RouterOutcome:
    """라우터 호출 중 난 예외 → RouterOutcome. detail에는 예외 이름과 짧은 메시지만 남긴다."""
    base = {"exception": type(exc).__name__, "message": _short(str(exc), 200)}
    if _is_bad_tool_call_shape(exc):
        return RouterOutcome("dropped", None, {**base, "reason": "args_not_object"})
    if is_model_timeout_error(exc):
        return RouterOutcome("error", None, {**base, "error": "timeout", "retry_policy": False})
    if is_model_connection_error(exc):
        return RouterOutcome("error", None, {**base, "error": "connection", "retry_policy": True})
    if isinstance(exc, openai.APIStatusError):
        return RouterOutcome("error", None, {**base, "error": "server", "status_code": exc.status_code,
                                             "retry_policy": False})
    if isinstance(exc, openai.APIError):
        return RouterOutcome("error", None, {**base, "error": "server", "retry_policy": False})
    if _is_unreadable_response(exc):
        return RouterOutcome("error", None, {**base, "error": "bad_response", "retry_policy": False})
    return RouterOutcome("error", None, {**base, "error": "unknown", "retry_policy": False})

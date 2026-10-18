"""M4 평가·A/B 하네스(design.md 13절 M4, 15.2): 시나리오를 그래프에 흘려 라우터 × 답변 모드를 비교하고 Markdown 표를 쓴다.

    # 나중에 실제 서버로(오너가 serve.sh로 먼저 띄움. 학습 중에는 띄우지 않음)
    python -m glople_agent.eval.abtest --router-url http://127.0.0.1:8090/v1 --a1-url http://127.0.0.1:8091/v1 \\
        --modes a3,a3-v2,a1,template --out docs/m4-ab.md [--record cassettes/m4]
    # 지금(GPU·서버 없이): 가짜 모델 또는 녹화한 카세트
    python -m glople_agent.eval.abtest --fake --modes a3,a3-v2,a1,template --out /tmp/m4-fake.md
    python -m glople_agent.eval.abtest --cassette cassettes/m4 --modes a3,a3-v2,a1,template --out docs/m4-ab.md

- 시나리오: eval_sets/scenarios_m4.jsonl(한 줄 = 대화 하나, 턴마다 정답 라우터 출력 gold). 도구 결과는 늘 같은 가짜 Spring
  fixture(FakeBackend, 기준 시각 2026-10-17 12:00 KST)라 모델만 바뀐다. backend_errors로 도구 오류, anon으로 비로그인을 흉내 낸다.
- 라우터: 어댑터마다 서버 하나(base URL). --router-url(이름 router) 또는 --router 이름=URL을 여러 번.
- 답변 모드: a3(답변 v1 문구, 라우터와 같은 서버), a3-v2(v2 문구, 같은 서버), a1(v1 문구, --a1-url 서버 = 기본 모델 8091),
  a1-v2, template(LLM 답 없음), (M7) loop(도구 결과 뒤 라우터 모델이 답하거나 한 번 더 부름, 최대 2라운드, 가드 실패·막힘은
  템플릿 — graph/after_tools.py). 라우터 응답은 모드 사이에 캐시해서(요청 키 같으면 같은 응답) 모드끼리 같은 판단 위에서 비교한다.
  그래서 라우터 지연은 첫 모드의 실제 호출만으로 잰다. loop의 결과 뒤 요청은 키가 달라 늘 실제로 부른다.
- (M7) loop 지표: 결과 뒤 재호출(결과 뒤 라우터가 호출을 낸 턴), 2라운드 실행, 결과 뒤 판단 분포(answer·call·guard_fail·
  round_limit …), 가드 통과(결과 뒤 글 답 기준). --fake에서 결과 뒤 요청은 LoopAnswerScript(다시 부르지 않는 착한 가짜)가 받는다.
- 지표(15.2): 첫 판단 정확도(도구/되묻기/바로 답변 3분류 + evaluate.py의 도구/아님), 궤적 완전 일치(턴마다 결과 종류 + validate 뒤
  실행 호출 집합), 호출 F1(라우터 원출력, (이름, 정규화 인자)), 답 근거성(이름을 말했고 결과에 없는 숫자가 없음: LLM 원답·최종 답),
  가드 실패율(이유별), 정형 문장, <tool_call> 시도, 2~4문장·이름만 나열(a3-v2가 고치려는 것), 지연 p50/p95. 비율은 n/N과
  부트스트랩 95% 구간(B=2000, seed 0)을 같이 적는다(세트가 작음).
- 카세트: --record DIR이면 실제 호출을 profile마다 한 파일(router-<라우터>.json, answer-<라우터>-<모드>.json)로 녹화하고,
  --cassette DIR이면 그 파일로 서버 없이 같은 결과를 다시 만든다(지연은 의미 없음).
- 토큰: 고정 가짜 값(AB_TOKEN)만 쓰고 모델 요청·보고서·raw에 넣지 않는다. raw(--raw)에는 발화와 모델 답 글이 들어가니 로컬에만 둔다.
"""
import os

# langgraph를 import하기 전에(STRICT는 import 때 한 번 읽힘, api-notes)
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from collections.abc import Callable, Iterator, Mapping, Sequence  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime  # noqa: E402
from functools import reduce  # noqa: E402
from operator import add  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Literal, Protocol, TextIO  # noqa: E402

from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from pydantic import ConfigDict, Field  # noqa: E402

from glople_agent import contract  # noqa: E402
from glople_agent.answer import templates as tpl  # noqa: E402
from glople_agent.config import AGENT_DIR  # noqa: E402
from glople_agent.context import AgentContext, BearerToken  # noqa: E402
from glople_agent.graph.after_tools import VIA as AFTER_TOOLS_VIA  # noqa: E402
from glople_agent.graph.after_tools import tool_rounds  # noqa: E402
from glople_agent.graph.answer_input import llm_answerable  # noqa: E402
from glople_agent.graph.build import Deps, build_graph, new_thread_id, resume, run_turn  # noqa: E402
from glople_agent.graph.guard import answer_facts, canned_answers, mentioned_names, numbers_in  # noqa: E402
from glople_agent.graph.nodes import router_message, turn_messages  # noqa: E402
from glople_agent.llm.cassette import Cassette, recording_model, request_key  # noqa: E402
from glople_agent.llm.fake import (AfterToolsScript, CassetteScript, LoopAnswerScript, ResultEchoScript,  # noqa: E402
                                   ScriptedChatModel, after_tools_rounds, fake_answer_model, fake_router_model,
                                   last_human_text, text_message, tool_call_message)
from glople_agent.plan import InMemoryPlanStore  # noqa: E402
from glople_agent.sanitize import clean_text  # noqa: E402
from glople_agent.tools.backend import KST  # noqa: E402
from glople_agent.tools.envelope import parse_content  # noqa: E402
from glople_agent.tools.fake_backend import FakeBackend  # noqa: E402
from glople_agent.tools.registry import build_read_tools  # noqa: E402
from glople_agent.tools.spring import SpringError  # noqa: E402

DEFAULT_SCENARIOS = AGENT_DIR / "eval_sets" / "scenarios_m4.jsonl"
AB_NOW = datetime(2026, 10, 17, 12, 0, tzinfo=KST)   # 토요일(이번 주말·나이 → 생년월일이 늘 같게)
AB_USER = "ab-user"
AB_TOKEN = "ab-fake-token-not-a-jwt"                 # 고정 테스트 값(실제 JWT 아님)
BOOTSTRAP = 2000
DECISIONS = ("tool", "clarify", "direct")                       # 정답 판단(라우터 응답은 empty·error도 나옴)
OUTCOMES = ("tool", "plan", "clarify", "direct", "fallback")      # 그래프가 턴을 끝낸 종류(outcome_of, + unfinished)


# --- 시나리오 -----------------------------------------------------------------------------

def _decision_of_gold(gold: Mapping[str, Any]) -> str:
    if gold.get("tool_calls"):
        return "tool"
    return "clarify" if tpl.clarify_kind(gold.get("content") or "") else "direct"


@dataclass(frozen=True)
class Turn:
    user: str
    gold: dict                               # {"tool_calls": [{"name", "arguments"}]} 또는 {"content": 글}
    router_input: str | None = None          # 라우터가 받을 한 마디(이어 붙이기·지시어 풀이). 없으면 정제한 user
    expect_results: tuple[str, ...] | None = None   # 호출 순서대로 결과 상태(fixture가 바뀌면 시나리오 검사가 깨짐)
    confirm: tuple = ("approve",)            # 확인 카드에 차례로 줄 답("approve"·"reject"·{"decision": "edit", …})

    @property
    def gold_calls(self) -> list[dict]:
        return [{"name": c["name"], "arguments": c["arguments"]} for c in self.gold.get("tool_calls") or []]

    @property
    def expected_decision(self) -> str:
        return _decision_of_gold(self.gold)

    @property
    def expected_outcome(self) -> str:
        if any(c["name"] == contract.PLAN_TOOL_NAME for c in self.gold_calls):
            return "plan"
        return self.expected_decision

    @property
    def router_key(self) -> str:
        return self.router_input or clean_text(self.user)


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    turns: tuple[Turn, ...]
    tags: tuple[str, ...] = ()
    source: str = "new"
    note: str = ""
    backend_errors: Mapping[str, str] = field(default_factory=dict)   # 도구 → SpringError code
    anon: bool = False


def load_scenarios(path: str | Path = DEFAULT_SCENARIOS) -> list[Scenario]:
    """JSONL → Scenario. id 중복, 모르는 도구, 같은 라우터 입력에 다른 gold, 빈 턴은 ValueError."""
    out: list[Scenario] = []
    seen_ids: set[str] = set()
    by_key: dict[str, dict] = {}
    for line_no, row in enumerate(contract.read_jsonl(Path(path)), 1):
        sid = row["id"]
        if sid in seen_ids:
            raise ValueError(f"{path}:{line_no} 같은 id: {sid}")
        seen_ids.add(sid)
        turns = []
        for t in row["turns"]:
            gold = t["gold"]
            expected = (t.get("expect") or {}).get("results")
            for c in gold.get("tool_calls") or []:
                if c["name"] not in contract.TOOL_NAMES:
                    raise ValueError(f"{sid}: 모르는 도구 {c['name']!r}")
            if not gold.get("tool_calls") and not gold.get("content"):
                raise ValueError(f"{sid}: gold에 호출도 글도 없음")
            turn = Turn(user=t["user"], gold=gold, router_input=t.get("router_input"),
                        expect_results=tuple(expected) if expected is not None else None,
                        confirm=tuple(t.get("confirm", ["approve"])))
            prev = by_key.setdefault(turn.router_key, gold)
            if prev != gold:
                raise ValueError(f"{sid}: 같은 라우터 입력에 다른 gold: {turn.router_key!r}")
            turns.append(turn)
        if not turns:
            raise ValueError(f"{sid}: 턴이 없음")
        errors = dict(row.get("backend_errors") or {})
        for tool, code in errors.items():
            if tool not in contract.READ_TOOL_NAMES or code not in ("upstream_unavailable", "timeout", "bad_request"):
                raise ValueError(f"{sid}: backend_errors {tool}={code}")
        out.append(Scenario(sid, row["category"], tuple(turns), tuple(row.get("tags") or ()), row.get("source", "new"),
                            row.get("note", ""), errors, bool(row.get("anon"))))
    return out


def gold_message(gold: Mapping[str, Any]) -> AIMessage:
    """gold → 서버가 냈을 응답 모양(병렬이면 content "\\n", 서버 uuid id)."""
    if gold.get("tool_calls"):
        return tool_call_message([(c["name"], c["arguments"]) for c in gold["tool_calls"]])
    return text_message(gold["content"])


class ScenarioOracle:
    """가짜 라우터: 라우터 입력(router_key) → gold. overrides(라우터 입력 → AIMessage·예외·함수)로 실수를 끼워 넣는다(테스트)."""

    def __init__(self, scenarios: Sequence[Scenario], overrides: Mapping[str, Any] | None = None) -> None:
        self.by_key = {t.router_key: t.gold for s in scenarios for t in s.turns}
        self.overrides = dict(overrides or {})

    def __call__(self, messages: list[BaseMessage], kwargs: dict) -> AIMessage:
        text = last_human_text(messages)
        if text in self.overrides:
            item = self.overrides[text]
            if isinstance(item, BaseException):
                raise item
            if callable(item):
                return item(messages, kwargs)
            return item.model_copy(deep=True)
        if text not in self.by_key:
            raise KeyError(f"시나리오에 없는 라우터 입력: {text!r}")
        return gold_message(self.by_key[text])


# --- 답변 모드 ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Mode:
    name: str
    answer: Literal["llm", "template"]
    prompt: Literal["v1", "v2"] = "v1"
    server: Literal["router", "a1"] = "router"   # 답변 서버: 라우터와 같은 서버(a3) / --a1-url(a1)
    after_tools: Literal["answer", "loop"] = "answer"   # M7: loop면 결과 뒤 라우터가 답함(대체 답은 템플릿)


MODES: dict[str, Mode] = {m.name: m for m in (
    Mode("a3", "llm", "v1", "router"), Mode("a3-v2", "llm", "v2", "router"),
    Mode("a1", "llm", "v1", "a1"), Mode("a1-v2", "llm", "v2", "a1"), Mode("template", "template"),
    Mode("loop", "template", after_tools="loop"))}


def parse_modes(text: str) -> list[Mode]:
    names = [n.strip() for n in text.split(",") if n.strip()]
    unknown = [n for n in names if n not in MODES]
    if unknown or not names:
        raise ValueError(f"모르는 모드: {unknown or text!r} (가능: {', '.join(MODES)})")
    return [MODES[n] for n in dict.fromkeys(names)]


# --- 모델 공급자(가짜·실제·카세트) ------------------------------------------------------------

class Provider(Protocol):
    label: str

    def router(self, name: str) -> BaseChatModel: ...
    def answer(self, router: str, mode: Mode) -> BaseChatModel: ...
    def describe(self) -> list[str]: ...


_LIST_FIELDS = ("name", "title", "nickname")


class FakeAnswerScript:
    """--fake·테스트용 가짜 답변. 실제 모델 흉내가 아니라 지표 계산이 맞는지 보려는 세 가지 버릇:
    bare(v1 문구: M2에서 본 '이름, 지역' 나열), sentences(v2 문구: ResultEchoScript 2~3문장), chatty(a1: 문장 + 결과에 없는 점수)."""

    def __init__(self, style: Literal["bare", "sentences", "chatty"]) -> None:
        self.style = style
        self._echo = ResultEchoScript()

    def __call__(self, messages: list[BaseMessage], kwargs: dict) -> AIMessage:
        if self.style == "bare":
            parts: list[str] = []
            for m in messages:
                body = parse_content(m.content) if isinstance(m, ToolMessage) and isinstance(m.content, str) else None
                for item in (body or {}).get("items") or []:
                    if isinstance(item, dict):
                        name = next((str(item[k]) for k in _LIST_FIELDS if item.get(k)), None)
                        if name:
                            parts.append(name + (f", {item['where']}" if item.get("where") else ""))
            return text_message(" / ".join(parts[:3]) or "결과가 있어요")
        msg = self._echo(messages, kwargs)
        if self.style == "chatty":
            return text_message(f"{msg.content} 평균 평점은 4.7점이라 인기가 많아요.")
        return msg


class FakeProvider:
    """가짜 모델(GPU·서버 없음): 라우터 = 시나리오 gold(ScenarioOracle), 답변 = FakeAnswerScript. 실제 성능이 아님."""

    label = "가짜 모델(--fake): 지표 계산 확인용, 실제 성능 아님"

    def __init__(self, scenarios: Sequence[Scenario], *, router_scripts: Mapping[str, Callable] | None = None,
                 answer_scripts: Mapping[str, Callable] | None = None) -> None:
        self.scenarios = list(scenarios)
        self.router_scripts = dict(router_scripts or {})
        self.answer_scripts = dict(answer_scripts or {})

    def router(self, name: str) -> BaseChatModel:
        # 결과 뒤 요청(M7 loop 모드에서만 옴)은 LoopAnswerScript가 받는다. 다른 모드의 요청에는 영향 없음
        first = self.router_scripts.get(name) or ScenarioOracle(self.scenarios)
        return fake_router_model(first if isinstance(first, AfterToolsScript) else AfterToolsScript(first))

    def answer(self, router: str, mode: Mode) -> BaseChatModel:
        if mode.name in self.answer_scripts:
            return fake_answer_model(self.answer_scripts[mode.name])
        style = "chatty" if mode.server == "a1" else ("sentences" if mode.prompt == "v2" else "bare")
        return fake_answer_model(FakeAnswerScript(style))

    def describe(self) -> list[str]:
        return [self.label]


def _server(url: str) -> str:
    """보고서용 주소(사용자 정보·query 없이 scheme://host:port/path)."""
    from urllib.parse import urlsplit

    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}{f':{p.port}' if p.port else ''}{p.path}"


class RealProvider:
    """실제 OpenAI 호환 서버(mlx_lm.server). 모델을 만들기만 하고 접속은 그래프가 돌 때(오너가 서버를 먼저 띄움)."""

    label = "실제 서버"

    def __init__(self, routers: Mapping[str, str], *, answer_url: str | None = None, a1_url: str | None = None,
                 timeout: float = 20.0) -> None:
        if not routers:
            raise ValueError("라우터 주소가 없어요(--router-url 또는 --router 이름=URL)")
        self.routers = dict(routers)
        self.answer_url = answer_url
        self.a1_url = a1_url
        self.timeout = timeout

    def answer_base(self, router: str, mode: Mode) -> str:
        if mode.server == "a1":
            if not self.a1_url:
                raise ValueError(f"{mode.name}에는 --a1-url(예: http://127.0.0.1:8091/v1)이 필요해요")
            return self.a1_url
        return self.answer_url or self.routers[router]

    def router(self, name: str) -> BaseChatModel:
        from glople_agent.llm.models import router_model

        return router_model(self.routers[name], timeout=self.timeout)

    def answer(self, router: str, mode: Mode) -> BaseChatModel:
        from glople_agent.llm.models import answer_model

        return answer_model(self.answer_base(router, mode), timeout=self.timeout)

    def describe(self) -> list[str]:
        lines = [f"라우터 {n} = {_server(u)}" for n, u in self.routers.items()]
        lines.append(f"a3 답변 = {_server(self.answer_url) if self.answer_url else '각 라우터와 같은 서버'}")
        if self.a1_url:
            lines.append(f"a1 답변 = {_server(self.a1_url)}")
        return lines


def _profile(*parts: str) -> str:
    return "-".join(re.sub(r"[^A-Za-z0-9_.-]+", "_", p) for p in parts)


class RecordingProvider:
    """inner(실제 서버)를 감싸 profile마다 카세트 하나로 녹화한다(router-<라우터>.json, answer-<라우터>-<모드>.json)."""

    def __init__(self, inner: Provider, directory: str | Path) -> None:
        self.inner = inner
        self.directory = Path(directory)
        self.label = f"{inner.label} + 녹화({self.directory})"

    def _cassette(self, profile: str) -> Cassette:
        return Cassette.open(self.directory / f"{profile}.json")

    def router(self, name: str) -> BaseChatModel:
        profile = _profile("router", name)
        return recording_model(self.inner.router(name), self._cassette(profile), profile=profile)

    def answer(self, router: str, mode: Mode) -> BaseChatModel:
        profile = _profile("answer", router, mode.name)
        return recording_model(self.inner.answer(router, mode), self._cassette(profile), profile=profile)

    def describe(self) -> list[str]:
        return [*self.inner.describe(), f"녹화: {self.directory}"]


class CassetteProvider:
    """녹화한 카세트로 재생(서버 없음). 녹화에 없는 요청은 CassetteMiss → 그 턴은 오류로 센다."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.label = f"카세트 재생({self.directory}, 지연은 의미 없음)"

    def _script(self, profile: str) -> CassetteScript:
        path = self.directory / f"{profile}.json"
        return CassetteScript(path if path.is_file() else Cassette(path=path))

    def router(self, name: str) -> BaseChatModel:
        return ScriptedChatModel(script=self._script(_profile("router", name)), disable_streaming=True,
                                 tags=["nostream"])

    def answer(self, router: str, mode: Mode) -> BaseChatModel:
        return fake_answer_model(self._script(_profile("answer", router, mode.name)))

    def describe(self) -> list[str]:
        return [self.label]


# --- 탐침(지연·원출력 기록, 라우터 캐시) ---------------------------------------------------------

@dataclass
class ProbeCall:
    label: str
    request: list[BaseMessage]
    response: AIMessage | None
    seconds: float | None            # None = 캐시에서(라우터만)
    first_token: float | None = None
    error: str | None = None
    cached: bool = False


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b if isinstance(b, str) else str(b.get("text", "")) for b in content
                       if isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text"))
    return "" if content is None else str(content)


def _fresh(msg: AIMessage) -> AIMessage:
    """캐시한 응답을 새 id로(같은 응답이 다른 스레드에 들어가도 id가 겹치지 않게)."""
    calls = [{**tc, "id": f"call_{uuid.uuid4().hex[:16]}"} for tc in msg.tool_calls]
    return msg.model_copy(update={"id": None, "tool_calls": calls}, deep=True)


class ResponseMemo:
    """라우터 응답 캐시(요청 키 → AIMessage). 모드마다 새 ProbeChatModel이 같은 객체를 나눠 쓴다.
    dict 필드로 두면 pydantic이 검증하면서 복사해 공유가 끊기므로 일반 클래스로 둔다."""

    def __init__(self) -> None:
        self.entries: dict[str, AIMessage] = {}

    def __len__(self) -> int:
        return len(self.entries)


class ProbeChatModel(BaseChatModel):
    """inner를 그대로 부르고 (요청, 응답, 걸린 시간, 첫 토큰 시간)을 log에 남긴다. memo(ResponseMemo)를 주면 같은 요청은 다시
    부르지 않는다.
    (BaseChatModel에는 이미 `cache` 필드(langchain LLM 캐시)가 있어 이름을 memo로 둔다)"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseChatModel
    label: str = "model"
    log: list = Field(default_factory=list)
    memo: ResponseMemo | None = None
    clock: Callable[[], float] = time.perf_counter

    @property
    def _llm_type(self) -> str:
        return "probe"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=tools, **kwargs)

    def _target(self, kwargs: Mapping[str, Any]) -> tuple[Any, dict]:
        rest = {k: v for k, v in kwargs.items() if k != "tools"}
        tools = kwargs.get("tools")
        return (self.inner.bind_tools(tools) if tools is not None else self.inner), rest

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        key = request_key(messages, kwargs) if self.memo is not None else None
        if key is not None and key in self.memo.entries:
            msg = _fresh(self.memo.entries[key])
            self.log.append(ProbeCall(self.label, list(messages), msg, None, cached=True))
            return ChatResult(generations=[ChatGeneration(message=msg)])
        target, rest = self._target(kwargs)
        start = self.clock()
        try:
            msg = target.invoke(messages, stop=stop, **rest)
        except Exception as e:
            self.log.append(ProbeCall(self.label, list(messages), None, self.clock() - start, error=type(e).__name__))
            raise
        self.log.append(ProbeCall(self.label, list(messages), msg, self.clock() - start))
        if key is not None:
            self.memo.entries[key] = msg
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs) -> Iterator[ChatGenerationChunk]:
        target, rest = self._target(kwargs)
        start = self.clock()
        first: float | None = None
        chunks = []
        try:
            for chunk in target.stream(messages, stop=stop, **rest):
                if first is None and _text(chunk.content):
                    first = self.clock() - start
                chunks.append(chunk)
                yield ChatGenerationChunk(message=chunk)
        except Exception as e:
            self.log.append(ProbeCall(self.label, list(messages), None, self.clock() - start, first,
                                      error=type(e).__name__))
            raise
        merged = reduce(add, chunks) if chunks else None
        self.log.append(ProbeCall(self.label, list(messages), merged, self.clock() - start, first))


# --- 실행 -------------------------------------------------------------------------------

@dataclass
class TurnRecord:
    scenario: Scenario
    index: int
    router: str
    mode: str
    router_calls: list[ProbeCall] = field(default_factory=list)
    answer_calls: list[ProbeCall] = field(default_factory=list)
    loop_calls: list[ProbeCall] = field(default_factory=list)   # M7 loop: 도구 결과 뒤 라우터 호출(첫 판단과 따로)
    rounds: int = 0                                            # 이번 턴에 도구가 실행된 라운드 수
    after_tools: list[dict] = field(default_factory=list)      # trace["after_tools"](라운드마다 판단)
    path: list[str] = field(default_factory=list)
    outcome: str = "unfinished"
    executed: list[dict] = field(default_factory=list)       # validate 뒤 실제로 돈 호출 {"name", "arguments"}
    results: list[str] = field(default_factory=list)         # 결과 상태(호출 순서)
    tool_contents: list[str] = field(default_factory=list)   # LLM이 답할 결과의 LLM용 봉투(근거성 판정)
    # (M6) 코드가 답하는 ok 결과(날씨)의 봉투: 최종 답에 붙는 날씨 문장의 숫자는 여기서 나온다(근거성 판정에 더함)
    code_contents: list[str] = field(default_factory=list)
    reply_kind: str | None = None
    final_text: str | None = None
    router_input: str = ""
    answer_trace: dict = field(default_factory=dict)
    seconds: float = 0.0
    error: str | None = None

    @property
    def turn(self) -> Turn:
        return self.scenario.turns[self.index]


def _decision_value(decision: Any) -> dict:
    return {"decision": decision} if isinstance(decision, str) else dict(decision)


def outcome_of(path: Sequence[str], reply_kind: str | None, final_text: str | None) -> str:
    """그래프가 이번 턴을 어떻게 끝냈나(tool/plan/clarify/direct/fallback). 라우터의 학습 되묻기 문장은 clarify."""
    if "plan_prepare" in path:
        return "plan" if reply_kind == "plan" else "clarify"
    if "clarify" in path:
        return "clarify"
    if "direct_reply" in path:
        return "clarify" if tpl.clarify_kind(final_text or "") else "direct"
    if "fallback" in path:
        return "fallback"
    if "tools" in path:
        return "tool"
    return "unfinished"


def run_mode(provider: Provider, scenarios: Sequence[Scenario], *, router: str, mode: Mode,
             router_cache: ResponseMemo | None = None, plan_graph: str = "flat", loop_router: str | None = None,
             retry_initial_interval: float = 0.0, clock: Callable[[], float] = time.perf_counter) -> list[TurnRecord]:
    """시나리오 전부를 (라우터, 모드) 하나로 돈다. 시나리오마다 새 thread, 같은 가짜 Spring·메모리 저장소."""
    backend = FakeBackend(today=AB_NOW.date())
    probe_router = ProbeChatModel(inner=provider.router(router), label="router", memo=router_cache, clock=clock,
                                  disable_streaming=True, tags=["nostream"])
    probe_answer = (ProbeChatModel(inner=provider.answer(router, mode), label="answer", clock=clock)
                    if mode.answer == "llm" else None)
    loop = mode.after_tools == "loop"
    # (M7) loop_router를 주면 결과 뒤 요청만 그 라우터 서버로(예: 첫 판단 v3 + 결과 뒤 v4). 결과 뒤 요청은 키가 달라 캐시하지 않는다
    probe_after = (ProbeChatModel(inner=provider.router(loop_router), label="after_tools", clock=clock,
                                  disable_streaming=True, tags=["nostream"])
                   if loop and loop_router and loop_router != router else None)
    deps = Deps(router_llm=probe_router, read_tools=build_read_tools(backend), plan_store=InMemoryPlanStore(),
                answer_llm=probe_answer, answer_mode=mode.answer, answer_prompt=mode.prompt, plan_graph=plan_graph,
                retry_initial_interval=retry_initial_interval, after_tools=mode.after_tools, after_tools_llm=probe_after)
    graph = build_graph(deps, InMemorySaver())
    user_ctx = AgentContext(user_id=AB_USER, bearer=BearerToken(AB_TOKEN), request_id="abtest", now=AB_NOW, seed=0)
    anon_ctx = AgentContext(user_id=None, bearer=None, request_id="abtest-anon", now=AB_NOW, seed=0)
    out: list[TurnRecord] = []
    for sc in scenarios:
        backend.errors = {tool: SpringError(code) for tool, code in sc.backend_errors.items()}   # type: ignore[arg-type]
        thread = new_thread_id()
        ctx = anon_ctx if sc.anon else user_ctx
        for i, turn in enumerate(sc.turns):
            rec = TurnRecord(sc, i, router, mode.name)
            r0 = len(probe_router.log)
            a0 = len(probe_answer.log) if probe_answer is not None else 0
            l0 = len(probe_after.log) if probe_after is not None else 0
            start = clock()
            try:
                res = run_turn(graph, thread, turn.user, ctx)
                rec.path = list(res.path)
                answers = list(turn.confirm)
                while res.pending is not None:
                    decision = answers.pop(0) if answers else "reject"   # 시나리오가 답을 다 썼으면 닫음
                    res = resume(graph, thread, _decision_value(decision), ctx, interrupt_id=res.pending.id)
                    rec.path += res.path
                values = res.values
                rec.reply_kind, rec.final_text = res.reply_kind, res.final_text
                rec.router_input = values.get("router_input", "")
                rec.answer_trace = res.answer_trace
                ai = router_message(values)
                if loop:   # 두 번째 라운드가 있으면 router_message는 그 호출. 정답(gold)과는 첫 라운드 호출을 비교
                    rounds = tool_rounds(values.get("messages") or [])
                    rec.rounds, rec.after_tools = len(rounds), res.after_tools
                    ai = rounds[0][0] if rounds else ai
                if ai is not None and ("tools" in rec.path or "plan_prepare" in rec.path):
                    rec.executed = [{"name": tc["name"], "arguments": tc["args"]} for tc in ai.tool_calls]
                rec.results = [r.get("status") for r in values.get("results") or []]
                tool_msgs = {m.tool_call_id: m for m in turn_messages(values.get("messages") or [])
                             if isinstance(m, ToolMessage)}
                for r in values.get("results") or []:
                    msg = tool_msgs.get(r.get("tool_call_id"))
                    if msg is not None and llm_answerable(r.get("tool"), r.get("status")) \
                            and r.get("tool") != contract.PLAN_TOOL_NAME and isinstance(msg.content, str):
                        rec.tool_contents.append(msg.content)
                    elif msg is not None and r.get("status") == "ok" and isinstance(msg.content, str) \
                            and r.get("tool") != contract.PLAN_TOOL_NAME:
                        rec.code_contents.append(msg.content)
                rec.outcome = outcome_of(rec.path, rec.reply_kind, rec.final_text)
                if res.error:
                    rec.error = res.error
            except Exception as e:  # noqa: BLE001 - 측정은 예외를 세어 표로 보여 준다(CassetteMiss·서버 꺼짐 …)
                rec.error = f"{type(e).__name__}: {str(e)[:160]}"
            rec.seconds = clock() - start
            calls = probe_router.log[r0:] + (probe_after.log[l0:] if probe_after is not None else [])
            rec.router_calls = [c for c in calls if not after_tools_rounds(c.request)]
            rec.loop_calls = [c for c in calls if after_tools_rounds(c.request)]
            rec.answer_calls = probe_answer.log[a0:] if probe_answer is not None else []
            out.append(rec)
    return out


@dataclass
class ABRun:
    scenarios: list[Scenario]
    routers: list[str]
    modes: list[Mode]
    records: dict[tuple[str, str], list[TurnRecord]]
    provider: list[str]
    started: datetime
    seconds: float
    scenario_path: str = ""
    loop_router: str | None = None   # (M7) loop 모드의 결과 뒤 요청을 받은 라우터


def run_ab(provider: Provider, scenarios: Sequence[Scenario], *, routers: Sequence[str], modes: Sequence[Mode],
           plan_graph: str = "flat", retry_initial_interval: float = 0.0,
           clock: Callable[[], float] = time.perf_counter, scenario_path: str = "",
           progress: Callable[[str], None] | None = None, loop_router: str | None = None) -> ABRun:
    started = datetime.now(KST)
    t0 = time.perf_counter()
    records: dict[tuple[str, str], list[TurnRecord]] = {}
    for router in routers:
        cache = ResponseMemo()   # 라우터 응답은 모드 사이에 같은 것을 씀(같은 판단 위에서 답변만 비교)
        for mode in modes:
            records[(router, mode.name)] = run_mode(provider, scenarios, router=router, mode=mode, router_cache=cache,
                                                    plan_graph=plan_graph, loop_router=loop_router,
                                                    retry_initial_interval=retry_initial_interval, clock=clock)
            if progress:
                errors = sum(1 for r in records[(router, mode.name)] if r.error)
                progress(f"{router} × {mode.name}: {len(records[(router, mode.name)])}턴, 오류 {errors}")
    return ABRun(list(scenarios), list(routers), list(modes), records, provider.describe(), started,
                 time.perf_counter() - t0, scenario_path, loop_router)


# --- 지표 -------------------------------------------------------------------------------

def router_decision(call: ProbeCall | None) -> str:
    """라우터 응답 하나의 판단(evaluate.py의 '도구를 불렀나'와 같게): 호출·깨진 호출(invalid_tool_calls, 호출 없이
    finish_reason=tool_calls)·<tool_call> 글 → tool, 빈 답 → empty(도구 아님), 학습 되묻기 문장 → clarify, 그 밖 글 → direct.
    잘림(length)이어도 호출이 있으면 tool. 응답이 없으면(연결 오류) error."""
    if call is None or call.error or call.response is None:
        return "error"
    msg = call.response
    content = _text(msg.content)
    finish = (msg.response_metadata or {}).get("finish_reason")
    if msg.tool_calls or msg.invalid_tool_calls or finish == "tool_calls" or "<tool_call>" in content:
        return "tool"
    if not content.strip():
        return "empty"
    return "clarify" if tpl.clarify_kind(content.strip()) else "direct"


def router_calls_of(call: ProbeCall | None) -> list[dict]:
    if call is None or call.response is None:
        return []
    return [{"name": tc["name"], "arguments": tc.get("args") or {}} for tc in call.response.tool_calls]


def canonical_set(calls: Sequence[Mapping[str, Any]]) -> Counter:
    """(이름, 정렬한 인자 JSON) 멀티셋. evaluate.py canonical(eval/scoring.py)이 있으면 그것으로(숫자 문자열 차이 무시)."""
    try:
        from glople_agent.eval.scoring import canonical

        return Counter(canonical([{"name": c["name"], "arguments": c["arguments"]} for c in calls]))
    except (OSError, RuntimeError):   # training/scripts/evaluate.py가 없는 사본
        return Counter((c["name"], json.dumps(c["arguments"], ensure_ascii=False, sort_keys=True)) for c in calls)


@dataclass(frozen=True)
class TurnScore:
    """턴 하나의 채점(지표 계산의 단위). None은 '이 지표의 대상이 아님'."""

    record: TurnRecord
    decision: str
    decision_ok: bool
    binary_ok: bool                  # evaluate.py decision_accuracy(도구를 불렀나/아니나)
    tp: int
    pred: int
    gold: int
    turn_exact: bool                 # 결과 종류 + validate 뒤 실행 호출이 정답과 같음
    llm_attempted: bool
    guard_pass: bool | None
    reasons: tuple[str, ...]
    raw_text: str | None
    raw_grounded: bool | None
    final_grounded: bool | None
    sentences_ok: bool | None        # 2~4문장(이름만 나열이 아님)
    bare_list: bool | None
    canned: bool | None
    tool_call_attempt: bool | None
    replaced: bool
    blocked: bool = False            # 보내기 전 차단(input_overlap: 결과에 카드 원문이 섞임, no_llm_result)
    recalled: bool | None = None     # (M7 loop) 결과 뒤 라우터가 호출을 냄(loop가 아니거나 결과 뒤 요청이 없으면 None)
    two_rounds: bool | None = None   # (M7 loop) 도구가 2라운드 실행됨(도구를 안 쓴 턴은 None)


_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\n+")
_ENDING_RE = re.compile(r"(요|다|까|죠|네|세요|니다)[.!?~…)\]'\"]*$")


def sentence_parts(text: str) -> list[str]:
    return [p.strip() for p in _SPLIT_RE.split(text or "") if p.strip()]


def is_bare_list(text: str) -> bool:
    """서술어로 끝나는 문장이 하나도 없음(예: '국립중앙박물관, 서울특별시 용산구')."""
    parts = sentence_parts(text)
    return bool(parts) and not any(_ENDING_RE.search(p) for p in parts)


def grounded(text: str | None, contents: Sequence[str], router_input: str) -> bool | None:
    """답 근거성: 결과에 이름이 있으면 하나 이상 말했고, 결과·발화에 없는 숫자가 없음. 결과가 없으면 None."""
    if text is None or not contents:
        return None
    facts = answer_facts(contents, [], router_input)
    names_ok = bool(mentioned_names(text, facts)) if (facts.names or facts.titles) else True
    return names_ok and not (numbers_in(text) - facts.numbers)


def score_turn(rec: TurnRecord) -> TurnScore:
    turn = rec.turn
    # 첫 판단 = 응답이 온 첫 라우터 호출(연결 오류 뒤 RetryPolicy가 다시 부른 것은 같은 판단의 재시도)
    first = next((c for c in rec.router_calls if c.response is not None), rec.router_calls[0] if rec.router_calls
                 else None)
    last = rec.router_calls[-1] if rec.router_calls else None
    decision = router_decision(first)
    expected = turn.expected_decision
    pred = canonical_set(router_calls_of(last))
    gold = canonical_set(turn.gold_calls)
    tp = sum((pred & gold).values())
    turn_exact = (rec.error is None and rec.outcome == turn.expected_outcome
                  and canonical_set(rec.executed) == gold)
    trace = rec.answer_trace
    attempted = bool(rec.answer_calls)   # 답변 모델을 실제로 부른 턴(보내기 전 차단 input_overlap·no_llm_result는 빼고 따로)
    raw = None
    seen: list[str] = []                 # 답변 모델이 본 결과(길이 상한으로 줄인 content, 가드와 같은 근거)
    loop_text = False                    # (M7 loop) 결과 뒤 라우터의 마지막 응답이 글(가드를 거침)
    if attempted:
        call = rec.answer_calls[-1]
        seen = [m.content for m in call.request if isinstance(m, ToolMessage) and isinstance(m.content, str)]
        if call.response is not None:
            raw = _text(call.response.content).strip()
    elif rec.loop_calls:   # M7 loop: 결과 뒤 라우터가 답변 모델 자리. 글이면 그 글이 원답, 호출이면 원답 없음
        call = rec.loop_calls[-1]
        seen = [m.content for m in call.request if isinstance(m, ToolMessage) and isinstance(m.content, str)]
        loop_text = trace.get("via") == AFTER_TOOLS_VIA
        attempted = loop_text
        if loop_text and call.response is not None and not call.response.tool_calls:
            raw = _text(call.response.content).strip()
    reasons = tuple(trace.get("reasons") or ([trace["reason"]] if trace.get("reason") else [])) if attempted else ()
    canned_set = canned_answers()
    raw_g = grounded(raw, seen, rec.router_input) if raw is not None else None
    final_basis = seen if rec.reply_kind == "llm" and seen else rec.tool_contents   # 템플릿 답은 전체 결과 기준
    final_basis = [*final_basis, *rec.code_contents]   # M6: 답에 붙은 날씨 문장(코드)의 숫자는 날씨 결과 근거
    final_g = grounded(rec.final_text, final_basis, rec.router_input) if rec.tool_contents else None
    return TurnScore(
        record=rec, decision=decision, decision_ok=decision == expected,
        binary_ok=(decision == "tool") == (expected == "tool"), tp=tp, pred=sum(pred.values()),
        gold=sum(gold.values()), turn_exact=turn_exact, llm_attempted=attempted,
        guard_pass=(trace.get("guard") == "pass") if attempted else None, reasons=reasons, raw_text=raw,
        raw_grounded=raw_g, final_grounded=final_g,
        sentences_ok=(2 <= len(sentence_parts(raw)) <= 4 and not is_bare_list(raw)) if raw is not None else None,
        bare_list=is_bare_list(raw) if raw is not None else None,
        canned=(" ".join(raw.split()) in canned_set) if raw is not None else None,
        tool_call_attempt=(bool(set(reasons) & {"tool_calls", "tool_call_text"}) or "<tool_call>" in (raw or ""))
        if attempted and not loop_text else None,
        replaced=bool(trace.get("replaced")),
        blocked=not attempted and trace.get("reason") in ("input_overlap", "no_llm_result"),
        recalled=any(c.response is not None and bool(c.response.tool_calls) for c in rec.loop_calls)
        if rec.loop_calls else None,
        two_rounds=(rec.rounds >= 2) if rec.loop_calls else None)


def percentile(values: Sequence[float], q: float) -> float | None:
    """가장 가까운 순위(nearest-rank). 비면 None."""
    vals = sorted(values)
    if not vals:
        return None
    k = max(1, math.ceil(q * len(vals)))
    return vals[min(k, len(vals)) - 1]


def bootstrap_ci(units: Sequence[Any], stat: Callable[[Sequence[Any]], float], *, b: int = BOOTSTRAP,
                 seed: int = 0, alpha: float = 0.05) -> tuple[float, float] | None:
    """퍼센타일 부트스트랩 구간(단위를 복원 추출). 단위가 없으면 None. seed가 같으면 늘 같은 구간."""
    n = len(units)
    if n == 0:
        return None
    rng = random.Random(seed)
    vals = sorted(stat([units[rng.randrange(n)] for _ in range(n)]) for _ in range(b))
    lo = vals[math.floor(alpha / 2 * (b - 1))]
    hi = vals[math.ceil((1 - alpha / 2) * (b - 1))]
    return lo, hi


@dataclass(frozen=True)
class Rate:
    k: int
    n: int
    ci: tuple[float, float] | None

    @property
    def value(self) -> float | None:
        return self.k / self.n if self.n else None

    def __str__(self) -> str:
        if not self.n:
            return "-"
        lo, hi = self.ci or (self.value, self.value)
        return f"{self.k}/{self.n} ({self.value * 100:.0f}%) [{lo * 100:.0f}–{hi * 100:.0f}]"


def rate(flags: Sequence[bool | None], *, b: int = BOOTSTRAP, seed: int = 0) -> Rate:
    units = [bool(f) for f in flags if f is not None]
    return Rate(sum(units), len(units), bootstrap_ci(units, lambda xs: sum(xs) / len(xs), b=b, seed=seed))


@dataclass(frozen=True)
class F1:
    precision: float | None
    recall: float | None
    f1: float | None
    ci: tuple[float, float] | None
    tp: int
    pred: int
    gold: int

    def __str__(self) -> str:
        if self.f1 is None:
            return "-"
        lo, hi = self.ci or (self.f1, self.f1)
        return f"{self.f1:.3f} [{lo:.3f}–{hi:.3f}]"


def _micro_f1(scores: Sequence[TurnScore]) -> float:
    tp = sum(s.tp for s in scores)
    pred = sum(s.pred for s in scores)
    gold = sum(s.gold for s in scores)
    if pred + gold == 0:
        return 1.0
    return 2 * tp / (pred + gold)


def call_f1(scores: Sequence[TurnScore], *, b: int = BOOTSTRAP, seed: int = 0) -> F1:
    """호출 F1(마이크로): 라우터 원출력의 (이름, 정규화 인자) 멀티셋 vs gold. 호출도 정답도 없는 턴은 빼지 않는다(0/0)."""
    tp, pred, gold = sum(s.tp for s in scores), sum(s.pred for s in scores), sum(s.gold for s in scores)
    if not scores:
        return F1(None, None, None, None, 0, 0, 0)
    p = tp / pred if pred else None
    r = tp / gold if gold else None
    return F1(p, r, _micro_f1(scores), bootstrap_ci(list(scores), _micro_f1, b=b, seed=seed), tp, pred, gold)


@dataclass(frozen=True)
class Latency:
    values: tuple[float, ...]

    def __str__(self) -> str:
        if not self.values:
            return "-"
        return f"{percentile(self.values, 0.5):.2f}s / {percentile(self.values, 0.95):.2f}s (n={len(self.values)})"


@dataclass
class ModeMetrics:
    router: str
    mode: str
    scores: list[TurnScore]
    scenarios_exact: Rate
    decision: Rate
    binary: Rate
    f1: F1
    turn_exact: Rate
    llm_attempts: int
    blocked: int
    guard_pass: Rate
    reasons: Counter
    raw_grounded: Rate
    final_grounded: Rate
    sentences_ok: Rate
    bare_list: Rate
    canned: Rate
    tool_call_attempt: Rate
    replaced: Rate
    reply_kinds: Counter
    recalled: Rate                   # M7 loop: 결과 뒤 재호출
    two_rounds: Rate                 # M7 loop: 2라운드 실행
    loop_decisions: Counter          # M7 loop: 결과 뒤 판단(라운드마다)
    router_latency: Latency
    answer_latency: Latency
    first_token: Latency
    turn_latency: Latency
    errors: list[TurnRecord]
    by_category: dict[str, Rate]


def compute_metrics(records: Sequence[TurnRecord], *, b: int = BOOTSTRAP, seed: int = 0) -> ModeMetrics:
    scores = [score_turn(r) for r in records]
    by_scenario: dict[str, list[TurnScore]] = defaultdict(list)
    for s in scores:
        by_scenario[s.record.scenario.id].append(s)
    exact = [all(x.turn_exact for x in turns) for turns in by_scenario.values()]
    cats: dict[str, list[bool]] = defaultdict(list)
    for turns in by_scenario.values():
        cats[turns[0].record.scenario.category].append(all(x.turn_exact for x in turns))
    attempted = [s for s in scores if s.llm_attempted]
    reasons: Counter = Counter()
    for s in attempted:
        if s.guard_pass is False:
            reasons.update(s.reasons)
    router_live = [c.seconds for r in records for c in r.router_calls if not c.cached and c.seconds is not None]
    # 답변 지연: a3·a1은 답변 모델, loop는 결과 뒤 라우터 호출(M7)
    answer_live = [c.seconds for r in records for c in [*r.answer_calls, *r.loop_calls] if c.seconds is not None]
    ttft = [c.first_token for r in records for c in r.answer_calls if c.first_token is not None]
    first = records[0] if records else None
    return ModeMetrics(
        router=first.router if first else "", mode=first.mode if first else "", scores=scores,
        scenarios_exact=rate(exact, b=b, seed=seed), decision=rate([s.decision_ok for s in scores], b=b, seed=seed),
        binary=rate([s.binary_ok for s in scores], b=b, seed=seed), f1=call_f1(scores, b=b, seed=seed),
        turn_exact=rate([s.turn_exact for s in scores], b=b, seed=seed), llm_attempts=len(attempted),
        blocked=sum(s.blocked for s in scores),
        guard_pass=rate([s.guard_pass for s in attempted], b=b, seed=seed), reasons=reasons,
        raw_grounded=rate([s.raw_grounded for s in attempted], b=b, seed=seed),
        final_grounded=rate([s.final_grounded for s in scores], b=b, seed=seed),
        sentences_ok=rate([s.sentences_ok for s in attempted], b=b, seed=seed),
        bare_list=rate([s.bare_list for s in attempted], b=b, seed=seed),
        canned=rate([s.canned for s in attempted], b=b, seed=seed),
        tool_call_attempt=rate([s.tool_call_attempt for s in attempted], b=b, seed=seed),
        replaced=rate([s.replaced for s in attempted], b=b, seed=seed),
        reply_kinds=Counter(s.record.reply_kind or "-" for s in scores),
        recalled=rate([s.recalled for s in scores], b=b, seed=seed),
        two_rounds=rate([s.two_rounds for s in scores], b=b, seed=seed),
        loop_decisions=Counter(e.get("decision") or "?" for r in records for e in r.after_tools),
        router_latency=Latency(tuple(router_live)), answer_latency=Latency(tuple(answer_live)),
        first_token=Latency(tuple(ttft)),
        # 라우터는 첫 모드만 실제로 부르므로(나머지는 캐시) 턴 지연은 라우터 호출 시간을 빼서 모드끼리 비교
        turn_latency=Latency(tuple(max(0.0, r.seconds - sum(c.seconds or 0.0 for c in r.router_calls if not c.cached))
                                   for r in records)),
        errors=[r for r in records if r.error],
        by_category={c: rate(v, b=b, seed=seed) for c, v in sorted(cats.items())})


# --- 보고서 ---------------------------------------------------------------------------

def _clip(text: str | None, n: int = 70) -> str:
    text = " ".join((text or "").split()).replace("|", "/")
    return text if len(text) <= n else text[: n - 1] + "…"


def _mode_label(m: Mode) -> str:
    if m.after_tools == "loop":   # M7
        return f"{m.name}(결과 뒤 라우터 · 대체는 템플릿)"
    return (f"{m.name}({'템플릿' if m.answer == 'template' else f'답변 {m.prompt} · '}"
            f"{'' if m.answer == 'template' else ('라우터 서버' if m.server == 'router' else 'a1 서버')})")


def _shown_path(path: str) -> str:
    """보고서용 시나리오 경로: agent/ 안이면 상대 경로(커밋하는 보고서에 사용자 이름이 든 절대 경로를 남기지 않음)."""
    if not path:
        return DEFAULT_SCENARIOS.relative_to(AGENT_DIR).as_posix()
    p = Path(path).expanduser().resolve()
    try:
        return p.relative_to(AGENT_DIR.resolve()).as_posix()
    except ValueError:
        return p.name


def format_report(run: ABRun, *, b: int = BOOTSTRAP, seed: int = 0, failures: int = 15) -> str:
    metrics = {key: compute_metrics(recs, b=b, seed=seed) for key, recs in run.records.items()}
    n_turns = sum(len(s.turns) for s in run.scenarios)
    cats = Counter(s.category for s in run.scenarios)
    tags = Counter(t for s in run.scenarios for t in s.tags)
    lines = [f"# M4 답변 A/B ({run.started:%Y-%m-%d %H:%M} KST)", "",
             f"- 시나리오: `{_shown_path(run.scenario_path)}` · {len(run.scenarios)}개(턴 {n_turns}개) · "
             + ", ".join(f"{c} {n}" for c, n in sorted(cats.items())),
             "- 태그: " + ", ".join(f"{t} {n}" for t, n in sorted(tags.items())),
             *[f"- 모델: {line}" for line in run.provider],
             *([f"- loop 모드의 결과 뒤 요청: 라우터 {run.loop_router} 서버(첫 판단은 각 라우터)"] if run.loop_router else []),
             "- 모드: " + ", ".join(_mode_label(m) for m in run.modes),
             f"- 도구 결과: 가짜 Spring fixture(FakeBackend), 기준 시각 {AB_NOW:%Y-%m-%d %H:%M} KST. 라우터 응답은 모드 사이 캐시",
             f"- 비율은 k/N (%) [부트스트랩 95% 구간, B={b}, seed {seed}]. 지연은 p50 / p95. 걸린 시간 {run.seconds:.1f}초", ""]

    lines += ["## 라우터 (모드와 무관: 첫 모드 기준)", "",
              "| 라우터 | 첫 판단(도구·되묻기·직답) | 첫 판단(도구/아님, evaluate.py) | 궤적 완전 일치(대화) | 턴 일치 | "
              "호출 F1 | P / R | 라우터 지연 |", "|---|---|---|---|---|---|---|---|"]
    for router in run.routers:
        m = metrics[(router, run.modes[0].name)]
        pr = f"{m.f1.precision:.3f} / {m.f1.recall:.3f}" if m.f1.precision is not None and m.f1.recall is not None \
            else "-"
        lines.append(f"| {router} | {m.decision} | {m.binary} | {m.scenarios_exact} | {m.turn_exact} | {m.f1} | {pr} | "
                     f"{m.router_latency} |")
    lines.append("")

    for router in run.routers:
        ms = [metrics[(router, mode.name)] for mode in run.modes]
        lines += [f"## 답변: 라우터 {router}", "", "| 지표 | " + " | ".join(m.mode for m in ms) + " |",
                  "|---|" + "---|" * len(ms)]
        rows: list[tuple[str, Callable[[ModeMetrics], Any]]] = [
            ("LLM 답 시도(턴)", lambda m: m.llm_attempts),
            ("보내기 전 차단", lambda m: m.blocked),
            ("가드 통과", lambda m: m.guard_pass),
            ("미리보기 뒤 교체", lambda m: m.replaced),
            ("원답 근거(이름·숫자)", lambda m: m.raw_grounded),
            ("최종 답 근거(결과 있는 턴)", lambda m: m.final_grounded),
            ("2~4문장", lambda m: m.sentences_ok),
            ("이름만 나열", lambda m: m.bare_list),
            ("정형 문장", lambda m: m.canned),
            ("<tool_call> 시도", lambda m: m.tool_call_attempt),
            ("답 종류", lambda m: ", ".join(f"{k} {v}" for k, v in sorted(m.reply_kinds.items()))),
            ("답변 지연(loop는 결과 뒤 라우터)", lambda m: m.answer_latency),
            ("첫 토큰", lambda m: m.first_token),
            ("턴 지연(라우터 호출 제외)", lambda m: m.turn_latency),
            ("오류 턴", lambda m: len(m.errors)),
        ]
        if any(mode.after_tools == "loop" for mode in run.modes):   # M7 loop 지표(다른 모드는 '-')
            rows += [
                ("결과 뒤 재호출(loop)", lambda m: m.recalled),
                ("2라운드 실행(loop)", lambda m: m.two_rounds),
                ("결과 뒤 판단(loop)", lambda m: ", ".join(f"{k} {v}" for k, v in sorted(m.loop_decisions.items()))
                 or "-"),
            ]
        for label, get in rows:
            lines.append(f"| {label} | " + " | ".join(str(get(m)) for m in ms) + " |")
        reasons = sorted({r for m in ms for r in m.reasons})
        if reasons:
            lines += ["", "가드 실패 이유(실패한 답 기준, 한 답에 여러 이유 가능):", "",
                      "| 이유 | " + " | ".join(m.mode for m in ms) + " |", "|---|" + "---|" * len(ms)]
            for reason in reasons:
                lines.append(f"| {reason} | " + " | ".join(str(m.reasons.get(reason, 0)) for m in ms) + " |")
        lines.append("")

    first_mode = run.modes[0].name
    lines += ["## 범주별 궤적 완전 일치(대화 단위)", "", "| 범주 | " + " | ".join(run.routers) + " |",
              "|---|" + "---|" * len(run.routers)]
    for cat in sorted(cats):
        lines.append(f"| {cat} | " + " | ".join(str(metrics[(r, first_mode)].by_category.get(cat, '-'))
                                                for r in run.routers) + " |")
    lines.append("")

    lines += ["## 틀린 턴·가드 실패 (앞 {0}개)".format(failures), ""]
    shown = 0
    for (router, mode), m in metrics.items():
        for s in m.scores:
            rec = s.record
            bad = []
            if mode == first_mode and not s.decision_ok:
                bad.append(f"판단 {s.decision}≠{rec.turn.expected_decision}")
            if mode == first_mode and not s.turn_exact:
                bad.append(f"결과 {rec.outcome}≠{rec.turn.expected_outcome}")
            if s.guard_pass is False:
                bad.append("가드 " + ",".join(s.reasons))
            if rec.error:
                bad.append(f"오류 {_clip(rec.error, 50)}")
            if not bad or shown >= failures:
                continue
            shown += 1
            lines.append(f"- `{rec.scenario.id}`#{rec.index + 1} {router}×{mode}: {'; '.join(bad)} · "
                         f"“{_clip(rec.turn.user, 40)}” → {_clip(s.raw_text or rec.final_text)}")
    if not shown:
        lines.append("- 없음")
    lines += ["", "정의: 첫 판단 = 턴의 첫 라우터 응답(되묻기는 학습 되묻기 문장). 궤적 완전 일치 = 대화의 모든 턴에서 결과 종류"
              "(tool/plan/clarify/direct/fallback)와 validate 뒤 실행된 호출 집합이 정답과 같음. 호출 F1 = 마지막 라우터 응답의"
              " (이름, 정규화 인자) 멀티셋. 근거 = 결과 이름을 하나 이상 말하고 결과·발화에 없는 숫자가 없음(guard.py와 같은 숫자 규칙)."
              " 2~4문장·이름만 나열·정형 문장·<tool_call>은 LLM 원답(가드 전) 기준."]
    return "\n".join(lines) + "\n"


def raw_rows(run: ABRun) -> Iterator[dict]:
    """턴마다 한 줄(--raw). 발화·모델 답 글이 들어가므로 로컬에만. 토큰·사용자 id는 없음."""
    for (router, mode), recs in run.records.items():
        for rec in recs:
            s = score_turn(rec)
            yield {"scenario": rec.scenario.id, "turn": rec.index + 1, "router": router, "mode": mode,
                   "user": rec.turn.user, "router_input": rec.router_input, "decision": s.decision,
                   "expected_decision": rec.turn.expected_decision, "outcome": rec.outcome,
                   "expected_outcome": rec.turn.expected_outcome,
                   "router_calls": router_calls_of(rec.router_calls[-1] if rec.router_calls else None),
                   "executed": rec.executed, "results": rec.results, "reply_kind": rec.reply_kind,
                   "guard": rec.answer_trace.get("guard"), "reasons": list(s.reasons), "raw_text": s.raw_text,
                   "final_text": rec.final_text, "raw_grounded": s.raw_grounded, "final_grounded": s.final_grounded,
                   "router_seconds": [c.seconds for c in rec.router_calls],
                   "answer_seconds": [c.seconds for c in rec.answer_calls],
                   "first_token": [c.first_token for c in rec.answer_calls], "turn_seconds": round(rec.seconds, 4),
                   "rounds": rec.rounds, "after_tools": [e.get("decision") for e in rec.after_tools],
                   "loop_seconds": [c.seconds for c in rec.loop_calls], "error": rec.error}


# --- CLI -------------------------------------------------------------------------------

def _routers(args: argparse.Namespace) -> dict[str, str]:
    routers: dict[str, str] = {}
    if args.router_url:
        routers[args.router_name or "router"] = args.router_url
    for spec in args.router or []:
        name, sep, url = spec.partition("=")
        if not sep or not name.strip() or not url.strip():
            raise ValueError(f"--router는 이름=URL 꼴이에요: {spec!r}")
        routers[name.strip()] = url.strip()
    return routers


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m glople_agent.eval.abtest", description="M4 답변 A/B(design.md 15.2)")
    p.add_argument("--router-url", default=None, help="라우터 서버(OpenAI 호환), 예: http://127.0.0.1:8090/v1")
    p.add_argument("--router-name", default=None,
                   help="--router-url의 이름(표에 나옴, 기본 router). --fake·--cassette에서는 라우터 이름만(카세트 파일 이름)")
    p.add_argument("--router", action="append", metavar="NAME=URL", help="어댑터마다 라우터 서버(여러 번)")
    p.add_argument("--answer-url", default=None, help="a3 답변 서버(기본: 각 라우터와 같은 서버)")
    p.add_argument("--a1-url", default=None, help="a1 답변 서버(기본 모델), 예: http://127.0.0.1:8091/v1")
    p.add_argument("--modes", default="a3,a3-v2,template", help=f"쉼표로: {', '.join(MODES)}")
    p.add_argument("--run-routers", default=None, metavar="NAME,...",
                   help="이 라우터만 돈다(기본: --router 전부). 결과 뒤 전용 서버를 --router로 등록할 때 씀")
    p.add_argument("--loop-router", default=None, metavar="NAME",
                   help="(M7) loop 모드의 결과 뒤 요청을 이 라우터 서버로(예: --router v3=… --router v4=… --loop-router v4)")
    p.add_argument("--scenarios", default=str(DEFAULT_SCENARIOS), help="시나리오 JSONL")
    p.add_argument("--ids", default=None, help="이 id만(쉼표로)")
    p.add_argument("--limit", type=int, default=None, help="앞에서 N개만")
    p.add_argument("--out", default=None, help="Markdown 보고서 경로(없으면 화면에)")
    p.add_argument("--raw", default=None, help="턴마다 JSON 한 줄(발화·답 글 포함, 로컬 전용)")
    where = p.add_mutually_exclusive_group()
    where.add_argument("--fake", action="store_true", help="가짜 모델(서버 없음, 지표 계산 확인용)")
    where.add_argument("--cassette", default=None, metavar="DIR", help="녹화한 카세트로 재생(서버 없음)")
    p.add_argument("--record", default=None, metavar="DIR", help="실제 서버 호출을 DIR에 카세트로 녹화")
    p.add_argument("--plan-graph", choices=("flat", "subgraph"), default="flat", help="일정 노드 판(M3)")
    p.add_argument("--timeout", type=float, default=20.0, help="모델 요청 timeout(초)")
    p.add_argument("--bootstrap", type=int, default=BOOTSTRAP, help="부트스트랩 반복 수")
    p.add_argument("--seed", type=int, default=0, help="부트스트랩 seed")
    return p


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    args = parser().parse_args(argv)
    out = out or sys.stdout
    try:
        modes = parse_modes(args.modes)
        scenarios = load_scenarios(args.scenarios)
        if args.ids:
            wanted = [i.strip() for i in args.ids.split(",") if i.strip()]
            missing = sorted(set(wanted) - {s.id for s in scenarios})
            if missing:
                raise ValueError(f"없는 시나리오 id: {missing}")
            scenarios = [s for s in scenarios if s.id in wanted]
        if args.bootstrap < 1:
            raise ValueError("--bootstrap은 1 이상이에요")
        if args.limit is not None:
            if args.limit < 1:
                raise ValueError("--limit은 1 이상이에요")
            scenarios = scenarios[: args.limit]
        if args.record and (args.fake or args.cassette):
            raise ValueError("--record는 실제 서버에서만(--fake·--cassette와 함께 쓰지 않음)")
        provider: Provider
        if args.fake:
            provider, routers = FakeProvider(scenarios), list(_routers(args)) or [args.router_name or "fake"]
        elif args.cassette:
            provider = CassetteProvider(args.cassette)
            routers = list(_routers(args)) or [args.router_name or "router"]
        else:
            real = RealProvider(_routers(args), answer_url=args.answer_url, a1_url=args.a1_url, timeout=args.timeout)
            for mode in modes:   # a1인데 --a1-url이 없으면 시작 전에 멈춤
                for router in real.routers:
                    if mode.answer == "llm":
                        real.answer_base(router, mode)
            routers = list(real.routers)
            provider = RecordingProvider(real, args.record) if args.record else real
        if args.loop_router and args.loop_router not in routers:
            raise ValueError(f"--loop-router는 --router 이름 중 하나여야 해요: {args.loop_router!r}")
        if args.run_routers:
            wanted_routers = [r.strip() for r in args.run_routers.split(",") if r.strip()]
            unknown = sorted(set(wanted_routers) - set(routers))
            if unknown:
                raise ValueError(f"--run-routers에 없는 라우터: {unknown}")
            routers = wanted_routers
    except ValueError as e:
        print(str(e), file=out)
        return 2
    run = run_ab(provider, scenarios, routers=routers, modes=modes, plan_graph=args.plan_graph,
                 retry_initial_interval=0.0 if args.fake or args.cassette else 0.5, scenario_path=args.scenarios,
                 progress=lambda line: print(line, file=sys.stderr, flush=True), loop_router=args.loop_router)
    if args.raw:   # 보고서보다 먼저(긴 실제 실행 뒤 보고서에서 문제가 나도 원자료는 남게)
        Path(args.raw).parent.mkdir(parents=True, exist_ok=True)
        with Path(args.raw).open("w", encoding="utf-8") as f:
            for row in raw_rows(run):
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = format_report(run, b=args.bootstrap, seed=args.seed)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"보고서: {args.out}", file=out)
    else:
        print(report, file=out)
    return 0


__all__ = [
    "AB_NOW", "AB_TOKEN", "ABRun", "CassetteProvider", "DEFAULT_SCENARIOS", "FakeAnswerScript", "FakeProvider",
    "MODES", "Mode", "ModeMetrics", "ProbeCall", "ProbeChatModel", "Rate", "RealProvider", "RecordingProvider",
    "ResponseMemo",
    "Scenario", "ScenarioOracle", "Turn", "TurnRecord", "TurnScore", "bootstrap_ci", "call_f1", "compute_metrics",
    "format_report", "gold_message", "grounded", "is_bare_list", "load_scenarios", "main", "outcome_of",
    "parse_modes", "percentile", "rate", "raw_rows", "router_decision", "run_ab", "run_mode", "score_turn",
    "sentence_parts",
]


if __name__ == "__main__":
    sys.exit(main())

"""`cli tool`: 도구 하나를 실행해 LLM용 content와 카드 JSON을 나란히 보여 준다(design.md 13절 M1).

같은 길을 탄다: (선택) validate와 같은 판정(judge_call: 스키마 + 근거 검사) → registry.run_tool → 어댑터 →
SpringClient(경로 허용 목록, 공개 경로엔 토큰 없음) → 가짜 Spring(fixture, 기본) 또는 실제 Spring(--live).
마지막에 'content에 카드 원문(설명문·발췌·소개)이 20자 이상 겹치지 않는지'를 검사해 보여 준다(Type3 격리, 11절).
"""
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from glople_agent import contract
from glople_agent.tools.adapters import SpringBackend
from glople_agent.tools.args import arg_errors, validate_args
from glople_agent.tools.backend import ToolContext
from glople_agent.tools.envelope import MAX_CONTENT_CHARS, ToolBackendError, classify_error, tool_error_to_json
from glople_agent.tools.fake_backend import FakeBackend
from glople_agent.tools.fixture_spring import SeenRequest
from glople_agent.tools.registry import run_tool
from glople_agent.tools.spring import SpringClient

CARD_TEXT_FIELDS = ("text", "excerpt", "introduction")   # 카드에만 있어야 하는 글
OVERLAP_CHARS = 20                                       # 답변 가드(6.5)와 같은 겹침 기준
CALL_ID = "cli-tool"


def _squash(s: str) -> str:
    return " ".join(s.split())


def overlaps(content: str, text: str, n: int = OVERLAP_CHARS) -> bool:
    """text의 n자 조각이 content에 그대로 들어 있는지(공백 정규화 뒤)."""
    c, t = _squash(content), _squash(text)
    return any(t[i:i + n] in c for i in range(0, max(len(t) - n + 1, 0)))


class _RecordingTransport(httpx.BaseTransport):
    """실제 Spring(--live)으로 나간 요청을 fixture와 같은 모양으로 남긴다."""

    def __init__(self) -> None:
        self.inner = httpx.HTTPTransport()
        self.requests: list[SeenRequest] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append(SeenRequest(request.method, request.url.path, dict(request.url.params.items()), body,
                                         "authorization" in request.headers))
        return self.inner.handle_request(request)

    def close(self) -> None:
        self.inner.close()


@dataclass
class ToolReport:
    name: str
    given: Any
    backend: str
    now: datetime
    utterance: str | None = None
    verdict: dict | None = None
    args: dict | None = None
    problems: list[str] = field(default_factory=list)
    requests: list[SeenRequest] = field(default_factory=list)
    content: str | None = None
    card: dict | None = None
    leaks: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.problems:
            return 2
        return 1 if self.leaks else 0

    def format(self) -> str:
        lines = [f"도구 {self.name} · 인자 {json.dumps(self.given, ensure_ascii=False)} · 백엔드 {self.backend} · "
                 f"기준일 {self.now.date().isoformat()}"]
        if self.utterance is not None and self.verdict is not None:
            issues = (self.verdict.get("grounding") or {}).get("issues") or []
            head = "통과" if self.args is not None else f"막힘({self.verdict['reason']})"
            lines.append(f"[근거 검사] 발화 {self.utterance!r} → {head}" + (f" · {', '.join(issues)}" if issues else ""))
            if self.verdict.get("clarify"):
                lines.append(f"  되묻기: {self.verdict['clarify']}")
            if self.args is not None and self.args != self.given:
                lines.append(f"  고친 인자: {json.dumps(self.args, ensure_ascii=False)}")
        for p in self.problems:
            lines.append(f"[인자] {p}")
        if self.content is None:
            return "\n".join(lines)
        if self.requests:
            for r in self.requests:
                what = (f"query {json.dumps(r.params, ensure_ascii=False)}" if r.params else "") + \
                       (f"body {json.dumps(r.body, ensure_ascii=False)}" if r.body is not None else "")
                auth = "Authorization 있음(!)" if r.has_authorization else "Authorization 없음"
                lines.append(f"[HTTP] {r.method} {r.path} · {what or '인자 없음'} · {auth}")
        else:
            lines.append("[HTTP] 부르지 않음(준비 중·미지원이거나 어댑터가 미리 막음)")
        body = json.loads(self.content)
        lines += ["", f"── LLM용 content ({len(self.content)}자 / 상한 {MAX_CONTENT_CHARS}, 모델 요청에 들어가는 것) ──",
                  json.dumps(body, ensure_ascii=False, indent=2)]
        if self.card is not None:
            lines += ["", "── 카드 JSON (ToolMessage.artifact → state.cards, 화면 전용: 원문·출처·이미지) ──",
                      json.dumps(self.card, ensure_ascii=False, indent=2)]
            lines += ["", "── 나란히: 항목별로 LLM에 간 필드 / 카드에만 있는 필드 ──"]
            llm_items = body.get("items") or []
            for i, item in enumerate(self.card.get("data", {}).get("items") or []):
                llm = llm_items[i] if i < len(llm_items) else {}
                only = [k for k in item if k not in llm and item.get(k) not in (None, [], "")]
                label = item.get("name") or item.get("title") or item.get("nickname") or item.get("ageBand") or i + 1
                lines.append(f"  {label}: LLM {sorted(llm) or '(안 감)'} | 카드만 {only}")
        if self.leaks:
            lines.append(f"[검사] content에 카드 원문이 {OVERLAP_CHARS}자 이상 겹침: {self.leaks}")
        else:
            lines.append(f"[검사] content에 카드 원문(설명문·발췌·소개, FREE_FACTS 틀 문장 제외) {OVERLAP_CHARS}자 겹침 0건")
        return "\n".join(lines)


def _leaks(content: str, card: Mapping[str, Any] | None) -> list[str]:
    out = []
    for i, item in enumerate(((card or {}).get("data") or {}).get("items") or []):
        for f in CARD_TEXT_FIELDS:
            if f == "text" and item.get("textPolicy") == "FREE_FACTS":
                continue   # Glople가 수집 필드로 만든 틀 문장은 60자까지 LLM에 가도 됨(11절 표)
            text = item.get(f)
            if isinstance(text, str) and len(_squash(text)) >= OVERLAP_CHARS and overlaps(content, text):
                out.append(f"items[{i}].{f}")
    return out


def inspect_tool(name: str, given: Any, *, utterance: str | None = None, now: datetime,
                 live_base_url: str | None = None, backend: SpringBackend | None = None) -> ToolReport:
    from glople_agent.graph.nodes import judge_call   # graph → tools 순서로 import(순환 피함)

    if live_base_url:
        recorder = _RecordingTransport()
        backend = backend or SpringBackend(SpringClient(live_base_url, transport=recorder))
        label, requests = f"live({live_base_url})", recorder.requests
    else:
        backend = backend or FakeBackend(today=now.date())
        label = "fixture(가짜 Spring, 네트워크 없음)"
        requests = backend.fixture.requests if isinstance(backend, FakeBackend) else []
    report = ToolReport(name=name, given=given, backend=label, now=now, utterance=utterance)
    if name == contract.PLAN_TOOL_NAME:
        report.problems.append("create_travel_plan은 확인 카드(interrupt)를 거쳐야 해서 chat에서만 저장합니다")
        return report
    if name not in contract.READ_TOOL_NAMES:
        report.problems.append(f"모르는 도구입니다. 읽기 도구: {', '.join(contract.READ_TOOL_NAMES)}")
        return report
    if utterance is not None:
        verdict, clean = judge_call(name, given, utterance, known=contract.READ_TOOL_NAMES)
        report.verdict = verdict
        if clean is None:
            return report
        report.args = clean
    else:
        if errors := arg_errors(name, given):
            report.problems.append("스키마에 맞지 않음: " + ", ".join(f"{f or '(전체)'}:{k}" for f, k in errors))
            return report
        report.args = validate_args(name, given).model_dump(exclude_none=True)
    try:
        report.content, report.card = run_tool(name, backend, report.args, CALL_ID, ToolContext(now=now))
    except ToolBackendError as e:   # ToolNode의 handle_tool_errors와 같은 봉투
        report.content = tool_error_to_json(e)
    except Exception as e:  # noqa: BLE001 - 직접 실행이라 ToolNode처럼 오류 봉투로 보여 줌
        report.content = tool_error_to_json(ToolBackendError(name, classify_error(e)))
    report.requests = list(requests)
    report.leaks = _leaks(report.content, report.card)
    return report


__all__ = ["CARD_TEXT_FIELDS", "OVERLAP_CHARS", "ToolReport", "inspect_tool", "overlaps"]

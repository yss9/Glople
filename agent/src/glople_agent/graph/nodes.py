"""그래프 노드(design.md 5.3). 노드는 (state, runtime)을 받아 상태 갱신 dict나 Command를 돌려준다.

- 모델·도구·저장소는 state에 넣지 않고 make_* 팩토리의 클로저로 받는다(build.py의 Deps).
- 토큰은 runtime.context(AgentContext)에만 있고, 꺼내는 노드는 save_plan 하나다.
- 최종 AIMessage는 finish만 만든다. 다른 노드는 final_text·reply_kind만 정한다.
- 일정 노드(plan_prepare·review·save_plan)는 M3부터 graph/plan_subgraph.py에 있다(평평한 판·서브그래프 판 공용).
- 도구 결과 뒤 루프 노드(route_after_tools·validate_after_tools, M7)는 graph/after_tools.py에 있다(after_tools="loop"일 때만).
- 노드의 state 타입 주석은 ParentState(일정 키 없음)다. langgraph는 노드 함수 첫 인자의 TypedDict 주석을 그 노드의 입력
  스키마로 보고 그 키들을 그래프 채널에 더하므로, AgentState로 적으면 서브그래프 판 부모에도 plan_* 채널이 생긴다(api-notes M3).
"""
import json
import operator
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import reduce
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (AIMessage, AnyMessage, HumanMessage, RemoveMessage, SystemMessage,
                                     ToolMessage)
from langchain_core.messages.tool import tool_call as make_tool_call
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from langgraph.types import Overwrite

from glople_agent import contract
from glople_agent.answer import templates as tpl
from glople_agent.context import AgentContext
from glople_agent.graph.answer_input import build_answer_input
from glople_agent.graph.edges import ROUTE_RETRY_ATTEMPTS, fallback_reason
from glople_agent.graph.followup import compose_router_input
from glople_agent.graph.grounding import Grounding, check_call
from glople_agent.graph.guard import MAX_ANSWER_CHARS, answer_facts, check_answer, input_leaks
from glople_agent.llm.normalize import (RouterOutcome, is_answer_model_error, is_model_connection_error,
                                        is_model_error, normalize_router, normalize_router_error, retry_overrides)
from glople_agent.llm.prompts import answer_system_prompt
from glople_agent.sanitize import clean_text
from glople_agent.state import Card, ParentState
from glople_agent.tools.args import ARGS_MODELS, arg_errors, validate_args
from glople_agent.tools.envelope import card_from_tool_message
from glople_agent.tools.regions import resolve as resolve_region
from glople_agent.tools.regions import text_tokens

PLAN = contract.PLAN_TOOL_NAME
MAX_CALLS_PER_TURN = 4      # 턴당 도구 호출 상한(같은 서명은 한 번)
MAX_RECENT_PLACES = 50      # memory.recent_places 상한
CLARIFY_KINDS = frozenset({"missing", "enum", "type", "value"})  # 사용자에게 다시 물을 인자 오류
# 짝 없이 남은 호출을 닫는 ToolMessage 문장(모델 요청에만 쓰일 수 있고 화면에는 안 나옴)
UNFINISHED_CALL = "처리를 마치지 못한 요청이라 닫았어요."

Ctx = Runtime[AgentContext]


# --- 공용 도우미 --------------------------------------------------------------------

def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b if isinstance(b, str) else str(b.get("text", "")) for b in content
                       if isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text"))
    return "" if content is None else str(content)


def _last_human_index(messages: Sequence[AnyMessage]) -> int | None:
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return i
    return None


def turn_messages(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """마지막 HumanMessage 뒤(이번 턴) 메시지."""
    i = _last_human_index(messages)
    return list(messages) if i is None else list(messages[i + 1:])


def router_message(state: ParentState) -> AIMessage | None:
    """이번 턴 라우터 AIMessage(validate가 같은 id로 교체한 것). 없으면 None."""
    for m in reversed(turn_messages(state.get("messages") or [])):
        if isinstance(m, AIMessage) and m.tool_calls:
            return m
    return None


def unpaired_calls(messages: Iterable[AnyMessage]) -> list[dict]:
    """ToolMessage 짝이 없는 tool_call 목록(순서대로)."""
    messages = list(messages)
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    return [tc for m in messages if isinstance(m, AIMessage) for tc in m.tool_calls if tc["id"] not in answered]


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _tool_error(call_id: str, tool: str, error: str, message: str) -> ToolMessage:
    content = _dumps({"tool": tool, "status": "error", "error": error, "message_ko": message})
    return ToolMessage(content=content, tool_call_id=call_id, name=tool, status="error")


def _cancelled(calls: Iterable[dict]) -> list[ToolMessage]:
    return [_tool_error(tc["id"], tc["name"], "cancelled",
                        tpl.PLAN_PREVIOUS_CANCELLED if tc["name"] == PLAN else UNFINISHED_CALL) for tc in calls]


def _with_trace(state: ParentState, key: str, value: Any) -> dict:
    """trace는 리듀서 없는 채널이라 읽어서 고친 새 dict를 돌려준다(병렬 노드가 없어 충돌 없음)."""
    trace = dict(state.get("trace") or {})
    trace[key] = value
    return trace


def _emit(event: dict) -> None:
    get_stream_writer()(event)   # stream_mode에 custom이 없으면 아무것도 안 함


def _signature(name: str, args: Mapping[str, Any]) -> str:
    return name + ":" + json.dumps(args, ensure_ascii=False, sort_keys=True)


# --- start_turn ---------------------------------------------------------------------

def _turn_of_call(messages: Sequence[AnyMessage], call_id: str) -> str:
    """그 호출이 나온 턴의 turn_id(= 그 앞 HumanMessage id, start_turn과 같은 규칙)."""
    turn = ""
    for m in messages:
        if isinstance(m, HumanMessage):
            turn = m.id or turn
        elif isinstance(m, AIMessage) and any(tc["id"] == call_id for tc in m.tool_calls):
            return turn
    return turn


def _cancelled_plan_cards(state: ParentState, calls: Iterable[dict]) -> list[Card]:
    """닫는 일정 호출의 확인 카드를 취소 상태로 바꾼 카드(reject 재개와 같은 모양: status error, cancelled).

    서브그래프 판(M3)에서는 대기 중이던 확인 카드가 부모 state에 없다(서브그래프가 END에 닿기 전 쓰기는 부모로 안 옴).
    그때는 같은 card_id의 취소 카드를 새로 만든다(data.draft는 None: 초안은 버려진 서브그래프 체크포인트에만 있었음)."""
    calls = [tc for tc in calls if tc["name"] == PLAN]
    by_id = {c["card_id"]: c for c in state.get("cards") or []}
    out: list[Card] = []
    for tc in calls:
        card = by_id.get(plan_card_id(tc["id"]))
        if card is not None and card["kind"] != "plan_confirm":
            continue
        if card is not None:
            closed: Card = {**card, "status": "error", "data": {**card["data"], "cancelled": True}}
        else:
            closed = {"card_id": plan_card_id(tc["id"]), "call_id": tc["id"], "tool": PLAN, "kind": "plan_confirm",
                      "turn_id": _turn_of_call(state.get("messages") or [], tc["id"]), "status": "error",
                      "data": {"draft": None, "cancelled": True}}
        _emit({"event": "card", "card": closed})
        out.append(closed)   # card_id가 같아 merge_cards가 제자리에서 교체(turn_id는 그 카드의 턴 그대로)
    return out


def start_turn(state: ParentState) -> dict:
    """마지막 HumanMessage를 정제해 라우터 한 마디로 두고, 이번 턴 필드를 비운다.

    지난 턴에 짝 없이 남은 tool_call(확인 카드가 떠 있는데 새 메시지가 온 경우, 중간 실패)은
    cancelled ToolMessage로 닫고, 일정 호출이면 그 확인 카드도 취소 상태로 바꾼다.
    원문 HumanMessage는 그대로 둔다(라우터용 문장만 정제).
    """
    messages = state.get("messages") or []
    i = _last_human_index(messages)
    human = messages[i] if i is not None else None
    text = clean_text(_text(human.content) if human is not None else "")   # 특수 토큰·HTML 제거, 500자
    stale = unpaired_calls(messages[:i] if i is not None else messages)
    update_cards = _cancelled_plan_cards(state, stale) if stale else []
    memory = state.get("memory") or {}
    # M1 후속 발화: 되묻기 답이면 직전 발화에 이어 붙이고, 거기·그곳·그쪽은 last_region으로(라우터 문장에서만)
    router_input, followup = compose_router_input(text, memory)
    trace: dict[str, Any] = {}
    if stale:
        trace["closed_stale_calls"] = len(stale)
    if followup:
        trace["followup"] = followup
    return {
        "messages": _cancelled(stale),
        **({"cards": update_cards} if update_cards else {}),
        **({"memory": {"pending_clarify": None}} if memory.get("pending_clarify") else {}),   # 다음 한 턴만
        "call_log": Overwrite([]),          # operator.add 채널을 턴마다 비움
        "turn_id": (human.id if human is not None and human.id else None) or f"turn-{uuid.uuid4()}",
        "user_text": text,
        "router_input": router_input,
        "router_kind": None,
        "router_attempts": 0,
        "verdicts": [],
        "results": [],
        # 일정 키: 평평한 판만 비운다. 서브그래프 판의 부모(ParentState)에는 이 채널이 없어 langgraph가 조용히 버리고,
        # 서브그래프는 턴마다 새 namespace라 빈 값에서 시작한다(plan_subgraph.py)
        "plan_draft": None,
        "plan_edits": 0,
        "plan_notice": None,
        "final_text": None,
        "reply_kind": None,
        "trace": trace,
    }


# --- route --------------------------------------------------------------------------

def make_route(router_llm: BaseChatModel, *, max_connection_attempts: int = ROUTE_RETRY_ATTEMPTS):
    """라우터: [System(SYSTEM_PROMPT), Human(한 마디)] + 도구 8개(glople_tools.json dict 그대로).

    이전 대화는 넣지 않는다(학습 분포). 연결 오류는 RetryPolicy가 다시 부르도록 그대로 던지되,
    마지막 시도(runtime.execution_info.node_attempt)에서는 잡아서 error로 바꿔 fallback으로 보낸다.
    """
    bound = router_llm.bind_tools(contract.TOOLS)   # tool_choice·strict·parallel_tool_calls는 넘기지 않음

    def route(state: ParentState, runtime: Ctx) -> dict:
        attempts = state.get("router_attempts", 0) + 1
        overrides = (retry_overrides(state.get("router_kind") or "") or {}) if attempts > 1 else {}
        request = [SystemMessage(content=contract.SYSTEM_PROMPT), HumanMessage(content=state.get("router_input", ""))]
        info = getattr(runtime, "execution_info", None)   # 1.2.12: RetryPolicy 시도 번호(1부터)
        node_attempt = info.node_attempt if info is not None else 1
        try:
            outcome = normalize_router(bound.invoke(request, **overrides))
        except Exception as e:  # noqa: BLE001 - 모델 오류만 RouterOutcome으로, 나머지(우리 버그)는 다시 던짐
            if is_model_connection_error(e) and node_attempt < max_connection_attempts:
                raise   # RetryPolicy(retry_on=is_model_connection_error)가 한 번 더
            if not is_model_error(e):
                raise
            outcome = normalize_router_error(e)
        entry = {"attempt": attempts, "node_attempt": node_attempt, "kind": outcome.kind,
                 "overrides": overrides, "detail": outcome.detail}
        update: dict[str, Any] = {
            "router_kind": outcome.kind,
            "router_attempts": attempts,
            "trace": _with_trace(state, "route", [*(state.get("trace") or {}).get("route", []), entry]),
        }
        if outcome.kind == "tool":
            update["messages"] = [_with_id(outcome)]
        elif outcome.kind == "text":
            update["final_text"] = outcome.text   # direct_reply가 그대로 씀(최종 메시지는 finish가 만듦)
        return update

    return route


def _with_id(outcome: RouterOutcome) -> AIMessage:
    msg = outcome.message
    assert msg is not None
    # validate가 같은 id로 교체·삭제하므로 id가 꼭 있어야 한다(add_messages는 없는 id를 새로 만듦)
    return msg if msg.id else msg.model_copy(update={"id": f"router-{uuid.uuid4()}"})


# --- validate -----------------------------------------------------------------------

def _clarify_text(name: str, fields: Sequence[str], args: Mapping[str, Any]) -> str | None:
    category_word = args.get("category") if isinstance(args.get("category"), str) else None
    try:
        return tpl.clarify_for(name, fields, category_word=category_word)
    except ValueError:   # 되묻기 표에 없는 칸(제목 등)만 틀림 → 그 도구의 기본 되묻기
        return tpl.clarify_for(name, (), category_word=category_word)


def grounding_clarify_text(name: str, g: Grounding, args: Mapping[str, Any]) -> str | None:
    """근거 검사가 되물을 때의 문장. 모호한 지역은 후보를, 나머지는 labeling_rules 6절 문장."""
    if "clarify:region_ambiguous" in g.issues and len(g.candidates) >= 2:
        return tpl.clarify_region_choice(g.candidates)
    return _clarify_text(name, g.clarify_fields, args)


def judge_call(name: str, args: Any, text: str, *, known: Iterable[str], recent_places: Iterable[str] = (),
               region_clarify_tools: frozenset[str] = frozenset()) -> tuple[dict[str, Any], dict | None]:
    """호출 하나의 판정: 이름 → 스키마(required·enum·int·모르는 키) → 근거(grounding, M1).

    (verdict, 실행할 인자)를 돌려준다. 실행할 인자는 실행 모델로 정규화하고 근거 검사가 고친 값("3"→3, 앞뒤 공백,
    MBTI 대문자, 키워드 목록 표기, 근거 없는 선택 인자 뺌). 실패면 None. 중복·턴당 상한·일정 단독은 validate가 본다.
    validate 노드와 근거 검사 측정(eval/grounding_eval.py)이 같은 함수를 쓴다.
    """
    verdict: dict[str, Any] = {"name": name, "ok": False, "reason": "ok"}
    if name not in set(known):
        verdict["reason"] = "unknown_tool"
        return verdict, None
    errors = arg_errors(name, args)
    extra = [e.field for e in errors if e.kind == "extra"]
    if errors and len(extra) == len(errors) and isinstance(args, dict):
        # 모르는 키만 틀림(v1·v2 모두 search_reviews에 limit을 붙임) → 그 키만 빼고 나머지로 계속(design.md 7절 '빼기')
        args = {k: v for k, v in args.items() if k not in extra}
        errors = arg_errors(name, args)
    if errors:
        fields = list(dict.fromkeys(e.field for e in errors if e.kind in CLARIFY_KINDS and e.field))
        verdict.update(reason="schema", errors=[list(e) for e in errors])
        if fields or not any(e.kind == "extra" for e in errors):
            verdict["clarify"] = _clarify_text(name, fields, args)
        return verdict, None
    clean = validate_args(name, args).model_dump(exclude_none=True)
    g = check_call(name, clean, text, recent_places=recent_places, region_clarify_tools=region_clarify_tools)
    issues = [*(["drop:extra_key"] * bool(extra)), *g.issues]
    if issues:
        verdict["grounding"] = {**g.as_trace(), "issues": issues}
    if not g.ok:
        verdict.update(reason="grounding", clarify=grounding_clarify_text(name, g, clean))
        return verdict, None
    verdict["ok"] = True
    return verdict, validate_args(name, g.args).model_dump(exclude_none=True)


def place_region_hint(answer: str, name: str) -> str | None:
    """동명 장소 되묻기에 대한 답('강화요', '서울이요')에서 지역 낱말 하나. 이름에 이미 있으면 None."""
    for token in text_tokens(answer):
        if token and token not in name and resolve_region(token).status == "ok":
            return token
    return None


def _with_place_hint(clean: dict, verdict: dict, answer: str) -> tuple[dict, dict]:
    """M6: 지난 턴이 동명 장소 되묻기였으면 답의 지역 낱말을 이름 앞에 붙인다('보문사' + '서울이요' → '서울 보문사').
    어댑터가 '서울 보문사'를 '보문사' 중 서울 행으로 좁힌다(destinations.split_region_word). 라우터는 한 마디만 받아
    이름만 다시 낼 가능성이 커서, 그대로 두면 같은 되묻기가 되풀이된다."""
    hint = place_region_hint(answer, clean.get("name") or "")
    if hint is None:
        return clean, verdict
    grounding = dict(verdict.get("grounding") or {"issues": []})
    grounding["issues"] = [*grounding.get("issues", []), "fix:place_region"]
    return {**clean, "name": f"{hint} {clean['name']}"}, {**verdict, "grounding": grounding}


def make_validate(tool_names: Iterable[str], *, region_clarify_tools: Iterable[str] = ()):
    """이름·스키마·근거(M1)·중복 서명·create_travel_plan 단독·턴당 4개.

    통과한 호출만 남기도록 라우터 AIMessage를 같은 id로 교체한다(인자는 judge_call이 정규화·고친 값).
    전부 탈락이면 RemoveMessage. 원출력은 trace["validate"]에 남긴다.
    region_clarify_tools: 지역 해석이 모호하면(광주) 후보를 되물을 도구. M1에는 비웠고(지역을 쓰는 백엔드가 없었음),
    M6부터 그래프 기본(Deps)은 S1 검색·날씨다. 발화의 다른 낱말이 후보 하나를 가리키면 되묻지 않고 고친다(fix:region_choice).
    (M6) 지난 턴이 동명 장소 되묻기였으면 상세 호출 이름 앞에 답의 지역 낱말을 붙인다(fix:place_region).
    """
    known = (set(tool_names) & set(ARGS_MODELS)) | {PLAN}
    region_tools = frozenset(region_clarify_tools)

    def validate(state: ParentState) -> dict:
        ai = router_message(state)
        if ai is None:
            return {"verdicts": [], "trace": _with_trace(state, "validate", {"error": "no_router_message"})}
        seen = set(state.get("call_log") or [])
        used = len(seen)
        text = state.get("router_input", "")
        recent = (state.get("memory") or {}).get("recent_places") or []
        verdicts: list[dict] = []
        passed: list[dict] = []          # 실행할 호출(정규화·근거 검사로 고친 인자)
        joined = ((state.get("trace") or {}).get("followup") or {}).get("joined") or {}
        place_answer = state.get("user_text", "") if joined.get("reason") == PLACE_AMBIGUOUS else None
        for tc in ai.tool_calls:
            name, args, cid = tc["name"], tc.get("args") or {}, tc["id"]
            verdict, clean = judge_call(name, args, text, known=known, recent_places=recent,
                                        region_clarify_tools=region_tools)
            verdict = {"tool_call_id": cid, **verdict}
            if clean is not None and name == "get_destination_detail" and place_answer:
                clean, verdict = _with_place_hint(clean, verdict, place_answer)
            if clean is not None:
                sig = _signature(name, clean)
                if sig in seen:
                    verdict.update(ok=False, reason="duplicate")
                elif used + len(passed) >= MAX_CALLS_PER_TURN:
                    verdict.update(ok=False, reason="over_limit")
                else:
                    seen.add(sig)
                    verdict["signature"] = sig
                    passed.append(make_tool_call(name=name, args=clean, id=cid))
            verdicts.append(verdict)

        plan_calls = [c for c in passed if c["name"] == PLAN]
        if plan_calls:   # 학습 데이터에 일정과 다른 도구가 섞인 예는 0건 → 일정 하나만 처리
            keep = plan_calls[0]["id"]
            for v in verdicts:
                if v["ok"] and v["tool_call_id"] != keep:
                    v.update(ok=False, reason="plan_not_alone")
            passed = [plan_calls[0]]

        if passed:
            new_ai = AIMessage(content="", tool_calls=passed, id=ai.id, name=ai.name,
                               response_metadata=dict(ai.response_metadata or {}), usage_metadata=ai.usage_metadata)
            message_update: list[AnyMessage] = [new_ai]   # 같은 id → add_messages가 제자리에서 교체
        else:
            message_update = [RemoveMessage(id=ai.id)]     # 전부 탈락 → 짝 없는 호출이 남지 않게 삭제
        raw = [{"id": tc["id"], "name": tc["name"], "args": tc.get("args")} for tc in ai.tool_calls]
        return {
            "messages": message_update,
            "verdicts": verdicts,
            "call_log": [v["signature"] for v in verdicts if v["ok"]],
            "trace": _with_trace(state, "validate", {"raw": raw, "passed": len(passed)}),
        }

    return validate


# --- collect · 답변 ---------------------------------------------------------------------

REGION_ARG_KEYS = ("hashtag", "query", "name")   # region 인자가 없을 때 지역 이름이면 last_region으로 쓸 인자


def region_of_args(args: Mapping[str, Any]) -> str | None:
    """호출 인자에서 '이번에 말한 지역'(사용자 표기 그대로). region이 있으면 그것, 아니면 글로플러 해시태그·후기
    검색어·장소 이름 중 지역 해석기가 아는 이름(지시어 '거기'를 다음 턴에 이것으로 바꿈)."""
    region = args.get("region")
    if isinstance(region, str) and region.strip():
        return region.strip()
    for key in REGION_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip() and resolve_region(value).status != "not_found":
            return value.strip().lstrip("#")
    return None


def _memory_after(memory: Mapping[str, Any] | None, cards: Sequence[Card]) -> dict:
    old = list((memory or {}).get("recent_places") or [])
    names: list[str] = []
    region = None
    for card in cards:
        data = card.get("data") or {}
        if card["status"] == "ok" and card["kind"] in ("destination_list", "destination_detail"):
            names += [i["name"] for i in data.get("items") or [] if isinstance(i, dict) and i.get("name")]
        region = region_of_args(data.get("args") or {}) or region
    update: dict[str, Any] = {}
    if names:
        update["recent_places"] = list(dict.fromkeys([*names, *old]))[:MAX_RECENT_PLACES]
    if region:
        update["last_region"] = region
    return update


def collect(state: ParentState) -> dict:
    """이번 ToolMessage의 artifact → cards(호출 순서), results 기록, card 이벤트, memory 갱신.

    원문(Type3 포함)은 cards에만 둔다(11절 ⑥). 카드를 만든 ToolMessage는 같은 id로 교체해
    artifact를 {"card_id"} 참조로 줄인다(add_messages가 제자리에서 바꿈, content는 그대로).
    (M7 loop) 두 번째 라운드는 마지막 호출 묶음만 카드로 만들고 results를 앞 라운드 뒤에 덧붙인다. 턴마다 start_turn이
    results를 비우므로 answer 모드(한 턴에 collect 한 번)에서는 덧붙일 것이 없어 지금과 같다.
    """
    ai = router_message(state)
    calls = ai.tool_calls if ai is not None else []
    by_id = {m.tool_call_id: m for m in turn_messages(state.get("messages") or []) if isinstance(m, ToolMessage)}
    cards: list[Card] = []
    results: list[dict] = []
    slim: list[ToolMessage] = []
    for tc in calls:
        msg = by_id.get(tc["id"])
        if msg is None:
            continue   # 짝 없는 호출은 finish가 닫음
        card = card_from_tool_message(msg, turn_id=state["turn_id"], args=tc.get("args"))
        cards.append(card)
        result = {"tool_call_id": tc["id"], "tool": tc["name"], "status": card["status"]}
        if (card.get("data") or {}).get("needsClarification"):   # M6 동명 장소 → after_collect가 clarify로
            result["clarify"] = True
        results.append(result)
        if msg.artifact is not None and msg.id:   # id가 없으면 교체 대신 덧붙여지므로 건드리지 않음
            slim.append(msg.model_copy(update={"artifact": {"card_id": card["card_id"]}}))
    for card in cards:
        _emit({"event": "card", "card": card})
    new_ids = {r["tool_call_id"] for r in results}
    earlier = [r for r in state.get("results") or [] if r.get("tool_call_id") not in new_ids]
    update: dict[str, Any] = {"cards": cards, "results": [*earlier, *results]}
    if slim:
        update["messages"] = slim
    if memory := _memory_after(state.get("memory"), cards):
        update["memory"] = memory
    return update


def turn_cards(state: ParentState) -> list[Card]:
    """이번 턴 도구 카드(results 순서 = 호출 순서)."""
    by_call = {c["call_id"]: c for c in state.get("cards") or []
               if c.get("turn_id") == state.get("turn_id") and c.get("tool") != PLAN}
    return [by_call[r["tool_call_id"]] for r in state.get("results") or [] if r["tool_call_id"] in by_call]


def _clarify_questions(state: ParentState) -> list[str]:
    return [v["clarify"] for v in state.get("verdicts") or [] if not v["ok"] and v.get("clarify")]


def _card_questions(state: ParentState) -> list[tuple[str, str]]:
    """(도구, 되묻기 문장): 이번 턴 결과 카드 중 되물을 것(M6 동명 장소 후보)."""
    return [(c.get("tool") or "", (c.get("data") or {}).get("clarify"))
            for c in turn_cards(state) if (c.get("data") or {}).get("needsClarification")
            and (c.get("data") or {}).get("clarify")]


def _notices(state: ParentState) -> list[str]:
    """(M7 loop) 막힌 호출이 남긴 안내 문장(루프 안 일정 호출 → tpl.PLAN_IN_LOOP). answer 모드에는 없다."""
    return [v["notice"] for v in state.get("verdicts") or [] if not v["ok"] and v.get("notice")]


def answer_template(state: ParentState) -> dict:
    """도구별 정해진 문장(카드에 있는 사실만). 같이 탈락한 호출의 되묻기 문장(M7 loop: 안내 문장도)을 뒤에 붙인다."""
    text = tpl.join_answers([tpl.answer_for_cards(turn_cards(state)), *_clarify_questions(state), *_notices(state)])
    return {"final_text": text, "reply_kind": "template"}


REJECTED_TEXT_MAX = 600   # 가드에 걸린 답을 trace에 남기는 길이(카드 원문과 겹친 답은 남기지 않음)


def _answer_trace(base: Mapping[str, Any], *, ok: bool, reasons: Sequence[str], replaced: bool,
                  **extra: Any) -> dict:
    return {"guard": "pass" if ok else "fail", "reason": None if ok else (list(reasons) or ["unknown"])[0],
            "reasons": list(reasons), "reply_kind": "llm" if ok else "template", "replaced": replaced,
            **base, **extra}


def make_answer_llm(answer_llm: BaseChatModel | None, *, max_answer_chars: int = MAX_ANSWER_CHARS,
                    prompt: str = "v1"):
    """M2 a3: 답변 전용 프롬프트(6.4)로 스트리밍 생성 → 가드(6.5). 통과하면 reply_kind="llm"으로 finish,
    걸리면 trace에 이유만 남기고 answer_template이 같은 턴의 템플릿 답으로 바꾼다(after_answer).

    - 입력은 graph/answer_input.py: 이번 턴 한 마디 + LLM이 볼 호출·결과만(도구 목록 없음, 카드 원문 없음).
    - 토큰은 custom 스트림 {"event": "token", "text"}로 미리보기를 낸다(첫 토큰이 오면 템플릿 prefix를 먼저).
      messages 스트림 모드에도 이 노드의 AIMessageChunk가 그대로 나온다(api-notes M0 A11).
    - 스트림이 끊기거나 모델 오류면(is_answer_model_error) 가드 실패와 같게 템플릿으로. 우리 코드 버그는 다시 던진다.
    - replaced: 미리보기를 이미 보낸 뒤 템플릿으로 바뀌었는지(SSE message{replaced}, design.md 6.5).
    - prompt: 답변 system 문구 이름(M4, "v1" = design.md 6.4 / "v2" = llm/prompts.ANSWER_SYSTEM_PROMPT_V2). trace에 남긴다.
    """
    system_prompt = answer_system_prompt(prompt)

    def answer_llm_node(state: ParentState) -> dict:
        if answer_llm is None:   # llm 모드인데 모델을 주지 않음(설정 실수) → 템플릿
            return {"trace": _with_trace(state, "answer_llm", {"guard": "fail", "reason": "no_model"})}
        cards = turn_cards(state)
        ai = router_message(state)
        tool_msgs = {m.tool_call_id: m for m in turn_messages(state.get("messages") or [])
                     if isinstance(m, ToolMessage)}
        router_input = state.get("router_input", "")
        inp = build_answer_input(router_input, ai.tool_calls if ai is not None else [], tool_msgs,
                                 {c["call_id"]: c for c in cards}, system_prompt=system_prompt)
        base = {"visible": [v["tool"] for v in inp.visible], "removed": [r["tool"] for r in inp.removed],
                "input_chars": inp.tool_chars, "prompt": prompt}

        def fail(reasons: Sequence[str], replaced: bool, **extra: Any) -> dict:
            trace = _answer_trace(base, ok=False, reasons=reasons, replaced=replaced, **extra)
            return {"trace": _with_trace(state, "answer_llm", trace)}

        if not inp.visible:   # after_collect가 llm_answerable 결과가 있을 때만 보냄(방어)
            return fail(["no_llm_result"], False)
        facts = answer_facts(inp.contents, cards, router_input)
        if leaks := input_leaks(inp.contents, facts):   # 어댑터 실수로 원문이 섞였으면 보내지 않음
            return fail(["input_overlap"], False, input_overlap=leaks)

        chunks: list[Any] = []
        streamed = 0
        try:
            for chunk in answer_llm.stream(inp.messages):   # 도구를 묶지 않는다(bind_tools 없음)
                chunks.append(chunk)
                piece = _text(chunk.content)
                if piece:
                    if not streamed and inp.prefix:
                        _emit({"event": "token", "text": inp.prefix + " "})
                    _emit({"event": "token", "text": piece})
                    streamed += len(piece)
        except Exception as e:  # noqa: BLE001 - 모델 오류만 템플릿으로, 나머지(우리 버그)는 다시 던짐
            if not is_answer_model_error(e):
                raise
            return fail(["model_error"], streamed > 0, error=type(e).__name__, streamed_chars=streamed)
        merged = reduce(operator.add, chunks) if chunks else AIMessage(content="")
        finish = (merged.response_metadata or {}).get("finish_reason")
        verdict = check_answer(merged, facts, finish_reason=finish, max_chars=max_answer_chars)
        text = _text(merged.content).strip()
        if not verdict.ok:
            extra: dict[str, Any] = {**verdict.details, "streamed_chars": streamed}
            if "card_overlap" not in verdict.reasons:
                extra["rejected_text"] = text[:REJECTED_TEXT_MAX]
            return fail(verdict.reasons, streamed > 0, **extra)
        final = " ".join(p for p in (inp.prefix, text, tpl.join_answers(_clarify_questions(state))) if p)
        trace = _answer_trace(base, ok=True, reasons=(), replaced=False, **verdict.details, streamed_chars=streamed)
        return {"final_text": final, "reply_kind": "llm", "trace": _with_trace(state, "answer_llm", trace)}

    return answer_llm_node


def pending_clarify(state: ParentState, reason: str, tool: str | None) -> dict:
    """다음 턴 start_turn이 이번 발화에 답을 이어 붙이도록 남기는 기억(memory.pending_clarify)."""
    return {"pending_clarify": {"utterance": state.get("router_input", ""), "reason": reason, "tool": tool}}


def _clarify_reason(v: Mapping[str, Any]) -> str:
    issues = (v.get("grounding") or {}).get("issues") or []
    first = next((i for i in issues if i.startswith("clarify:")), None)
    return first or f"{v.get('reason', 'schema')}"


PLACE_AMBIGUOUS = "clarify:place_ambiguous"   # M6: 동명 장소 되묻기(다음 턴 validate가 답의 지역 낱말을 이름에 붙임)


def clarify(state: ParentState) -> dict:
    """되묻기: validate가 막은 호출의 문장(근거·스키마·모호한 지역) + (M6) 결과 카드의 동명 장소 문장(collect → clarify)."""
    questions = _clarify_questions(state)
    card_questions = _card_questions(state)
    first = next((v for v in state.get("verdicts") or [] if not v["ok"] and v.get("clarify")), None)
    text = tpl.join_answers([*questions, *(q for _, q in card_questions)])
    update: dict[str, Any] = {"final_text": text or tpl.FALLBACK, "reply_kind": "clarify"}
    if first is not None and state.get("router_input"):
        update["memory"] = pending_clarify(state, _clarify_reason(first), first.get("name"))
    elif card_questions and state.get("router_input"):
        update["memory"] = pending_clarify(state, PLACE_AMBIGUOUS, card_questions[0][0])
    return update


def direct_reply(state: ParentState) -> dict:
    """라우터가 낸 문장(인사·거절·되묻기) 그대로. 학습한 되묻기 문장이면 pending_clarify를 남긴다(M1)."""
    text = state.get("final_text") or tpl.FALLBACK
    update: dict[str, Any] = {"final_text": text, "reply_kind": "direct"}
    if (kind := tpl.clarify_kind(text)) is not None and state.get("router_input"):
        update["memory"] = pending_clarify(state, f"router:{kind}", None)
    return update


def fallback(state: ParentState) -> dict:
    reason = fallback_reason(state)
    return {"final_text": tpl.fallback_text(reason), "reply_kind": "fallback",
            "trace": _with_trace(state, "fallback", reason)}


# --- 일정 노드 공용(노드 자체는 graph/plan_subgraph.py) -------------------------------------

def plan_card_id(call_id: str) -> str:
    return f"plan:draft:{call_id}"


# --- finish -------------------------------------------------------------------------

TrajectorySink = Callable[[dict], None]


def trajectory_record(state: ParentState) -> dict:
    """궤적 한 줄(eval/trajectory.py가 파일로). 토큰·사용자 id·원문(발화·인자 값·결과 글)은 넣지 않는다.
    호출은 이름·판정·근거 검사 코드만, 결과는 도구·상태만 남긴다(막힌 호출로 오탐을 보려는 용도, design.md 7절)."""
    trace = state.get("trace") or {}
    followup = trace.get("followup") or {}
    verdicts = []
    for v in state.get("verdicts") or []:
        row = {k: v[k] for k in ("name", "ok", "reason")}
        if issues := (v.get("grounding") or {}).get("issues"):
            row["issues"] = list(issues)
        if v.get("round"):   # M7 loop: 두 번째 라운드 호출
            row["round"] = v["round"]
        verdicts.append(row)
    record = {"turn_id": state.get("turn_id"), "router_kind": state.get("router_kind"),
              "router_attempts": state.get("router_attempts", 0), "reply_kind": state.get("reply_kind"),
              "followup": {"joined": "joined" in followup and "skipped" not in followup["joined"],
                           "demonstratives": followup.get("demonstratives", 0)},
              "verdicts": verdicts,
              "results": [{"tool": r.get("tool"), "status": r.get("status")} for r in state.get("results") or []],
              "remaining_steps": state.get("remaining_steps")}
    if answer := trace.get("answer_llm"):   # M2: 가드 판정(이유 코드·교체 여부만, 답 글은 넣지 않음)
        reasons = answer.get("reasons")
        if reasons is None:
            reasons = [answer["reason"]] if answer.get("reason") else []
        record["answer"] = {"guard": answer.get("guard"), "reasons": list(reasons),
                            "replaced": bool(answer.get("replaced"))}
        if answer.get("via"):   # M7 loop: 도구 결과 뒤 라우터의 글(a3 답변 노드가 아님)
            record["answer"]["via"] = answer["via"]
    if steps := trace.get("after_tools"):   # M7 loop: 라운드마다 판단 코드만(글·인자 없음)
        record["after_tools"] = [{"round": e.get("round"), "decision": e.get("decision")} for e in steps]
    return record


def make_finish(sink: TrajectorySink | None = None):
    def finish(state: ParentState) -> dict:
        """최종 AIMessage는 여기서만 만든다. 이번 턴에 짝 없이 남은 tool_call은 cancelled로 닫는다."""
        closing = _cancelled(unpaired_calls(turn_messages(state.get("messages") or [])))
        text = state.get("final_text") or tpl.FALLBACK
        if any(v.get("reason") == "plan_not_alone" for v in state.get("verdicts") or []):
            text = tpl.join_answers([tpl.PLAN_ONE_AT_A_TIME, text])
        reply_kind = state.get("reply_kind") or "fallback"
        if sink is not None:
            sink({**trajectory_record(state), "reply_kind": reply_kind})
        return {"messages": [*closing, AIMessage(content=text)], "final_text": text, "reply_kind": reply_kind}

    return finish


__all__ = [
    "CLARIFY_KINDS", "MAX_CALLS_PER_TURN", "MAX_RECENT_PLACES", "REJECTED_TEXT_MAX",
    "PLACE_AMBIGUOUS", "TrajectorySink", "UNFINISHED_CALL", "answer_template", "clarify", "collect",
    "direct_reply", "fallback", "grounding_clarify_text", "judge_call", "make_answer_llm",
    "make_finish", "make_route", "make_validate", "pending_clarify", "place_region_hint", "plan_card_id",
    "region_of_args", "router_message", "start_turn", "trajectory_record", "turn_cards",
    "turn_messages", "unpaired_calls",
]

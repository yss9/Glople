"""그래프 노드(design.md 5.3). 노드는 (state, runtime)을 받아 상태 갱신 dict나 Command를 돌려준다.

- 모델·도구·저장소는 state에 넣지 않고 make_* 팩토리의 클로저로 받는다(build.py의 Deps).
- 토큰은 runtime.context(AgentContext)에만 있고, 꺼내는 노드는 save_plan 하나다.
- 최종 AIMessage는 finish만 만든다. 다른 노드는 final_text·reply_kind만 정한다.
"""
import json
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (AIMessage, AnyMessage, HumanMessage, RemoveMessage, SystemMessage,
                                     ToolMessage)
from langchain_core.messages.tool import tool_call as make_tool_call
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from langgraph.types import Command, Overwrite, interrupt

from glople_agent import contract
from glople_agent.answer import templates as tpl
from glople_agent.context import AgentContext
from glople_agent.graph.edges import ROUTE_RETRY_ATTEMPTS, fallback_reason
from glople_agent.llm.normalize import (RouterOutcome, is_model_connection_error, is_model_error,
                                        normalize_router, normalize_router_error, retry_overrides)
from glople_agent.plan import (AuthExpired, PlanDecision, PlanRejected, PlanStore, PlanStoreError, apply_edit,
                               check_draft, check_edit, make_draft, save_body, selected_items)
from glople_agent.sanitize import clean_text
from glople_agent.state import AgentState, Card, PlanDraft
from glople_agent.tools.args import ARGS_MODELS, arg_errors, validate_args
from glople_agent.tools.envelope import card_from_tool_message

PLAN = contract.PLAN_TOOL_NAME
MAX_CALLS_PER_TURN = 4      # 턴당 도구 호출 상한(같은 서명은 한 번)
MAX_PLAN_EDITS = 3          # 확인 카드 edit 최대 횟수
MAX_RECENT_PLACES = 50      # memory.recent_places 상한
SAVE_ATTEMPTS = 2           # 일정 저장: 연결 오류·5xx만 1회 더(서버 멱등키가 중복을 막음)
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


def router_message(state: AgentState) -> AIMessage | None:
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


def _with_trace(state: AgentState, key: str, value: Any) -> dict:
    """trace는 리듀서 없는 채널이라 읽어서 고친 새 dict를 돌려준다(병렬 노드가 없어 충돌 없음)."""
    trace = dict(state.get("trace") or {})
    trace[key] = value
    return trace


def _emit(event: dict) -> None:
    get_stream_writer()(event)   # stream_mode에 custom이 없으면 아무것도 안 함


def _signature(name: str, args: Mapping[str, Any]) -> str:
    return name + ":" + json.dumps(args, ensure_ascii=False, sort_keys=True)


# --- start_turn ---------------------------------------------------------------------

def _cancelled_plan_cards(state: AgentState, calls: Iterable[dict]) -> list[Card]:
    """닫는 일정 호출의 확인 카드를 취소 상태로 바꾼 카드(reject 재개와 같은 모양: status error, cancelled)."""
    ids = {plan_card_id(tc["id"]) for tc in calls if tc["name"] == PLAN}
    out: list[Card] = []
    for card in state.get("cards") or []:
        if card["card_id"] in ids and card["kind"] == "plan_confirm":
            closed: Card = {**card, "status": "error", "data": {**card["data"], "cancelled": True}}
            _emit({"event": "card", "card": closed})
            out.append(closed)   # card_id가 같아 merge_cards가 제자리에서 교체(turn_id는 그 카드의 턴 그대로)
    return out


def start_turn(state: AgentState) -> dict:
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
    return {
        "messages": _cancelled(stale),
        **({"cards": update_cards} if update_cards else {}),
        "call_log": Overwrite([]),          # operator.add 채널을 턴마다 비움
        "turn_id": (human.id if human is not None and human.id else None) or f"turn-{uuid.uuid4()}",
        "user_text": text,
        "router_input": text,               # M1: pending_clarify 이어 붙이기·지시어 풀이
        "router_kind": None,
        "router_attempts": 0,
        "verdicts": [],
        "results": [],
        "plan_draft": None,
        "plan_edits": 0,
        "plan_notice": None,
        "final_text": None,
        "reply_kind": None,
        "trace": {"closed_stale_calls": len(stale)} if stale else {},
    }


# --- route --------------------------------------------------------------------------

def make_route(router_llm: BaseChatModel, *, max_connection_attempts: int = ROUTE_RETRY_ATTEMPTS):
    """라우터: [System(SYSTEM_PROMPT), Human(한 마디)] + 도구 8개(glople_tools.json dict 그대로).

    이전 대화는 넣지 않는다(학습 분포). 연결 오류는 RetryPolicy가 다시 부르도록 그대로 던지되,
    마지막 시도(runtime.execution_info.node_attempt)에서는 잡아서 error로 바꿔 fallback으로 보낸다.
    """
    bound = router_llm.bind_tools(contract.TOOLS)   # tool_choice·strict·parallel_tool_calls는 넘기지 않음

    def route(state: AgentState, runtime: Ctx) -> dict:
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


def make_validate(tool_names: Iterable[str]):
    """이름·스키마(required·enum·int·모르는 키)·중복 서명·create_travel_plan 단독·턴당 4개.

    통과한 호출만 남기도록 라우터 AIMessage를 같은 id로 교체한다(인자는 실행 모델로 정규화: "3"→3,
    앞뒤 공백, MBTI 대문자). 전부 탈락이면 RemoveMessage. 원출력은 trace["validate"]에 남긴다.
    """
    known = (set(tool_names) & set(ARGS_MODELS)) | {PLAN}

    def validate(state: AgentState) -> dict:
        ai = router_message(state)
        if ai is None:
            return {"verdicts": [], "trace": _with_trace(state, "validate", {"error": "no_router_message"})}
        seen = set(state.get("call_log") or [])
        used = len(seen)
        verdicts: list[dict] = []
        passed: list[dict] = []          # 실행할 호출(정규화한 인자)
        for tc in ai.tool_calls:
            name, args, cid = tc["name"], tc.get("args") or {}, tc["id"]
            verdict: dict[str, Any] = {"tool_call_id": cid, "name": name, "ok": False, "reason": "ok"}
            if name not in known:
                verdict["reason"] = "unknown_tool"
            elif errors := arg_errors(name, args):
                fields = list(dict.fromkeys(e.field for e in errors if e.kind in CLARIFY_KINDS and e.field))
                verdict.update(reason="schema", errors=[list(e) for e in errors])
                if fields or not any(e.kind == "extra" for e in errors):
                    verdict["clarify"] = _clarify_text(name, fields, args)
            else:
                clean = validate_args(name, args).model_dump(exclude_none=True)
                sig = _signature(name, clean)
                if sig in seen:
                    verdict["reason"] = "duplicate"
                elif used + len(passed) >= MAX_CALLS_PER_TURN:
                    verdict["reason"] = "over_limit"
                else:
                    seen.add(sig)
                    verdict.update(ok=True, signature=sig)
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

def _memory_after(memory: Mapping[str, Any] | None, cards: Sequence[Card]) -> dict:
    old = list((memory or {}).get("recent_places") or [])
    names: list[str] = []
    region = None
    for card in cards:
        data = card.get("data") or {}
        if card["status"] == "ok" and card["kind"] in ("destination_list", "destination_detail"):
            names += [i["name"] for i in data.get("items") or [] if isinstance(i, dict) and i.get("name")]
        if isinstance((data.get("args") or {}).get("region"), str):
            region = data["args"]["region"]
    update: dict[str, Any] = {}
    if names:
        update["recent_places"] = list(dict.fromkeys([*names, *old]))[:MAX_RECENT_PLACES]
    if region:
        update["last_region"] = region
    return update


def collect(state: AgentState) -> dict:
    """이번 ToolMessage의 artifact → cards(호출 순서), results 기록, card 이벤트, memory 갱신.

    원문(Type3 포함)은 cards에만 둔다(11절 ⑥). 카드를 만든 ToolMessage는 같은 id로 교체해
    artifact를 {"card_id"} 참조로 줄인다(add_messages가 제자리에서 바꿈, content는 그대로).
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
        results.append({"tool_call_id": tc["id"], "tool": tc["name"], "status": card["status"]})
        if msg.artifact is not None and msg.id:   # id가 없으면 교체 대신 덧붙여지므로 건드리지 않음
            slim.append(msg.model_copy(update={"artifact": {"card_id": card["card_id"]}}))
    for card in cards:
        _emit({"event": "card", "card": card})
    update: dict[str, Any] = {"cards": cards, "results": results}
    if slim:
        update["messages"] = slim
    if memory := _memory_after(state.get("memory"), cards):
        update["memory"] = memory
    return update


def turn_cards(state: AgentState) -> list[Card]:
    """이번 턴 도구 카드(results 순서 = 호출 순서)."""
    by_call = {c["call_id"]: c for c in state.get("cards") or []
               if c.get("turn_id") == state.get("turn_id") and c.get("tool") != PLAN}
    return [by_call[r["tool_call_id"]] for r in state.get("results") or [] if r["tool_call_id"] in by_call]


def _clarify_questions(state: AgentState) -> list[str]:
    return [v["clarify"] for v in state.get("verdicts") or [] if not v["ok"] and v.get("clarify")]


def answer_template(state: AgentState) -> dict:
    """도구별 정해진 문장(카드에 있는 사실만). 같이 탈락한 호출의 되묻기 문장을 뒤에 붙인다."""
    text = tpl.join_answers([tpl.answer_for_cards(turn_cards(state)), *_clarify_questions(state)])
    return {"final_text": text, "reply_kind": "template"}


def make_answer_llm(answer_llm: BaseChatModel | None):
    """M2 자리: 답변 전용 프롬프트(6.4)로 생성 → 가드(6.5). M0는 template 모드라 엣지가 여기로 오지 않는다.
    모드를 llm으로 바꿔 닿더라도 답을 만들지 않고 trace만 남겨 answer_template으로 넘긴다."""

    def answer_llm_node(state: AgentState) -> dict:
        reason = "no_model" if answer_llm is None else "not_implemented"
        return {"trace": _with_trace(state, "answer_llm", {"guard": "fail", "reason": reason})}

    return answer_llm_node


def clarify(state: AgentState) -> dict:
    questions = _clarify_questions(state)
    return {"final_text": tpl.join_answers(questions) or tpl.FALLBACK, "reply_kind": "clarify"}


def direct_reply(state: AgentState) -> dict:
    # 라우터가 낸 문장(인사·거절·되묻기) 그대로. M1: 학습한 되묻기 문장이면 pending_clarify 저장
    return {"final_text": state.get("final_text") or tpl.FALLBACK, "reply_kind": "direct"}


def fallback(state: AgentState) -> dict:
    reason = fallback_reason(state)
    return {"final_text": tpl.fallback_text(reason), "reply_kind": "fallback",
            "trace": _with_trace(state, "fallback", reason)}


# --- 일정: plan_prepare → review(interrupt) → save_plan ---------------------------------

def plan_card_id(call_id: str) -> str:
    return f"plan:draft:{call_id}"


def _plan_card(state: AgentState, draft: PlanDraft, *, kind: str = "plan_confirm", status: str = "ok",
               **data: Any) -> Card:
    call_id = draft["clientRequestId"]
    card: Card = {"card_id": plan_card_id(call_id), "turn_id": state.get("turn_id", ""), "call_id": call_id,
                  "tool": PLAN, "kind": kind, "status": status, "data": {"draft": draft, **data}}  # type: ignore[typeddict-item]
    _emit({"event": "card", "card": card})
    return card


def _plan_result(call_id: str, status: str) -> dict:
    return {"tool_call_id": call_id, "tool": PLAN, "status": status}


def plan_prepare(state: AgentState, runtime: Ctx) -> Command[Literal["review", "finish"]]:
    """create_travel_plan 인자 → PlanDraft → review. edit로 돌아온 경우는 review가 고친 초안을 그대로 다시 띄운다."""
    if (draft := state.get("plan_draft")) is not None:
        return Command(goto="review", update={"cards": [_plan_card(state, draft)]})
    ai = router_message(state)
    call = next((tc for tc in (ai.tool_calls if ai else []) if tc["name"] == PLAN), None)
    if call is None:   # after_validate가 일정 호출이 통과했을 때만 보냄
        raise RuntimeError("plan_prepare: 이번 턴 create_travel_plan 호출이 없습니다")
    ctx = runtime.context
    if ctx is None or not ctx.user_id:   # 비로그인: 카드 없이 로그인 안내
        msg = _tool_error(call["id"], PLAN, "login_required", tpl.PLAN_LOGIN_REQUIRED)
        return Command(goto="finish", update={"messages": [msg], "results": [_plan_result(call["id"], "error")],
                                              "final_text": tpl.PLAN_LOGIN_REQUIRED, "reply_kind": "plan"})
    draft = make_draft(call["args"], client_request_id=call["id"], utterance=state.get("router_input", ""),
                       recent_places=(state.get("memory") or {}).get("recent_places") or [])
    if not selected_items(draft):   # 근거 있는 장소 0곳(전부 지어낸 장소이거나 다 빠짐) → 장소 되묻기
        question = tpl.clarify_plan(["places"])
        msg = _tool_error(call["id"], PLAN, "no_places", question)
        return Command(goto="finish", update={"messages": [msg], "results": [_plan_result(call["id"], "error")],
                                              "final_text": question, "reply_kind": "clarify",
                                              "trace": _with_trace(state, "plan", {"draft": draft})})
    return Command(goto="review", update={"plan_draft": draft, "plan_edits": 0, "plan_notice": None,
                                          "cards": [_plan_card(state, draft)]})


def confirm_payload(state: AgentState) -> dict:
    """interrupt 값(확인 카드). 토큰·사용자 id는 넣지 않는다."""
    d = state["plan_draft"]
    notice = state.get("plan_notice")
    return {"type": "plan_confirm", "tool_call_id": d["clientRequestId"], "draft": d,
            "message": tpl.plan_confirm_text(d["title"]), "notice": notice,
            "notice_text": tpl.plan_notice_text(notice),
            "edits_left": max(0, MAX_PLAN_EDITS - state.get("plan_edits", 0))}


def _plan_finish(state: AgentState, draft: PlanDraft, error: str, text: str, **card_data: Any) -> dict:
    call_id = draft["clientRequestId"]
    return {"messages": [_tool_error(call_id, PLAN, error, text)],
            "results": [*(state.get("results") or []), _plan_result(call_id, "error")],
            "cards": [_plan_card(state, draft, status="error", **card_data)],
            "final_text": text, "reply_kind": "plan"}


def review(state: AgentState) -> Command[Literal["save_plan", "plan_prepare", "finish"]]:
    """확인 카드. 재개하면 이 노드가 처음부터 다시 돌므로 interrupt 앞에 부작용을 두지 않는다."""
    draft = state["plan_draft"]
    edits = state.get("plan_edits", 0)
    ans = interrupt(confirm_payload(state), response_schema=PlanDecision)
    # response_schema가 재개 값을 검증해 PlanDecision으로 돌려준다(틀리면 여기 오기 전에 ValidationError).
    # test_pins_langgraph.py::test_interrupt_response_schema_returns_validated_model
    assert isinstance(ans, PlanDecision)
    if ans.decision == "approve":
        return Command(goto="save_plan", update={"plan_notice": None})
    if ans.decision == "edit" and edits < MAX_PLAN_EDITS:
        problems = check_edit(ans.draft)   # 범위·길이(스키마 밖 의미 검사)
        return Command(goto="plan_prepare", update={"plan_draft": apply_edit(draft, ans.draft, problems),
                                                    "plan_edits": edits + 1, "plan_notice": None})
    text = tpl.PLAN_EDIT_LIMIT if ans.decision == "edit" else tpl.PLAN_CANCELLED
    return Command(goto="finish", update=_plan_finish(state, draft, "cancelled", text, cancelled=True))


def make_save_plan(store: PlanStore):
    def save_plan(state: AgentState, runtime: Ctx) -> Command[Literal["finish", "review"]]:
        """토큰을 꺼내는 유일한 노드. 멱등키 client_request_id = create_travel_plan의 tool_call_id.

        중복 저장은 호출부의 대기 id 검사(9.2 1겹)와 저장소 멱등키(3겹)가 막는다. state를 보고 건너뛰는
        2겹은 두지 않는다: 저장에 성공하면 finish → END뿐이고, 이 노드가 다시 도는 경우(시간 여행·
        저장 직후 중단)에는 state에 저장 기록이 없기 때문이다(api-notes, test_hitl의 시간 여행 테스트).
        """
        draft = state["plan_draft"]
        call_id = draft["clientRequestId"]
        ctx = runtime.context
        if ctx is None or not ctx.user_id:   # 재개에 context가 없음 → 다시 로그인해 승인하게
            return Command(goto="review", update={"plan_notice": "AUTH_EXPIRED"})
        if problems := check_draft(draft):   # Spring 400과 같은 규칙: 보내기 전에 카드로 돌려보냄
            fixed = {**draft, "problems": [p["message"] for p in problems]}
            return Command(goto="review", update={"plan_draft": fixed})
        body = save_body(draft)   # selected만(MODEL_SUGGESTED 기본 해제는 빠짐)
        result = None
        for attempt in range(1, SAVE_ATTEMPTS + 1):
            try:
                result = store.save(ctx.user_id, body, client_request_id=call_id, auth=ctx.bearer)
                break
            except AuthExpired:
                return Command(goto="review", update={"plan_notice": "AUTH_EXPIRED"})
            except PlanRejected:
                return Command(goto="finish", update=_plan_finish(state, draft, "bad_request", tpl.PLAN_SAVE_FAILED))
            except PlanStoreError as e:
                if e.retryable and attempt < SAVE_ATTEMPTS:
                    continue
                return Command(goto="finish", update=_plan_finish(state, draft, e.code, tpl.PLAN_SAVE_FAILED))
        assert result is not None
        content = _dumps({"tool": PLAN, "status": "ok", "saved": True, "plan_id": result.plan_id})
        msg = ToolMessage(content=content, tool_call_id=call_id, name=PLAN, status="success")
        card = _plan_card(state, draft, kind="plan_saved", plan_id=result.plan_id, saved=result.plan)
        return Command(goto="finish", update={
            "messages": [msg], "cards": [card],
            "results": [*(state.get("results") or []), _plan_result(call_id, "ok")],
            "final_text": tpl.plan_saved_text(draft["title"]), "reply_kind": "plan"})

    return save_plan


# --- finish -------------------------------------------------------------------------

TrajectorySink = Callable[[dict], None]


def trajectory_record(state: AgentState) -> dict:
    """궤적 한 줄(M1부터 파일로). 토큰·사용자 id·원문은 넣지 않는다."""
    return {"turn_id": state.get("turn_id"), "router_kind": state.get("router_kind"),
            "router_attempts": state.get("router_attempts", 0), "reply_kind": state.get("reply_kind"),
            "verdicts": [{k: v[k] for k in ("name", "ok", "reason")} for v in state.get("verdicts") or []],
            "results": list(state.get("results") or []), "remaining_steps": state.get("remaining_steps")}


def make_finish(sink: TrajectorySink | None = None):
    def finish(state: AgentState) -> dict:
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
    "CLARIFY_KINDS", "MAX_CALLS_PER_TURN", "MAX_PLAN_EDITS", "MAX_RECENT_PLACES", "SAVE_ATTEMPTS",
    "TrajectorySink", "UNFINISHED_CALL", "answer_template", "clarify", "collect",
    "confirm_payload", "direct_reply", "fallback", "make_answer_llm", "make_finish", "make_route",
    "make_save_plan", "make_validate", "plan_card_id", "plan_prepare", "review", "router_message",
    "start_turn", "trajectory_record", "turn_cards", "turn_messages", "unpaired_calls",
]

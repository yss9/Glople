"""일정 저장 노드(design.md 9절)와 M3 서브그래프 판(9.3).

plan_prepare → review(interrupt) → save_plan 세 노드를 여기 한곳에 두고, 두 가지로 조립한다.

| 판 | 조립 | 끝나면 | 일정 키(plan_draft·plan_edits·plan_notice) |
|---|---|---|---|
| 평평한 판(flat, M0~M2, 기본) | 부모 그래프에 노드 셋을 그대로(add_plan_nodes(done="finish")) | Command(goto="finish") | 부모 state(AgentState) |
| 서브그래프 판(subgraph, M3) | StateGraph(PlanState)를 compile()해 부모에 add_node("plan", …) | Command(goto=END) → 부모 엣지 plan → finish | 서브그래프 자기 키(부모에 없음) |

- 노드 코드는 두 판이 같다. 다른 것은 '끝' 노드 이름(done)뿐이다. 그래서 tests/test_hitl.py가 두 판을 같은 테스트로 돈다.
- 서브그래프는 checkpointer 없이 compile()한다. 부모 체크포인터를 이어받아 review의 interrupt가 최상위
  (get_state(config).interrupts)까지 올라오고, 서브그래프 안은 get_state(config, subgraphs=True)의 task.state로 본다.
- 서브그래프가 interrupt로 멈춘 동안에는 서브그래프의 쓰기(확인 카드·plan_draft)가 부모 state에 없다. 부모에 들어가는 것은
  서브그래프가 END에 닿은 뒤 한 번, 공유 키 전체 값이다(api-notes 'M3', tests/test_pins_m3.py).
- 턴마다 부모의 plan 작업(task) id가 새로 생기고 서브그래프 체크포인트 namespace(plan:<task id>)도 새것이라, 일정 키는
  start_turn이 비우지 않아도 턴마다 빈 값에서 시작한다(평평한 판은 start_turn이 비움).
"""
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from glople_agent.answer import templates as tpl
from glople_agent.context import AgentContext
from glople_agent.graph.grounding import grounded
from glople_agent.graph.nodes import (PLAN, Ctx, _dumps, _emit, _tool_error, _with_trace, pending_clarify,
                                      plan_card_id, router_message)
from glople_agent.plan import (AuthExpired, PlanDecision, PlanRejected, PlanStore, PlanStoreError, apply_edit,
                               check_draft, check_edit, make_draft, save_body, selected_items)
from glople_agent.state import Card, PlanDraft, PlanState

PLAN_NODE = "plan"                                     # 서브그래프 판에서 부모가 보는 노드 이름
PLAN_NODES = ("plan_prepare", "review", "save_plan")   # 두 판 모두의 안쪽 노드
FLAT_DONE = "finish"                                   # 평평한 판의 '끝'(부모 finish 노드)
MAX_PLAN_EDITS = 3          # 확인 카드 edit 최대 횟수
SAVE_ATTEMPTS = 2           # 일정 저장: 연결 오류·5xx만 1회 더(서버 멱등키가 중복을 막음)

State = Mapping[str, Any]   # AgentState(평평한 판) 또는 PlanState(서브그래프 판). 두 판이 쓰는 키는 같다


def _plan_card(state: State, draft: PlanDraft, *, kind: str = "plan_confirm", status: str = "ok",
               **data: Any) -> Card:
    call_id = draft["clientRequestId"]
    card: Card = {"card_id": plan_card_id(call_id), "turn_id": state.get("turn_id", ""), "call_id": call_id,
                  "tool": PLAN, "kind": kind, "status": status, "data": {"draft": draft, **data}}  # type: ignore[typeddict-item]
    _emit({"event": "card", "card": card})
    return card


def _plan_result(call_id: str, status: str) -> dict:
    return {"tool_call_id": call_id, "tool": PLAN, "status": status}


def _plan_finish(state: State, draft: PlanDraft, error: str, text: str, **card_data: Any) -> dict:
    call_id = draft["clientRequestId"]
    return {"messages": [_tool_error(call_id, PLAN, error, text)],
            "results": [*(state.get("results") or []), _plan_result(call_id, "error")],
            "cards": [_plan_card(state, draft, status="error", **card_data)],
            "final_text": text, "reply_kind": "plan"}


def confirm_payload(state: State) -> dict:
    """interrupt 값(확인 카드). 토큰·사용자 id는 넣지 않는다."""
    d = state["plan_draft"]
    notice = state.get("plan_notice")
    return {"type": "plan_confirm", "tool_call_id": d["clientRequestId"], "draft": d,
            "message": tpl.plan_confirm_text(d["title"]), "notice": notice,
            "notice_text": tpl.plan_notice_text(notice),
            "edits_left": max(0, MAX_PLAN_EDITS - state.get("plan_edits", 0))}


def plan_signature(plan: Mapping[str, Any]) -> tuple:
    """저장된 일정이 같은 내용인지 비교할 값: 제목·지역·일수·(선택된) 장소 이름과 일차 순서."""
    items = [(it.get("name"), it.get("dayNo")) for it in plan.get("items") or [] if it.get("selected", True)]
    return plan.get("title"), plan.get("region"), plan.get("days"), tuple(items)


# --- 노드 팩토리(done = 평평한 판 "finish", 서브그래프 판 END) ------------------------------

def make_plan_prepare(done: str = FLAT_DONE):
    def plan_prepare(state: State, runtime: Ctx) -> Command:
        """create_travel_plan 인자 → PlanDraft → review. edit로 돌아온 경우는 review가 고친 초안을 그대로 다시 띄운다."""
        if (draft := state.get("plan_draft")) is not None:
            return Command(goto="review", update={"cards": [_plan_card(state, draft)]})
        ai = router_message(state)   # type: ignore[arg-type]
        call = next((tc for tc in (ai.tool_calls if ai else []) if tc["name"] == PLAN), None)
        if call is None:   # after_validate가 일정 호출이 통과했을 때만 보냄
            raise RuntimeError("plan_prepare: 이번 턴 create_travel_plan 호출이 없습니다")
        ctx = runtime.context
        if ctx is None or not ctx.user_id:   # 비로그인: 카드 없이 로그인 안내
            msg = _tool_error(call["id"], PLAN, "login_required", tpl.PLAN_LOGIN_REQUIRED)
            return Command(goto=done, update={"messages": [msg], "results": [_plan_result(call["id"], "error")],
                                              "final_text": tpl.PLAN_LOGIN_REQUIRED, "reply_kind": "plan"})
        # 장소 출처: 발화에 근거(grounding 규칙: 앞 경계·조사만) → USER, 최근 결과 → TOOL_RESULT, 그 밖 MODEL_SUGGESTED
        draft = make_draft(call["args"], client_request_id=call["id"], utterance=state.get("router_input", ""),
                           recent_places=(state.get("memory") or {}).get("recent_places") or [], matcher=grounded)
        if not selected_items(draft):   # 근거 있는 장소 0곳(전부 지어낸 장소이거나 다 빠짐) → 장소 되묻기
            question = tpl.clarify_plan(["places"])
            msg = _tool_error(call["id"], PLAN, "no_places", question)
            return Command(goto=done, update={"messages": [msg], "results": [_plan_result(call["id"], "error")],
                                              "final_text": question, "reply_kind": "clarify",
                                              "memory": pending_clarify(state, "clarify:places", PLAN),   # type: ignore[arg-type]
                                              "trace": _with_trace(state, "plan", {"draft": draft})})   # type: ignore[arg-type]
        return Command(goto="review", update={"plan_draft": draft, "plan_edits": 0, "plan_notice": None,
                                              "memory": {"last_region": draft["region"]},
                                              "cards": [_plan_card(state, draft)]})

    return plan_prepare


def make_review(done: str = FLAT_DONE):
    def review(state: State) -> Command:
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
        return Command(goto=done, update=_plan_finish(state, draft, "cancelled", text, cancelled=True))

    return review


def make_save_plan(store: PlanStore, done: str = FLAT_DONE):
    def save_plan(state: State, runtime: Ctx) -> Command:
        """토큰을 꺼내는 유일한 노드. 멱등키 client_request_id = create_travel_plan의 tool_call_id.

        중복 저장은 호출부의 대기 id 검사(9.2 1겹)와 저장소 멱등키(3겹)가 막는다. state를 보고 건너뛰는
        2겹은 두지 않는다: 저장에 성공하면 끝(finish/END)뿐이고, 이 노드가 다시 도는 경우(시간 여행·
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
                return Command(goto=done, update=_plan_finish(state, draft, "bad_request", tpl.PLAN_SAVE_FAILED))
            except PlanStoreError as e:
                if e.retryable and attempt < SAVE_ATTEMPTS:
                    continue
                return Command(goto=done, update=_plan_finish(state, draft, e.code, tpl.PLAN_SAVE_FAILED))
        assert result is not None
        if not result.created and plan_signature(result.plan) != plan_signature(body):
            # 같은 멱등키로 이미 '다른 내용'이 저장돼 있음: /fork로 저장 전 카드에 돌아가 고친 뒤 승인한 경우.
            # 저장소는 처음 것을 돌려주므로(Spring 200) 바꾼 내용이 저장된 것처럼 말하지 않는다(api-notes M3)
            content = _dumps({"tool": PLAN, "status": "error", "error": "already_saved", "plan_id": result.plan_id})
            msg = ToolMessage(content=content, tool_call_id=call_id, name=PLAN, status="error")
            card = _plan_card(state, draft, kind="plan_saved", plan_id=result.plan_id, saved=result.plan,
                              already_saved=True)
            return Command(goto=done, update={
                "messages": [msg], "cards": [card],
                "results": [*(state.get("results") or []), _plan_result(call_id, "error")],
                "final_text": tpl.PLAN_ALREADY_SAVED, "reply_kind": "plan"})
        content = _dumps({"tool": PLAN, "status": "ok", "saved": True, "plan_id": result.plan_id})
        msg = ToolMessage(content=content, tool_call_id=call_id, name=PLAN, status="success")
        card = _plan_card(state, draft, kind="plan_saved", plan_id=result.plan_id, saved=result.plan)
        return Command(goto=done, update={
            "messages": [msg], "cards": [card],
            "results": [*(state.get("results") or []), _plan_result(call_id, "ok")],
            "final_text": tpl.plan_saved_text(draft["title"]), "reply_kind": "plan"})

    return save_plan


plan_prepare = make_plan_prepare()   # 평평한 판의 노드(M0~M2 이름 그대로)
review = make_review()


# --- 조립 -----------------------------------------------------------------------------

def add_plan_nodes(g: StateGraph, store: PlanStore, *, done: str = FLAT_DONE) -> None:
    """노드 셋을 g에 붙인다. Command를 내는 노드라 정적 엣지는 달지 않고 destinations로 그림만 잇는다."""
    g.add_node("plan_prepare", make_plan_prepare(done), destinations=("review", done))
    g.add_node("review", make_review(done), destinations=("save_plan", "plan_prepare", done))
    g.add_node("save_plan", make_save_plan(store, done), destinations=(done, "review"))


def build_plan_subgraph(store: PlanStore) -> CompiledStateGraph:
    """서브그래프 판(9.3): START → plan_prepare → review ⇄ … → END. 부모가 add_node("plan", 이것)으로 붙인다."""
    g = StateGraph(PlanState, context_schema=AgentContext)
    add_plan_nodes(g, store, done=END)
    g.add_edge(START, "plan_prepare")
    return g.compile()   # checkpointer=None → 부모 체크포인터를 이어받음(interrupt가 최상위까지 올라옴)


__all__ = [
    "FLAT_DONE", "MAX_PLAN_EDITS", "PLAN_NODE", "PLAN_NODES", "SAVE_ATTEMPTS", "add_plan_nodes",
    "build_plan_subgraph", "confirm_payload", "make_plan_prepare", "make_review", "make_save_plan", "plan_prepare",
    "plan_signature", "review",
]

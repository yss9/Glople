"""사람 확인(design.md 9절, 14.2 test_hitl): plan_prepare → review(interrupt) → save_plan.

M3: 모든 테스트가 평평한 판(flat)과 서브그래프 판(subgraph, graph/plan_subgraph.py) 두 번 돈다(리팩터 확인, 9.3).
두 판에서 다를 수밖에 없는 것만 판을 보고 갈랐다(테스트 안 주석): 부모 단계 수(remaining_steps), 시간 여행의 갈래점.
일정 키는 kit.plan_values()로 본다(서브그래프 판에서는 서브그래프 자기 키라 부모 state에 없음).
"""
import functools
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from pydantic import TypeAdapter, ValidationError

from glople_agent.answer import templates as tpl
from glople_agent.context import AgentContext, BearerToken
from glople_agent.graph import InterruptNotPending, InvalidResume, cancel_pending, is_invalid_resume, resume
from glople_agent.graph.nodes import plan_card_id, unpaired_calls
from glople_agent.llm.fake import QuirkScript, RuleScript, tool_call_message
from glople_agent.plan import InMemoryPlanStore, PlanDecision, PlanDraftIn, PlanRejected, PlanStoreError
from glople_agent.tools.args import ARGS_MODELS

PLAN_TEXT = "제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘"
PLAN_PATH = ["start_turn", "route", "validate", "plan_prepare"]   # review에서 멈춤(updates에 안 나옴)


def plan_tool_message(values) -> list[ToolMessage]:
    return [m for m in values["messages"] if isinstance(m, ToolMessage) and m.name == "create_travel_plan"]


def router_call_id(values) -> str:
    [ai] = [m for m in values["messages"] if isinstance(m, AIMessage) and m.tool_calls]
    [tc] = ai.tool_calls
    return tc["id"]


@pytest.fixture(params=["flat", "subgraph"])
def make_graph(request, make_graph):
    """conftest의 make_graph를 두 판으로 감싼다: 이 파일의 테스트는 모두 판마다 한 번씩."""
    return functools.partial(make_graph, plan_graph=request.param)


@pytest.fixture
def kit(make_graph):
    return make_graph(RuleScript())


def test_interrupt_returns_confirm_card(kit, agent_context):
    r = kit.turn(PLAN_TEXT, agent_context)
    assert r.path == PLAN_PATH and r.final_text is None and r.reply_kind is None
    [intr] = r.interrupts
    value = intr.value
    assert value["type"] == "plan_confirm" and value["tool_call_id"] == router_call_id(kit.values())
    assert value["message"] == "'제주 2박 3일' 일정을 이대로 저장할까요?" and value["edits_left"] == 3
    draft = value["draft"]
    assert (draft["schema"], draft["title"], draft["region"], draft["days"]) == ("glople.plan.v1", "제주 2박 3일",
                                                                                "제주", 3)
    assert [(i["name"], i["source"], i["dayNo"], i["selected"]) for i in draft["items"]] == [
        ("성산일출봉", "USER", 1, True), ("우도", "USER", 2, True)]
    assert intr.response_schema["required"] == ["decision"]   # 클라이언트용 JSON Schema
    snap = kit.graph.get_state(kit.config)
    assert kit.waiting_at() == ("review",) and [i.id for i in snap.interrupts] == [intr.id]   # get_state도 대기 카드만
    # 서브그래프 판: 부모는 plan 노드에서 멈춘 것으로 보이고, interrupt는 최상위까지 올라온다(9.3)
    assert snap.next == (("plan",) if kit.plan_graph == "subgraph" else ("review",))
    assert [c["kind"] for c in r.cards] == ["plan_confirm"] and kit.store.save_count == 0
    # 확인 전에는 일정 ToolMessage가 없음(짝은 review/save_plan이 닫음)
    assert plan_tool_message(kit.values()) == []


def test_invoke_v2_graph_output_has_interrupts(kit, agent_context):
    from langchain_core.messages import HumanMessage
    out = kit.graph.invoke({"messages": [HumanMessage(PLAN_TEXT)]}, kit.config, context=agent_context, version="v2")
    [intr] = out.interrupts   # GraphOutput.interrupts
    assert intr.value["type"] == "plan_confirm" and kit.plan_values()["plan_draft"]["title"] == "제주 2박 3일"
    assert ("plan_draft" in out.value) == (kit.plan_graph == "flat")   # 서브그래프 판은 서브그래프 자기 키


def test_approve_saves_once_with_tool_call_id_as_key(kit, agent_context):
    kit.turn(PLAN_TEXT, agent_context)
    call_id = router_call_id(kit.values())
    r = kit.resume({"decision": "approve"}, agent_context)
    assert r.path == ["review", "save_plan", "finish"] and r.done
    assert r.final_text == "'제주 2박 3일' 일정을 저장했어요." and r.reply_kind == "plan"
    assert kit.store.save_count == 1
    [saved] = kit.store.list_plans(agent_context.user_id)
    assert saved["clientRequestId"] == call_id   # client_request_id == tool_call_id
    [msg] = plan_tool_message(kit.values())
    assert msg.tool_call_id == call_id and msg.status == "success"
    assert json.loads(msg.content) == {"tool": "create_travel_plan", "status": "ok", "saved": True, "plan_id": 1}
    [card] = [c for c in r.cards if c["kind"] == "plan_saved"]
    assert card["card_id"] == plan_card_id(call_id) and card["data"]["plan_id"] == 1
    assert unpaired_calls(kit.values()["messages"]) == []


def test_same_resume_twice_saves_once(kit, agent_context):
    first = kit.turn(PLAN_TEXT, agent_context)
    intr_id = first.pending.id
    kit.resume({"decision": "approve"}, agent_context, interrupt_id=intr_id)
    with pytest.raises(InterruptNotPending):   # 9.2 첫 번째 겹: 호출부가 대기 카드를 확인
        kit.resume({"decision": "approve"}, agent_context, interrupt_id=intr_id)
    # 호출부 검사를 건너뛰고 그래프에 바로 보내도 아무 노드도 돌지 않음(대기 interrupt 없음)
    again = kit.graph.invoke(Command(resume={intr_id: {"decision": "approve"}}), kit.config,
                             context=agent_context, version="v2")
    assert again.interrupts == () and kit.store.save_count == 1 and kit.store.call_count == 1


def test_stale_interrupt_id_is_rejected(kit, agent_context):
    first = kit.turn(PLAN_TEXT, agent_context)
    edited = kit.resume({"decision": "edit", "draft": {"region": "제주", "days": 2, "places": ["우도"]}},
                        agent_context)
    assert edited.pending.id != first.pending.id
    with pytest.raises(InterruptNotPending):   # 예전 카드의 승인이 새 카드에 적용되지 않음
        kit.resume({"decision": "approve"}, agent_context, interrupt_id=first.pending.id)
    assert kit.store.save_count == 0


def test_resume_requires_interrupt_id(kit, agent_context):
    # id 없이 보내면 그 순간 대기 중인 카드(사용자가 못 본 새 카드일 수 있음)에 들어가므로 받지 않음
    first = kit.turn(PLAN_TEXT, agent_context)
    with pytest.raises(TypeError):
        resume(kit.graph, kit.thread, {"decision": "approve"}, agent_context)  # type: ignore[call-arg]
    for bad in ("", None):
        with pytest.raises(InterruptNotPending):
            resume(kit.graph, kit.thread, {"decision": "approve"}, agent_context, interrupt_id=bad)
    assert kit.store.call_count == 0
    assert [i.id for i in kit.graph.get_state(kit.config).interrupts] == [first.pending.id]


def test_double_submit_after_401_does_not_approve_new_card(make_graph, agent_context):
    # 승인이 두 번 오면(더블클릭·재전송) 두 번째는 401 뒤 새로 뜬 AUTH_EXPIRED 카드를 승인하지 않음
    kit = make_graph(RuleScript(), store=InMemoryPlanStore(expire_auth=1))
    first = kit.turn(PLAN_TEXT, agent_context)
    again = kit.resume({"decision": "approve"}, agent_context, interrupt_id=first.pending.id)
    assert again.pending.value["notice"] == "AUTH_EXPIRED" and again.pending.id != first.pending.id
    with pytest.raises(InterruptNotPending):
        kit.resume({"decision": "approve"}, agent_context, interrupt_id=first.pending.id)
    assert kit.store.call_count == 1 and kit.store.save_count == 0
    assert [i.id for i in kit.graph.get_state(kit.config).interrupts] == [again.pending.id]   # 새 카드는 그대로


def test_double_submit_of_edit_applies_once(kit, agent_context):
    first = kit.turn(PLAN_TEXT, agent_context)
    edit = {"decision": "edit", "draft": {"region": "제주", "days": 2, "places": ["우도"]}}
    edited = kit.resume(edit, agent_context, interrupt_id=first.pending.id)
    with pytest.raises(InterruptNotPending):
        kit.resume(edit, agent_context, interrupt_id=first.pending.id)
    assert kit.plan_values()["plan_edits"] == 1 and edited.pending.value["edits_left"] == 2


def test_reject_saves_nothing_and_closes_call(kit, agent_context):
    kit.turn(PLAN_TEXT, agent_context)
    r = kit.resume({"decision": "reject"}, agent_context)
    assert r.path == ["review", "finish"] and r.final_text == tpl.PLAN_CANCELLED and r.reply_kind == "plan"
    assert kit.store.save_count == 0 and kit.store.call_count == 0
    [msg] = plan_tool_message(kit.values())
    assert msg.status == "error" and json.loads(msg.content)["error"] == "cancelled"
    assert unpaired_calls(kit.values()["messages"]) == []


def test_edit_shows_card_again_up_to_three_times(kit, agent_context):
    r = kit.turn(PLAN_TEXT, agent_context)
    ids = [r.pending.id]
    for n, days in enumerate((2, 4, 1), start=1):
        r = kit.resume({"decision": "edit", "draft": {"region": "제주", "days": days,
                                                      "places": ["성산일출봉", "우도", "오설록"]}}, agent_context)
        assert r.path == ["review", "plan_prepare"] and r.pending is not None   # 다시 카드
        ids.append(r.pending.id)
        value = r.pending.value
        assert value["draft"]["days"] == days and value["edits_left"] == 3 - n
        assert [i["source"] for i in value["draft"]["items"]] == ["USER", "USER", "USER_EDIT"]
    assert value["draft"]["title"] == "제주 당일치기"   # 기본 제목은 일수에 맞춰 다시 만듦
    assert len(set(ids)) == 4   # edit 뒤 카드는 매번 새 interrupt id
    r = kit.resume({"decision": "edit", "draft": {"region": "제주", "days": 2, "places": ["우도"]}}, agent_context)
    assert r.done and r.final_text == tpl.PLAN_EDIT_LIMIT and kit.store.save_count == 0   # 네 번째 edit은 취소


def test_edit_with_out_of_range_values_keeps_old_values_and_shows_problems(kit, agent_context):
    kit.turn(PLAN_TEXT, agent_context)
    r = kit.resume({"decision": "edit", "draft": {"region": "제주", "days": 45, "places": ["우도"]}}, agent_context)
    draft = r.pending.value["draft"]
    assert draft["days"] == 3 and [i["name"] for i in draft["items"]] == ["우도"]   # 문제 칸만 이전 값
    assert any("1~30일" in p for p in draft["problems"])
    done = kit.resume({"decision": "approve"}, agent_context)
    [saved] = kit.store.list_plans(agent_context.user_id)
    assert done.done and saved["days"] == 3 and "problems" not in saved


def test_schema_violation_resume_is_invalid_and_card_stays(kit, agent_context):
    first = kit.turn(PLAN_TEXT, agent_context)
    history_before = len(list(kit.graph.get_state_history(kit.config)))
    # 그래프에 바로 보내면 interrupt(response_schema=PlanDecision)가 ValidationError를 그대로 올림
    with pytest.raises(ValidationError) as info:
        kit.graph.invoke(Command(resume={first.pending.id: {"decision": "maybe"}}), kit.config,
                         context=agent_context, version="v2")
    assert is_invalid_resume(info.value)
    # 호출부 도우미는 그래프를 부르기 전에 검사(422)
    with pytest.raises(InvalidResume) as info2:
        kit.resume({"decision": "edit", "draft": {"region": "제주"}}, agent_context)
    assert is_invalid_resume(info2.value) and info2.value.errors
    snap = kit.graph.get_state(kit.config)
    assert [i.id for i in snap.interrupts] == [first.pending.id]   # 같은 카드가 그대로
    assert len(list(kit.graph.get_state_history(kit.config))) == history_before
    assert not is_invalid_resume(ValueError("다른 오류"))
    done = kit.resume(PlanDecision(decision="approve"), agent_context)
    assert done.done and kit.store.save_count == 1


def _validation_error(validate, value) -> ValidationError:
    with pytest.raises(ValidationError) as info:
        validate(value)
    return info.value


def test_is_invalid_resume_only_for_plan_decision_errors():
    # 422로 돌려줄 것은 재개 값(PlanDecision) 오류뿐. 노드 안에서 난 다른 모델의 ValidationError는 섞지 않음
    assert is_invalid_resume(_validation_error(TypeAdapter(PlanDecision).validate_python, {"decision": "maybe"}))
    assert is_invalid_resume(_validation_error(PlanDecision.model_validate,
                                               {"decision": "edit", "draft": {"region": "제주"}}))  # 안쪽 draft 오류도
    assert not is_invalid_resume(_validation_error(PlanDraftIn.model_validate, {}))
    assert not is_invalid_resume(_validation_error(ARGS_MODELS["create_travel_plan"].model_validate, {}))
    assert not is_invalid_resume(_validation_error(ARGS_MODELS["search_reviews"].model_validate, {"query": 3}))


def test_anonymous_user_gets_login_message_without_card(kit, anon_context):
    r = kit.turn(PLAN_TEXT, anon_context)
    assert r.path == ["start_turn", "route", "validate", "plan_prepare", "finish"] and r.done
    assert r.final_text == tpl.PLAN_LOGIN_REQUIRED and r.interrupts == () and r.cards == []
    [msg] = plan_tool_message(kit.values())
    assert msg.status == "error" and json.loads(msg.content)["error"] == "login_required"
    assert kit.store.save_count == 0 and kit.store.call_count == 0


def test_401_shows_card_again_with_notice_then_reapprove_saves_once(make_graph, agent_context, fixed_now, seed):
    kit = make_graph(RuleScript(), store=InMemoryPlanStore(expire_auth=1))
    first = kit.turn(PLAN_TEXT, agent_context)
    r = kit.resume({"decision": "approve"}, agent_context)
    assert r.path == ["review", "save_plan"] and r.pending is not None   # 401 → review에서 다시 멈춤
    value = r.pending.value
    assert value["notice"] == "AUTH_EXPIRED" and value["notice_text"] == tpl.PLAN_NOTICES["AUTH_EXPIRED"]
    assert r.pending.id != first.pending.id and kit.store.save_count == 0
    # 다시 로그인한 뒤 새 토큰의 context로 재승인 → 같은 멱등키라 1건
    fresh = AgentContext(user_id=agent_context.user_id, bearer=BearerToken("다시-로그인한-토큰"),
                         request_id="req-2", now=fixed_now, seed=seed)
    done = kit.resume({"decision": "approve"}, fresh)
    assert done.done and done.final_text == "'제주 2박 3일' 일정을 저장했어요."
    assert kit.store.save_count == 1 and kit.store.call_count == 2
    assert kit.plan_values().get("plan_notice") is None


def test_resume_without_context_asks_login_again(kit, agent_context):
    kit.turn(PLAN_TEXT, agent_context)
    r = kit.resume({"decision": "approve"}, None)   # context는 저장되지 않으므로 재개마다 넘겨야 함
    assert r.pending is not None and r.pending.value["notice"] == "AUTH_EXPIRED"
    assert kit.store.call_count == 0
    done = kit.resume({"decision": "approve"}, agent_context)
    assert done.done and kit.store.save_count == 1


def test_model_suggested_places_are_not_saved(make_graph, agent_context):
    raw = tool_call_message([("create_travel_plan", {"region": "제주", "days": 3,
                                                     "places": ["성산일출봉", "오설록", "우도"]})])
    kit = make_graph(QuirkScript([raw]))
    r = kit.turn("제주 2박 3일 성산일출봉이랑 우도 저장해줘", agent_context)
    items = r.pending.value["draft"]["items"]
    assert [(i["name"], i["source"], i["selected"]) for i in items] == [
        ("성산일출봉", "USER", True), ("오설록", "MODEL_SUGGESTED", False), ("우도", "USER", True)]
    kit.resume({"decision": "approve"}, agent_context)
    [saved] = kit.store.list_plans(agent_context.user_id)
    assert [i["name"] for i in saved["items"]] == ["성산일출봉", "우도"]
    assert [i["position"] for i in saved["items"]] == [1, 2]


def test_all_places_invented_asks_for_places(make_graph, agent_context):
    # M1: 근거 검사가 validate에서 먼저 막는다(v1 실제 오답 '울진 일정 → 장소 3개 지어냄'과 같은 꼴).
    # 일수도 발화에 없으므로 학습 정답과 같은 '며칠 일정으로, 어떤 장소를 …' 되묻기
    raw = tool_call_message([("create_travel_plan", {"region": "울진", "days": 2, "places": ["지어낸 곳"]})])
    kit = make_graph(QuirkScript([raw]))
    r = kit.turn("울진 가는데 일정으로 남겨두고 싶어요", agent_context)
    assert r.done and r.interrupts == () and r.final_text == tpl.CLARIFY["plan_days_places"]
    assert r.path == ["start_turn", "route", "validate", "clarify", "finish"]
    assert unpaired_calls(kit.values()["messages"]) == [] and kit.store.save_count == 0
    v = kit.values()["verdicts"][0]
    assert v["reason"] == "grounding" and v["grounding"]["issues"] == ["clarify:days", "clarify:places"]


def test_invented_places_with_grounded_days_ask_places_only(make_graph, agent_context):
    raw = tool_call_message([("create_travel_plan", {"region": "울진", "days": 3, "places": ["지어낸 곳"]})])
    kit = make_graph(QuirkScript([raw]))
    r = kit.turn("울진 2박 3일 일정으로 남겨두고 싶어요", agent_context)
    assert r.final_text == tpl.CLARIFY["plan_places"] and r.reply_kind == "clarify"
    assert kit.values()["memory"]["pending_clarify"]["reason"] == "clarify:places"


def test_server_error_is_retried_once_then_saves(make_graph, agent_context):
    store = InMemoryPlanStore()
    store.fail_next(PlanStoreError())   # 5xx 흉내(retryable)
    kit = make_graph(RuleScript(), store=store)
    kit.turn(PLAN_TEXT, agent_context)
    r = kit.resume({"decision": "approve"}, agent_context)
    assert r.done and r.final_text == "'제주 2박 3일' 일정을 저장했어요."
    assert store.call_count == 2 and store.save_count == 1


@pytest.mark.parametrize("errors", [[PlanStoreError(), PlanStoreError()], [PlanRejected([])]])
def test_save_failure_gives_fixed_message(make_graph, agent_context, errors):
    store = InMemoryPlanStore()
    for e in errors:
        store.fail_next(e)
    kit = make_graph(RuleScript(), store=store)
    kit.turn(PLAN_TEXT, agent_context)
    r = kit.resume({"decision": "approve"}, agent_context)
    assert r.done and r.final_text == tpl.PLAN_SAVE_FAILED and store.save_count == 0
    [msg] = plan_tool_message(kit.values())
    assert msg.status == "error"


def plan_card(values, call_id: str) -> dict:
    [card] = [c for c in values["cards"] if c["card_id"] == plan_card_id(call_id)]
    return card


def card_state(card: dict) -> tuple:
    return card["kind"], card["status"], card["data"].get("cancelled")


def test_new_message_while_card_pending(kit, agent_context):
    # 그래프에 새 입력을 바로 보내면 대기 카드는 버려지고(1.2.12) start_turn이 짝 없는 호출을 닫는다
    first = kit.turn(PLAN_TEXT, agent_context)
    call_id = first.pending.value["tool_call_id"]
    r = kit.turn("안녕", agent_context)
    assert r.path == ["start_turn", "route", "direct_reply", "finish"] and r.done
    assert kit.graph.get_state(kit.config).interrupts == ()
    [msg] = plan_tool_message(kit.values())
    assert msg.tool_call_id == call_id and json.loads(msg.content)["error"] == "cancelled"
    assert unpaired_calls(kit.values()["messages"]) == [] and kit.store.save_count == 0
    # 확인 카드도 취소 상태로 바뀜(card 이벤트도 나감). 카드는 처음 뜬 턴에 그대로 남음
    card = plan_card(kit.values(), call_id)
    assert card_state(card) == ("plan_confirm", "error", True) and card in r.cards
    assert card["turn_id"] == first.values["turn_id"] != r.values["turn_id"]
    # 호출부(CLI)는 먼저 reject로 닫는다(9.3) → 취소 문장이 남고, 카드는 위와 같은 모양
    second = kit.turn(PLAN_TEXT, agent_context)
    closed = cancel_pending(kit.graph, kit.thread, agent_context)
    assert closed is not None and closed.final_text == tpl.PLAN_CANCELLED
    assert card_state(plan_card(kit.values(), second.pending.value["tool_call_id"])) == card_state(card)
    assert cancel_pending(kit.graph, kit.thread, agent_context) is None


def test_resume_uses_fresh_step_budget(kit, agent_context):
    kit.turn(PLAN_TEXT, agent_context)
    for _ in range(3):
        kit.resume({"decision": "edit", "draft": {"region": "제주", "days": 2, "places": ["우도"]}}, agent_context)
    done = kit.resume({"decision": "approve"}, agent_context)
    assert done.done and kit.store.save_count == 1
    # 재개 invoke마다 새 예산: 다시 도는 review가 26, save_plan 25, finish 24(edit 3회를 나눠 쓰지 않음).
    # 서브그래프 판은 부모에서 plan이 한 단계(26)라 finish가 25(서브그래프 안은 자기 예산, test_pins_m3)
    assert kit.sink[-1]["remaining_steps"] == (25 if kit.plan_graph == "subgraph" else 24)


def test_save_plan_reruns_only_by_time_travel_and_store_key_dedupes(kit, agent_context):
    # 9.2의 2겹(state에 저장 기록이 있으면 건너뜀)은 두지 않는다(api-notes). 그 근거:
    first = kit.turn(PLAN_TEXT, agent_context)
    kit.resume({"decision": "approve"}, agent_context)
    assert kit.store.call_count == 1
    # ① 저장 뒤에는 다시 돌 노드가 없음: END 뒤 invoke(None)·재개는 아무 노드도 돌리지 않음
    kit.graph.invoke(None, kit.config, context=agent_context)
    kit.graph.invoke(Command(resume={first.pending.id: {"decision": "approve"}}), kit.config, context=agent_context)
    assert kit.store.call_count == 1 and kit.graph.get_state(kit.config).next == ()
    # ② save_plan이 다시 도는 길은 저장 전 체크포인트에서 다시 실행(시간 여행)뿐인데, 그 갈래점 state에는
    #    저장 기록이 없어 state 검사로는 막을 수 없음 → 저장소 멱등키(3겹)가 같은 일정 1건으로 막음
    #    서브그래프 판의 시간 여행은 부모 체크포인트 단위다: 갈래점은 plan 앞이고, 다시 돌면 plan_prepare부터라
    #    확인 카드가 새로 뜬다(서브그래프 안 save_plan 앞 체크포인트로는 바로 못 감, api-notes M3)
    before = ("plan",) if kit.plan_graph == "subgraph" else ("save_plan",)
    fork = next(snap for snap in kit.graph.get_state_history(kit.config) if snap.next == before)
    assert fork.values["results"] == []
    out = kit.graph.invoke(None, {**fork.config, "recursion_limit": 25}, context=agent_context, version="v2")
    if kit.plan_graph == "subgraph":
        [again] = out.interrupts
        assert again.id != first.pending.id and kit.store.call_count == 1
        kit.resume({"decision": "approve"}, agent_context, interrupt_id=again.id)
    assert kit.store.call_count == 2 and kit.store.save_count == 1
    [saved] = kit.store.list_plans(agent_context.user_id)
    assert saved["clientRequestId"] == first.pending.value["tool_call_id"]
    assert kit.values()["final_text"] == "'제주 2박 3일' 일정을 저장했어요."


def test_fork_back_edit_and_approve_does_not_claim_a_new_save(kit, agent_context):
    # M3 시간 여행: 저장한 뒤 /fork로 확인 카드에 돌아가 고쳐서 승인하면 멱등키(tool_call_id)가 같아 저장소는 처음 일정을
    # 돌려준다(Spring 200). 바꾼 내용이 저장된 것처럼 말하지 않고, 저장소에는 처음 일정 1건만 남는다
    from glople_agent.graph import fork

    first = kit.turn(PLAN_TEXT, agent_context)
    kit.resume({"decision": "approve"}, agent_context)
    waiting = ("plan",) if kit.plan_graph == "subgraph" else ("review",)
    snap = next(s for s in kit.graph.get_state_history(kit.config) if s.next == waiting and s.interrupts)
    kit.last = fork(kit.graph, snap.config, agent_context)
    assert kit.last.pending is not None and kit.last.pending.id != first.pending.id
    edited = kit.resume({"decision": "edit", "draft": {"region": "제주", "days": 2, "places": ["우도"],
                                                       "title": "우도만"}}, agent_context)
    done = kit.resume({"decision": "approve"}, agent_context, interrupt_id=edited.pending.id)
    assert done.final_text == tpl.PLAN_ALREADY_SAVED and done.reply_kind == "plan"
    assert kit.store.save_count == 1 and kit.store.call_count == 2
    [saved] = kit.store.list_plans(agent_context.user_id)
    assert saved["title"] == "제주 2박 3일"
    [card] = [c for c in done.cards if c["kind"] == "plan_saved"]
    assert card["data"]["already_saved"] is True and card["data"]["saved"]["title"] == "제주 2박 3일"

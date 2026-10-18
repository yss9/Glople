"""M3 일정 서브그래프(graph/plan_subgraph.py, design.md 9.3): 평평한 판과 같은 동작, 다른 모양.

같은 동작은 test_hitl.py(두 판 모두)와 여기의 대화 비교·정답 재생 비교가 확인한다. 여기서는 서브그래프 판만의 모양
(부모 state에 일정 키 없음, 체크포인트 namespace, 그림, TurnResult의 qualified_path·graph_path·waiting)을 본다.
"""
import pytest
from langchain_core.messages import ToolMessage

from glople_agent.graph import NODE_NAMES, SUBGRAPH_NODE_NAMES, build_graph, fake_deps, mermaid, plan_state
from glople_agent.graph.plan_subgraph import PLAN_NODES
from glople_agent.llm.fake import RuleScript, sequential_ids
from glople_agent.state import PLAN_KEYS

PLAN_TEXT = "제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘"


def test_two_builds_have_expected_nodes_and_state_keys():
    flat = build_graph(fake_deps(RuleScript()))
    sub = build_graph(fake_deps(RuleScript(), plan_graph="subgraph"))
    assert set(flat.nodes) - {"__start__"} == set(NODE_NAMES)
    assert set(sub.nodes) - {"__start__"} == set(SUBGRAPH_NODE_NAMES)
    assert set(PLAN_KEYS) <= set(flat.channels) and not set(PLAN_KEYS) & set(sub.channels)   # 서브그래프 자기 키
    inner = sub.nodes["plan"].bound   # 컴파일된 서브그래프
    assert set(inner.nodes) - {"__start__"} == set(PLAN_NODES) and set(PLAN_KEYS) <= set(inner.channels)
    assert "call_log" not in inner.channels   # operator.add 채널은 공유하지 않음(test_pins_m3)
    with pytest.raises(ValueError):
        build_graph(fake_deps(RuleScript()), plan_graph="nested")


def test_mermaid_shows_plan_node_and_xray_shows_inside():
    flat = mermaid(build_graph(fake_deps(RuleScript())))
    assert "subgraph" not in flat and "review -.-> save_plan;" in flat   # 평평한 판 그림은 M0~M2 그대로
    sub_graph = build_graph(fake_deps(RuleScript(), plan_graph="subgraph"))
    plain = mermaid(sub_graph)
    assert "plan(plan)" in plain and "plan --> finish;" in plain and "plan_prepare&nbsp; .-> plan;" in plain
    xray = mermaid(sub_graph, xray=True)
    assert "subgraph plan" in xray
    for edge in ("plan\\3aplan_prepare -.-> plan\\3areview;", "plan\\3areview -.-> plan\\3asave_plan;",
                 "plan\\3areview -.-> plan\\3aplan_prepare;", "plan\\3asave_plan -.-> plan\\3areview;",
                 "plan\\3a__end__ --> finish;"):
        assert edge in xray, edge


def test_paths_and_waiting_point(make_graph, agent_context):
    kit = make_graph(RuleScript(), plan_graph="subgraph")
    r = kit.turn(PLAN_TEXT, agent_context)
    assert r.path == ["start_turn", "route", "validate", "plan_prepare"]   # 평평한 판과 같은 이름
    assert r.qualified_path == ["start_turn", "route", "validate", "plan/plan_prepare"]
    assert r.graph_path == ["start_turn", "route", "validate"] and r.waiting == ("plan/review",)
    assert [c["kind"] for c in r.cards] == ["plan_confirm"]   # 서브그래프 안 카드도 custom 스트림으로(subgraphs=True)
    # 대기 중: 부모 state에는 일정 키·확인 카드가 없고 서브그래프 state에 있다
    assert not set(PLAN_KEYS) & set(kit.values()) and kit.values()["cards"] == []
    assert plan_state(kit.graph, kit.thread)["plan_draft"]["title"] == "제주 2박 3일"
    done = kit.resume({"decision": "approve"}, agent_context)
    assert done.path == ["review", "save_plan", "finish"] and done.graph_path == ["plan", "finish"]
    assert done.qualified_path == ["plan/review", "plan/save_plan", "finish"] and done.waiting == ()
    v = kit.values()   # 서브그래프가 끝나면 공유 키(메시지·카드·답)가 부모에 들어옴
    assert [c["kind"] for c in v["cards"]] == ["plan_saved"] and v["final_text"] == "'제주 2박 3일' 일정을 저장했어요."
    assert [m.name for m in v["messages"] if isinstance(m, ToolMessage)] == ["create_travel_plan"]
    assert plan_state(kit.graph, kit.thread) == dict(v)   # 끝난 뒤에는 부모 값(일정 키 없음)
    namespaces = {ns for per_thread in kit.saver.storage.values() for ns in per_thread}
    assert "" in namespaces and any(ns.startswith("plan:") for ns in namespaces)


def test_plan_keys_start_empty_each_turn_without_start_turn_reset(make_graph, agent_context):
    kit = make_graph(RuleScript(), plan_graph="subgraph")
    kit.turn(PLAN_TEXT, agent_context)
    kit.resume({"decision": "edit", "draft": {"region": "제주", "days": 2, "places": ["우도"]}}, agent_context)
    assert kit.plan_values()["plan_edits"] == 1
    kit.resume({"decision": "reject"}, agent_context)
    second = kit.turn(PLAN_TEXT, agent_context)   # 새 턴의 plan 작업 = 새 namespace → 빈 일정 키에서 시작
    assert kit.plan_values()["plan_edits"] == 0 and second.pending.value["edits_left"] == 3


def _conversation(kit, ctx) -> list[tuple]:
    """데모 대화를 돌리고 판과 무관한 값만 남긴다(turn_id·메시지 id는 uuid라 뺌)."""
    out = []

    def keep(r):
        cards = [{k: v for k, v in c.items() if k != "turn_id"} for c in r.cards]
        for c in cards:   # 대기 카드를 새 말로 닫은 카드: 서브그래프 판은 초안 없이 새로 만든다(아래 테스트가 따로 봄)
            if c["status"] == "error" and c["data"].get("cancelled") and c["kind"] == "plan_confirm":
                c["data"] = {"cancelled": True, "had_draft": c["data"].get("draft") is not None}
        out.append((r.path, r.final_text, r.reply_kind, cards, r.pending and r.pending.value))

    keep(kit.turn("바나힐 정보랑 사람들 리뷰 같이 알려줘", ctx))
    keep(kit.turn(PLAN_TEXT, ctx))
    keep(kit.resume({"decision": "edit", "draft": {"region": "제주", "days": 2,
                                                   "places": ["성산일출봉", "우도", "오설록"]}}, ctx))
    keep(kit.resume({"decision": "approve"}, ctx))
    keep(kit.turn("거기 후기 좀 찾아줘", ctx))
    keep(kit.turn("(잘림) 강릉 2박 3일 경포대랑 안목해변 일정 저장해줘", ctx))
    keep(kit.turn("안녕", ctx))                     # 카드가 떠 있는데 새 말 → start_turn이 닫음
    keep(kit.turn("나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", ctx))
    keep(kit.turn("28살이요", ctx))
    v = kit.values()
    out.append(([(type(m).__name__, m.content) for m in v["messages"]], v["memory"],
                [{k: c[k] for k in ("card_id", "kind", "status")} for c in v["cards"]],
                kit.store.list_plans(ctx.user_id)))
    return out


def test_same_conversation_gives_same_answers_in_both_builds(make_graph, agent_context):
    flat = _conversation(make_graph(RuleScript(new_id=sequential_ids())), agent_context)
    sub = _conversation(make_graph(RuleScript(new_id=sequential_ids()), plan_graph="subgraph"), agent_context)
    closing = 6   # "안녕": 떠 있던 강릉 확인 카드를 start_turn이 닫는 턴
    for i, (a, b) in enumerate(zip(flat[:-1], sub[:-1], strict=True)):
        assert a[:3] == b[:3] and a[4] == b[4], f"{i}번째 턴"
        if i != closing:
            assert a[3] == b[3], f"{i}번째 턴 카드"
    [flat_closed], [sub_closed] = flat[closing][3], sub[closing][3]
    assert flat_closed["data"] == {"cancelled": True, "had_draft": True}
    assert sub_closed["data"] == {"cancelled": True, "had_draft": False}
    assert {k: v for k, v in flat_closed.items() if k != "data"} == {k: v for k, v in sub_closed.items() if k != "data"}
    flat_end, sub_end = flat[-1], sub[-1]
    assert flat_end[0] == sub_end[0] and flat_end[2] == sub_end[2]   # 메시지·카드(id·종류·상태)
    assert flat_end[3] == sub_end[3] and len(flat_end[3]) == 1         # 저장된 일정
    # 다른 점 하나: 버려진 서브그래프(새 말로 닫은 강릉 카드)의 memory 쓰기는 부모에 오지 않는다.
    # 평평한 판은 plan_prepare가 부모 노드라 카드가 뜰 때 last_region=강릉이 이미 남음(api-notes M3)
    assert {k: v for k, v in flat_end[1].items() if k != "last_region"} == \
        {k: v for k, v in sub_end[1].items() if k != "last_region"}
    assert (flat_end[1]["last_region"], sub_end[1]["last_region"]) == ("강릉", "제주")


@pytest.mark.parametrize("plan_graph", ["flat", "subgraph"])
def test_cancel_first_keeps_memory_the_same(make_graph, agent_context, plan_graph):
    # 호출부 계약(9.3: 새 말 전에 reject로 닫음)을 지키면 서브그래프가 END까지 가서 memory도 두 판이 같다
    from glople_agent.graph import cancel_pending

    kit = make_graph(RuleScript(), plan_graph=plan_graph)
    kit.turn(PLAN_TEXT, agent_context)
    kit.resume({"decision": "approve"}, agent_context)
    kit.turn("(잘림) 강릉 2박 3일 경포대랑 안목해변 일정 저장해줘", agent_context)
    assert cancel_pending(kit.graph, kit.thread, agent_context).final_text == "저장하지 않았어요."
    r = kit.turn("거기 후기 좀 찾아줘", agent_context)
    assert kit.values()["memory"]["last_region"] == "강릉" and r.values["router_input"] == "강릉 후기 좀 찾아줘"


@pytest.mark.needs_training_data("tool_calling_v2/test.jsonl")
def test_oracle_replay_is_identical_in_both_builds():
    from glople_agent.eval.replay import dataset_paths, replay_cases
    from glople_agent.llm.fake import load_oracle_cases

    paths, _ = dataset_paths(["v2-test", "human"])
    cases = load_oracle_cases(paths)
    flat, flat_saved = replay_cases(cases)
    sub, sub_saved = replay_cases(cases, plan_graph="subgraph")
    assert [(r.category, r.reply_kind, r.as_expected, r.error) for r in flat] == \
        [(r.category, r.reply_kind, r.as_expected, r.error) for r in sub]
    assert flat_saved == sub_saved > 0 and all(r.as_expected for r in sub)

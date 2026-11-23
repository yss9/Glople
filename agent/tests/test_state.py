"""state 리듀서와 체크포인트 왕복(역직렬화 허용 목록을 켠 채로)."""
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde import _msgpack
from langgraph.graph import END, START, StateGraph

from glople_agent.context import AgentContext
from glople_agent.state import MAX_CARDS, AgentState, merge_cards, merge_memory


def _card(cid: str, status: str = "ok") -> dict:
    return {"card_id": cid, "turn_id": "t1", "call_id": "call_1", "tool": "search_reviews",
            "kind": "review_list", "status": status, "data": {}}


def test_merge_cards_replaces_same_id_in_place():
    old = [_card("a"), _card("b")]
    merged = merge_cards(old, [_card("a", "empty"), _card("c")])
    assert [c["card_id"] for c in merged] == ["a", "b", "c"]
    assert merged[0]["status"] == "empty"
    assert old[0]["status"] == "ok"  # 원래 목록은 그대로


def test_merge_cards_keeps_latest_max_cards():
    merged = merge_cards([_card(f"old{i}") for i in range(50)], [_card(f"new{i}") for i in range(20)])
    assert len(merged) == MAX_CARDS == 60
    assert merged[0]["card_id"] == "old10" and merged[-1]["card_id"] == "new19"


def test_merge_cards_handles_empty():
    assert merge_cards(None, None) == []
    assert merge_cards(None, [_card("a")]) == [_card("a")]


def test_merge_memory_overwrites_by_key():
    old = {"last_region": "제주", "recent_places": ["우도"], "pending_clarify": {"reason": "age"}}
    merged = merge_memory(old, {"last_region": "부산", "pending_clarify": None})
    assert merged == {"last_region": "부산", "recent_places": ["우도"], "pending_clarify": None}
    assert merge_memory(None, None) == {}
    assert merge_memory(None, {"last_region": "강릉"}) == {"last_region": "강릉"}


def test_strict_msgpack_enabled_for_tests():
    # conftest가 langgraph import 전에 켬. 아래 왕복 테스트가 실행 때와 같은 허용 목록으로 돈다
    assert _msgpack.STRICT_MSGPACK_ENABLED


def test_state_round_trips_through_checkpoint(agent_context: AgentContext):
    draft = {"schema": "glople.plan.v1", "title": "제주 2박 3일", "region": "제주", "days": 3,
             "items": [{"position": 1, "dayNo": 1, "daySuggested": True, "name": "성산일출봉",
                        "source": "USER", "selected": True}],
             "createdVia": "AGENT", "clientRequestId": "call_1"}

    def first(state: AgentState):
        return {"cards": [_card("a")], "memory": {"last_region": "제주"}, "call_log": ["search_reviews:{}"],
                "turn_id": "t1", "router_kind": "tool", "plan_draft": draft,
                "trace": {"remaining": state["remaining_steps"]}}

    def second(state: AgentState):
        return {"cards": [_card("a", "empty"), _card("b")], "memory": {"recent_places": ["우도"]},
                "call_log": ["get_weather:{}"], "messages": [AIMessage("끝")], "final_text": "끝"}

    g = StateGraph(AgentState, context_schema=AgentContext)
    g.add_node("first", first)
    g.add_node("second", second)
    g.add_edge(START, "first")
    g.add_edge("first", "second")
    g.add_edge("second", END)
    app = g.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-state"}, "recursion_limit": 25}
    app.invoke({"messages": [HumanMessage("제주 후기")]}, config, context=agent_context, version="v2")

    values = app.get_state(config).values  # 저장소에서 다시 읽은 값
    assert [c["card_id"] for c in values["cards"]] == ["a", "b"]
    assert values["cards"][0]["status"] == "empty"
    assert values["memory"] == {"last_region": "제주", "recent_places": ["우도"]}
    assert values["call_log"] == ["search_reviews:{}", "get_weather:{}"]
    assert values["plan_draft"] == draft
    assert values["trace"]["remaining"] == 24  # recursion_limit 25에서 첫 노드
    assert [m.content for m in values["messages"]] == ["제주 후기", "끝"]
    assert "remaining_steps" not in values  # 관리 값은 저장되지 않음

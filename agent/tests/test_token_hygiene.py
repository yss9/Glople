"""토큰 격리(design.md 10.1, 14.2 test_token_hygiene): sentinel 토큰으로 그래프 전 구간을 돌린 뒤
저장소 바이트·체크포인트 기록·로그·가짜 모델 요청·interrupt 값 어디에도 0건인지 본다.

M3에서 SqliteSaver 파일 바이트와 metadata 열 판을 더한다(api-notes '확인 못 함').
"""
import json
import logging

import pytest
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from glople_agent.context import AgentContext
from glople_agent.graph import TurnResult
from glople_agent.llm.fake import QuirkScript, RuleScript
from glople_agent.plan import InMemoryPlanStore


def _flatten_bytes(obj) -> bytes:
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    if isinstance(obj, str):
        return obj.encode()
    if isinstance(obj, dict):
        return b"".join(_flatten_bytes(k) + _flatten_bytes(v) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return b"".join(_flatten_bytes(v) for v in obj)
    return repr(obj).encode()


def saver_bytes(saver: InMemorySaver) -> bytes:
    return _flatten_bytes((saver.storage, saver.writes, saver.blobs))


@pytest.fixture
def scenario(make_graph, agent_context, anon_context, caplog, fixed_now, seed):
    """조회·병렬·되묻기·직답·quirk·일정(401 재승인·edit·reject·스키마 위반 재개)·비로그인을 한 저장소에서."""
    caplog.set_level(logging.DEBUG)   # 모든 로거(langgraph 재시도 로그 포함)
    saver = InMemorySaver()
    store = InMemoryPlanStore(expire_auth=1)
    kit = make_graph(QuirkScript(["connection_error"], then=RuleScript()), store=store, saver=saver)
    outputs: list[TurnResult] = []
    threads: list[str] = []

    def on(thread: str):
        kit.thread = thread
        threads.append(thread)

    on("t-lookup")
    outputs.append(kit.turn("바나힐 정보랑 사람들 리뷰 같이 알려줘", agent_context))   # 연결 오류 → RetryPolicy
    outputs.append(kit.turn("나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", agent_context))
    outputs.append(kit.turn("안녕", agent_context))
    outputs.append(kit.turn("깨진 응답 흉내: 부산 날씨 알려줘", agent_context))

    on("t-plan-401")
    outputs.append(kit.turn("제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘", agent_context))
    outputs.append(kit.resume({"decision": "approve"}, agent_context))          # 401 → 카드 다시
    fresh = AgentContext(user_id=agent_context.user_id, bearer=agent_context.bearer, request_id="req-test-2",
                         now=fixed_now, seed=seed)
    outputs.append(kit.resume({"decision": "approve"}, fresh))                  # 재승인 → 저장

    on("t-plan-edit")
    outputs.append(kit.turn("(잘림) 강릉 2박 3일 경포대랑 안목해변 일정 저장해줘", agent_context))
    with pytest.raises(ValidationError):
        kit.graph.invoke(Command(resume={"decision": "maybe"}), kit.config, context=agent_context, version="v2")
    outputs.append(kit.resume({"decision": "edit", "draft": {"region": "강릉", "days": 2, "places": ["경포대"]}},
                              agent_context))
    outputs.append(kit.resume({"decision": "reject"}, agent_context))

    on("t-anon")
    outputs.append(kit.turn("제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘", anon_context))
    assert store.save_count == 1 and store.call_count == 2
    return kit, saver, outputs, threads, caplog


def test_sentinel_never_leaves_context(scenario, sentinel_raw, agent_context):
    kit, saver, outputs, threads, caplog = scenario
    records = caplog.get_records("setup") + caplog.get_records("call")   # 시나리오는 픽스처(setup)에서 돌았음
    log_text = "\n".join(logging.Formatter().format(r) for r in records)   # 예외 traceback 포함
    needles = [sentinel_raw, agent_context.request_id]   # context 전체가 저장되지 않음

    stored = saver_bytes(saver)
    assert "바나힐".encode() in stored and b"plan_confirm" in stored   # 저장소를 실제로 훑었는지
    histories = {t: list(kit.graph.get_state_history({"configurable": {"thread_id": t}})) for t in threads}
    assert all(histories.values())
    places = {
        "InMemorySaver 바이트": stored,
        "get_state_history": repr(histories).encode(),
        "로그": (log_text + repr([r.__dict__ for r in records])).encode(),
        "가짜 모델 요청": repr(kit.model.calls).encode(),
        "interrupt 값": repr([(o.interrupts, [i.value for i in o.interrupts]) for o in outputs]).encode(),
        "턴 결과": repr([(o.path, o.cards, o.values) for o in outputs]).encode(),
        "궤적 sink": repr(kit.sink).encode(),
        "저장된 일정": repr(kit.store.list_plans(agent_context.user_id)).encode(),
        "가짜 백엔드 호출": repr(kit.backend.calls).encode(),
        "Spring으로 나간 HTTP 요청(M1)": repr(kit.backend.fixture.requests).encode(),
    }
    for name, blob in places.items():
        for needle in needles:
            assert needle.encode() not in blob, f"{name}에 {needle!r}"
    assert "Retrying task route" in log_text   # 로그 캡처가 실제로 동작했는지
    # M1: 읽기 도구는 공개 API만 부르고 Authorization을 붙이지 않는다(SpringClient + 어댑터, 10.1)
    assert kit.backend.fixture.requests and not any(r.has_authorization for r in kit.backend.fixture.requests)
    # 사용자 id도 state·체크포인트에는 없음(context로만)
    assert agent_context.user_id.encode() not in stored


def test_bearer_token_in_state_update_fails(make_graph, agent_context, sentinel_token, sentinel_raw):
    kit = make_graph(RuleScript())
    kit.turn("안녕", agent_context)
    for update in ({"trace": {"bearer": sentinel_token}}, {"memory": {"last_region": sentinel_token}},
                   {"plan_notice": sentinel_token}):
        with pytest.raises(TypeError, match="BearerToken"):
            kit.graph.update_state(kit.config, update)   # 저장 대신 오류(SecretStr과 달리 평문 저장 안 됨)
    assert sentinel_raw.encode() not in saver_bytes(kit.saver)


def test_bearer_token_returned_by_node_fails(agent_context):
    # 노드가 실수로 토큰을 state에 넣으면 체크포인트 저장에서 그래프가 멈춘다(평문으로 저장되지 않음)
    from langchain_core.messages import HumanMessage
    from langgraph.graph import END, START, StateGraph
    from langgraph.runtime import Runtime

    from glople_agent.state import AgentState

    def leaky(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        return {"trace": {"bearer": runtime.context.bearer}}

    g = StateGraph(AgentState, context_schema=AgentContext)
    g.add_node("leaky", leaky)
    g.add_edge(START, "leaky")
    g.add_edge("leaky", END)
    saver = InMemorySaver()
    with pytest.raises(TypeError, match="BearerToken"):
        g.compile(checkpointer=saver).invoke({"messages": [HumanMessage("x")]},
                                             {"configurable": {"thread_id": "t-leak"}, "recursion_limit": 25},
                                             context=agent_context)
    assert agent_context.bearer.header()["Authorization"].split()[1].encode() not in saver_bytes(saver)


def test_context_repr_and_token_repr_hide_secret(agent_context, sentinel_raw):
    assert sentinel_raw not in repr(agent_context) and sentinel_raw not in str(agent_context.bearer)


TYPE3_SENTINEL = "TYPE3-SENTINEL-5d1c8e2a"   # tests/fixtures/backend/recommend_by_keywords.json의 가짜 Type3 글


def test_type3_text_stays_in_cards_not_messages(make_graph, anon_context):
    # 11절 ⑥: 원문은 cards에만. collect가 ToolMessage.artifact를 카드 참조로 줄여 messages에는 남지 않음
    kit = make_graph(RuleScript())
    for _ in range(2):   # 턴이 쌓여도 messages 쪽에는 늘지 않음
        r = kit.turn("사찰 야경 바다 섬 추천해줘", anon_context)
        assert [c["tool"] for c in r.cards] == ["recommend_by_keywords"]
    v = kit.values()
    tool_msgs = [m for m in v["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 2 and all(m.artifact == {"card_id": f"recommend_by_keywords:{m.tool_call_id}"}
                                       for m in tool_msgs)
    messages_blob = json.dumps([m.model_dump() for m in v["messages"]], ensure_ascii=False, default=str)
    assert TYPE3_SENTINEL not in messages_blob                                   # content·artifact 모두
    assert TYPE3_SENTINEL in json.dumps(v["cards"], ensure_ascii=False)          # 카드에는 그대로
    assert TYPE3_SENTINEL in json.dumps(r.cards, ensure_ascii=False)             # card 이벤트에도
    assert TYPE3_SENTINEL not in repr(kit.model.calls) + repr(kit.sink)          # 모델 요청·궤적에는 없음

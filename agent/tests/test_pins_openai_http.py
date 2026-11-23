"""동작 고정(부록 A의 HTTP 항목 A7~A10·A12): 실제 ChatOpenAI(langchain-openai 1.6.7 → openai 3.24.0 → httpx2)를
가짜 OpenAI 호환 서버(scripts/fake_openai_server.py, mlx_lm.server 응답 모양)에 붙여 왕복한다.

서버는 테스트 안에서 127.0.0.1의 빈 포트(0)에 띄운다. 8080·8090·8091에는 접속하지 않는다. GPU·모델 없음.
"""
import importlib.util
import json
from pathlib import Path

import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from glople_agent import contract
from langgraph.checkpoint.memory import InMemorySaver

from glople_agent.graph import build_graph, run_turn
from glople_agent.graph.build import Deps
from glople_agent.llm.fake import RuleScript
from glople_agent.llm.models import (ANSWER_LOGIT_BIAS, DUMMY_API_KEY, MODEL_NAME, answer_model, bind_router,
                                     router_model)
from glople_agent.llm.normalize import (is_model_connection_error, is_model_error, is_model_timeout_error,
                                        normalize_router)
from glople_agent.plan import InMemoryPlanStore
from glople_agent.tools.fake_backend import FakeBackend
from glople_agent.tools.registry import build_read_tools

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fake_openai_server.py"
_spec = importlib.util.spec_from_file_location("fake_openai_server", SCRIPT)
fos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fos)
Reply = fos.Reply


@pytest.fixture
def server():
    s = fos.serve(port=0)
    yield s
    s.shutdown()
    s.server_close()


def router_request(text: str):
    return [SystemMessage(content=contract.SYSTEM_PROMPT), HumanMessage(content=text)]


def test_server_refuses_reserved_ports():
    for port in (8080, 8090, 8091):
        with pytest.raises(ValueError):
            fos.serve(port=port)


# --- A7: bind_tools(dict) 요청 JSON이 학습 분포 그대로 -------------------------------------

def test_router_request_json_is_training_distribution(server):
    server.replies.append(Reply.calls([("search_reviews", {"query": "바나힐"})]))
    out = bind_router(router_model(server.base_url)).invoke(router_request("바나힐 후기"))
    req = server.requests[-1]
    body = req["body"]
    assert req["path"] == "/v1/chat/completions"
    assert json.dumps(body["tools"], ensure_ascii=False) == json.dumps(contract.TOOLS, ensure_ascii=False)  # 키 순서까지
    assert body["messages"] == [{"role": "system", "content": contract.SYSTEM_PROMPT},
                                {"role": "user", "content": "바나힐 후기"}]   # developer 역할 없음
    assert {k: v for k, v in body.items() if k not in ("tools", "messages")} == {
        "model": MODEL_NAME, "stream": False, "max_completion_tokens": 384, "temperature": 0.0}
    assert not {"tool_choice", "parallel_tool_calls", "max_tokens", "n", "stream_options"} & set(body)
    assert req["headers"]["authorization"] == f"Bearer {DUMMY_API_KEY}"   # 사용자 토큰이 아닌 더미
    assert out.tool_calls[0]["name"] == "search_reviews"


def test_retry_overrides_reach_the_wire(server):
    server.replies.extend([Reply.text("a"), Reply.text("b")])
    bound = bind_router(router_model(server.base_url))
    bound.invoke(router_request("x"), max_tokens=768)
    bound.invoke(router_request("x"), temperature=0.2)
    first, second = (r["body"] for r in server.requests[-2:])
    assert first["max_completion_tokens"] == 768 and second["temperature"] == 0.2


def test_env_vars_cannot_redirect_or_rekey(server, monkeypatch):
    # OPENAI_* · LangSmith 게이트웨이 환경 변수가 있어도 명시한 base_url·더미 키가 이긴다(langchain_core _gateway)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SENTINEL-env-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("LANGSMITH_GATEWAY", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-SENTINEL")
    server.replies.append(Reply.text("ok"))
    assert router_model(server.base_url).invoke([HumanMessage("x")]).content == "ok"
    headers = server.requests[-1]["headers"]
    assert headers["authorization"] == f"Bearer {DUMMY_API_KEY}"
    assert "SENTINEL" not in json.dumps(headers)


# --- A8: logit_bias 키, 답변 모델 스트리밍 -------------------------------------------------------

def test_answer_model_logit_bias_top_k_and_streaming(server):
    server.replies.append(Reply.text("부산 후기 3개를 찾았어요.", keepalive=2))
    model = answer_model(server.base_url)
    chunks = list(model.stream([SystemMessage("답변"), HumanMessage("부산 후기")]))
    body = server.requests[-1]["body"]
    assert body["logit_bias"] == {str(k): v for k, v in ANSWER_LOGIT_BIAS.items()}   # 문자열 키 JSON(mlx가 int로 바꿈)
    assert body["top_k"] == 20 and body["top_p"] == 0.8 and body["temperature"] == 0.3   # extra_body는 최상위로
    assert body["stream"] is True and body["max_completion_tokens"] == 400 and "tools" not in body
    assert "stream_options" not in body                    # base_url을 바꾸면 stream_usage 기본 꺼짐
    assert "".join(c.content for c in chunks) == "부산 후기 3개를 찾았어요."   # ': keepalive' 주석 줄은 건너뜀
    merged = chunks[0]
    for c in chunks[1:]:
        merged = merged + c
    # finish_reason은 서버 마지막 청크(합친 메시지)에 있고, langchain이 덧붙인 chunk_position="last" 청크는 비어 있음
    assert len(chunks) > 5 and merged.response_metadata.get("finish_reason") == "stop"
    assert chunks[-1].chunk_position == "last" and chunks[-1].response_metadata == {}


# --- A9: ToolMessage.artifact는 직렬화되지 않음 ---------------------------------------------------

def test_tool_message_artifact_is_not_sent(server):
    server.replies.append(Reply.text("ok"))
    call = {"name": "search_reviews", "args": {"query": "바나힐"}, "id": "call_1"}
    msgs = [SystemMessage("s"), HumanMessage("바나힐 후기"), AIMessage(content="", tool_calls=[call]),
            ToolMessage(content='{"tool": "search_reviews"}', tool_call_id="call_1",
                        artifact={"data": {"text": "TYPE3-SENTINEL 원문"}})]
    router_model(server.base_url).invoke(msgs)
    raw = server.requests[-1]["raw"].decode()
    sent = server.requests[-1]["body"]["messages"]
    assert "TYPE3-SENTINEL" not in raw and "artifact" not in raw
    assert sent[3] == {"role": "tool", "content": '{"tool": "search_reviews"}', "tool_call_id": "call_1"}
    assert sent[2]["content"] is None and sent[2]["tool_calls"][0]["function"] == {
        "name": "search_reviews", "arguments": '{"query": "바나힐"}'}   # 빈 content는 null, 인자는 한글 그대로


# --- A10: finish_reason·invalid_tool_calls·quirk가 AIMessage에 드러남 --------------------------------

@pytest.mark.parametrize(("reply", "kind"), [
    (Reply.calls([("get_destination_detail", {"name": "바나힐"}), ("search_reviews", {"query": "바나힐"})]), "tool"),
    (Reply.dropped(), "dropped"),
    (Reply.length(), "truncated"),
    (Reply.calls([("search_reviews", '{"query": "바나')]), "dropped"),        # JSON이 아님 → invalid_tool_calls
    (Reply.text('<tool_call>\n{"name": "get_weather", "arguments": {"region": "부산"}}\n</tool_call>'), "tool"),
    (Reply.text("어느 지역 날씨를 알려드릴까요?"), "text"),
])
def test_server_quirks_normalize_over_http(server, reply, kind):
    server.replies.append(reply)
    msg = bind_router(router_model(server.base_url)).invoke(router_request("x"))
    assert msg.response_metadata["finish_reason"] == (reply.finish_reason or "stop")
    out = normalize_router(msg)
    assert out.kind == kind
    if kind == "tool" and len(reply.tool_calls) == 2:
        assert msg.content == "\n" and out.message.content == ""        # 병렬 "\n" → ""
        assert [tc["id"] for tc in msg.tool_calls][0].count("-") == 4     # 서버 uuid id 그대로


def test_arguments_not_object_raises_validation_error_like_m0_notes(server):
    server.replies.append(Reply.calls([("get_weather", '"{부산}"')]))
    with pytest.raises(Exception) as e:
        bind_router(router_model(server.base_url)).invoke(router_request("x"))
    assert is_model_error(e.value) and normalize_router(e.value).kind == "dropped"


# --- A12: openai 3.24.0(httpx2) 오류 분류와 max_retries=0 -------------------------------------------

def test_server_error_is_not_retried_by_sdk(server):
    server.replies.append(Reply.http_error(500))
    with pytest.raises(openai.InternalServerError) as e:
        router_model(server.base_url).invoke([HumanMessage("x")])
    assert len(server.requests) == 1 and is_model_error(e.value) and not is_model_connection_error(e.value)


def test_drop_without_response_is_connection_error(server):
    # mlx 비스트리밍: 헤더는 생성이 끝나야 나가므로 생성 중 예외면 아무것도 안 온 채 끊긴다
    server.replies.append(Reply.drop())
    with pytest.raises(openai.APIConnectionError) as e:
        router_model(server.base_url).invoke([HumanMessage("x")])
    assert is_model_connection_error(e.value) and len(server.requests) == 1   # SDK가 같은 생성을 다시 보내지 않음


def test_headers_then_close_is_json_error_not_connection_error(server):
    # HTTP/1.0에서 200 헤더 뒤 본문 없이 끊기면 httpx2는 빈 본문으로 읽고 openai 3.24.0은 JSONDecodeError를
    # 그대로 올린다(APIError로 감싸지 않음). route 노드가 이것도 모델 오류(server)로 잡아 fallback으로 보낸다(api-notes)
    server.replies.append(Reply.disconnect())
    with pytest.raises(json.JSONDecodeError) as e:
        router_model(server.base_url).invoke([HumanMessage("x")])
    assert is_model_error(e.value) and not is_model_connection_error(e.value)
    assert normalize_router(e.value).detail["error"] == "bad_response"


def test_stream_cut_after_headers_raises_no_chunks(server):
    # 스트리밍(M2 답변)에서 헤더 뒤 끊기면 [DONE] 없이 끝나 청크가 0개이고, langchain-core가 ValueError를 낸다
    # (APIConnectionError가 아님) → M2 answer_llm은 이 예외를 가드 실패로 보고 템플릿으로 간다
    server.replies.append(Reply.disconnect())
    with pytest.raises(ValueError, match="No generation chunks"):
        list(answer_model(server.base_url).stream([HumanMessage("x")]))


def test_timeout_is_timeout_error_without_retry(server):
    server.replies.append(Reply.text("늦음", delay=1.5))
    with pytest.raises(openai.APITimeoutError) as e:
        router_model(server.base_url, timeout=0.3).invoke([HumanMessage("x")])
    assert is_model_timeout_error(e.value) and not is_model_connection_error(e.value)
    assert len(server.requests) == 1


def test_connection_refused_is_connection_error():
    s = fos.serve(port=0)
    url = s.base_url
    s.shutdown()
    s.server_close()   # 아무도 듣지 않는 포트
    with pytest.raises(openai.APIConnectionError) as e:
        router_model(url).invoke([HumanMessage("x")])
    assert is_model_connection_error(e.value)


# --- 그래프 끝까지: route 노드가 실제 ChatOpenAI로 HTTP를 탄다 ------------------------------------------

def _server_script(rules: RuleScript):
    """가짜 서버가 RuleScript 규칙으로 답하게(서버 모양으로 바꿔서)."""
    def script(body: dict):
        msg = rules.respond(body["messages"][-1]["content"])
        if msg.tool_calls:
            return Reply.calls([(tc["name"], tc["args"]) for tc in msg.tool_calls])
        return Reply.text(msg.content)
    return script


def test_graph_runs_end_to_end_with_real_chat_openai(agent_context):
    s = fos.serve(port=0, script=_server_script(RuleScript()))
    try:
        backend = FakeBackend(today=agent_context.now.date())
        deps = Deps(router_llm=router_model(s.base_url), read_tools=build_read_tools(backend),
                    plan_store=InMemoryPlanStore(), retry_initial_interval=0.0)
        graph = build_graph(deps, InMemorySaver())
        r = run_turn(graph, "t-http", "바나힐 정보랑 사람들 리뷰 같이 알려줘", agent_context)
        assert r.path == ["start_turn", "route", "validate", "tools", "collect", "answer_template", "finish"]
        assert "바나힐 관련 Glople 후기 3개" in r.final_text
        r2 = run_turn(graph, "t-http", "안녕", agent_context)
        assert r2.reply_kind == "direct"
        for req in s.requests:   # 두 번째 턴에도 한 마디만, 토큰은 모델 서버에 없음
            assert [m["role"] for m in req["body"]["messages"]] == ["system", "user"]
            assert req["headers"]["authorization"] == f"Bearer {DUMMY_API_KEY}"
            assert "SENTINEL" not in req["raw"].decode()
        msg = graph.get_state({"configurable": {"thread_id": "t-http"}}).values["messages"][1]
        assert msg.content == "" and len(msg.tool_calls) == 2           # 병렬 "\n"은 ""로
    finally:
        s.shutdown()
        s.server_close()


def test_graph_connection_error_twice_goes_fallback(agent_context):
    s = fos.serve(port=0)
    s.replies.extend([Reply.drop(), Reply.drop()])
    try:
        deps = Deps(router_llm=router_model(s.base_url), read_tools=build_read_tools(FakeBackend()),
                    plan_store=InMemoryPlanStore(), retry_initial_interval=0.0)
        r = run_turn(build_graph(deps, InMemorySaver()), "t-down", "부산 날씨", agent_context)
        assert r.path == ["start_turn", "route", "fallback", "finish"] and r.reply_kind == "fallback"
        assert len(s.requests) == 2   # RetryPolicy 1회 + 마지막 시도에서 잡음
    finally:
        s.shutdown()
        s.server_close()


def test_graph_headers_then_close_goes_fallback_instead_of_crashing(agent_context):
    s = fos.serve(port=0)
    s.replies.append(Reply.disconnect())
    try:
        deps = Deps(router_llm=router_model(s.base_url), read_tools=build_read_tools(FakeBackend()),
                    plan_store=InMemoryPlanStore(), retry_initial_interval=0.0)
        graph = build_graph(deps, InMemorySaver())
        r = run_turn(graph, "t-cut", "부산 날씨", agent_context)
        assert r.path == ["start_turn", "route", "fallback", "finish"] and len(s.requests) == 1   # 재시도 안 함
        route = graph.get_state({"configurable": {"thread_id": "t-cut"}}).values["trace"]["route"]
        assert route[-1]["detail"]["error"] == "bad_response"
    finally:
        s.shutdown()
        s.server_close()

"""카세트(design.md 6.6, llm/cassette.py + fake.CassetteScript): 녹화한 모델 응답을 GPU·서버 없이 그대로 다시 돌린다.

녹화는 RecordingChatModel(가짜 모델 또는 가짜 OpenAI 서버에 붙인 실제 ChatOpenAI)로 하고, 재생은 CassetteScript로 한다.
실제 mlx 서버 녹화는 오너가 한다(`cli chat --llm real --record PATH`). 여기서는 8090에 접속하지 않는다.
"""
import importlib.util
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from glople_agent import contract
from glople_agent.graph import Deps, build_graph, fake_deps, real_deps, run_turn
from glople_agent.llm.cassette import (CASSETTE_VERSION, Cassette, CassetteMiss, normalize_request,
                                       recording_model, request_key)
from glople_agent.llm.fake import (CassetteScript, ResultEchoScript, RuleScript, fake_answer_model,
                                   fake_router_model)
from glople_agent.plan import InMemoryPlanStore
from glople_agent.tools.fake_backend import FakeBackend
from glople_agent.tools.registry import build_read_tools

from conftest import FIXED_NOW

AGENT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("fake_openai_server", AGENT / "scripts" / "fake_openai_server.py")
fos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fos)

TURNS = ["바다 보이는 곳 추천해줘", "바나힐 정보랑 사람들 리뷰 같이 알려줘", "안녕", "깨진 응답 흉내: 부산 날씨 알려줘",
         "부산 이번 주말 날씨 어때?"]


def _answer_request(call_id: str, content: str = '{"tool": "search_reviews", "status": "ok", "items": []}'):
    call = {"name": "search_reviews", "args": {"query": "바나힐"}, "id": call_id}
    return [SystemMessage("s"), HumanMessage("바나힐 후기"), AIMessage(content="", tool_calls=[call]),
            ToolMessage(content=content, tool_call_id=call_id)]


def test_request_key_ignores_server_call_ids_but_not_content_or_overrides():
    a, b = _answer_request("uuid-a"), _answer_request("uuid-b")
    assert request_key(a, {}) == request_key(b, {})                          # 서버 uuid는 순서 이름으로
    assert normalize_request(a, {})["messages"][3]["tool_call_id"] == "call_1"
    assert request_key(a, {}) != request_key(_answer_request("uuid-a", content="{}"), {})
    route = [SystemMessage(contract.SYSTEM_PROMPT), HumanMessage("부산 날씨")]
    base = request_key(route, {"tools": contract.TOOLS})
    assert base != request_key(route, {})                                    # tools 유무(sha256)
    assert base != request_key(route, {"tools": contract.TOOLS, "max_tokens": 768})   # 재시도 덮어쓰기
    assert base != request_key(route, {"tools": contract.TOOLS, "temperature": 0.2})
    assert "tools_sha256" in normalize_request(route, {"tools": contract.TOOLS})["kwargs"]


def test_cassette_file_roundtrip_and_version(tmp_path):
    c = Cassette()
    msgs = _answer_request("x")
    c.put(msgs, {}, {"content": "ok", "tool_calls": [], "invalid_tool_calls": [], "finish_reason": "stop"})
    path = c.save(tmp_path / "c.json")
    loaded = Cassette.load(path)
    assert len(loaded) == 1 and loaded.lookup(msgs, {})["content"] == "ok"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["version"] == CASSETTE_VERSION and next(iter(body["entries"].values()))["request"]["messages"]
    path.write_text(json.dumps({**body, "version": 999}), encoding="utf-8")
    with pytest.raises(ValueError, match="버전"):
        Cassette.load(path)


def test_miss_is_explicit_and_not_swallowed_by_route(make_graph, agent_context):
    kit = make_graph(CassetteScript(Cassette()))
    with pytest.raises(CassetteMiss, match="카세트에 없는 요청"):
        kit.turn("안녕", agent_context)


def _recording_deps(cassette: Cassette) -> Deps:
    router = recording_model(fake_router_model(RuleScript()), cassette, profile="router")
    answer = recording_model(fake_answer_model(ResultEchoScript()), cassette, profile="answer")
    assert router.disable_streaming is True and answer.disable_streaming is False
    return Deps(router_llm=router, answer_llm=answer, read_tools=build_read_tools(FakeBackend(today=FIXED_NOW.date())),
                plan_store=InMemoryPlanStore(), answer_mode="llm", retry_initial_interval=0.0)


def _run(graph, ctx, thread: str) -> list[tuple[list[str], str, str]]:
    out = []
    for text in TURNS:
        r = run_turn(graph, thread, text, ctx)
        out.append((r.path, r.final_text, r.reply_kind))
    return out


def test_record_with_fake_models_then_replay(tmp_path, agent_context):
    cassette = Cassette(path=tmp_path / "fake.json")
    recorded = _run(build_graph(_recording_deps(cassette), InMemorySaver()), agent_context, "t-rec")
    assert [k for _, _, k in recorded] == ["llm", "llm", "direct", "fallback", "template"]
    # 라우터 5턴(깨진 응답은 재시도로 2번) + 답변 2번 = 8개 요청, 키는 모두 다름
    assert len(Cassette.load(tmp_path / "fake.json")) == 8
    assert set(cassette.meta["models"]) == {"router", "answer"}

    script = CassetteScript(tmp_path / "fake.json")
    deps = fake_deps(script, backend=FakeBackend(today=FIXED_NOW.date()), answer_mode="llm",
                     answer_llm=fake_answer_model(script))
    replayed = _run(build_graph(deps, InMemorySaver()), agent_context, "t-play")
    assert replayed == recorded and script.hits == 8


def _server_script():
    rules = RuleScript()

    def script(body: dict):
        if "tools" not in body:
            tool = next(m for m in body["messages"] if m["role"] == "tool")
            item = json.loads(tool["content"])["items"][0]
            return fos.Reply.text(f"{item.get('name') or item.get('title')} 같은 결과가 있어요.")
        text = body["messages"][-1]["content"]
        if text.startswith("(끊김)"):
            return fos.Reply.drop()
        msg = rules.respond(text)
        if msg.tool_calls:
            return fos.Reply.calls([(tc["name"], tc["args"]) for tc in msg.tool_calls])
        return fos.Reply.text(msg.content)

    return script


def test_record_real_chat_openai_over_http_then_replay_offline(tmp_path, agent_context, sentinel_raw):
    s = fos.serve(port=0, script=_server_script())
    path = tmp_path / "http.json"
    turns = [*TURNS[:3], "(끊김) 부산 날씨"]
    try:
        deps = real_deps(s.base_url, backend=FakeBackend(today=FIXED_NOW.date()), retry_initial_interval=0.0,
                         record=path)
        graph = build_graph(deps, InMemorySaver())
        recorded = [(r.path, r.final_text, r.reply_kind) for r in
                    (run_turn(graph, "t-http-rec", t, agent_context) for t in turns)]
        n_requests = len(s.requests)
    finally:
        s.shutdown()
        s.server_close()
    assert [k for _, _, k in recorded] == ["llm", "llm", "direct", "fallback"]
    assert recorded[3][0] == ["start_turn", "route", "fallback", "finish"]   # 연결 끊김 2번(RetryPolicy) → fallback
    raw = path.read_text(encoding="utf-8")
    assert sentinel_raw not in raw and agent_context.user_id not in raw and "TYPE3-SENTINEL" not in raw
    cassette = Cassette.load(path)
    assert {e["response"].get("error") for e in cassette.entries.values()} == {None, "connection"}
    assert cassette.meta["models"]["answer"]["logit_bias"] == {"151657": -100, "151658": -100}

    script = CassetteScript(path)   # 서버를 끈 뒤 같은 대화가 그대로(연결 오류 기록까지)
    deps = fake_deps(script, backend=FakeBackend(today=FIXED_NOW.date()), answer_mode="llm",
                     answer_llm=fake_answer_model(script))
    graph = build_graph(deps, InMemorySaver())
    replayed = [(r.path, r.final_text, r.reply_kind) for r in
                (run_turn(graph, "t-http-play", t, agent_context) for t in turns)]
    assert replayed == recorded and script.hits == n_requests

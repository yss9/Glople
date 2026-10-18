"""M2 answer_llm(a3, design.md 6.4·6.5): 가짜 답변 모델(ScriptedChatModel)과 가짜 OpenAI 서버로 그래프 전 구간.

- 통과하면 answer_llm → finish(reply_kind llm), 가드에 걸리면 answer_llm → answer_template(같은 턴의 템플릿 답).
- 답변 요청 = [System(ANSWER_SYSTEM_PROMPT), Human(router_input), AI(LLM이 볼 호출만), Tool(LLM용 봉투)…], 도구 목록 없음.
- 템플릿이 답할 결과(준비 중·미지원·빈 결과·오류·날씨)는 호출·결과를 짝째로 빼고 템플릿 문장을 앞에 붙인다.
- Type3 원문·카드 데이터·사용자 토큰은 답변 요청에 0건.
서버는 테스트 안에서 127.0.0.1 빈 포트에 띄운다(8080·8090·8091 접속 없음).
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from glople_agent.answer import templates as tpl
from glople_agent.config import Settings
from glople_agent.eval.trajectory import JsonlTrajectorySink, format_line
from glople_agent.graph import build_graph, real_deps, run_config, run_turn
from glople_agent.graph.answer_input import (TOOL_SECTION_MAX_CHARS, build_answer_input, llm_answerable,
                                             section_caps)
from glople_agent.graph.guard import MAX_ANSWER_CHARS
from glople_agent.llm.fake import (QuirkScript, ResultEchoScript, RuleScript, fake_answer_model, from_server,
                                   generator_literals, tool_call_message)
from glople_agent.llm.models import ANSWER_LOGIT_BIAS, DUMMY_API_KEY, answer_model
from glople_agent.llm.prompts import ANSWER_SYSTEM_PROMPT
from glople_agent.tools.envelope import make_result
from glople_agent.tools.fake_backend import FakeBackend

AGENT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("fake_openai_server", AGENT / "scripts" / "fake_openai_server.py")
fos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fos)
Reply = fos.Reply

TYPE3_SENTINEL = "TYPE3-SENTINEL-5d1c8e2a"
TYPE3_TEXT = f"{TYPE3_SENTINEL} 가거도 설명문 자리 표시자입니다."
SEA = "바다 보이는 곳 추천해줘"
SEA_ANSWER = "바다 취향으로 산토리니, 가거도, 가천 다랭이마을 등 5곳을 찾았어요. 자세한 소개는 카드에서 볼 수 있어요."
PASS_PATH = ["start_turn", "route", "validate", "tools", "collect", "answer_llm", "finish"]
FAIL_PATH = ["start_turn", "route", "validate", "tools", "collect", "answer_llm", "answer_template", "finish"]
TEMPLATE_PATH = ["start_turn", "route", "validate", "tools", "collect", "answer_template", "finish"]


def says(text: str, finish: str = "stop"):
    return lambda messages, kwargs: from_server(text, None, finish)


def llm_kit(make_graph, answer_script, router_script=None):
    model = fake_answer_model(answer_script)
    kit = make_graph(router_script or RuleScript(), answer_mode="llm", answer_llm=model)
    return kit, model


def template_answer(make_graph, text: str, ctx, router_script=None) -> str:
    return make_graph(router_script or RuleScript()).turn(text, ctx).final_text


# --- 프롬프트 ----------------------------------------------------------------------------

def test_answer_prompt_matches_design_doc():
    doc = (AGENT / "docs" / "design.md").read_text(encoding="utf-8")
    section = doc[doc.index("### 6.4"):doc.index("### 6.5")]
    block = re.search(r"```\n(.*?)\n```", section, re.S).group(1)
    assert ANSWER_SYSTEM_PROMPT == block


# --- 통과 경로 ----------------------------------------------------------------------------

def test_llm_answer_passes_guard_and_streams_preview(make_graph, agent_context):
    kit, model = llm_kit(make_graph, says(SEA_ANSWER))
    r = kit.turn(SEA, agent_context)
    assert r.path == PASS_PATH and r.reply_kind == "llm" and r.final_text == SEA_ANSWER
    assert r.preview == SEA_ANSWER and len(r.tokens) == len(SEA_ANSWER)    # custom 스트림 글자 단위 미리보기
    assert not r.replaced and r.answer_trace["guard"] == "pass" and r.answer_trace["reasons"] == []
    assert r.answer_trace["mentioned"] == ["산토리니", "가거도", "가천 다랭이마을"]
    v = kit.values()
    assert v["messages"][-1].content == SEA_ANSWER and isinstance(v["messages"][-1], AIMessage)

    [(messages, kwargs)] = model.calls
    assert kwargs == {}                                                   # 도구 목록·tool_choice 없음
    assert [type(m) for m in messages] == [SystemMessage, HumanMessage, AIMessage, ToolMessage]
    system, human, ai, tool = messages
    assert system.content == ANSWER_SYSTEM_PROMPT and human.content == SEA
    router = next(m for m in v["messages"] if isinstance(m, AIMessage) and m.tool_calls)
    state_tool = next(m for m in v["messages"] if isinstance(m, ToolMessage))
    assert ai.content == "" and ai.tool_calls == router.tool_calls        # validate가 남긴 호출 그대로
    assert tool.tool_call_id == router.tool_calls[0]["id"] and tool.content == state_tool.content   # LLM용 봉투 그대로
    assert json.loads(tool.content)["tool"] == "recommend_by_keywords"
    assert tool.artifact is None                                          # 카드는 답변 요청에 없음


def test_result_echo_script_passes_the_guard_on_every_answerable_demo(make_graph, agent_context):
    kit, model = llm_kit(make_graph, ResultEchoScript())
    for text in (SEA, "바나힐 정보랑 사람들 리뷰 같이 알려줘", "28살 남자 ENTP 나랑 비슷한 여행자 루트", "도쿄 글로플러 찾아줘"):
        r = kit.turn(text, agent_context)
        assert r.reply_kind == "llm" and r.path[-2:] == ["answer_llm", "finish"], (text, r.answer_trace)


# --- 가드 실패 → 템플릿 -----------------------------------------------------------------------

LONG = ("산토리니가 좋아요. " * 70)[:MAX_ANSWER_CHARS + 5]


@pytest.mark.parametrize(("script", "reason", "replaced"), [
    (says(""), "empty", False),
    (lambda m, k: tool_call_message([("search_reviews", {"query": "가거도"})]), "tool_calls", False),
    (says('<tool_call>\n{"name": "search_reviews", "arguments": {"query": "가거도"}}\n</tool_call>'),
     "tool_call_text", True),
    (says('산토리니를 찾았어요. {"name": "가거도"}'), "json_fragment", True),
    (says(generator_literals()["GREETING_ANSWER"]), "canned", True),
    (says("바다가 보이는 좋은 곳이 많아요. 카드를 확인해 보세요."), "no_item_name", True),
    (says("산토리니 등 7곳을 찾았어요."), "unknown_number", True),
    (says(f"가거도는 {TYPE3_TEXT}"), "card_overlap", True),
    (says(LONG), "too_long", True),
    (says(SEA_ANSWER, finish="length"), "incomplete", True),
], ids=["empty", "tool_calls", "tool_call_text", "json", "canned", "no_name", "number", "type3", "long", "length"])
def test_guard_failure_falls_back_to_same_turn_template(make_graph, agent_context, script, reason, replaced):
    kit, _ = llm_kit(make_graph, script)
    r = kit.turn(SEA, agent_context)
    assert r.path == FAIL_PATH and r.reply_kind == "template"
    assert r.final_text == template_answer(make_graph, SEA, agent_context)   # 템플릿 모드와 같은 답
    trace = r.answer_trace
    assert trace["guard"] == "fail" and reason in trace["reasons"] and trace["reply_kind"] == "template"
    assert r.replaced is replaced and trace["replaced"] is replaced         # 미리보기를 보낸 뒤 바뀌었는지
    assert bool(r.tokens) is replaced
    if reason == "card_overlap":   # 원문과 겹친 답은 trace에도 남기지 않음(Type3 복제 방지)
        assert "rejected_text" not in trace and TYPE3_SENTINEL not in json.dumps(kit.values()["trace"])
    elif replaced:
        assert trace["rejected_text"]


def test_model_errors_fall_back_without_preview(make_graph, agent_context):
    kit, _ = llm_kit(make_graph, QuirkScript(["connection_error", "timeout", "server_error"]))
    for _ in range(3):
        r = kit.turn(SEA, agent_context)
        assert r.path == FAIL_PATH and r.reply_kind == "template" and not r.replaced
        assert r.answer_trace["reason"] == "model_error"
    assert r.answer_trace["error"] == "InternalServerError"


def test_bug_in_answer_model_is_not_swallowed(make_graph, agent_context):
    def broken(messages, kwargs):
        raise KeyError("우리 코드 버그")

    kit, _ = llm_kit(make_graph, broken)
    with pytest.raises(KeyError):
        kit.turn(SEA, agent_context)


def test_no_model_in_llm_mode_keeps_m0_trace(make_graph, agent_context):
    kit = make_graph(RuleScript(), answer_mode="llm")
    r = kit.turn(SEA, agent_context)
    assert r.path == FAIL_PATH and r.answer_trace == {"guard": "fail", "reason": "no_model"} and not r.replaced


# --- 템플릿이 답할 결과는 짝째로 뺌 ------------------------------------------------------------

def test_pending_feature_result_is_removed_pairwise_and_its_sentence_prepended(make_graph, agent_context):
    text = "바나힐 정보랑 사람들 리뷰 같이 알려줘"
    llm_text = "바나힐 관련 후기로 '바나힐 골든브릿지 다녀왔어요' 등이 있어요."
    kit, model = llm_kit(make_graph, says(llm_text))
    r = kit.turn(text, agent_context)
    assert r.path == PASS_PATH
    prefix = tpl.status_message("get_destination_detail", "pending_feature")
    assert r.final_text == f"{prefix} {llm_text}" and r.preview == r.final_text
    [(messages, _)] = model.calls
    ai = messages[2]
    tools = [m for m in messages if isinstance(m, ToolMessage)]
    assert [tc["name"] for tc in ai.tool_calls] == ["search_reviews"]           # 상세(준비 중) 호출은 빠짐
    assert [m.tool_call_id for m in tools] == [ai.tool_calls[0]["id"]]          # 결과도 짝째로
    assert "pending_feature" not in json.dumps([m.content for m in messages], ensure_ascii=False)
    assert r.answer_trace["visible"] == ["search_reviews"] and r.answer_trace["removed"] == ["get_destination_detail"]


def test_weather_and_review_in_parallel(make_graph, agent_context):
    raw = tool_call_message([("search_reviews", {"query": "바나힐"}), ("get_weather", {"region": "부산"})])
    llm_text = "'바나힐 골든브릿지 다녀왔어요' 같은 후기가 있어요."
    kit, model = llm_kit(make_graph, says(llm_text), QuirkScript([raw]))
    r = kit.turn("바나힐 후기랑 부산 날씨 알려줘", agent_context)
    assert r.final_text == f"{tpl.status_message('get_weather', 'unsupported')} {llm_text}"
    [(messages, _)] = model.calls
    assert [tc["name"] for tc in messages[2].tool_calls] == ["search_reviews"] and len(messages) == 4


@pytest.mark.parametrize("text", ["부산 이번 주말 날씨 어때?", "제주 후기 좀 찾아줘"])
def test_only_template_results_skip_answer_llm(make_graph, agent_context, text):
    kit, model = llm_kit(make_graph, says("부르면 안 됨"))
    r = kit.turn(text, agent_context)
    assert r.path == TEMPLATE_PATH and model.calls == [] and r.answer_trace == {}
    assert r.final_text == template_answer(make_graph, text, agent_context)


def test_clarify_question_of_dropped_call_follows_llm_answer(make_graph, agent_context):
    raw = tool_call_message([("search_reviews", {"query": "바나힐"}), ("get_weather", {"region": "부산", "when": "모레"})])
    llm_text = "'바나힐 골든브릿지 다녀왔어요' 같은 후기가 있어요."
    kit, _ = llm_kit(make_graph, says(llm_text), QuirkScript([raw]))
    r = kit.turn("바나힐 후기랑 부산 모레 날씨", agent_context)
    assert r.reply_kind == "llm" and r.final_text == f"{llm_text} {tpl.CLARIFY['weather_region']}"


def test_llm_answerable_rule():
    assert llm_answerable("search_reviews", "ok") and not llm_answerable("get_weather", "ok")
    for status in ("empty", "error", "unsupported", "pending_feature"):
        assert not llm_answerable("recommend_by_keywords", status)


# --- 입력 위생: Type3·카드·토큰 ---------------------------------------------------------------

def test_type3_text_and_card_data_never_reach_answer_model(make_graph, agent_context, sentinel_raw):
    kit, model = llm_kit(make_graph, ResultEchoScript())
    r = kit.turn("사찰 야경 바다 섬 추천해줘", agent_context)
    assert r.reply_kind == "llm"
    sent = repr(model.calls)
    assert TYPE3_SENTINEL not in sent and "attribution" not in sent and "detailPath" not in sent
    assert sentinel_raw not in sent and agent_context.user_id not in sent
    assert TYPE3_SENTINEL in json.dumps(kit.values()["cards"], ensure_ascii=False)   # 카드에는 그대로
    assert TYPE3_SENTINEL not in json.dumps(kit.values()["trace"], ensure_ascii=False)


def test_tool_section_is_capped_at_1000_chars():
    items = [{"name": f"{i}번 " + "가" * 90, "where": "나" * 30} for i in range(5)]
    calls, msgs, cards = [], {}, {}
    for i, tool in enumerate(("recommend_by_keywords", "search_reviews", "search_gloplers")):
        cid = f"c{i}"
        content, card = make_result(tool, "ok", items, call_id=cid)
        assert len(content) > 700
        calls.append({"id": cid, "name": tool, "args": {}})
        msgs[cid] = ToolMessage(content=content, tool_call_id=cid)
        cards[cid] = card
    inp = build_answer_input("x", calls, msgs, cards)
    assert inp.tool_chars <= TOOL_SECTION_MAX_CHARS and len(inp.visible) == 3
    for v in inp.visible:
        body = json.loads(v["content"])
        assert body["tool"] == v["tool"] and any("화면 카드에만" in n for n in body["notes"])
    assert section_caps([100, 900, 900], 1000) == [100, 450, 450] and section_caps([100, 200], 1000) == [100, 200]


def test_input_overlap_blocks_the_request(make_graph, agent_context, monkeypatch):
    # 어댑터 실수로 원문이 LLM용 content에 섞이면 답변 모델을 부르지 않고 템플릿으로(fail closed)
    from glople_agent.graph import answer_input

    original = answer_input.build_answer_input

    def leaky(*args, **kwargs):
        inp = original(*args, **kwargs)
        inp.visible[0]["content"] = inp.visible[0]["content"][:-2] + f', "leak": "{TYPE3_TEXT}"}}'
        return inp

    monkeypatch.setattr("glople_agent.graph.nodes.build_answer_input", leaky)
    kit, model = llm_kit(make_graph, says(SEA_ANSWER))
    r = kit.turn(SEA, agent_context)
    assert r.path == FAIL_PATH and model.calls == [] and r.answer_trace["reason"] == "input_overlap"


# --- 단계 수·궤적·스트림 모드 -----------------------------------------------------------------

def test_llm_mode_step_budget(make_graph, agent_context):
    # llm 모드 도구 경로는 노드 8개(가드 실패 시 answer_template까지) → recursion_limit 9면 끝까지, 8이면 fallback
    kit, _ = llm_kit(make_graph, says("카드를 보세요."))
    r = kit.turn(SEA, agent_context, recursion_limit=9)
    assert r.path == FAIL_PATH and r.error is None
    r = kit.turn(SEA, agent_context, recursion_limit=8)
    assert r.path == ["start_turn", "route", "fallback", "finish"] and r.error is None


def test_trajectory_records_guard_verdict_without_text(make_graph, agent_context, tmp_path):
    kit, _ = llm_kit(make_graph, QuirkScript([says("산토리니 등 7곳이에요.")], then=says(SEA_ANSWER)))
    kit.turn(SEA, agent_context)
    kit.turn(SEA, agent_context)
    failed, passed = kit.sink
    assert failed["answer"] == {"guard": "fail", "reasons": ["unknown_number"], "replaced": True}
    assert passed["answer"] == {"guard": "pass", "reasons": [], "replaced": False}
    sink = JsonlTrajectorySink(tmp_path)
    sink(failed)   # 금지 키(text 등) 없음
    raw = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "7곳" not in raw and "산토리니" not in raw
    assert format_line(failed).endswith("reply=template guard=fail[unknown_number] 교체")


def test_messages_stream_mode_yields_answer_chunks(make_graph, agent_context):
    kit, _ = llm_kit(make_graph, says(SEA_ANSWER))
    from langchain_core.messages import HumanMessage as H

    parts = list(kit.graph.stream({"messages": [H(SEA)]}, run_config("t-msg-mode"), context=agent_context,
                                  stream_mode="messages", version="v2"))
    chunks = [p["data"][0] for p in parts if p["data"][1]["langgraph_node"] == "answer_llm"
              and isinstance(p["data"][0], AIMessageChunk)]
    assert "".join(c.content for c in chunks) == SEA_ANSWER


# --- 실제 ChatOpenAI(answer_model) ↔ 가짜 OpenAI 서버(스트리밍) -----------------------------------------

def _first_name(body: dict) -> str:
    for m in body["messages"]:
        if m["role"] == "tool":
            item = json.loads(m["content"])["items"][0]
            return item.get("name") or item.get("title") or item.get("nickname")
    raise AssertionError("tool 메시지 없음")


def server_script(answer=None):
    """tools가 있으면 라우터(RuleScript 규칙), 없으면 답변(answer(body) 또는 첫 결과 이름으로 한 문장)."""
    rules = RuleScript()

    def script(body: dict):
        if "tools" in body:
            msg = rules.respond(body["messages"][-1]["content"])
            if msg.tool_calls:
                return Reply.calls([(tc["name"], tc["args"]) for tc in msg.tool_calls])
            return Reply.text(msg.content)
        if answer is not None:
            return answer(body)
        return Reply.text(f"{_first_name(body)} 같은 곳을 찾았어요. 자세한 내용은 카드에서 볼 수 있어요.")

    return script


@pytest.fixture
def http_server():
    servers = []

    def start(script):
        s = fos.serve(port=0, script=script)
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def http_graph(base_url: str, **kw):
    deps = real_deps(base_url, backend=FakeBackend(), retry_initial_interval=0.0, **kw)
    return build_graph(deps, InMemorySaver()), deps


def test_real_answer_model_streams_over_http_and_passes_guard(http_server, agent_context, sentinel_raw):
    s = http_server(server_script())
    graph, deps = http_graph(s.base_url)
    assert deps.answer_mode == "llm" and deps.answer_llm.logit_bias == ANSWER_LOGIT_BIAS
    r = run_turn(graph, "t-http-llm", "사찰 야경 바다 섬 추천해줘", agent_context)
    assert r.path == PASS_PATH and r.reply_kind == "llm" and r.final_text.startswith("산토리니 같은 곳")
    assert len(r.tokens) > 5 and r.preview == r.final_text                   # 서버가 한 글자씩 보낸 SSE
    router_req, answer_req = (q["body"] for q in s.requests)
    assert "tools" in router_req and router_req["stream"] is False
    assert "tools" not in answer_req and "tool_choice" not in answer_req and answer_req["stream"] is True
    assert answer_req["logit_bias"] == {"151657": -100, "151658": -100}       # <tool_call>·</tool_call> 막기
    assert (answer_req["temperature"], answer_req["top_p"], answer_req["top_k"]) == (0.3, 0.8, 20)
    assert answer_req["max_completion_tokens"] == 400
    roles = [m["role"] for m in answer_req["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    system, user, assistant, tool = answer_req["messages"]
    assert system["content"] == ANSWER_SYSTEM_PROMPT and user["content"] == "사찰 야경 바다 섬 추천해줘"
    assert assistant["content"] is None and [c["function"]["name"] for c in assistant["tool_calls"]] == [
        "recommend_by_keywords"]
    assert set(tool) == {"role", "content", "tool_call_id"} and json.loads(tool["content"])["status"] == "ok"
    for q in s.requests:   # 토큰 위생: 사용자 토큰·Type3 원문은 모델 서버에 0건
        raw = q["raw"].decode()
        assert sentinel_raw not in raw and TYPE3_SENTINEL not in raw and agent_context.user_id not in raw
        assert q["headers"]["authorization"] == f"Bearer {DUMMY_API_KEY}"


def test_stream_cut_midway_is_incomplete_and_replaced(http_server, agent_context):
    s = http_server(server_script(lambda body: Reply.cut(f"{_first_name(body)} 같은 곳을 찾았어요.", 6)))
    graph, _ = http_graph(s.base_url)
    r = run_turn(graph, "t-cut", SEA, agent_context)
    assert r.path == FAIL_PATH and r.reply_kind == "template" and r.replaced
    assert r.answer_trace["reasons"] == ["incomplete"] and r.answer_trace["finish_reason"] is None
    assert r.preview and r.preview != r.final_text


def test_stream_cut_after_headers_is_model_error(http_server, agent_context):
    s = http_server(server_script(lambda body: Reply.disconnect()))
    graph, _ = http_graph(s.base_url)
    r = run_turn(graph, "t-cut0", SEA, agent_context)
    assert r.path == FAIL_PATH and not r.replaced
    assert r.answer_trace["reason"] == "model_error" and r.answer_trace["error"] == "ValueError"


def test_real_deps_template_mode_and_settings(http_server, agent_context):
    s = http_server(server_script())
    graph, deps = http_graph(s.base_url, answer_mode="template")
    assert deps.answer_llm is None
    r = run_turn(graph, "t-tpl", SEA, agent_context)
    assert r.path == TEMPLATE_PATH and len(s.requests) == 1                  # 라우터만 실제 모델
    d = real_deps(Settings(model_base_url=s.base_url))                       # Settings 기본 answer_mode는 template
    assert d.answer_mode == "template" and d.router_llm.openai_api_base == s.base_url
    d = real_deps(Settings(model_base_url=s.base_url, answer_mode="llm"))
    assert d.answer_llm is not None and d.answer_llm.openai_api_base == s.base_url


def test_answer_model_factory_has_logit_bias_and_no_tools():
    m = answer_model("http://127.0.0.1:1/v1")
    payload = m._get_request_payload([HumanMessage("x")])
    assert payload["logit_bias"] == ANSWER_LOGIT_BIAS and "tools" not in payload and payload["stream"] is True

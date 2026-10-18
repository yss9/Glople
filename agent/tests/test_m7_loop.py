"""M7 도구 결과 뒤 루프(graph/after_tools.py, README 'M7 도구 결과 뒤 루프'): 가짜 모델로 그래프 전 구간.

- 기본(after_tools="answer")은 지금 경로 그대로: collect → answer_llm·answer_template, 라우터는 턴마다 한 번.
- loop: collect → route_after_tools(라우터에 [system, user, 호출, 결과…] + 도구 8개) → 글이면 가드 → finish / answer_template,
  호출이면 validate_after_tools(근거 = 발화 + 앞 결과, 같은 서명·빈 결과 뒤 같은 도구·일정 금지) → tools → … 최대 2라운드.
- 라우터 요청은 v4 학습 기록(training/data/tool_calling_v4/test.jsonl의 after_call·after2_answer)과 같은 모양이어야 한다:
  실제 ChatOpenAI가 가짜 OpenAI 서버에 보낸 JSON을 기록 앞부분과 비교하고, training/.venv가 있으면 채팅 템플릿으로 렌더링해
  바이트까지 같은지 본다(scripts/render_prompt.py, CPU 토크나이저만).
- 카드(2라운드 포함)·SSE·체크포인트·확인 카드·토큰 위생도 loop에서 그대로인지 본다. GPU·모델 서버는 쓰지 않는다.
"""
import importlib.util
import io
import json
import logging
import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from glople_agent import cli, contract
from glople_agent.answer import templates as tpl
from glople_agent.config import Settings, SettingsError
from glople_agent.context import AgentContext
from glople_agent.eval.trajectory import JsonlTrajectorySink
from glople_agent.graph import LOOP_NODE_NAMES, NODE_NAMES, build_graph, fake_deps, mermaid, real_deps, run_turn
from glople_agent.graph.after_tools import after_tools_request, evidence_text, tool_rounds
from glople_agent.graph.edges import MAX_TOOL_ROUNDS
from glople_agent.graph.nodes import unpaired_calls
from glople_agent.llm.fake import (V4_TEST_PATH, AfterToolsScript, LoopAnswerScript, QuirkScript, RequestShapeError,
                                   ResultEchoScript, RuleScript, V4OracleScript, after_tools_rounds,
                                   check_after_tools_request, fake_answer_model, from_server, last_human_text,
                                   text_message, tool_call_message)
from glople_agent.plan import InMemoryPlanStore
from glople_agent.tools.backend import KST
from glople_agent.tools.fake_backend import FakeBackend

AGENT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("fake_openai_server", AGENT / "scripts" / "fake_openai_server.py")
fos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fos)

TRAINING_PY = contract.TRAINING_DIR / ".venv" / "bin" / "python"
MODEL_DIR = contract.TRAINING_DIR / "models" / "qwen3-4b-instruct-2507-4bit"
RENDER = AGENT / "scripts" / "render_prompt.py"
TYPE3_SENTINEL = "TYPE3-SENTINEL-5d1c8e2a"   # tests/fixtures/backend/recommend_by_keywords.json의 가짜 Type3 글

SEA = "바다 보이는 곳 추천해줘"
SEA_ANSWER = "바다 취향으로 산토리니, 가거도, 가천 다랭이마을 등 5곳을 찾았어요. 자세한 소개는 카드에서 볼 수 있어요."
ROUTES = ("recommend_similar_traveler_routes", {"age": 28, "gender": "남성", "mbti": "ENTP"})
CHAIN = "28살 남자 ENTP인데 나랑 비슷한 여행자 루트 보고 그중 섬 여행지 후기도 보여줘"   # '울릉도'는 발화에 없음(결과에만)
ISLAND_REVIEWS = ("search_reviews", {"query": "울릉도"})
CHAIN_ANSWER = "비슷한 여행자 루트에 울릉도가 있어요. 울릉도 후기로는 '울릉도 도동항 첫 배 타고 간 후기'가 있어요."
ANSWER_PATH = ["start_turn", "route", "validate", "tools", "collect", "route_after_tools", "finish"]
TWO_ROUND_PATH = ["start_turn", "route", "validate", "tools", "collect", "route_after_tools", "validate_after_tools",
                  "tools", "collect", "route_after_tools", "finish"]
BLOCKED_PATH = ["start_turn", "route", "validate", "tools", "collect", "route_after_tools", "validate_after_tools",
                "answer_template", "finish"]
TEMPLATE_PATH = ["start_turn", "route", "validate", "tools", "collect", "answer_template", "finish"]
V4_NOW = datetime(2026, 10, 18, 12, tzinfo=KST)   # v4 생성기의 CTX(build_multiturn_dataset.py)와 같은 기준 시각


class Chain:
    """가짜 라우터: 첫 판단은 first 호출들, 도구 결과 k라운드 뒤에는 steps[k-1](AIMessage·예외·함수, 모자라면 마지막 것)."""

    def __init__(self, first, *steps):
        self.first = list(first)
        self.steps = steps
        self.requests: list[list] = []

    def __call__(self, messages, kwargs):
        rounds = after_tools_rounds(messages)
        if rounds == 0:
            return tool_call_message(self.first)
        self.requests.append(list(messages))
        step = self.steps[min(rounds, len(self.steps)) - 1]
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(messages, kwargs)
        return step.model_copy(deep=True)


def says(text: str, finish: str = "stop"):
    return lambda messages, kwargs: from_server(text, None, finish)


def calls(*items):
    return tool_call_message(list(items))


def loop_kit(make_graph, router, **kw):
    return make_graph(router, after_tools="loop", **kw)


def decisions(result) -> list[str]:
    return [e["decision"] for e in result.after_tools]


# --- 설정·그래프 모양 ---------------------------------------------------------------------

def test_settings_default_is_answer_and_env_selects_loop():
    assert Settings.from_env({}).after_tools == "answer" and Settings().after_tools == "answer"
    assert Settings.from_env({"GLOPLE_AFTER_TOOLS": "loop"}).after_tools == "loop"
    with pytest.raises(SettingsError):
        Settings.from_env({"GLOPLE_AFTER_TOOLS": "agent"})
    from glople_agent.service.settings import ServiceSettings

    assert ServiceSettings.from_env({}).after_tools == "answer"
    assert ServiceSettings.from_env({"GLOPLE_AFTER_TOOLS": "loop"}).after_tools == "loop"
    with pytest.raises(SettingsError):
        ServiceSettings(after_tools="always")


def test_cli_and_service_flags_parse():
    p = cli.parser()
    assert p.parse_args(["chat"]).after_tools is None   # None = GLOPLE_AFTER_TOOLS(없으면 answer)
    assert p.parse_args(["chat", "--after-tools", "loop", "--fake", "v4"]).after_tools == "loop"
    assert p.parse_args(["replay"]).after_tools == "answer" and p.parse_args(["graph"]).after_tools == "answer"
    with pytest.raises(SystemExit):
        p.parse_args(["chat", "--after-tools", "agent"])


def test_default_graph_has_no_loop_nodes_and_loop_graph_replaces_answer_llm():
    default = build_graph(fake_deps(RuleScript()))
    loop = build_graph(fake_deps(RuleScript(), after_tools="loop"))
    assert set(default.nodes) - {"__start__"} == set(NODE_NAMES)
    assert set(loop.nodes) - {"__start__"} == set(LOOP_NODE_NAMES)
    assert "answer_llm" not in loop.nodes and "route_after_tools" not in default.nodes
    assert "route_after_tools" not in mermaid(default)
    text = mermaid(loop)
    for edge in ("collect -.-> route_after_tools", "route_after_tools -.-> validate_after_tools",
                 "validate_after_tools -.-> tools", "route_after_tools -.-> finish"):
        assert edge in text
    with pytest.raises(ValueError, match="after_tools"):
        build_graph(fake_deps(RuleScript(), after_tools="agent"))   # type: ignore[arg-type]


def test_cli_graph_prints_loop_nodes():
    out = io.StringIO()
    assert cli.main(["graph", "--after-tools", "loop"], out=out) == 0
    assert "route_after_tools" in out.getvalue() and "answer_llm" not in out.getvalue()


@pytest.mark.parametrize("answer_mode", ["template", "llm"])
def test_default_mode_paths_and_router_calls_unchanged(make_graph, agent_context, answer_mode):
    answer = fake_answer_model(ResultEchoScript()) if answer_mode == "llm" else None
    kit = make_graph(RuleScript(), answer_mode=answer_mode, answer_llm=answer)
    r = kit.turn(SEA, agent_context)
    expected = TEMPLATE_PATH if answer_mode == "template" else [*TEMPLATE_PATH[:5], "answer_llm", "finish"]
    assert r.path == expected and len(kit.model.calls) == 1   # 라우터는 턴마다 한 번(결과 뒤에 다시 부르지 않음)
    assert r.after_tools == [] and r.tool_rounds == 1
    assert "after_tools" not in kit.sink[-1] and all("round" not in v for v in kit.sink[-1]["verdicts"])


# --- 1라운드 답 ------------------------------------------------------------------------------

def test_round1_text_answer_passes_guard(make_graph, agent_context):
    kit = loop_kit(make_graph, AfterToolsScript(RuleScript(), says(SEA_ANSWER), check_request=True))
    r = kit.turn(SEA, agent_context)
    assert r.path == ANSWER_PATH and r.reply_kind == "llm" and r.final_text == SEA_ANSWER
    assert decisions(r) == ["answer"] and r.after_tools[0]["round"] == 1 and r.after_tools[0]["next"] == "finish"
    t = r.answer_trace
    assert t["guard"] == "pass" and t["via"] == "after_tools" and t["prompt"] == "router" and not r.replaced
    assert t["mentioned"] == ["산토리니", "가거도", "가천 다랭이마을"]
    v = kit.values()
    assert isinstance(v["messages"][-1], AIMessage) and v["messages"][-1].content == SEA_ANSWER
    assert not unpaired_calls(v["messages"])
    assert [c["tool"] for c in r.cards] == ["recommend_by_keywords"]
    assert kit.sink[-1]["answer"] == {"guard": "pass", "reasons": [], "replaced": False, "via": "after_tools"}
    assert kit.sink[-1]["after_tools"] == [{"round": 1, "decision": "answer"}]


def test_after_tools_request_is_training_shape(make_graph, agent_context):
    kit = loop_kit(make_graph, AfterToolsScript(RuleScript(), says(SEA_ANSWER)))
    kit.turn(SEA, agent_context)
    assert len(kit.model.calls) == 2
    (first, first_kw), (after, after_kw) = kit.model.calls
    assert [type(m) for m in first] == [SystemMessage, HumanMessage]
    check_after_tools_request(after, after_kw)   # [System(SYSTEM_PROMPT), Human, AI(호출, content ""), Tool] + 도구 8개
    assert [type(m) for m in after] == [SystemMessage, HumanMessage, AIMessage, ToolMessage]
    assert after[1].content == SEA and after_kw == first_kw == {"tools": contract.TOOLS}
    state_tool = next(m for m in kit.values()["messages"] if isinstance(m, ToolMessage))
    assert after[3].content == state_tool.content                  # LLM용 봉투 그대로(길이를 줄이지 않음)
    assert after[2].tool_calls[0]["args"] == kit.values()["messages"][1].tool_calls[0]["args"]   # validate가 고친 인자
    assert after[2].response_metadata == {} and after[2].content == ""
    assert TYPE3_SENTINEL not in repr(after) and TYPE3_SENTINEL in json.dumps(kit.values()["cards"], ensure_ascii=False)


def test_request_shape_checker_rejects_wrong_requests():
    good = [SystemMessage(contract.SYSTEM_PROMPT), HumanMessage("x"),
            AIMessage(content="", tool_calls=[{"name": "search_reviews", "args": {"query": "x"}, "id": "c1"}]),
            ToolMessage(content='{"tool": "search_reviews"}', tool_call_id="c1")]
    check_after_tools_request(good, {"tools": contract.TOOLS})
    bad = [
        ([SystemMessage("다른 system"), *good[1:]], {"tools": contract.TOOLS}),
        (good[:3], {"tools": contract.TOOLS}),
        ([*good[:3], ToolMessage(content="{}", tool_call_id="other")], {"tools": contract.TOOLS}),
        (good, {"tools": contract.TOOLS[:3]}),
        (good, {"tools": contract.TOOLS, "tool_choice": "auto"}),
    ]
    for messages, kwargs in bad:
        with pytest.raises(RequestShapeError):
            check_after_tools_request(messages, kwargs)


# --- 2라운드 ------------------------------------------------------------------------------

def test_second_call_grounded_by_previous_results_runs_and_answers(make_graph, agent_context):
    router = Chain([ROUTES], calls(ISLAND_REVIEWS), says(CHAIN_ANSWER))
    kit = loop_kit(make_graph, router)
    r = kit.turn(CHAIN, agent_context)
    assert r.path == TWO_ROUND_PATH and r.reply_kind == "llm" and r.final_text == CHAIN_ANSWER
    assert decisions(r) == ["call", "answer"] and r.tool_rounds == 2
    # 두 라운드 카드가 모두 화면 이벤트로 나가고(같은 turn_id), state에도 남는다
    assert [c["tool"] for c in r.cards] == ["recommend_similar_traveler_routes", "search_reviews"]
    assert len({c["turn_id"] for c in r.cards}) == 1 and len({c["card_id"] for c in r.cards}) == 2
    v = kit.values()
    assert [(x["tool"], x["status"]) for x in v["results"]] == [("recommend_similar_traveler_routes", "ok"),
                                                               ("search_reviews", "ok")]
    second = [x for x in v["verdicts"] if x.get("round") == 2]
    assert len(second) == 1 and second[0]["ok"] and second[0]["name"] == "search_reviews"
    assert len(v["call_log"]) == 2 and not unpaired_calls(v["messages"])
    # 라우터 요청: 첫 판단 [S,H], 1라운드 뒤 [S,H,A,T], 2라운드 뒤 [S,H,A,T,A,T]
    assert [len(m) for m, _ in kit.model.calls] == [2, 4, 6]
    check_after_tools_request(*kit.model.calls[2])
    assert kit.sink[-1]["after_tools"] == [{"round": 1, "decision": "call"}, {"round": 2, "decision": "answer"}]
    assert [x.get("round") for x in kit.sink[-1]["verdicts"]] == [None, 2]
    assert r.answer_trace["round"] == 2 and r.answer_trace["visible"] == ["recommend_similar_traveler_routes",
                                                                          "search_reviews"]


def test_evidence_text_matches_training_rule(make_graph, agent_context):
    router = Chain([ROUTES], calls(ISLAND_REVIEWS), says(CHAIN_ANSWER))
    kit = loop_kit(make_graph, router)
    kit.turn(CHAIN, agent_context)
    v = kit.values()
    rounds = tool_rounds(v["messages"])
    text = evidence_text(v["router_input"], rounds[:1])
    lines = text.split("\n")
    assert lines[0] == CHAIN and "울릉도" in lines and "남성" in lines   # 결과의 목적지 + 앞 호출의 글 인자(gender)
    assert "울릉도" not in CHAIN
    # 요청 함수도 실행된 라운드만(학습 기록과 같은 순서)
    req = after_tools_request(v["router_input"], rounds)
    assert [type(m).__name__ for m in req] == ["SystemMessage", "HumanMessage", "AIMessage", "ToolMessage",
                                               "AIMessage", "ToolMessage"]


def test_same_signature_repeat_is_blocked(make_graph, agent_context):
    def repeat_first(messages, kwargs):
        tc = messages[2].tool_calls[0]
        return calls((tc["name"], tc["args"]))

    kit = loop_kit(make_graph, AfterToolsScript(RuleScript(), repeat_first))
    r = kit.turn(SEA, agent_context)
    assert r.path == BLOCKED_PATH and r.reply_kind == "template" and decisions(r) == ["call"]
    blocked = [x for x in kit.values()["verdicts"] if x.get("round")]
    assert [(x["ok"], x["reason"]) for x in blocked] == [(False, "duplicate")]
    assert r.final_text == make_graph(RuleScript()).turn(SEA, agent_context).final_text   # 1라운드 결과의 템플릿 답
    assert len(kit.model.calls) == 2 and not unpaired_calls(kit.values()["messages"])
    assert [c["tool"] for c in r.cards] == ["recommend_by_keywords"]


def test_round_limit_forces_answer_without_third_call(make_graph, agent_context):
    third = ("get_weather", {"region": "울릉도"})
    kit = loop_kit(make_graph, Chain([ROUTES], calls(ISLAND_REVIEWS), calls(third)))
    r = kit.turn(CHAIN, agent_context)
    assert MAX_TOOL_ROUNDS == 2
    assert decisions(r) == ["call", "round_limit"] and r.reply_kind == "template"
    assert r.path == [*TWO_ROUND_PATH[:-1], "answer_template", "finish"] and r.path.count("tools") == 2
    assert r.after_tools[-1]["calls"] == ["get_weather"]
    assert [c["tool"] for c in r.cards] == ["recommend_similar_traveler_routes", "search_reviews"]
    assert not unpaired_calls(kit.values()["messages"]) and len(kit.model.calls) == 3
    assert "울릉도 도동항 첫 배 타고 간 후기" in r.final_text   # 템플릿 답도 두 라운드 카드로


def test_ungrounded_second_argument_is_blocked_and_asked(make_graph, agent_context):
    kit = loop_kit(make_graph, Chain([ROUTES], calls(("search_reviews", {"query": "다낭"}))))
    r = kit.turn(CHAIN, agent_context)
    assert r.path == BLOCKED_PATH and r.reply_kind == "template"
    blocked = [x for x in kit.values()["verdicts"] if x.get("round")]
    assert blocked[0]["reason"] == "grounding" and "clarify:query" in blocked[0]["grounding"]["issues"]
    assert r.final_text.endswith(tpl.clarify_for("search_reviews", ["query"]))   # 앞 결과 답 + 되묻기
    assert [c["tool"] for c in r.cards] == ["recommend_similar_traveler_routes"]


@pytest.mark.parametrize("backend_error", [False, True], ids=["empty", "error"])
def test_same_tool_after_empty_or_error_result_is_blocked(make_graph, agent_context, backend_error):
    backend = FakeBackend(today=agent_context.now.date(),
                          errors={"search_reviews": TimeoutError("timeout")} if backend_error else None)
    query = "바나힐" if backend_error else "산토리니"   # 바나힐은 후기가 있지만 오류를 흉내, 산토리니는 빈 결과
    kit = loop_kit(make_graph, Chain([("search_reviews", {"query": query})],
                                     calls(("search_reviews", {"query": f"{query} 여행"}))), backend=backend)
    r = kit.turn(f"{query} 후기 보여줘", agent_context)
    status = kit.values()["results"][0]["status"]
    assert status == ("error" if backend_error else "empty")
    assert r.path == BLOCKED_PATH and r.reply_kind == "template"
    assert [x["reason"] for x in kit.values()["verdicts"] if x.get("round")] == ["retry_after_limit"]


def test_other_tool_after_empty_result_is_allowed(make_graph, agent_context):
    # 학습 detail_reviews: '정보가 없으면 후기라도' → 상세가 빈 결과면 다른 도구(후기)는 부를 수 있다
    kit = loop_kit(make_graph, Chain([("get_destination_detail", {"name": "가거도"})],
                                     calls(("search_reviews", {"query": "가거도"})), LoopAnswerScript()))
    r = kit.turn("가거도에 대해 알려 줘. 정보가 없으면 후기라도 대신 찾아 줘", agent_context)
    assert r.path == TWO_ROUND_PATH and decisions(r) == ["call", "answer"]
    assert [(x["tool"], x["status"]) for x in kit.values()["results"]] == [("get_destination_detail", "empty"),
                                                                          ("search_reviews", "empty")]
    assert r.final_text == "Glople에 그 장소 정보가 없어요. 아직 가거도 후기가 없어요."


# --- 가드·일정·quirk·단계 --------------------------------------------------------------------

def test_guard_failure_falls_back_to_template(make_graph, agent_context):
    bad = "바다 취향으로 산토리니를 찾았어요. 평점은 4.7점이에요."
    kit = loop_kit(make_graph, AfterToolsScript(RuleScript(), says(bad)))
    r = kit.turn(SEA, agent_context)
    assert r.path == [*ANSWER_PATH[:-1], "answer_template", "finish"] and r.reply_kind == "template"
    assert decisions(r) == ["guard_fail"]
    t = r.answer_trace
    assert t["guard"] == "fail" and t["reasons"] == ["unknown_number"] and t["via"] == "after_tools"
    assert t["rejected_text"] == bad and t["unknown_numbers"] == ["4.7"]
    assert r.final_text == make_graph(RuleScript()).turn(SEA, agent_context).final_text
    assert cli.answer_label(r) == "[template · 가드 unknown_number]"
    assert kit.sink[-1]["answer"]["guard"] == "fail"


def test_canned_text_after_results_fails_guard(make_graph, agent_context):
    canned = "비슷한 여행자를 찾으려면 나이, 성별, MBTI가 필요해요. 알려주시겠어요?"   # M2에서 본 LoRA 말버릇
    kit = loop_kit(make_graph, Chain([ROUTES], says(canned)))
    r = kit.turn(CHAIN, agent_context)
    assert r.reply_kind == "template" and "no_item_name" in r.answer_trace["reasons"]


def test_plan_call_inside_loop_is_not_accepted(make_graph, agent_context):
    plan = ("create_travel_plan", {"region": "강릉", "days": 2, "places": ["경포해변"]})
    store = InMemoryPlanStore()
    kit = loop_kit(make_graph, AfterToolsScript(RuleScript(), lambda m, k: calls(plan)), store=store)
    r = kit.turn(SEA, agent_context)
    assert r.path == BLOCKED_PATH and r.pending is None and r.reply_kind == "template"
    assert [x["reason"] for x in kit.values()["verdicts"] if x.get("round")] == ["plan_in_loop"]
    assert r.final_text.endswith(tpl.PLAN_IN_LOOP)
    assert store.call_count == 0 and not any(c["kind"].startswith("plan") for c in r.cards)
    # 다음 턴에 사용자가 직접 부탁한 일정은 지금 그대로(확인 카드 → 승인 → 저장), loop 판에서도
    r2 = kit.turn("제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘", agent_context)
    assert r2.pending is not None and kit.waiting_at() == ("review",)
    done = kit.resume({"decision": "approve"}, agent_context)
    assert done.reply_kind == "plan" and store.save_count == 1


@pytest.mark.parametrize("quirk,decision", [("dropped", "dropped"), ("truncated", "truncated"),
                                            ("unparsed_broken", "unparsed"), ("timeout", "error"),
                                            ("server_error", "error")])
def test_router_quirks_after_tools_fall_back_to_template(make_graph, agent_context, quirk, decision):
    kit = loop_kit(make_graph, AfterToolsScript(RuleScript(), QuirkScript([quirk])))
    r = kit.turn(SEA, agent_context)
    assert r.reply_kind == "template" and decisions(r) == [decision] and r.error is None
    assert r.path == [*ANSWER_PATH[:-1], "answer_template", "finish"]


def test_connection_error_retries_once_then_answers(make_graph, agent_context, caplog):
    caplog.set_level(logging.DEBUG)
    kit = loop_kit(make_graph, AfterToolsScript(RuleScript(), QuirkScript(["connection_error"], then=says(SEA_ANSWER))))
    r = kit.turn(SEA, agent_context)
    assert r.reply_kind == "llm" and r.after_tools[-1]["node_attempt"] == 2
    assert "Retrying task route_after_tools" in caplog.text
    kit2 = loop_kit(make_graph, AfterToolsScript(RuleScript(), QuirkScript(["connection_error", "connection_error"])))
    r2 = kit2.turn(SEA, agent_context)
    assert r2.reply_kind == "template" and decisions(r2) == ["error"]


@pytest.mark.parametrize("limit,expected", [(8, []), (10, ["no_steps"]), (12, ["call"]), (13, ["call", "answer"])])
def test_step_budget_degrades_to_template_without_recursion_error(make_graph, agent_context, limit, expected):
    kit = loop_kit(make_graph, Chain([ROUTES], calls(ISLAND_REVIEWS), says(CHAIN_ANSWER)))
    r = kit.turn(CHAIN, agent_context, recursion_limit=limit)
    assert r.error is None and r.path[-1] == "finish" and decisions(r) == expected
    assert not unpaired_calls(kit.values()["messages"])


def test_all_place_candidates_still_clarify_in_loop(make_graph, agent_context):
    router = Chain([("get_destination_detail", {"name": "보문사"})], says("쓰이면 안 됨"))
    kit = loop_kit(make_graph, router)
    r = kit.turn("보문사 알려줘", agent_context)
    assert r.path == ["start_turn", "route", "validate", "tools", "collect", "clarify", "finish"]
    assert router.requests == [] and r.reply_kind == "clarify"


def test_trajectory_lines_have_no_forbidden_keys(make_graph, agent_context, tmp_path):
    sink = JsonlTrajectorySink(tmp_path)
    deps = fake_deps(Chain([ROUTES], calls(ISLAND_REVIEWS), says(CHAIN_ANSWER)), sink=sink,
                     backend=FakeBackend(today=agent_context.now.date()), after_tools="loop")
    run_turn(build_graph(deps, InMemorySaver()), "t-traj", CHAIN, agent_context)
    assert sink.last["after_tools"] == [{"round": 1, "decision": "call"}, {"round": 2, "decision": "answer"}]
    from glople_agent.eval.trajectory import format_line

    assert "search_reviews:ok@2" in format_line(sink.last) and "loop=call→answer" in format_line(sink.last)


# --- 토큰·Type3 위생, 체크포인트, 확인 카드 ------------------------------------------------------

def test_loop_mode_keeps_token_and_type3_hygiene(make_graph, agent_context, sentinel_raw, caplog):
    caplog.set_level(logging.DEBUG)
    from test_token_hygiene import saver_bytes

    def after(messages, kwargs):
        if after_tools_rounds(messages) == 1 and last_human_text(messages) == CHAIN:
            return calls(ISLAND_REVIEWS)
        return LoopAnswerScript()(messages, kwargs)

    first = lambda messages, kwargs: (tool_call_message([ROUTES]) if last_human_text(messages) == CHAIN  # noqa: E731
                                      else RuleScript()(messages, kwargs))
    store = InMemoryPlanStore()
    kit = loop_kit(make_graph, AfterToolsScript(first, after, check_request=True), store=store)
    for text in (SEA, CHAIN, "바나힐 정보랑 사람들 리뷰 같이 알려줘", "부산 이번 주말 날씨 어때?"):
        assert kit.turn(text, agent_context).reply_kind in ("llm", "template")
    kit.turn("제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘", agent_context)
    assert kit.resume({"decision": "approve"}, agent_context).reply_kind == "plan" and store.save_count == 1
    stored = saver_bytes(kit.saver)
    blobs = {"라우터 요청": repr(kit.model.calls), "체크포인트": stored.decode("utf-8", "replace"),
             "궤적": repr(kit.sink), "로그": caplog.text,
             "기록": repr(list(kit.graph.get_state_history(kit.config)))}
    for name, blob in blobs.items():
        for needle in (sentinel_raw, agent_context.request_id, agent_context.user_id):
            assert needle not in blob, f"{name}에 {needle!r}"
    assert TYPE3_SENTINEL not in blobs["라우터 요청"] and TYPE3_SENTINEL not in blobs["궤적"]
    assert TYPE3_SENTINEL in json.dumps(kit.values()["cards"], ensure_ascii=False)   # 원문은 카드에만
    assert sum(1 for m, _ in kit.model.calls if after_tools_rounds(m)) >= 5          # 결과 뒤 요청을 실제로 훑었는지
    assert not any(r.has_authorization for r in kit.backend.fixture.requests)


def test_two_round_turn_survives_checkpoint_reload(make_graph, agent_context, tmp_path):
    from glople_agent import checkpoints

    saver, conn = checkpoints.open_saver(tmp_path / "cp.sqlite")
    try:
        kit = loop_kit(make_graph, Chain([ROUTES], calls(ISLAND_REVIEWS), says(CHAIN_ANSWER)), saver=saver)
        r = kit.turn(CHAIN, agent_context)
        assert r.tool_rounds == 2
    finally:
        conn.close()
    saver2, conn2 = checkpoints.open_saver(tmp_path / "cp.sqlite")
    try:
        values = build_graph(kit.deps, saver2).get_state(kit.config).values
        assert len(tool_rounds(values["messages"])) == 2 and values["final_text"] == CHAIN_ANSWER
        assert [c["tool"] for c in values["cards"]] == ["recommend_similar_traveler_routes", "search_reviews"]
        assert [e["decision"] for e in values["trace"]["after_tools"]] == ["call", "answer"]
    finally:
        conn2.close()


def test_subgraph_plan_graph_works_with_loop(make_graph, agent_context):
    kit = make_graph(Chain([ROUTES], calls(ISLAND_REVIEWS), says(CHAIN_ANSWER)), after_tools="loop",
                     plan_graph="subgraph")
    assert kit.turn(CHAIN, agent_context).path == TWO_ROUND_PATH


# --- 서비스(SSE) -----------------------------------------------------------------------------

def test_service_streams_second_round_cards_and_final(tmp_path):
    pytest.importorskip("fastapi")
    from service_kit import ALICE, API, service_kit

    router = Chain([ROUTES], calls(ISLAND_REVIEWS), says(CHAIN_ANSWER))
    with service_kit(tmp_path, router=router, after_tools="loop") as kit:
        assert kit.svc is not None and kit.settings.after_tools == "loop"
        tid = kit.new_thread()
        s = kit.say(tid, CHAIN)
        names = s.names()
        assert names[0] == "meta" and names[-2:] == ["final", "done"]
        nodes = [d["node"] for d in s.all("node")]
        assert nodes.count("tools") == 2 and "route_after_tools" in nodes and "validate_after_tools" in nodes
        cards = s.all("card")
        assert [c["card"]["tool"] for c in cards] == ["recommend_similar_traveler_routes", "search_reviews"]
        assert names.index("card") < names.index("final") and max(i for i, n in enumerate(names) if n == "card") \
            < names.index("final")
        assert s.first("final") == {"text": CHAIN_ANSWER, "kind": "llm", "replaced": False}
        view = kit.client.get(f"{API}/threads/{tid}", headers=ALICE).json()
        assert [c["tool"] for c in view["turns"][-1]["cards"]] == ["recommend_similar_traveler_routes",
                                                                   "search_reviews"]
        assert view["turns"][-1]["answer"] == CHAIN_ANSWER and view["turns"][-1]["kind"] == "llm"


def test_service_fake_deps_follow_after_tools_setting(tmp_path):
    from glople_agent.service.deps import build_service_deps
    from glople_agent.service.settings import ServiceSettings

    loop = build_service_deps(ServiceSettings(llm="fake", plan_store="memory", after_tools="loop",
                                              data_dir=tmp_path))
    default = build_service_deps(ServiceSettings(llm="fake", plan_store="memory", data_dir=tmp_path))
    assert loop.graph.after_tools == "loop" and default.graph.after_tools == "answer"
    assert isinstance(loop.graph.router_llm.script, AfterToolsScript)
    assert isinstance(default.graph.router_llm.script, RuleScript)


def test_real_deps_take_after_tools_from_settings_without_connecting():
    deps = real_deps(Settings(after_tools="loop"), answer_mode="template")
    assert deps.after_tools == "loop" and deps.answer_llm is None
    assert real_deps("http://127.0.0.1:9/v1", answer_mode="template").after_tools == "answer"
    assert real_deps("http://127.0.0.1:9/v1", answer_mode="template", after_tools="loop").after_tools == "loop"


# --- CLI ----------------------------------------------------------------------------------

def test_cli_chat_fake_rules_loop_runs_route_after_tools():
    out = io.StringIO()
    s = cli.ChatSession("rules", inp=io.StringIO(SEA + "\n"), out=out, after_tools="loop", db=None)
    assert s.run() == 0
    text = out.getvalue()
    assert "M7: 도구 결과 뒤 루프" in text and "route_after_tools" in text and "[llm]" in text


@pytest.mark.needs_training_data()
def test_cli_replay_loop_keeps_expected_paths():
    out = io.StringIO()
    assert cli.main(["replay", "--data", "v2-test,human", "--after-tools", "loop"], out=out) == 0
    text = out.getvalue()
    assert "| 예외 | 0 |" in text and "| 정답과 다른 경로 | 0 |" in text and "| 도구 결과 뒤(loop) 판단 |" in text


# --- v4 학습 기록과 같은 요청(가짜 OpenAI 서버 + 실제 ChatOpenAI) ----------------------------------

def _v4_chain_records() -> dict[str, dict]:
    """발화 → {"first", "second", "answer", "records"}. 오류를 흉내 낸 기록(결과가 fixture와 다름)은 뺀다."""
    out: dict[str, dict] = {}
    for rec in contract.read_jsonl(V4_TEST_PATH):
        if rec["task"] not in ("after_call", "after2_answer"):
            continue
        msgs = rec["messages"]
        if any(m["role"] == "tool" and '"status": "error"' in m["content"] for m in msgs):
            continue
        user = msgs[1]["content"]
        entry = out.setdefault(user, {"records": []})
        entry["records"].append(rec)
        entry["first"] = [(c["function"]["name"], json.loads(c["function"]["arguments"]))
                          for c in msgs[2]["tool_calls"]]
        if rec["task"] == "after_call":
            entry["second"] = [(c["function"]["name"], json.loads(c["function"]["arguments"]))
                               for c in msgs[-1]["tool_calls"]]
        else:
            entry["second"] = [(c["function"]["name"], json.loads(c["function"]["arguments"]))
                               for c in msgs[4]["tool_calls"]]
            entry["answer"] = msgs[-1]["content"]
    return out


def _normalize(m: dict) -> dict:
    """역할·글·호출(이름·arguments 문자열)만. id·tool_call_id는 템플릿이 렌더링하지 않아 뺀다(짝은 따로 확인)."""
    out = {"role": m["role"], "content": m.get("content") or ""}
    if m.get("tool_calls"):
        out["tool_calls"] = [{"type": c.get("type", "function"), "function": {"name": c["function"]["name"],
                                                                              "arguments": c["function"]["arguments"]}}
                             for c in m["tool_calls"]]
    return out


def _capture_v4_requests(chains: dict[str, dict]) -> list[dict]:
    def script(body: dict):
        msgs = body["messages"]
        user = msgs[1]["content"]
        rounds = sum(1 for m in msgs if m["role"] == "assistant" and m.get("tool_calls"))
        entry = chains[user]
        if rounds == 0:
            return fos.Reply.calls(entry["first"])
        if rounds == 1:
            return fos.Reply.calls(entry["second"])
        return fos.Reply.text(entry.get("answer") or "결과를 찾았어요.")

    server = fos.serve(port=0, script=script)
    try:
        deps = real_deps(server.base_url, answer_mode="template", after_tools="loop",
                         backend=FakeBackend(today=date(2026, 10, 18)), retry_initial_interval=0.0)
        graph = build_graph(deps, InMemorySaver())
        ctx = AgentContext(user_id=None, bearer=None, request_id="v4-shape", now=V4_NOW, seed=0)
        for i, user in enumerate(chains):
            r = run_turn(graph, f"t-v4-{i}", user, ctx)
            assert r.tool_rounds == 2 and r.error is None, (user, r.path, r.after_tools)
        return [req["body"] for req in server.requests]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.needs_training_data("tool_calling_v4/test.jsonl")
def test_router_requests_match_v4_training_records():
    chains = _v4_chain_records()
    assert len(chains) >= 20
    bodies = _capture_v4_requests(chains)
    compared = {4: 0, 6: 0}
    for body in bodies:
        msgs = body["messages"]
        assert body["tools"] == contract.TOOLS and not {"tool_choice", "parallel_tool_calls"} & set(body)
        assert body["model"] == "default_model" and body.get("temperature") == 0
        if len(msgs) < 4:
            continue
        user = msgs[1]["content"]
        record = next((r for r in chains[user]["records"] if len(r["messages"]) > len(msgs)), None)
        if record is None:   # after2_answer가 오류 흉내라 빠진 발화(두 번째 결과 뒤 기록이 없음)
            continue
        assert [_normalize(m) for m in msgs] == [_normalize(m) for m in record["messages"][:len(msgs)]], user
        for i, m in enumerate(msgs):   # 결과는 바로 앞 호출과 같은 id로 짝지어짐
            if m["role"] == "tool":
                prev = next(x for x in reversed(msgs[:i]) if x["role"] == "assistant")
                assert m["tool_call_id"] in {c["id"] for c in prev["tool_calls"]}
                assert set(m) == {"role", "content", "tool_call_id"}
        compared[len(msgs)] += 1
    with_after2 = sum(1 for c in chains.values() if "answer" in c)
    assert compared[4] == len(chains) and compared[6] == with_after2 >= 20


def test_v4_oracle_script_reads_rounds(tmp_path):
    rec = {"messages": [{"role": "system", "content": contract.SYSTEM_PROMPT}, {"role": "user", "content": "u"},
                        {"role": "assistant", "content": "", "tool_calls": [
                            {"id": "call_0", "type": "function",
                             "function": {"name": "search_reviews", "arguments": '{"query": "바나힐"}'}}]},
                        {"role": "tool", "tool_call_id": "call_0", "content": "{}"},
                        {"role": "assistant", "content": "끝"}], "tools": contract.TOOLS}
    oracle = V4OracleScript(records=[rec], fallback=says("모름"))
    assert len(oracle) == 1 and oracle.conflicts == 0
    assert oracle.find("u", 1) == {"role": "assistant", "content": "끝"} and oracle.find("u", 0) is None
    req = [SystemMessage(contract.SYSTEM_PROMPT), HumanMessage("u"),
           AIMessage(content="", tool_calls=[{"name": "search_reviews", "args": {"query": "바나힐"}, "id": "x"}]),
           ToolMessage(content="{}", tool_call_id="x")]
    assert oracle(req, {}).content == "끝"
    assert oracle(req[:2], {}).content == "모름"


def _render(request: Path) -> dict:
    out = subprocess.run([str(TRAINING_PY), "-I", str(RENDER), "--request", str(request), "--record", str(V4_TEST_PATH),
                          "--model-dir", str(MODEL_DIR)], capture_output=True, text=True, timeout=120, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.needs_training_data("tool_calling_v4/test.jsonl")
@pytest.mark.skipif(not (TRAINING_PY.exists() and (MODEL_DIR / "tokenizer.json").exists()
                         and (MODEL_DIR / "chat_template.jinja").exists()),
                    reason="training/.venv 또는 Qwen 토크나이저 파일 없음")
def test_router_requests_render_like_v4_records(tmp_path):
    chains = _v4_chain_records()
    picked = dict([kv for kv in chains.items() if "answer" in kv[1]][:3])   # 1라운드 뒤(4개)·2라운드 뒤(6개) 요청 둘씩
    bodies = [b for b in _capture_v4_requests(picked) if len(b["messages"]) >= 4]
    assert len(bodies) == 2 * len(picked)
    for i, body in enumerate(bodies):
        path = tmp_path / f"req{i}.json"
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        result = _render(path)
        assert result["record_found"] and result["same_bytes"] and result["prefix_of_record"], result
        assert result["special_counts"] == result["record_special_counts"]
        rounds = (len(body["messages"]) - 2) // 2
        # <tool_call>은 도구 안내 머리에 2번 + 라운드마다 1번, <tool_response>는 라운드마다 1번
        assert result["special_counts"]["<tool_call>"] == 2 + rounds, result
        assert result["special_counts"]["<tool_response>"] == rounds, result


# --- 측정: A/B loop 모드, measure_after_tools.py --loop ------------------------------------------

def test_abtest_loop_mode_reports_recall_rounds_and_guard():
    from glople_agent.eval.abtest import (MODES, FakeProvider, ScenarioOracle, compute_metrics, format_report,
                                          load_scenarios, parse_modes, raw_rows, run_ab, run_mode)

    scenarios = load_scenarios()
    assert MODES["loop"].after_tools == "loop" and MODES["loop"].answer == "template"
    run = run_ab(FakeProvider(scenarios), scenarios, routers=["fake"], modes=parse_modes("a3,loop"))
    loop, a3 = (compute_metrics(run.records[("fake", m)], b=200) for m in ("loop", "a3"))
    assert loop.turn_exact.k == loop.turn_exact.n == a3.turn_exact.n   # 첫 라운드 호출로 정답과 비교(경로 같음)
    assert loop.recalled.n > 0 and loop.recalled.k == 0 and loop.two_rounds.k == 0   # 착한 가짜는 다시 부르지 않음
    assert loop.guard_pass.n == loop.llm_attempts > a3.llm_attempts                   # 빈 결과·날씨도 라우터가 답함
    assert set(loop.loop_decisions) == {"answer"} and a3.recalled.n == 0
    report = format_report(run, b=200)
    assert "결과 뒤 재호출(loop)" in report and "2라운드 실행(loop)" in report and "loop(결과 뒤 라우터" in report
    assert all("after_tools" in row and "rounds" in row for row in raw_rows(run))
    # 결과 뒤에 같은 호출을 또 내는 라우터: 재호출로 세고, 막혀서 템플릿으로 끝남
    def again(messages, kwargs):
        tc = messages[2].tool_calls[0]
        return calls((tc["name"], tc["args"]))

    picked = [s for s in scenarios if s.category == "keywords"][:4]
    provider = FakeProvider(picked, router_scripts={"fake": AfterToolsScript(ScenarioOracle(picked), again)})
    m = compute_metrics(run_mode(provider, picked, router="fake", mode=MODES["loop"]), b=200)
    assert m.recalled.k == m.recalled.n > 0 and m.two_rounds.k == 0 and m.loop_decisions["call"] == m.recalled.k
    assert m.reply_kinds.get("template", 0) >= m.recalled.k


@pytest.mark.needs_training_data("tool_calling_v4/test.jsonl")
def test_measure_after_tools_loop_fake_counts_chain_second_calls():
    spec = importlib.util.spec_from_file_location("measure_after_tools", AGENT / "scripts" / "measure_after_tools.py")
    measure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(measure)
    from glople_agent.llm.fake import fake_router_model

    data = measure.loop_cases(20, ("chain",))
    assert data and all(c["set"] == "chain" and c["second"] for c in data)
    model = fake_router_model(AfterToolsScript(LoopAnswerScript(), V4OracleScript(fallback=LoopAnswerScript())))
    rows = measure.run_loop("fake-v4", model, data, retry=0.0)
    ok = [r for r in rows if r.get("first_ok")]
    assert len(ok) == len(data) and all(r["recalled"] and r["rounds"] == 2 and r["second_correct"] for r in ok)
    assert all(r["guard"] == "pass" and r["decisions"] == ["call", "answer"] for r in ok)
    report = measure.format_loop_report(rows, ["fake-v4"], ["chain"], 0.0)
    assert f"| 2라운드 실행 | {len(ok)}/{len(ok)} (100%) |" in report and "두 번째 호출 = 정답(chain)" in report
    # 결과 뒤에 늘 같은 호출을 내는 모델: 재호출 100%, 2라운드 0, 막힘 이유는 같은 서명(빈 결과였으면 같은 도구 다시)
    def again(messages, kwargs):
        tc = messages[2].tool_calls[0]
        return calls((tc["name"], tc["args"]))

    rows = measure.run_loop("again", fake_router_model(AfterToolsScript(LoopAnswerScript(), again)), data,
                            retry=0.0)
    assert all(r["recalled"] and r["rounds"] == 1 and not r["second_correct"] for r in rows)
    assert {tuple(r["blocked"]) for r in rows} == {("duplicate",), ("retry_after_limit",)}
    assert all(r["reply_kind"] == "template" for r in rows)


# --- 결과 뒤 모델 따로(2026-10-18 실측 뒤: 첫 판단 v3 + 결과 뒤 v4, docs/m7-ab-hybrid.md) --------------------------

def test_after_tools_llm_gets_only_after_tools_requests(agent_context):
    import dataclasses

    from glople_agent.graph import new_thread_id
    from glople_agent.llm.fake import fake_router_model

    seen: dict[str, list[int]] = {"router": [], "after": []}
    rule = RuleScript()

    def router(messages, kwargs):
        seen["router"].append(after_tools_rounds(messages))
        return rule(messages, kwargs)

    def after(messages, kwargs):
        seen["after"].append(after_tools_rounds(messages))
        return from_server(SEA_ANSWER, None, "stop")

    deps = fake_deps(router, backend=FakeBackend(), store=InMemoryPlanStore(), after_tools="loop")
    deps = dataclasses.replace(deps, after_tools_llm=fake_router_model(after))
    r = run_turn(build_graph(deps, InMemorySaver()), new_thread_id(), SEA, agent_context)
    assert r.path == ANSWER_PATH and r.reply_kind == "llm" and r.final_text == SEA_ANSWER
    assert seen == {"router": [0], "after": [1]}   # 첫 판단은 라우터, 결과 뒤 요청은 따로 준 모델만

    seen.update(router=[], after=[])   # answer 모드에서는 결과 뒤 모델을 줘도 쓰지 않는다
    deps = dataclasses.replace(fake_deps(router, backend=FakeBackend(), store=InMemoryPlanStore()),
                               after_tools_llm=fake_router_model(after))
    r = run_turn(build_graph(deps, InMemorySaver()), new_thread_id(), SEA, agent_context)
    assert "route_after_tools" not in r.path and seen == {"router": [0], "after": []}


def test_after_tools_base_url_setting_builds_separate_model_without_connecting():
    url = "http://127.0.0.1:8092/v1"
    assert Settings.from_env({"GLOPLE_AFTER_TOOLS_BASE_URL": url}).after_tools_base_url == url
    assert Settings.from_env({}).after_tools_base_url is None
    d = real_deps(Settings(after_tools="loop", after_tools_base_url=url), answer_mode="template")
    assert d.after_tools_llm is not None and d.after_tools_llm is not d.router_llm
    assert str(d.after_tools_llm.openai_api_base) == url
    # answer 모드, 주소 없음, 라우터와 같은 주소면 따로 만들지 않는다
    assert real_deps(Settings(after_tools="answer", after_tools_base_url=url), answer_mode="template").after_tools_llm is None
    assert real_deps(Settings(after_tools="loop"), answer_mode="template").after_tools_llm is None
    same = Settings(after_tools="loop", after_tools_base_url=Settings().model_base_url)
    assert real_deps(same, answer_mode="template").after_tools_llm is None
    assert real_deps("http://127.0.0.1:9/v1", answer_mode="template", after_tools="loop",
                     after_tools_url=url).after_tools_llm is not None


def test_abtest_loop_router_runs_only_selected_routers():
    from glople_agent.eval import abtest

    out = io.StringIO()
    code = abtest.main(["--fake", "--router", "first=http://127.0.0.1:1/v1", "--router", "after=http://127.0.0.1:2/v1",
                        "--run-routers", "first", "--loop-router", "after", "--modes", "loop", "--limit", "3",
                        "--bootstrap", "10"], out=out)
    report = out.getvalue()
    assert code == 0 and "loop 모드의 결과 뒤 요청: 라우터 after 서버" in report
    assert "## 답변: 라우터 first" in report and "## 답변: 라우터 after" not in report
    assert "`eval_sets/scenarios_m4.jsonl`" in report and "/Users/" not in report   # 보고서에 절대 경로 없음
    bad = io.StringIO()
    assert abtest.main(["--fake", "--router", "first=x", "--loop-router", "nope", "--modes", "loop"], out=bad) == 2
    assert abtest.main(["--fake", "--router", "first=x", "--run-routers", "nope", "--modes", "loop"], out=bad) == 2

"""가짜 채팅 모델과 스크립트 세 가지(design.md 6.6, 14.1의 9번)."""
import json
import operator
from typing import Annotated, TypedDict

import openai
import pydantic
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableBinding
from langchain_openai.chat_models.base import _convert_dict_to_message
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from glople_agent import contract
from glople_agent.llm import fake
from glople_agent.llm.fake import (
    DEFAULT_QUIRKS,
    QUIRK_KINDS,
    RULE_DEMOS,
    OracleCase,
    OracleMiss,
    OracleScript,
    QuirkScript,
    RequestShapeError,
    RuleScript,
    ScriptedChatModel,
    ScriptExhausted,
    fake_answer_model,
    fake_router_model,
    from_server,
    load_oracle_cases,
    sequential_ids,
    server_tool_call,
    text_message,
    tool_call_message,
)
from glople_agent.llm.normalize import normalize_router


def router_request(text: str) -> list:
    return [SystemMessage(contract.SYSTEM_PROMPT), HumanMessage(text)]


def calls_of(msg: AIMessage) -> list[tuple[str, dict]]:
    return [(c["name"], c["args"]) for c in msg.tool_calls]


# --- ScriptedChatModel --------------------------------------------------------

def test_bind_tools_passes_tool_dicts_and_records_calls():
    model = fake_router_model(RuleScript(new_id=sequential_ids()))
    bound = model.bind_tools(contract.TOOLS)
    assert isinstance(bound, RunnableBinding)
    out = bound.invoke(router_request("바다 보이는 곳 추천해줘"), max_tokens=768)
    assert calls_of(out) == [("recommend_by_keywords", {"keywords": ["바다"]})]
    assert out.tool_calls[0]["id"] == "call_1"
    messages, kwargs = model.calls[-1]
    assert [type(m).__name__ for m in messages] == ["SystemMessage", "HumanMessage"]
    assert kwargs["tools"] is contract.TOOLS  # 변환 없이 파일 JSON 그대로
    assert kwargs["max_tokens"] == 768
    assert "tool_choice" not in kwargs and "parallel_tool_calls" not in kwargs


def test_fake_bind_tools_sends_what_chat_openai_sends():
    # 가짜는 dict를 변환하지 않는다 → 실제 ChatOpenAI도 이 dict를 바이트 순서 그대로 보내는지(네트워크 없음)
    from langchain_openai import ChatOpenAI
    real = ChatOpenAI(model="default_model", api_key="local-not-used", base_url="http://scripted.invalid/v1",
                      max_retries=0, disable_streaming=True, use_responses_api=False)
    bound = real.bind_tools(contract.TOOLS)
    payload = real._get_request_payload(router_request("x"), **bound.kwargs, max_tokens=768)
    assert json.dumps(payload["tools"], ensure_ascii=False) == json.dumps(contract.TOOLS, ensure_ascii=False)
    assert "tool_choice" not in payload and "parallel_tool_calls" not in payload
    assert payload["max_completion_tokens"] == 768  # mlx 서버는 둘 다 받고 이쪽을 먼저 봄
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    fake_bound = fake_router_model(lambda m, k: text_message("x")).bind_tools(contract.TOOLS)
    assert fake_bound.kwargs["tools"] == payload["tools"]


def test_invoke_sets_finish_reason_and_run_id():
    model = fake_router_model(lambda m, k: text_message("안녕하세요!"))
    out = model.invoke([HumanMessage("안녕")])
    assert out.content == "안녕하세요!"
    assert out.response_metadata["finish_reason"] == "stop"
    assert out.response_metadata["model_provider"] == "scripted"
    assert out.id and out.id.startswith("lc_run-")  # ChatOpenAI와 같이 실행 id가 붙음


def test_each_call_gets_a_fresh_message():
    shared = text_message("같은 객체")
    model = fake_router_model(lambda m, k: shared)
    a, b = model.invoke("x"), model.invoke("y")
    assert a is not b and a.id != b.id
    assert shared.id is None  # 스크립트의 원본은 고치지 않음
    assert len(model.calls) == 2


def test_script_must_return_ai_message():
    model = fake_router_model(lambda m, k: "문자열")
    with pytest.raises(TypeError):
        model.invoke("x")


def test_errors_are_recorded_then_raised():
    model = fake_router_model(QuirkScript(["connection_error"]))
    with pytest.raises(openai.APIConnectionError):
        model.invoke(router_request("바다"))
    assert len(model.calls) == 1  # 요청은 남김(요청 모양 검사)


def test_stream_is_character_by_character():
    model = fake_answer_model(lambda m, k: text_message("바나힐 후기"))
    chunks = list(model.stream("x"))
    texts = [c.content for c in chunks if c.content]
    assert texts == list("바나힐 후기")
    merged = chunks[0]
    for c in chunks[1:]:
        merged += c
    assert merged.content == "바나힐 후기"
    assert merged.response_metadata["finish_reason"] == "stop"


def test_stream_keeps_tool_calls_like_server():
    script = lambda m, k: tool_call_message([("get_destination_detail", {"name": "바나힐"}),  # noqa: E731
                                             ("search_reviews", {"query": "바나힐"})], new_id=sequential_ids())
    chunks = list(fake_answer_model(script).stream("x"))
    merged = chunks[0]
    for c in chunks[1:]:
        merged += c
    assert isinstance(merged, AIMessageChunk)
    assert merged.content == "\n"
    assert calls_of(merged) == [("get_destination_detail", {"name": "바나힐"}), ("search_reviews", {"query": "바나힐"})]
    assert [c["id"] for c in merged.tool_calls] == ["call_1", "call_2"]
    assert merged.response_metadata["finish_reason"] == "tool_calls"


def test_router_fake_does_not_stream():
    model = fake_router_model(lambda m, k: text_message("안녕하세요"))
    assert model.disable_streaming is True and "nostream" in model.tags
    chunks = list(model.stream("x"))
    assert len(chunks) == 1 and chunks[0].content == "안녕하세요"


class _S(TypedDict):
    messages: Annotated[list, add_messages]
    seen: Annotated[list, operator.add]


@pytest.mark.parametrize("make", [fake_router_model, fake_answer_model], ids=["router", "answer"])
def test_tool_calls_survive_langgraph_messages_stream(make):
    model = make(lambda m, k: tool_call_message([("get_weather", {"region": "부산"}),
                                                 ("search_reviews", {"query": "부산"})], new_id=sequential_ids()))

    def node(state):
        return {"messages": [model.invoke(state["messages"])]}

    g = StateGraph(_S)
    g.add_node("route", node)
    g.add_edge(START, "route")
    g.add_edge("route", END)
    app = g.compile()
    streamed, final = [], None
    for mode, data in app.stream({"messages": [HumanMessage("부산")]}, stream_mode=["messages", "values"]):
        if mode == "messages":
            streamed.append(data)
        else:
            final = data
    last = final["messages"][-1]
    assert calls_of(last) == [("get_weather", {"region": "부산"}), ("search_reviews", {"query": "부산"})]
    assert {meta["langgraph_node"] for _, meta in streamed} == {"route"}
    if make is fake_router_model:
        # nostream은 토큰만 막는다. 노드가 돌려준 메시지는 노드가 끝날 때 통째로 한 번 나온다(docs/api-notes.md)
        assert len(streamed) == 1 and not isinstance(streamed[0][0], AIMessageChunk)
        assert calls_of(streamed[0][0]) == calls_of(last)
    else:
        assert all(isinstance(m, AIMessageChunk) for m, _ in streamed)
        assert "".join(m.content for m, _ in streamed) == "\n"


# --- 서버 응답 변환 = langchain-openai 변환 ----------------------------------------

_SERVER_CASES = {
    "single": (None, [server_tool_call("get_destination_detail", {"name": "바나힐"}, id="a")]),
    "parallel": ("\n", [server_tool_call("get_destination_detail", {"name": "바나힐"}, id="a"),
                        server_tool_call("search_reviews", {"query": "바나힐"}, id="b")]),
    "dropped": (None, None),
    "invalid": (None, [server_tool_call("search_reviews", '{"query": "바나', id="c")]),
    "empty_args": (None, [server_tool_call("get_weather", "", id="d")]),
    "text": ("어느 지역 날씨를 알려드릴까요?", None),
}


@pytest.mark.parametrize("case", list(_SERVER_CASES))
def test_from_server_matches_langchain_openai(case):
    content, tool_calls = _SERVER_CASES[case]
    ours = from_server(content, tool_calls)
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    theirs = _convert_dict_to_message(message)
    assert ours.content == theirs.content
    assert ours.tool_calls == theirs.tool_calls
    assert ours.invalid_tool_calls == theirs.invalid_tool_calls


def test_from_server_args_not_object_raises_like_langchain_openai():
    raw = [server_tool_call("get_weather", '"{부산}"', id="e")]
    with pytest.raises(pydantic.ValidationError):
        _convert_dict_to_message({"role": "assistant", "content": None, "tool_calls": raw})
    with pytest.raises(pydantic.ValidationError):
        from_server(None, raw)


def test_parallel_content_has_newline_between_blocks():
    two = tool_call_message([("a", {}), ("b", {})])
    three = tool_call_message([("a", {}), ("b", {}), ("c", {})])
    one = tool_call_message([("a", {})])
    assert (one.content, two.content, three.content) == ("", "\n", "\n\n")
    ids = [c["id"] for c in three.tool_calls]
    assert len(set(ids)) == 3 and all(len(i) == 36 for i in ids)  # 서버처럼 uuid4


# --- RuleScript ----------------------------------------------------------------

# 데모 문장 → (첫 응답 kind, 호출) — path는 그래프에서 이어질 길(RULE_DEMOS)
_RULE_EXPECTED = {
    "바나힐 정보랑 사람들 리뷰 같이 알려줘":
        ("tool", [("get_destination_detail", {"name": "바나힐"}), ("search_reviews", {"query": "바나힐"})]),
    "나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임":
        ("tool", [("recommend_similar_traveler_routes", {"gender": "남성", "mbti": "ENTP"})]),
    "안녕": ("text", []),
    "제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘":
        ("tool", [("create_travel_plan", {"region": "제주", "days": 3, "places": ["성산일출봉", "우도"]})]),
    "깨진 응답 흉내: 부산 날씨 알려줘": ("dropped", []),
    "바다 보이는 곳 추천해줘": ("tool", [("recommend_by_keywords", {"keywords": ["바다"]})]),
    "부산 이번 주말 날씨 어때?": ("tool", [("get_weather", {"region": "부산", "when": "이번 주말"})]),
    "고마워": ("text", []),
    "(잘림) 강릉 2박 3일 경포대랑 안목해변 일정 저장해줘": ("truncated", []),
}


def test_rule_demos_cover_required_paths():
    assert len(RULE_DEMOS) >= 5
    assert {"tool", "parallel", "clarify", "direct", "plan", "fallback"} <= {d.path for d in RULE_DEMOS}
    assert set(_RULE_EXPECTED) == {d.text for d in RULE_DEMOS}


@pytest.mark.parametrize("demo", RULE_DEMOS, ids=[d.path + ":" + d.text[:8] for d in RULE_DEMOS])
def test_rule_demo_first_answer(demo):
    kind, calls = _RULE_EXPECTED[demo.text]
    out = normalize_router(RuleScript()(router_request(demo.text), {"tools": contract.TOOLS}))
    assert out.kind == kind
    assert [(c["name"], c["args"]) for c in out.tool_calls] == calls


def test_rule_parallel_demo_has_server_newline():
    msg = RuleScript()(router_request("바나힐 정보랑 사람들 리뷰 같이 알려줘"), {})
    assert msg.content == "\n" and len(msg.tool_calls) == 2


def test_rule_direct_answers_are_training_texts():
    texts = fake.generator_literals()
    rs = RuleScript()
    assert rs(router_request("안녕"), {}).content == texts["GREETING_ANSWER"]
    assert rs(router_request("고마워"), {}).content == texts["THANKS_ANSWER"]
    assert rs(router_request("주식 종목 추천해줘"), {}).content == texts["DECLINE_ANSWER"]
    assert rs(router_request("넌 누구야?"), {}).content == texts["ABILITY_ANSWER"]
    assert rs(router_request("음"), {}).content == texts["OPEN_CLARIFY_ANSWER"]  # 맞는 규칙 없음


@pytest.mark.needs_training_data("tool_calling_v2/train.jsonl")
def test_rule_texts_are_canned_texts():
    canned = set(contract.canned_texts())
    rs = RuleScript()
    for text in ["안녕", "고마워", "주식 종목 추천해줘", "넌 누구야?", "음", "맛집 알려줘", "거기 후기 알려줘",
                 "날씨 어때", "울진 가는데 일정으로 남겨두고 싶어요", "일정 저장해줘"]:
        out = rs(router_request(text), {})
        assert out.tool_calls == [] and out.content in canned, text


def test_rule_broken_quirk_fails_again_on_retry():
    rs = RuleScript()
    req = router_request("깨진 응답 흉내: 부산 날씨 알려줘")
    assert normalize_router(rs(req, {})).kind == "dropped"
    assert normalize_router(rs(req, {"temperature": 0.2})).kind == "dropped"  # → fallback


def test_rule_truncated_quirk_recovers_with_more_tokens():
    rs = RuleScript()
    req = router_request("(잘림) 강릉 2박 3일 경포대랑 안목해변 일정 저장해줘")
    first = normalize_router(rs(req, {}))
    assert first.kind == "truncated"
    second = normalize_router(rs(req, first.retry_overrides))
    assert second.kind == "tool"
    assert second.tool_calls[0]["args"] == {"region": "강릉", "days": 3, "places": ["경포대", "안목해변"]}


def test_rule_connection_markers_raise_openai_errors():
    rs = RuleScript()
    with pytest.raises(openai.APIConnectionError) as info:
        rs(router_request("(끊김) 바다 추천해줘"), {})
    assert not isinstance(info.value, openai.APITimeoutError)
    with pytest.raises(openai.APITimeoutError):
        rs(router_request("(지연) 바다 추천해줘"), {})


@pytest.mark.parametrize("text, expected", [
    ("울진 가는데 일정으로 남겨두고 싶어요", "며칠 일정으로, 어떤 장소를 넣어서 저장할까요?"),
    ("일정 저장해줘", "어느 지역으로 며칠 동안, 어떤 장소를 넣어서 저장할까요?"),
    ("맛집 알려줘", "어느 지역의 맛집을 찾아드릴까요?"),
    ("거기 후기 알려줘", "어느 장소나 지역의 후기를 찾아드릴까요?"),
])
def test_rule_clarify_texts(text, expected):
    assert RuleScript()(router_request(text), {}).content == expected


@pytest.mark.parametrize("text, calls", [
    ("부산 맛집 알려줘", [("search_destinations", {"region": "부산", "category": "음식점"})]),
    ("박물관 추천해줘", [("recommend_by_keywords", {"keywords": ["박물관"]})]),
    ("제주 여행 후기 보여줘", [("search_reviews", {"query": "제주"})]),
    ("도쿄 글로플러 찾아줘", [("search_gloplers", {"hashtag": "도쿄"})]),
    ("부석사 어떤 곳이야", [("get_destination_detail", {"name": "부석사"})]),
    ("28살 여자 인프피인데 나랑 비슷한 사람 루트",
     [("recommend_similar_traveler_routes", {"age": 28, "gender": "여성", "mbti": "INFP"})]),
    ("'졸업 여행' 이름으로 부산 1박 2일 해운대랑 광안리 저장해줘",
     [("create_travel_plan", {"region": "부산", "days": 2, "places": ["해운대", "광안리"], "title": "졸업 여행"})]),
])
def test_rule_generic_tools(text, calls):
    out = normalize_router(RuleScript()(router_request(text), {}))
    assert [(c["name"], c["args"]) for c in out.tool_calls] == calls


def test_rule_plan_title_in_quotes():
    out = normalize_router(RuleScript()(router_request("부산 1박 2일 해운대랑 광안리 '졸업 여행'으로 저장해줘"), {}))
    assert out.tool_calls[0]["args"] == {"region": "부산", "days": 2, "places": ["해운대", "광안리"],
                                         "title": "졸업 여행"}


def test_rule_needs_human_message():
    with pytest.raises(ValueError):
        RuleScript()([SystemMessage(contract.SYSTEM_PROMPT)], {})


# --- OracleScript --------------------------------------------------------------

def _assert_matches_gold(case: OracleCase, msg: AIMessage) -> None:
    out = normalize_router(msg)
    if case.is_call:
        n = len(case.expected_calls)
        assert msg.content == "\n" * (n - 1), case.utterance  # 병렬이면 서버처럼 줄바꿈
        assert out.kind == "tool"
        assert [(c["name"], c["args"]) for c in out.tool_calls] == case.expected_calls
        assert all(c["id"] for c in out.tool_calls)
        assert msg.response_metadata["finish_reason"] == "tool_calls"
    else:
        assert out.kind == "text" and out.text == case.answer["content"]
        assert msg.response_metadata["finish_reason"] == "stop"


def test_oracle_finds_all_human_style():
    # human_style.jsonl은 커밋돼 있어 데이터 없이도 돈다
    script = OracleScript([contract.HUMAN_STYLE_PATH], check_request=True)
    assert len(script) == 90
    for case in script.cases:
        _assert_matches_gold(case, script(router_request(case.utterance), {"tools": contract.TOOLS}))


@pytest.mark.needs_training_data("tool_calling_v2/test.jsonl")
def test_oracle_finds_all_390():
    script = OracleScript(check_request=True)
    assert len(script) == 390
    assert {c.source for c in script.cases} == {"test.jsonl", "human_style.jsonl"}
    found = 0
    for case in script.cases:
        msg = script(router_request(case.utterance), {"tools": contract.TOOLS})
        _assert_matches_gold(case, msg)
        found += 1
    assert found == 390
    parallel = [c for c in script.cases if len(c.expected_calls) > 1]
    assert len(parallel) == 29  # test 20 + human_style 9


@pytest.mark.needs_training_data("tool_calling_v2/test.jsonl")
def test_oracle_through_model_invoke():
    script = OracleScript()
    model = fake_router_model(script)
    case = next(c for c in script.cases if len(c.expected_calls) == 2)
    out = model.bind_tools(contract.TOOLS).invoke(router_request(case.utterance))
    assert out.content == "\n"
    assert normalize_router(out).message.content == ""


def test_oracle_miss_and_fallback():
    strict = OracleScript([contract.HUMAN_STYLE_PATH])
    with pytest.raises(OracleMiss):
        strict(router_request("정답 세트에 없는 문장"), {})
    lenient = OracleScript([contract.HUMAN_STYLE_PATH], fallback=RuleScript())
    out = lenient(router_request("안녕"), {})
    assert out.content == fake.generator_literals()["GREETING_ANSWER"]


def test_oracle_finds_by_cleaned_text():
    script = OracleScript([contract.HUMAN_STYLE_PATH])
    case = script.cases[0]
    assert script.find("  " + case.utterance + "<|im_end|>") is case  # route가 clean_text한 문장


def test_oracle_check_request_rejects_out_of_distribution():
    script = OracleScript([contract.HUMAN_STYLE_PATH], check_request=True)
    utt = script.cases[0].utterance
    good = {"tools": contract.TOOLS}
    with pytest.raises(RequestShapeError):  # 이전 대화가 섞임
        script([SystemMessage(contract.SYSTEM_PROMPT), HumanMessage("안녕"), AIMessage("안녕하세요"),
                HumanMessage(utt)], good)
    with pytest.raises(RequestShapeError):  # system이 다름
        script([SystemMessage("다른 프롬프트"), HumanMessage(utt)], good)
    with pytest.raises(RequestShapeError):  # 도구 순서가 다름
        script(router_request(utt), {"tools": list(reversed(contract.TOOLS))})
    with pytest.raises(RequestShapeError):  # 학습에 없던 tool_choice
        script(router_request(utt), {**good, "tool_choice": "auto"})
    script(router_request(utt), good)


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _record(utt, answer):
    return {"messages": [{"role": "system", "content": contract.SYSTEM_PROMPT}, {"role": "user", "content": utt},
                         {"role": "assistant", **answer}], "tools": contract.TOOLS, "task": "direct"}


def test_load_oracle_cases_rejects_conflicting_answers(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _write_jsonl(a, [_record("안녕", {"content": "하나"})])
    _write_jsonl(b, [_record("안녕", {"content": "하나"}), _record("고마워", {"content": "둘"})])
    assert [c.utterance for c in load_oracle_cases([a, b])] == ["안녕", "고마워"]  # 같은 정답 중복은 하나로
    _write_jsonl(b, [_record("안녕", {"content": "다른 정답"})])
    with pytest.raises(ValueError):
        load_oracle_cases([a, b])
    with pytest.raises(FileNotFoundError):
        load_oracle_cases([tmp_path / "없음.jsonl"])
    assert load_oracle_cases([a, tmp_path / "없음.jsonl"], skip_missing=True)[0].utterance == "안녕"


# --- QuirkScript ---------------------------------------------------------------

_QUIRK_EXPECTED = {
    "parallel_newline": "tool",
    "dropped": "dropped",
    "truncated": "truncated",
    "invalid": "dropped",
    "invalid_partial": "tool",
    "invented_arg": "tool",
    "unparsed": "tool",
    "unparsed_broken": "unparsed",
    "empty": "dropped",
    "args_not_object": pydantic.ValidationError,
    "connection_error": openai.APIConnectionError,
    "timeout": openai.APITimeoutError,
    "server_error": openai.InternalServerError,
}


def test_quirk_table_covers_all_kinds():
    assert set(_QUIRK_EXPECTED) == set(QUIRK_KINDS)
    assert set(DEFAULT_QUIRKS) <= set(QUIRK_KINDS)


@pytest.mark.parametrize("kind", QUIRK_KINDS)
def test_each_quirk(kind):
    expected = _QUIRK_EXPECTED[kind]
    model = fake_router_model(QuirkScript([kind]))
    if isinstance(expected, type):
        with pytest.raises(expected) as info:
            model.invoke(router_request("아무 말"))
        out = normalize_router(info.value)
        assert out.kind == ("dropped" if kind == "args_not_object" else "error")
    else:
        assert normalize_router(model.invoke(router_request("아무 말"))).kind == expected


def test_quirk_details():
    parallel = QuirkScript(["parallel_newline"])(router_request("x"), {})
    assert parallel.content == "\n" and len(parallel.tool_calls) == 2
    invented = normalize_router(QuirkScript(["invented_arg"])(router_request("ENTP 남자임"), {}))
    assert invented.tool_calls[0]["args"]["age"] == 30  # 발화에 없는 나이(근거 검사는 M1)
    partial = normalize_router(QuirkScript(["invalid_partial"])(router_request("x"), {}))
    assert [c["name"] for c in partial.tool_calls] == ["get_destination_detail"]
    assert partial.detail["invalid"][0]["name"] == "search_reviews"


def test_default_quirk_sequence_in_order():
    script = QuirkScript()
    kinds = []
    for _ in DEFAULT_QUIRKS:
        try:
            kinds.append(normalize_router(script(router_request("x"), {})).kind)
        except openai.APIConnectionError as e:
            kinds.append(normalize_router(e).kind)
    assert kinds == ["tool", "dropped", "truncated", "dropped", "tool", "error"]
    assert script.remaining == 0
    with pytest.raises(ScriptExhausted):
        script(router_request("x"), {})


def test_quirk_then_delegates_after_sequence():
    script = QuirkScript(["dropped"], then=RuleScript())
    model = fake_router_model(script)
    bound = model.bind_tools(contract.TOOLS)
    first = normalize_router(bound.invoke(router_request("바다 추천해줘")))
    assert first.kind == "dropped"
    second = normalize_router(bound.invoke(router_request("바다 추천해줘"), **first.retry_overrides))
    assert second.kind == "tool" and second.tool_calls[0]["args"] == {"keywords": ["바다"]}
    assert model.calls[1][1]["temperature"] == 0.2


def test_quirk_accepts_messages_errors_and_callables():
    custom = text_message("직접 만든 답")
    script = QuirkScript([custom, ValueError("직접 던짐"), lambda m, k: text_message("함수")])
    assert script([HumanMessage("x")], {}).content == "직접 만든 답"
    with pytest.raises(ValueError):
        script([HumanMessage("x")], {})
    assert script([HumanMessage("x")], {}).content == "함수"
    with pytest.raises(ValueError):
        QuirkScript(["없는_quirk"])


def test_model_is_basechatmodel_subclass():
    from langchain_core.language_models import BaseChatModel
    assert issubclass(ScriptedChatModel, BaseChatModel)
    assert ScriptedChatModel(script=lambda m, k: text_message("x"))._llm_type == "scripted"

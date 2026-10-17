"""도구: 인자 모델, 결과 봉투(1,000자), Type3 원문 격리, 가짜 백엔드, StructuredTool 등록, ToolNode 오류 처리."""
import json
import typing
from datetime import date

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

from glople_agent import contract
from glople_agent.state import Card
from glople_agent.tools import args as tool_args
from glople_agent.tools.envelope import (MAX_CONTENT_CHARS, ToolBackendError, bind_card, card_from_tool_message,
                                         make_result, tool_error_to_json)
from glople_agent.tools.fake_backend import FakeBackend, all_rows, text_policy
from glople_agent.tools.registry import build_read_tools

TYPE3_SENTINEL = "TYPE3-SENTINEL-5d1c8e2a"
# 카드에만 있어야 하는 설명문 표시(제3유형·유형 빈 값·제1유형·위키백과 도입부·출처 없는 기존 행)
CARD_ONLY_MARKERS = (TYPE3_SENTINEL, "TYPE1-DESC", "WIKI-DESC", "LEGACY-TEXT")
TYPE3_ATTRIBUTION = "출처: 한국관광공사 TourAPI · 공공누리 제3유형(출처표시·변경금지)"
TODAY = date(2026, 10, 17)


def _tool_def(name: str) -> dict:
    return next(t["function"] for t in contract.TOOLS if t["function"]["name"] == name)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend(today=TODAY)


@pytest.fixture
def tools(backend) -> dict:
    return {t.name: t for t in build_read_tools(backend)}


def _call(tool, args: dict, call_id: str = "call_1") -> ToolMessage:
    return tool.invoke({"type": "tool_call", "id": call_id, "name": tool.name, "args": args})


# --- args.py ------------------------------------------------------------------

def test_args_models_cover_all_tools_in_contract_order():
    assert tuple(tool_args.ARGS_MODELS) == contract.TOOL_NAMES


@pytest.mark.parametrize("name", contract.TOOL_NAMES)
def test_args_match_json_schema(name):
    params = _tool_def(name)["parameters"]
    model = tool_args.ARGS_MODELS[name]
    fields = model.model_fields
    assert list(fields) == list(params["properties"])  # 이름·순서
    assert {f for f, info in fields.items() if info.is_required()} == set(params["required"])
    assert model.model_config.get("extra") == "forbid"
    for field, spec in params["properties"].items():
        # enum은 args.py에 직접 적은 Literal과 JSON 값이 순서까지 같다
        assert tool_args.enum_values(model, field) == (tuple(spec["enum"]) if "enum" in spec else None)
        assert fields[field].description == spec.get("description")


def test_enum_values_against_json_explicitly():
    assert typing.get_args(tool_args.Category) == tuple(
        _tool_def("search_destinations")["parameters"]["properties"]["category"]["enum"])
    assert typing.get_args(tool_args.Gender) == tuple(
        _tool_def("recommend_similar_traveler_routes")["parameters"]["properties"]["gender"]["enum"])
    assert typing.get_args(tool_args.When) == tuple(
        _tool_def("get_weather")["parameters"]["properties"]["when"]["enum"])


def test_args_accept_training_style_values():
    a = tool_args.validate_args("search_destinations", {"region": " 강릉 ", "category": "숙박", "limit": 3})
    assert (a.region, a.category, a.limit, a.query) == ("강릉", "숙박", 3, None)
    r = tool_args.validate_args("recommend_similar_traveler_routes", {"age": "28", "gender": "남성", "mbti": "entp"})
    assert (r.age, r.mbti) == (28, "ENTP")
    p = tool_args.validate_args("create_travel_plan", {"region": "제주", "days": 3, "places": ["성산일출봉", "우도"]})
    assert p.places == ["성산일출봉", "우도"] and p.title is None


@pytest.mark.parametrize(("name", "args", "expected"), [
    ("search_destinations", {"region": "강릉", "foo": 1}, [("foo", "extra")]),
    ("search_destinations", {"category": "숙박"}, [("region", "missing")]),
    ("search_destinations", {"region": "강릉", "category": "카페"}, [("category", "enum")]),
    ("search_destinations", {"region": "강릉", "limit": "세 곳"}, [("limit", "type")]),
    ("search_destinations", {"region": "강릉", "limit": True}, [("limit", "type")]),
    ("search_destinations", {"region": "  "}, [("region", "value")]),
    ("get_weather", {"region": "부산", "when": "모레"}, [("when", "enum")]),
    ("recommend_similar_traveler_routes", {"gender": "남성", "mbti": "ENTP"}, [("age", "missing")]),
    ("recommend_similar_traveler_routes", {"age": 500, "gender": "남성", "mbti": "ENTP"}, [("age", "value")]),
    ("recommend_similar_traveler_routes", {"age": 30, "gender": "남자", "mbti": "ENTP"}, [("gender", "enum")]),
    ("recommend_similar_traveler_routes", {"age": 30, "gender": "남성", "mbti": "엔팁"}, [("mbti", "value")]),
    ("recommend_by_keywords", {"keywords": []}, [("keywords", "value")]),
    ("recommend_by_keywords", {"keywords": "바다"}, [("keywords", "type")]),
    ("create_travel_plan", {"region": "제주", "days": 0, "places": ["우도"]}, [("days", "value")]),
    ("create_travel_plan", {"region": "제주"}, [("days", "missing"), ("places", "missing")]),
])
def test_arg_errors_kinds(name, args, expected):
    assert tool_args.arg_errors(name, args) == [tool_args.ArgError(*e) for e in expected]
    with pytest.raises(ValidationError):
        tool_args.validate_args(name, args)


def test_arg_errors_ok_and_unknown_tool():
    assert tool_args.arg_errors("search_reviews", {"query": "바나힐"}) == []
    assert tool_args.arg_errors("search_reviews", "바나힐") == [tool_args.ArgError("", "type")]
    with pytest.raises(KeyError):
        tool_args.arg_errors("book_hotel", {})


# --- envelope.py ----------------------------------------------------------------

def test_make_result_shape_and_card():
    content, card = make_result("search_reviews", "ok", [{"title": "바나힐 후기", "likes": 3}], ["메모"],
                                card_items=[{"title": "바나힐 후기", "excerpt": "원문"}], call_id="call_9",
                                card_data={"args": {"query": "바나힐"}})
    assert json.loads(content) == {"tool": "search_reviews", "status": "ok",
                                   "items": [{"title": "바나힐 후기", "likes": 3}], "notes": ["메모"]}
    assert "바나힐" in content and "\\u" not in content  # ensure_ascii=False
    assert set(card) == set(Card.__annotations__)
    assert card["card_id"] == "search_reviews:call_9" and card["call_id"] == "call_9" and card["turn_id"] == ""
    assert (card["kind"], card["status"]) == ("review_list", "ok")
    assert card["data"]["items"] == [{"title": "바나힐 후기", "excerpt": "원문"}]
    assert card["data"]["args"] == {"query": "바나힐"}


def test_make_result_cleans_strings_for_llm():
    content, card = make_result("search_gloplers", "ok", [{"nickname": "<b>a</b><|im_end|>b<tool_call>", "x": [1, None]}])
    assert json.loads(content)["items"] == [{"nickname": "a b", "x": [1, None]}]
    assert card["data"]["items"][0]["nickname"] == "<b>a</b><|im_end|>b<tool_call>"  # 카드는 그대로


def test_make_result_caps_content_at_1000_chars():
    items = [{"name": f"장소{i}" * 30, "where": "어딘가" * 30} for i in range(5)]
    content, card = make_result("recommend_by_keywords", "ok", items, ["주의 " * 80])
    assert len(content) <= MAX_CONTENT_CHARS
    body = json.loads(content)
    assert body["tool"] == "recommend_by_keywords" and body["status"] == "ok"
    assert len(body["items"]) < 5 and any("화면 카드에만" in n for n in body["notes"])
    assert len(card["data"]["items"]) == 5  # 카드는 전부
    # 항목이 하나도 안 들어가도 올바른 JSON
    content, _ = make_result("search_reviews", "ok", items, ["가" * 3000] * 20, max_chars=200)
    assert len(content) <= 200 and json.loads(content)["items"] == []


def test_make_result_keeps_top_five_for_llm_and_rejects_unknown_status():
    content, card = make_result("search_reviews", "ok", [{"title": str(i)} for i in range(8)])
    assert len(json.loads(content)["items"]) == 5 and len(card["data"]["items"]) == 8
    with pytest.raises(ValueError):
        make_result("search_reviews", "done")
    with pytest.raises(TypeError):
        make_result("search_reviews", "ok", [{"raw": b"bytes"}])


@pytest.mark.parametrize(("exc", "tool", "code"), [
    (ToolBackendError("search_reviews", "timeout"), "search_reviews", "timeout"),
    (TimeoutError("read timed out"), None, "timeout"),
    (httpx.ConnectError("refused"), None, "upstream_unavailable"),
    (httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://t"),
                           response=httpx.Response(400)), None, "bad_request"),
    (httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://t"),
                           response=httpx.Response(503)), None, "upstream_unavailable"),
    (RuntimeError("Bearer SECRET-xyz in message"), None, "upstream_unavailable"),
])
def test_tool_error_to_json(exc, tool, code):
    content = tool_error_to_json(exc)
    body = json.loads(content)
    assert set(body) == {"tool", "status", "error", "message_ko"}
    assert (body["tool"], body["status"], body["error"]) == (tool, "error", code)
    assert body["message_ko"] and "SECRET" not in content and "refused" not in content


# --- registry.py ------------------------------------------------------------------

def test_registry_builds_seven_read_tools(tools):
    built = build_read_tools(FakeBackend(today=TODAY))
    assert tuple(t.name for t in built) == contract.READ_TOOL_NAMES
    assert len(built) == 7 and contract.PLAN_TOOL_NAME not in tools
    for t in built:
        assert t.description == _tool_def(t.name)["description"]
        assert t.args_schema is tool_args.ARGS_MODELS[t.name]
        assert t.response_format == "content_and_artifact"
        # 모델용 스키마에 tool_call_id(주입 값)는 안 보인다
        assert "tool_call_id" not in t.tool_call_schema.model_json_schema()["properties"]


def test_registry_rejects_backend_without_methods():
    with pytest.raises(TypeError):
        build_read_tools(object())


def test_tool_returns_content_and_artifact(tools):
    msg = _call(tools["search_reviews"], {"query": "바나힐"}, "call_r")
    assert isinstance(msg, ToolMessage) and msg.tool_call_id == "call_r" and msg.name == "search_reviews"
    body = json.loads(msg.content)
    assert body["status"] == "ok" and len(body["items"]) == 3
    assert msg.artifact["card_id"] == "search_reviews:call_r" and msg.artifact["kind"] == "review_list"
    # 좋아요↓, 같은 좋아요면 최신순. 주입 문자열·HTML은 LLM 쪽에 없음
    assert [i["likes"] for i in body["items"]] == [12, 12, 3]
    assert body["items"][0]["title"].startswith("다낭 3박 4일")
    for bad in ("<|im_end|>", "<|im_start|>", "<b>", "<script>", "<tool_call>", "excerpt", "/board/"):
        assert bad not in msg.content
    card_items = msg.artifact["data"]["items"]
    assert card_items[0]["link"] == "/board/47" and "excerpt" in card_items[0]


def test_tool_requires_tool_call_shape(tools):
    with pytest.raises(ValueError):  # InjectedToolCallId: 맨 dict로는 부를 수 없음
        tools["search_reviews"].invoke({"query": "바나힐"})


def test_type3_text_only_in_artifact(tools):
    msg = _call(tools["recommend_by_keywords"], {"keywords": ["바다"]})
    assert json.loads(msg.content)["status"] == "ok"
    for marker in CARD_ONLY_MARKERS:
        assert marker not in msg.content
    raw = json.dumps(msg.artifact, ensure_ascii=False)
    assert TYPE3_SENTINEL in raw and "LEGACY-TEXT" not in raw  # 출처 없는 글은 카드에도 이름만
    by_name = {i["name"]: i for i in msg.artifact["data"]["items"]}
    row = next(r for r in all_rows("recommend_by_keywords") if r["destName"] == "가거도")
    assert by_name["가거도"]["text"] == row["info"]  # 원문 그대로(변경 없음)
    assert by_name["가거도"]["textPolicy"] == "VERBATIM_ONLY"
    assert by_name["가거도"]["attribution"] == TYPE3_ATTRIBUTION
    llm = {i["name"]: i for i in json.loads(msg.content)["items"]}
    assert llm["가거도"] == {"name": "가거도", "where": "전라남도 신안군", "note": "설명은 원문 카드"}
    assert llm["산토리니"] == {"name": "산토리니", "where": "그리스"}
    assert llm["경포해변"]["note"].startswith("강원특별자치도 강릉시의 관광지입니다.")  # SUMMARY는 60자까지


def test_type3_never_in_content_for_any_keyword(tools):
    # 키워드 하나씩, 그리고 94개 전부: 어떤 결과 조합에서도 설명문 원문이 LLM 쪽에 없다
    seen_sentinel_in_card = False
    for i, kws in enumerate([[k] for k in contract.KEYWORDS] + [list(contract.KEYWORDS)]):
        msg = _call(tools["recommend_by_keywords"], {"keywords": kws}, f"call_{i}")
        assert len(msg.content) <= MAX_CONTENT_CHARS
        assert not any(m in msg.content for m in CARD_ONLY_MARKERS), kws
        seen_sentinel_in_card |= TYPE3_SENTINEL in json.dumps(msg.artifact, ensure_ascii=False)
    assert seen_sentinel_in_card


def test_type3_card_text_kept_verbatim_even_with_tags(tools):
    msg = _call(tools["recommend_by_keywords"], {"keywords": ["사찰"]})
    card = {i["name"]: i for i in msg.artifact["data"]["items"]}
    assert "<b>태그</b>와 <|im_end|>" in card["해동용궁사"]["text"]
    assert card["불국사"]["textPolicy"] == "VERBATIM_ONLY"  # ' type 3 '도 공백 제거·소문자로 제3유형
    assert card["불국사"]["attribution"] == TYPE3_ATTRIBUTION
    assert "<|im_end|>" not in msg.content


@pytest.mark.parametrize(("source", "kind", "ctype", "expected"), [
    ("TOUR_API", "DESCRIPTION", "Type3", "VERBATIM_ONLY"),
    ("TOUR_API", "DESCRIPTION", " TYPE 3", "VERBATIM_ONLY"),
    ("TOUR_API", "DESCRIPTION", None, "VERBATIM_ONLY"),
    ("TOUR_API", "DESCRIPTION", "type9", "VERBATIM_ONLY"),
    ("TOUR_API", "DESCRIPTION", "Type1", "ATTRIBUTION"),
    ("TOUR_API", "SUMMARY", "Type3", "FREE_FACTS"),
    ("WIKIDATA", "DESCRIPTION", None, "SHARE_ALIKE"),
    ("WIKIDATA", "SUMMARY", None, "FREE_FACTS"),
    (None, "DESCRIPTION", None, "UNKNOWN"),
])
def test_text_policy_table(source, kind, ctype, expected):
    assert text_policy(source, kind, ctype) == expected


def test_keywords_location_cap_and_unknown_keywords(tools, backend):
    msg = _call(tools["recommend_by_keywords"], {"keywords": ["야경", "맛집"]})
    body = json.loads(msg.content)
    names = [i["name"] for i in body["items"]]
    assert names == ["산토리니", "감천문화마을", "광안리해수욕장", "남산서울타워", "민락수변공원"]  # 수영구 2곳까지
    assert body["notes"] == ["목록에 없어 반영하지 못한 키워드: 맛집"]
    assert msg.artifact["data"]["appliedKeywords"] == ["야경"]
    empty = json.loads(_call(tools["recommend_by_keywords"], {"keywords": ["맛집"]}).content)
    assert empty["status"] == "empty" and empty["items"] == []
    assert backend.calls[-1] == ("recommend_by_keywords", {"keywords": ["맛집"]})


def test_reviews_empty_is_204(tools):
    body = json.loads(_call(tools["search_reviews"], {"query": "리스본"}).content)
    assert body == {"tool": "search_reviews", "status": "empty", "items": [], "notes": ["아직 리스본 후기가 없어요."]}


def test_gloplers_hashtag_country_and_domestic(tools):
    tokyo = json.loads(_call(tools["search_gloplers"], {"hashtag": "도쿄"}).content)
    assert [i["nickname"] for i in tokyo["items"]] == ["도쿄산책"]
    osaka_city = _call(tools["search_gloplers"], {"hashtag": "나고야"})  # 해시태그 없음 → 대행 국가로
    body = json.loads(osaka_city.content)
    assert body["status"] == "ok" and {i["country"] for i in body["items"]} == {"일본"}
    assert body["notes"] == ["나고야 해시태그 글로플러가 없어 일본 글로플러를 보여드려요."]
    assert "introduction" not in osaka_city.content and "<tool_call>" not in osaka_city.content
    domestic = json.loads(_call(tools["search_gloplers"], {"hashtag": "공주"}).content)
    assert domestic["status"] == "empty"
    assert domestic["notes"][-1] == "국내 글로플러는 아직 없어요(지금은 미국·이탈리아·일본)."


def test_routes_age_band_without_birthdate(tools):
    msg = _call(tools["recommend_similar_traveler_routes"], {"age": 28, "gender": "남성", "mbti": "ENTP"})
    body = json.loads(msg.content)
    assert body["items"][0] == {"ageBand": "20대", "gender": "남성", "mbti": "ENTP",
                                "destinations": ["제주", "부산", "강릉"]}
    assert all(i["gender"] == "남성" for i in body["items"])
    assert "1998" not in msg.content and "19980517" not in json.dumps(msg.artifact)
    assert msg.artifact["data"]["items"][0]["destinations"] == ["제주", "부산", "강릉", "여수"]


@pytest.mark.parametrize(("name", "args", "status"), [
    ("search_destinations", {"region": "강릉", "category": "숙박"}, "pending_feature"),
    ("get_destination_detail", {"name": "바나힐"}, "pending_feature"),
    ("get_weather", {"region": "부산", "when": "내일"}, "unsupported"),
])
def test_not_yet_connected_tools(tools, name, args, status):
    msg = _call(tools[name], args)
    body = json.loads(msg.content)
    assert body["status"] == status and body["items"] == [] and len(body["notes"]) == 1
    assert msg.artifact["status"] == status and msg.artifact["data"]["message"] == body["notes"][0]


# --- ToolNode(병렬·오류) + 체크포인트 왕복 --------------------------------------------

def _tool_graph(read_tools):
    g = StateGraph(MessagesState)
    g.add_node("tools", ToolNode(read_tools, handle_tool_errors=tool_error_to_json))
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    return g.compile(checkpointer=InMemorySaver())


def test_toolnode_parallel_order_errors_and_checkpoint():
    backend = FakeBackend(today=TODAY, errors={"search_gloplers": httpx.ConnectTimeout("slow")})
    app = _tool_graph(build_read_tools(backend))
    ai = AIMessage(content="", tool_calls=[
        {"name": "get_destination_detail", "args": {"name": "바나힐"}, "id": "c1"},
        {"name": "search_reviews", "args": {"query": "바나힐"}, "id": "c2"},
        {"name": "search_gloplers", "args": {"hashtag": "도쿄"}, "id": "c3"},              # 백엔드 예외
        {"name": "search_reviews", "args": {"query": "바나힐", "userId": "u2"}, "id": "c4"},  # 모르는 키
        {"name": "book_hotel", "args": {}, "id": "c5"},                                     # 모르는 도구
    ])
    config = {"configurable": {"thread_id": "t-tools"}}
    out = app.invoke({"messages": [ai]}, config)
    msgs = out["messages"][1:]
    assert [m.tool_call_id for m in msgs] == ["c1", "c2", "c3", "c4", "c5"]  # 호출 순서 그대로
    assert [m.status for m in msgs] == ["success", "success", "error", "error", "error"]
    err = json.loads(msgs[2].content)
    assert (err["tool"], err["error"]) == ("search_gloplers", "timeout")
    bad = json.loads(msgs[3].content)
    assert (bad["tool"], bad["error"]) == ("search_reviews", "bad_request")
    assert "u2" not in msgs[3].content and "userId" not in msgs[3].content  # 인자를 되풀이하지 않음
    cards = [card_from_tool_message(m, turn_id="turn-1") for m in msgs]
    assert [c["status"] for c in cards] == ["pending_feature", "ok", "error", "error", "error"]
    assert [c["card_id"] for c in cards][:3] == ["get_destination_detail:c1", "search_reviews:c2",
                                                 "search_gloplers:c3"]
    assert all(c["turn_id"] == "turn-1" for c in cards)
    assert cards[2]["data"]["notes"] == [err["message_ko"]] and cards[4]["kind"] == "notice"
    # artifact(카드)가 STRICT msgpack 체크포인트를 왕복한다
    saved = app.get_state(config).values["messages"]
    assert saved[2].artifact == msgs[1].artifact and saved[2].artifact["data"]["items"]


def test_bind_card_copies_and_fills_ids(tools):
    msg = _call(tools["search_reviews"], {"query": "도동항"}, "call_x")
    card = bind_card(msg.artifact, turn_id="t9")
    assert (card["turn_id"], card["call_id"], card["card_id"]) == ("t9", "call_x", "search_reviews:call_x")
    card["data"]["notes"].append("x")
    assert msg.artifact["turn_id"] == "" and "x" not in msg.artifact["data"]["notes"]  # 원본 artifact는 그대로


# --- 학습 정답 호출: 막지 않고, 끝까지 돈다 ------------------------------------------------

def _gold_calls(*paths):
    for path in paths:
        for r in contract.read_jsonl(path):
            for tc in r["messages"][-1].get("tool_calls") or []:
                yield tc["function"]["name"], json.loads(tc["function"]["arguments"])


def _assert_gold_calls_pass(calls, tools) -> int:
    n = 0
    for i, (name, call_args) in enumerate(calls):
        assert tool_args.arg_errors(name, call_args) == [], (name, call_args)  # 학습 정답은 하나도 막지 않음
        if name == contract.PLAN_TOOL_NAME:
            continue
        msg = _call(tools[name], call_args, f"gold_{i}")
        body = json.loads(msg.content)
        assert len(msg.content) <= MAX_CONTENT_CHARS and body["tool"] == name
        assert not any(m in msg.content for m in CARD_ONLY_MARKERS)
        n += 1
    return n


def test_human_style_gold_calls_pass(tools):
    assert _assert_gold_calls_pass(_gold_calls(contract.HUMAN_STYLE_PATH), tools) > 0


@pytest.mark.needs_training_data("tool_calling_v2/train.jsonl", "tool_calling_v2/valid.jsonl",
                                 "tool_calling_v2/test.jsonl")
def test_v2_gold_calls_pass(tools):
    calls = list(_gold_calls(contract.V2_TRAIN_PATH, contract.V2_DATA_DIR / "valid.jsonl", contract.V2_TEST_PATH))
    assert len(calls) > 3000
    assert _assert_gold_calls_pass(calls, tools) > 2500

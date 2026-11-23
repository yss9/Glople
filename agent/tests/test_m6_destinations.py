"""M6 S1 여행지(search_destinations · get_destination_detail): 어댑터 · 가짜 Spring 규칙 · 그래프(되묻기 두 갈래).

HTTP는 respx(오너가 2026-10-18 실제 서버로 확인한 응답 모양 그대로)와 가짜 Spring(MockTransport)뿐이다. 8080에 접속하지 않는다.
"""
import json
import zlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import respx
from langchain_core.messages import ToolMessage

from glople_agent.answer import templates as tpl
from glople_agent.llm.fake import last_human_text, text_message, tool_call_message
from glople_agent.tools.adapters import SpringBackend, destinations
from glople_agent.tools.args import GetDestinationDetailArgs, SearchDestinationsArgs
from glople_agent.tools.backend import ToolContext
from glople_agent.tools.envelope import ToolBackendError, make_result, tool_error_to_json
from glople_agent.tools.fake_backend import FakeBackend
from glople_agent.tools.fixture_spring import DESTINATION_KEYS, DETAIL_EXTRA_KEYS, region_matches, tie_hash
from glople_agent.tools.registry import run_tool
from glople_agent.tools.spring import SpringClient

BASE = "http://spring.test"
KST = ZoneInfo("Asia/Seoul")
CTX = ToolContext(now=datetime(2026, 10, 17, 12, 0, tzinfo=KST), seed=7)
NO_SEED = ToolContext(now=CTX.now, seed=0)
SENTINEL = "TYPE3-SENTINEL-live-shape-0b9e"

# 오너 확인 응답(region=부산&category=관광지&limit=3)과 같은 칸·꼴. info는 sentinel로 바꿈(실제 원문을 저장소에 두지 않음)
LIVE_ROW = {"category": "관광지", "copyrightType": "Type3", "country": "대한민국", "id": 1203,
            "imageUrl": "http://tong.visitkorea.or.kr/cms/resource/fixture/1203.jpg",
            "info": f"{SENTINEL} 경상좌수영성지 설명문 자리 표시자입니다. 변경금지 원문은 카드에만 그대로 있어야 합니다.",
            "infoKind": "DESCRIPTION", "lat": 35.1697, "license": None, "lng": 129.1153, "location": "부산광역시 수영구",
            "name": "경상좌수영성지", "source": "TOUR_API", "sourceUrl": None}
LOOKUP_ROW = {**LIVE_ROW, "id": 3405, "name": "마니산(강화)", "location": "인천 강화군", "lat": 37.613, "lng": 126.428}
DETAIL_ROW = {**LOOKUP_ROW, "address": "인천광역시 강화군 화도면", "regionCode": "28", "sigunguCode": None,
              "summary": "인천 강화군의 관광지입니다. 분류: 자연관광 > 자연경관 > 산"}


@pytest.fixture
def mock():
    with respx.mock(base_url=BASE, assert_all_called=False) as m:
        yield m


def _backend() -> SpringBackend:
    return SpringBackend(SpringClient(BASE), clock=lambda: CTX.now)


def _params(route) -> dict:
    return dict(route.calls.last.request.url.params.items())


# --- search_destinations: 요청 -----------------------------------------------------------------

def test_search_sends_resolved_region_category_limit_seed_and_no_token(mock):
    route = mock.get("/api/destinations").respond(200, json=[LIVE_ROW])
    res = _backend().search_destinations(SearchDestinationsArgs(region="부산", category="관광지", limit=3), CTX)
    assert res.status == "ok"
    # 넓은 지역(시도)은 같은 location 2곳 상한 때문에 더 받아 옴(3 × 4 = 12)
    assert _params(route) == {"region": "부산광역시", "category": "관광지", "limit": "12", "seed": "7"}
    assert "authorization" not in route.calls.last.request.headers
    destinations.search_destinations(SpringClient(BASE), SearchDestinationsArgs(region="강릉", limit=5), NO_SEED)
    assert _params(route) == {"region": "강원특별자치도 강릉시", "limit": "5"}   # 시군은 '시도 시군', seed 0이면 서버 기본


def test_search_llm_items_are_whitelisted_and_type3_text_stays_in_card(mock):
    mock.get("/api/destinations").respond(200, json=[{**LIVE_ROW, "extraField": "새 칸 원문", "homepage": "http://x"}])
    content, card = run_tool("search_destinations", _backend(), {"region": "부산", "category": "관광지"}, "c1", CTX)
    body = json.loads(content)
    assert body["items"] == [{"name": "경상좌수영성지", "where": "부산광역시 수영구", "note": "설명은 원문 카드",
                              "type": "관광지"}]
    assert SENTINEL not in content and "새 칸" not in content and "tong.visitkorea" not in content
    [item] = card["data"]["items"]
    assert item["text"] == LIVE_ROW["info"] and item["textPolicy"] == "VERBATIM_ONLY"   # 원문 그대로(변경 금지)
    assert item["attribution"] == "출처: 한국관광공사 TourAPI · 공공누리 제3유형(출처표시·변경금지)"
    assert (item["imageUrl"], item["lat"], item["lng"], item["category"]) == (LIVE_ROW["imageUrl"], 35.1697, 129.1153,
                                                                              "관광지")


@pytest.mark.parametrize(("region", "category"), [("강릉", "숙박"), ("광주", "음식점"), ("도쿄", "숙박"), ("어딘가", "숙박")])
def test_not_collected_categories_are_not_called(mock, region, category):
    route = mock.get("/api/destinations").respond(200, json=[LIVE_ROW])
    res = _backend().search_destinations(SearchDestinationsArgs(region=region, category=category), CTX)
    assert res.status == "unsupported" and res.extra["categoryCollected"] is False and route.call_count == 0
    assert tpl.status_message("search_destinations", "unsupported") == \
        "음식점·숙소 정보는 아직 모으지 않았어요. 관광지·문화시설·축제·레포츠는 찾아드릴 수 있어요."


def test_overseas_food_is_called_with_an_approximation_note(mock):
    mock.get("/api/destinations").respond(200, json=[{**LIVE_ROW, "country": "일본", "location": "일본 도쿄도",
                                                      "source": "WIKIDATA", "lat": 35.69, "lng": 139.70}])
    res = _backend().search_destinations(SearchDestinationsArgs(region="도쿄", category="음식점"), CTX)
    assert res.status == "ok" and res.notes == [destinations.note_overseas_food()]


def test_unknown_region_is_sent_only_if_it_fits_spring_rules(mock):
    route = mock.get("/api/destinations").respond(200, json=[])
    res = _backend().search_destinations(SearchDestinationsArgs(region="모르는 동네 이름 네 낱말"), CTX)
    assert res.status == "empty" and res.notes == [destinations.NOTE_REGION_UNREADABLE] and route.call_count == 0
    _backend().search_destinations(SearchDestinationsArgs(region="몽생미셸"), CTX)   # 해석기에 없지만 Spring 규칙엔 맞음
    assert _params(route)["region"] == "몽생미셸"


def test_limit_is_capped_and_empty_result(mock):
    rows = [{**LIVE_ROW, "id": 5000 + i, "name": f"강릉 여행지 {i}", "location": "강원특별자치도 강릉시"} for i in range(20)]
    route = mock.get("/api/destinations").respond(200, json=rows)
    content, card = run_tool("search_destinations", _backend(), {"region": "강릉", "limit": 30}, "c1", CTX)
    assert _params(route)["limit"] == "10" and len(card["data"]["items"]) == 10    # 카드 10곳까지
    body = json.loads(content)
    assert len(body["items"]) == 5 and destinations.note_limit(10) in body["notes"]   # LLM에는 5곳
    mock.get("/api/destinations").respond(200, json=[])
    res = _backend().search_destinations(SearchDestinationsArgs(region="강릉"), CTX)
    assert res.status == "empty" and tpl.status_message("search_destinations", "empty", {"region": "강릉"}) == \
        "강릉에서 찾은 곳이 없어요."


def test_query_that_matches_nothing_is_relaxed_once(mock):
    route = mock.get("/api/destinations")
    route.side_effect = [respx.MockResponse(200, json=[]), respx.MockResponse(200, json=[LIVE_ROW])]
    res = _backend().search_destinations(SearchDestinationsArgs(region="부산", query="반려견과 갈 수 있는"), CTX)
    assert res.status == "ok" and res.notes == [destinations.note_query_relaxed("반려견과 갈 수 있는")]
    first, second = (dict(c.request.url.params.items()) for c in route.calls)
    assert first["query"] == "반려견과 갈 수 있는" and "query" not in second


def test_wide_region_keeps_two_per_location_and_fills_in_rank_order():
    rows = [{"id": i, "name": f"p{i}", "location": loc} for i, loc in
            enumerate(["부산광역시 수영구"] * 4 + ["부산광역시 영도구"] + ["부산광역시 기장군"] * 2)]
    assert [r["id"] for r in destinations.cap_per_location(rows, 5)] == [0, 1, 4, 5, 6]
    assert [r["id"] for r in destinations.cap_per_location(rows[:4], 3)] == [0, 1, 2]   # 다른 곳이 없으면 채움


# --- 해외: 지역 표기가 고르지 않음(도쿄도의 섬, 삿포로 = 홋카이도) ---------------------------------

def test_tokyo_islands_are_dropped_by_city_radius():
    backend = FakeBackend(today=CTX.today)
    raw = backend.client.call("destinations", params={"region": "도쿄", "limit": "20"}).data
    islands = {"가잔 열도", "이즈오섬", "하치조섬", "오가사와라 제도"}
    assert raw[0]["location"] == "일본 도쿄도" and islands & {r["name"] for r in raw[:4]}   # 서버 순서로는 섬이 앞에 섞임
    res = backend.search_destinations(SearchDestinationsArgs(region="도쿄"), CTX)
    names = [i["name"] for i in res.items]
    assert len(names) == 5 and not islands & set(names)
    museums = backend.search_destinations(SearchDestinationsArgs(region="도쿄", category="문화시설"), CTX)
    assert "하치조 역사민속자료관" not in [i["name"] for i in museums.items] and museums.status == "ok"


def test_city_with_prefecture_level_rows_widens_to_country_with_the_same_radius():
    backend = FakeBackend(today=CTX.today)
    res = backend.search_destinations(SearchDestinationsArgs(region="삿포로"), CTX)
    assert [i["name"] for i in res.items] == ["삿포로 시계탑", "오도리 공원", "모이와산 전망대"]   # 오타루 운하(33km)는 밖
    assert res.notes == [destinations.note_country_widened("삿포로", "일본")]
    assert [r.params["region"] for r in backend.fixture.requests] == ["삿포로", "일본"]


def test_all_rows_outside_radius_fall_back_to_server_order_with_a_note(mock):
    far = {**LIVE_ROW, "country": "일본", "location": "일본 도쿄도", "lat": 24.78, "lng": 141.32, "name": "가잔 열도"}
    mock.get("/api/destinations").respond(200, json=[far])
    res = _backend().search_destinations(SearchDestinationsArgs(region="도쿄"), CTX)
    assert [i["name"] for i in res.items] == ["가잔 열도"] and res.notes[-1] == destinations.note_radius("도쿄", 40)


# --- 가짜 Spring이 S1 규칙을 흉내 내는지(docs/travel-api.md) ----------------------------------------

@pytest.mark.parametrize(("region", "text", "ok"), [
    ("부산", "대한민국 부산광역시 해운대구", True), ("해운대", "대한민국 부산광역시 해운대구", True),
    ("강원도", "대한민국 강원특별자치도 강릉시", True), ("경남", "대한민국 경상남도 거제시", True),
    ("전라북도", "대한민국 전북특별자치도 군산시", True), ("울릉도", "대한민국 경상북도 울릉군", True),
    ("교토", "일본 일본 교토부", True), ("교토", "일본 교토", True), ("캘리포니아", "미국 캘리포니아주", True),
    ("한국", "대한민국 부산광역시 수영구", True), ("광주", "대한민국 경기도 광주시", True),
    ("광주", "대한민국 광주광역시 동구", True), ("경기 광주", "대한민국 광주광역시 동구", False),
    ("경기 광주", "대한민국 경기도 광주시", True), ("부산", "대한민국 부산진구", False), ("대구", "대한민국 대구광역시", True),
])
def test_fixture_region_patterns(region, text, ok):
    assert region_matches(region, text) is ok


def test_fixture_spring_shapes_ordering_and_errors():
    backend = FakeBackend(today=CTX.today)
    http = backend.client
    [row] = http.call("destinations", params={"region": "부산", "category": "관광지", "limit": "1"}).data
    assert set(row) == set(DESTINATION_KEYS) and row["infoKind"] in ("DESCRIPTION", "SUMMARY")
    detail = http.call("destination_get", path_params={"id": 3405}).data
    assert set(detail) == set(DESTINATION_KEYS) | set(DETAIL_EXTRA_KEYS) and detail["name"] == "마니산(강화)"
    assert http.call("destination_get", path_params={"id": 999999}, accept={404}).status == 404
    assert [r["name"] for r in http.call("destination_lookup", params={"name": "마니산"}).data] == ["마니산(강화)"]
    assert [r["name"] for r in http.call("destination_lookup", params={"name": "앙코르와트"}).data] == ["앙코르 와트"]
    for bad in ({"category": "카페"}, {"limit": "0"}, {"limit": "51"}, {"region": "a b c d"}):
        assert http.call("destinations", params={"region": "부산", **bad}, accept={400}).status == 400
    assert http.call("destination_lookup", params={"name": " "}, accept={400}).status == 400
    # 동점 순서: CRC32(id:seed) — MySQL CRC32와 같은 CRC-32(SELECT CRC32('hello') = 907060870)
    assert zlib.crc32(b"hello") == 907060870 and tie_hash(17, 20261018) == zlib.crc32(b"17:20261018")
    order = {seed: [r["name"] for r in http.call("destinations", params={"region": "강릉", "category": "관광지",
                                                                         "seed": str(seed)}).data]
             for seed in (1, 2, 3)}
    assert all(o[0] == "오죽헌" for o in order.values())          # 실제 설명문이 있는 곳이 먼저
    assert len({tuple(o) for o in order.values()}) > 1               # seed로 동점 순서가 바뀜
    assert not any(r.has_authorization for r in backend.fixture.requests)


def test_gangneung_top5_is_gangneung_only_and_seed_stable():
    backend = FakeBackend(today=CTX.today)
    first = backend.search_destinations(SearchDestinationsArgs(region="강릉", category="관광지", limit=5), CTX)
    again = backend.search_destinations(SearchDestinationsArgs(region="강릉", category="관광지", limit=5), CTX)
    assert len(first.items) == 5 and {i["where"] for i in first.items} == {"강원특별자치도 강릉시"}
    assert first.items == again.items   # 같은 대화(seed)면 같은 순서


# --- get_destination_detail ------------------------------------------------------------------------

def test_detail_lookup_then_get_by_id_with_code_made_facts_only(mock):
    lookup = mock.get("/api/destinations/lookup").respond(200, json=[LOOKUP_ROW])
    get = mock.get("/api/destinations/3405").respond(200, json=DETAIL_ROW)
    content, card = run_tool("get_destination_detail", _backend(), {"name": "마니산"}, "c1", CTX)
    assert _params(lookup) == {"name": "마니산"} and get.call_count == 1
    assert "authorization" not in get.calls.last.request.headers
    [item] = json.loads(content)["items"]
    assert item == {"name": "마니산(강화)", "where": "인천 강화군", "note": "설명은 원문 카드", "type": "관광지",
                    "summary": "인천 강화군의 관광지입니다. 분류: 자연관광 > 자연경관 > 산", "address": "인천광역시 강화군 화도면"}
    assert SENTINEL not in content
    [c] = card["data"]["items"]
    assert c["text"] == DETAIL_ROW["info"] and c["address"] == DETAIL_ROW["address"] and c["regionCode"] == "28"
    assert tpl.answer_for_card(card) == "인천 강화군에 있는 마니산(강화) 정보를 찾았어요. 자세한 소개는 카드에서 볼 수 있어요."


def test_detail_404_keeps_the_lookup_row_and_empty_lookup(mock):
    mock.get("/api/destinations/lookup").respond(200, json=[LOOKUP_ROW])
    mock.get("/api/destinations/3405").respond(404, json={"status": 404, "error": "NOT_FOUND"})
    res = _backend().get_destination_detail(GetDestinationDetailArgs(name="마니산"), CTX)
    assert res.status == "ok" and res.card_items[0]["name"] == "마니산(강화)" and res.card_items[0]["address"] is None
    mock.get("/api/destinations/lookup").respond(200, json=[])
    assert _backend().get_destination_detail(GetDestinationDetailArgs(name="없는곳"), CTX).status == "empty"


def test_same_name_in_several_regions_gives_candidates_and_a_clarify_note(mock):
    rows = [{**LOOKUP_ROW, "id": 3406, "name": "보문사(강화)"},
            {**LOOKUP_ROW, "id": 3407, "name": "보문사 (서울)", "location": "서울특별시 성북구"}]
    mock.get("/api/destinations/lookup").respond(200, json=rows)
    get = mock.get(url__regex=r"/api/destinations/\d+").respond(200, json=DETAIL_ROW)
    content, card = run_tool("get_destination_detail", _backend(), {"name": "보문사"}, "c1", CTX)
    question = "보문사는 인천 강화군과 서울특별시 성북구에 있어요. 어느 지역의 보문사인가요?"
    assert get.call_count == 0 and card["status"] == "ok" and card["data"]["needsClarification"] is True
    assert card["data"]["clarify"] == question and tpl.answer_for_card(card) == question
    assert [c["location"] for c in card["data"]["candidates"]] == ["인천 강화군", "서울특별시 성북구"]
    assert json.loads(content)["items"] == [{"name": "보문사(강화)", "where": "인천 강화군", "type": "관광지"},
                                            {"name": "보문사 (서울)", "where": "서울특별시 성북구", "type": "관광지"}]
    assert SENTINEL not in content and tpl.clarify_kind(question) == "place_choice"


def test_region_word_in_the_name_narrows_candidates():
    backend = FakeBackend(today=CTX.today)
    for name, expected in (("서울 보문사", "보문사 (서울)"), ("보문사 강화", "보문사(강화)"), ("강화 마니산", "마니산(강화)")):
        res = backend.get_destination_detail(GetDestinationDetailArgs(name=name), CTX)
        assert res.status == "ok" and not res.extra.get("needsClarification") and res.items[0]["name"] == expected
    assert [r.params.get("name") for r in backend.fixture.requests if r.path.endswith("lookup")][:2] == \
        ["서울 보문사", "보문사"]


# --- 그래프: 모호한 지역 → validate 되묻기, 동명 장소 → collect → clarify, 답에 이어 풀기 ---------------------

def _router(table: dict):
    """라우터 입력 → 정해 둔 호출(라우터는 한 마디만 받음). 표에 없으면 글로 답함."""
    def script(messages, kwargs):
        text = last_human_text(messages)
        return tool_call_message(table[text]) if text in table else text_message("모르는 말")
    return script


def test_ambiguous_region_is_asked_then_the_answer_picks_one(make_graph, agent_context):
    calls = [("search_destinations", {"region": "광주", "category": "축제"})]
    kit = make_graph(_router({"광주 축제 알려줘": calls, "광주 축제 알려줘 경기도요": calls}))
    r = kit.turn("광주 축제 알려줘", agent_context)
    assert r.reply_kind == "clarify" and r.final_text == "광주광역시와 경기 광주시 중 어느 곳을 말씀하시는 건가요?"
    assert "tools" not in r.path and kit.backend.calls == []
    r = kit.turn("경기도요", agent_context)
    assert r.values["router_input"] == "광주 축제 알려줘 경기도요"
    assert kit.backend.calls == [("search_destinations", {"region": "경기도 광주시", "category": "축제"})]
    assert r.final_text == "경기도 광주시 축제 1곳을 찾았어요: 광주왕실도자기축제."
    [v] = r.values["verdicts"]
    assert "fix:region_choice" in v["grounding"]["issues"]


def test_same_name_place_goes_collect_to_clarify_and_the_answer_resolves_it(make_graph, agent_context):
    call = [("get_destination_detail", {"name": "보문사"})]
    kit = make_graph(_router({"보문사 어떤 곳이야?": call, "보문사 어떤 곳이야? 서울이요": call}))
    r = kit.turn("보문사 어떤 곳이야?", agent_context)
    assert r.path == ["start_turn", "route", "validate", "tools", "collect", "clarify", "finish"]
    assert r.reply_kind == "clarify"
    assert r.final_text == "보문사는 인천 강화군과 서울특별시 성북구에 있어요. 어느 지역의 보문사인가요?"
    assert r.cards[0]["data"]["needsClarification"] and r.values["results"][0]["clarify"] is True
    assert r.values["memory"]["pending_clarify"]["reason"] == "clarify:place_ambiguous"
    r = kit.turn("서울이요", agent_context)
    assert kit.backend.calls[-1] == ("get_destination_detail", {"name": "서울 보문사"})
    assert r.reply_kind == "template" and r.final_text.startswith("서울특별시 성북구에 있는 보문사 (서울) 정보를 찾았어요.")
    tool_msgs = [m for m in r.values["messages"] if isinstance(m, ToolMessage)]
    assert all("TYPE3-SENTINEL" not in m.content for m in tool_msgs)


def test_not_collected_category_in_ambiguous_region_is_not_asked(make_graph, agent_context):
    kit = make_graph(_router({"광주 맛집 알려줘": [("search_destinations", {"region": "광주", "category": "음식점"})]}))
    r = kit.turn("광주 맛집 알려줘", agent_context)
    assert r.reply_kind == "template" and r.final_text.startswith("음식점·숙소 정보는 아직 모으지 않았어요.")


def test_spring_400_becomes_a_bad_request_envelope(mock):
    mock.get("/api/destinations").respond(400, json={"status": 400, "error": "BAD_REQUEST", "errors": []})
    with pytest.raises(ToolBackendError) as e:
        run_tool("search_destinations", _backend(), {"region": "부산"}, "c1", CTX)
    body = json.loads(tool_error_to_json(e.value))
    assert (body["tool"], body["status"], body["error"]) == ("search_destinations", "error", "bad_request")


def test_make_result_keeps_needs_clarification_in_card_only():
    content, card = make_result("get_destination_detail", "ok", [{"name": "a", "where": "b"}],
                                card_data={"needsClarification": True, "clarify": "어느 지역의 a인가요?"})
    assert "needsClarification" not in content and card["data"]["needsClarification"] is True

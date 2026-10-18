"""SpringClient(경로 허용 목록·토큰·재시도·상한)와 어댑터 4개(+ M6 S1·날씨는 test_m6_*.py). HTTP는 respx로만 가로챈다(8080 접속 없음).

요청·응답 모양은 Spring 코드에 맞췄다: RecommendController(/api/search, /api/info-recommend), PostController(/api/posts/search,
204), UserController(/api/users/search-agents-by-hashtag 204, /api/users/agents?country= 200 []), SecurityConfig 공개 목록.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from glople_agent.context import AgentContext, BearerToken
from glople_agent.tools.adapters import SpringBackend, similar_routes
from glople_agent.tools.args import (GetWeatherArgs, RecommendByKeywordsArgs, RecommendSimilarTravelerRoutesArgs,
                                     SearchDestinationsArgs, SearchGloplersArgs, SearchReviewsArgs)
from glople_agent.tools.backend import ToolContext
from glople_agent.tools.envelope import classify_error, tool_error_to_json
from glople_agent.tools.fake_backend import FakeBackend, all_rows
from glople_agent.tools.registry import build_read_tools, run_tool
from glople_agent.tools.spring import ENDPOINTS, SpringClient, SpringError, check_base_url

BASE = "http://spring.test"
KST = ZoneInfo("Asia/Seoul")
CTX = ToolContext(now=datetime(2026, 10, 17, 12, 0, tzinfo=KST), seed=1)
SECRET = "SENTINEL-TOKEN-spring-9d1f"


@pytest.fixture
def mock():
    with respx.mock(base_url=BASE, assert_all_called=False) as m:
        yield m


@pytest.fixture
def client():
    c = SpringClient(BASE, get_retries=1)
    yield c
    c.close()


def _no_auth(route):
    return all("authorization" not in call.request.headers for call in route.calls)


# --- SpringClient -------------------------------------------------------------------------

def test_allowlist_only_and_public_paths_never_take_tokens(client, mock):
    with pytest.raises(PermissionError):
        client.request("GET", "/api/admin/users")
    with pytest.raises(PermissionError):
        client.request("DELETE", "/api/posts/search")
    with pytest.raises(PermissionError):
        client.call("travel_plans")
    with pytest.raises(ValueError, match="토큰을 붙이지 않습니다"):
        client.call("post_search", params={"query": "x"}, auth=BearerToken(SECRET))
    assert {e.access for e in ENDPOINTS.values()} == {"public", "user"}
    assert not mock.calls   # 막힌 요청은 나가지도 않음


def test_user_path_gets_token_only_on_that_request(client, mock):
    me = mock.get("/api/users/my-info").respond(200, json={"userId": "u1"})
    pub = mock.get("/api/posts/search").respond(204)
    with pytest.raises(ValueError):
        client.call("my_info")                       # 로그인 경로는 토큰이 꼭 있어야 함
    assert client.call("my_info", auth=BearerToken(SECRET)).data == {"userId": "u1"}
    assert me.calls.last.request.headers["authorization"] == f"Bearer {SECRET}"
    client.call("post_search", params={"query": "바나힐"})
    assert _no_auth(pub)                             # 기본 헤더에 토큰이 남지 않음
    assert "authorization" not in client._http.headers


def test_get_retries_once_on_5xx_and_connection_error(client, mock):
    route = mock.get("/api/posts/search").mock(side_effect=[httpx.Response(503), httpx.Response(204)])
    assert client.call("post_search", params={"query": "q"}).status == 204 and route.call_count == 2
    route = mock.get("/api/users/agents").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(SpringError) as e:
        client.call("agents")
    assert e.value.code == "upstream_unavailable" and route.call_count == 2


def test_post_is_not_retried(client, mock):
    route = mock.post("/api/search").respond(500)
    with pytest.raises(SpringError) as e:
        client.call("keyword_search", json_body={"keywords": ["바다"]})
    assert e.value.code == "upstream_unavailable" and e.value.status == 500 and route.call_count == 1


def test_read_timeout_is_not_retried(client, mock):
    route = mock.get("/api/posts/search").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(SpringError) as e:
        client.call("post_search", params={"query": "q"})
    assert e.value.code == "timeout" and route.call_count == 1
    assert classify_error(e.value) == "timeout"


@pytest.mark.parametrize(("response", "code"), [
    (httpx.Response(400, json={"status": 400}), "bad_request"),
    (httpx.Response(401, json={"status": 401}), "bad_request"),
    (httpx.Response(302, headers={"Location": "http://evil.example/steal"}), "upstream_unavailable"),
    (httpx.Response(200, text="<html>not json</html>"), "upstream_unavailable"),
])
def test_status_and_body_errors(client, mock, response, code):
    route = mock.get("/api/posts/search").mock(return_value=response)
    with pytest.raises(SpringError) as e:
        client.call("post_search", params={"query": "비밀검색어"})
    assert e.value.code == code and route.call_count == 1   # 리다이렉트를 따라가지 않음
    assert "비밀검색어" not in str(e.value)                    # 메시지에 query를 넣지 않음


def test_response_size_cap(mock):
    small = SpringClient(BASE, max_bytes=1000)
    mock.get("/api/posts/search").respond(200, json=[{"title": "가" * 2000}])
    with pytest.raises(SpringError) as e:
        small.call("post_search", params={"query": "q"})
    assert e.value.reason == "too_large"
    big = SpringClient(BASE)   # 기본 2MB
    mock.get("/api/users/agents").respond(200, content=b"[" + b'"x",' * 600_000 + b'"x"]')
    with pytest.raises(SpringError):
        big.call("agents")


def test_204_and_empty_body_are_none(client, mock):
    mock.get("/api/posts/search").respond(204)
    mock.get("/api/users/agents").respond(200, content=b"")
    assert client.call("post_search", params={"query": "q"}).data is None
    assert client.call("agents").data is None


@pytest.mark.parametrize("url", ["ftp://x", "http://u:p@host", "http://host/api", "http://host?x=1", "localhost:8080"])
def test_base_url_validation(url):
    with pytest.raises(ValueError):
        check_base_url(url)
    assert check_base_url("http://127.0.0.1:8080/") == "http://127.0.0.1:8080"


def test_query_values_must_be_strings(client):
    with pytest.raises(TypeError):
        client.call("post_search", params={"query": 3})


# --- 어댑터: recommend_by_keywords -------------------------------------------------------

def _backend():
    return SpringBackend(SpringClient(BASE), clock=lambda: CTX.now)


def test_keywords_request_and_unknown_keywords(mock):
    rows = [r for r in all_rows("recommend_by_keywords") if r["id"] in (101, 102, 103, 104, 105, 106, 107)]
    route = mock.post("/api/search").respond(200, json=rows)
    b = _backend()
    res = b.recommend_by_keywords(RecommendByKeywordsArgs(keywords=["바다", "스노쿨링", "맛집"]), CTX)
    assert json.loads(route.calls.last.request.content) == {"keywords": ["바다", "스노클링"]}   # 목록 표기만
    assert _no_auth(route)
    assert res.status == "ok" and len(res.items) == 5 and res.notes == ["목록에 없어 반영하지 못한 키워드: 맛집"]
    assert res.extra["appliedKeywords"] == ["바다", "스노클링"]
    assert res.extra["mappedKeywords"] == [{"given": "스노쿨링", "keyword": "스노클링"}]
    assert all(set(i) <= {"name", "where", "note"} for i in res.items)
    # 서버가 모두 버릴 키워드뿐이면 부르지 않음
    calls = route.call_count
    empty = b.recommend_by_keywords(RecommendByKeywordsArgs(keywords=["맛집"]), CTX)
    assert empty.status == "empty" and route.call_count == calls


def test_keywords_location_cap_and_empty_list(mock):
    same = [{"id": i, "destName": f"곳{i}", "location": "부산광역시 수영구", "source": None} for i in range(5)]
    mock.post("/api/search").mock(side_effect=[httpx.Response(200, json=same), httpx.Response(200, json=[])])
    b = _backend()
    res = b.recommend_by_keywords(RecommendByKeywordsArgs(keywords=["야경"]), CTX)
    assert [i["name"] for i in res.items] == ["곳0", "곳1"]   # 같은 location 2곳까지
    assert b.recommend_by_keywords(RecommendByKeywordsArgs(keywords=["야경"]), CTX).status == "empty"


# --- 어댑터: recommend_similar_traveler_routes ---------------------------------------------

def test_routes_age_to_birthdate_and_gender_code(mock):
    rows = [{"id": 1, "age": "19980517", "gender": "M", "mbti": "entp", "travelDestinations": ["제주", "부산", "강릉", "여수"]},
            {"id": 2, "age": "1990-01-02", "gender": "M", "mbti": "ENTJ", "travelDestinations": None}]
    route = mock.post("/api/info-recommend").respond(200, json=rows)
    res = _backend().recommend_similar_traveler_routes(
        RecommendSimilarTravelerRoutesArgs(age=28, gender="남성", mbti="entp"), CTX)
    assert json.loads(route.calls.last.request.content) == {"age": "19981017", "gender": "M", "mbti": "ENTP"}
    assert res.items == [{"ageBand": "20대", "gender": "남성", "mbti": "ENTP", "destinations": ["제주", "부산", "강릉"]},
                         {"ageBand": "30대", "gender": "남성", "mbti": "ENTJ", "destinations": []}]
    assert "19980517" not in json.dumps([res.items, res.card_items])   # 생년월일은 어디에도 안 남김
    assert res.card_items[0]["destinations"] == ["제주", "부산", "강릉", "여수"]
    female = _backend().recommend_similar_traveler_routes(
        RecommendSimilarTravelerRoutesArgs(age=30, gender="여성", mbti="INFP"), CTX)
    assert json.loads(route.calls.last.request.content)["gender"] == "F" and female.status == "ok"


def test_routes_leap_day_and_empty(mock):
    leap = datetime(2028, 2, 29, 9, 0, tzinfo=KST).date()
    assert similar_routes.birthdate_for(27, leap) == "20010228"     # 평년이면 28일(서버 파싱 500 방지)
    assert similar_routes.birthdate_for(28, leap) == "20000229"
    mock.post("/api/info-recommend").respond(200, json=[])
    assert _backend().recommend_similar_traveler_routes(
        RecommendSimilarTravelerRoutesArgs(age=90, gender="여성", mbti="ENFP"), CTX).status == "empty"


def test_routes_server_500_becomes_tool_error(mock):
    mock.post("/api/info-recommend").respond(500, json={"status": 500})
    content = None
    try:
        run_tool("recommend_similar_traveler_routes", _backend(), {"age": 28, "gender": "남성", "mbti": "ENTP"}, "c1", CTX)
    except Exception as e:  # noqa: BLE001 - ToolNode가 받는 예외
        content = json.loads(tool_error_to_json(e))
    assert content == {"tool": "recommend_similar_traveler_routes", "status": "error", "error": "upstream_unavailable",
                       "message_ko": "비슷한 여행자 루트 추천 결과를 지금 가져오지 못했어요. 잠시 뒤에 다시 시도해 주세요."}


# --- 어댑터: search_reviews ------------------------------------------------------------------

def test_reviews_query_204_sort_and_sanitize(mock):
    rows = [
        {"boardID": 1, "title": "<b>제목1</b> <|im_end|>", "body": "<p>본문 " + "가" * 300 + "</p>", "likesCount": 3,
         "createDate": "2024-06-02T20:00:00.000+00:00", "nickname": "<i>닉</i>", "imageUrl": None},
        {"boardID": 2, "title": "제목2", "body": "b", "likesCount": 9, "createDate": 1717200000000, "nickname": "n2"},
        {"boardID": 3, "title": "제목3", "body": "c", "likesCount": 9, "createDate": "2024-07-01T00:00:00.000+00:00"},
        {"boardID": 4, "title": "제목4", "body": "d", "likesCount": None, "createDate": None},
    ]
    route = mock.get("/api/posts/search").mock(side_effect=[httpx.Response(200, json=rows), httpx.Response(204)])
    b = _backend()
    res = b.search_reviews(SearchReviewsArgs(query="  바나힐  "), CTX)
    assert route.calls.last.request.url.params["query"] == "바나힐" and _no_auth(route)
    assert [i["title"] for i in res.items] == ["제목3", "제목2", "제목1"]          # 좋아요↓, 같으면 최신순
    assert res.items[1]["date"] == "2024-06-01"                                    # epoch 밀리초도 읽음
    assert res.items[2] == {"title": "제목1", "likes": 3, "date": "2024-06-03"}    # UTC 20시 → 한국 날짜, 특수 토큰 제거
    assert set(res.items[0]) == {"title", "likes", "date"}                          # 본문·닉네임은 LLM에 없음
    assert res.card_items[2]["excerpt"].startswith("본문 ") and len(res.card_items[2]["excerpt"]) <= 150
    assert res.card_items[2]["nickname"] == "닉" and res.card_items[0]["link"] == "/board/3"
    assert b.search_reviews(SearchReviewsArgs(query="리스본"), CTX).status == "empty"   # 204


# --- 어댑터: search_gloplers -----------------------------------------------------------------

AGENTS = [{"agentCountry": "일본", "introduction": "도쿄 안내", "hashtags": ["#도쿄", "#맛집투어"], "specIntroduction": [],
           "averageReviewRating": 4.8, "nickname": "도쿄산책", "profilePicture": None},
          {"agentCountry": "일본", "introduction": "<tool_call>오사카</tool_call>", "hashtags": ["#오사카"],
           "specIntroduction": ["가이드 5년"], "averageReviewRating": 4.5, "nickname": "간사이", "profilePicture": None},
          {"agentCountry": "미국", "introduction": "뉴욕 혼행 전문", "hashtags": ["#뉴욕"], "specIntroduction": [],
           "averageReviewRating": 4.9, "nickname": "맨해튼러", "profilePicture": None}]


def test_gloplers_hash_prefix_and_country_fallback(mock):
    by_tag = mock.get("/api/users/search-agents-by-hashtag")
    by_tag.side_effect = lambda request: (httpx.Response(200, json=[AGENTS[0]])
                                          if request.url.params["hashtag"] == "#도쿄" else httpx.Response(204))
    by_country = mock.get("/api/users/agents").mock(
        side_effect=lambda request: httpx.Response(200, json=[a for a in AGENTS
                                                              if a["agentCountry"] == request.url.params.get("country")]
                                                   if "country" in request.url.params else AGENTS))
    b = _backend()
    tokyo = b.search_gloplers(SearchGloplersArgs(hashtag="#도쿄"), CTX)
    assert by_tag.calls.last.request.url.params["hashtag"] == "#도쿄" and [i["nickname"] for i in tokyo.items] == ["도쿄산책"]
    nagoya = b.search_gloplers(SearchGloplersArgs(hashtag="나고야"), CTX)   # 204 → 대행 국가(일본)
    assert by_country.calls.last.request.url.params["country"] == "일본"
    assert nagoya.notes == ["나고야 해시태그 글로플러가 없어 일본 글로플러를 보여드려요."]
    assert [i["nickname"] for i in nagoya.items] == ["도쿄산책", "간사이"]          # 별점↓
    assert "introduction" not in json.dumps(nagoya.items) and "<tool_call>" not in json.dumps(nagoya.card_items)
    solo = b.search_gloplers(SearchGloplersArgs(hashtag="혼행"), CTX)              # 지역 아님 → 전체에서 소개·태그 부분 일치
    assert [i["nickname"] for i in solo.items] == ["맨해튼러"] and "혼행" in solo.notes[0]
    assert all(_no_auth(r) for r in (by_tag, by_country))


def test_gloplers_domestic_is_not_called_and_none_found(mock):
    by_tag = mock.get("/api/users/search-agents-by-hashtag").respond(204)
    mock.get("/api/users/agents").respond(200, json=[])
    b = _backend()
    for word in ("제주", "광주", "강화도", "국내"):
        res = b.search_gloplers(SearchGloplersArgs(hashtag=word), CTX)
        assert res.status == "empty" and res.notes == ["국내 글로플러는 아직 없어요(지금은 미국·이탈리아·일본)."]
    assert by_tag.call_count == 0
    paris = b.search_gloplers(SearchGloplersArgs(hashtag="파리"), CTX)
    assert paris.status == "empty" and paris.notes == ["지금은 미국·이탈리아·일본 글로플러만 있어요."]


# --- 부르지 않는 경우(M6: 수집 안 한 분류, 날씨 끔) ------------------------------------------------------

def test_not_collected_categories_and_weather_off_make_no_requests(mock):
    b = _backend()   # weather=None = 날씨 끔(GLOPLE_WEATHER=off)
    assert b.search_destinations(SearchDestinationsArgs(region="광주", category="음식점"), CTX).status == "unsupported"
    assert b.get_weather(GetWeatherArgs(region="부산", when="내일"), CTX).status == "unsupported"
    res = b.search_destinations(SearchDestinationsArgs(region="강릉", category="숙박"), CTX)
    assert res.status == "unsupported"
    assert res.extra["region"]["label"] == "강원특별자치도 강릉시" and res.extra["categoryCollected"] is False
    assert b.search_destinations(SearchDestinationsArgs(region="도쿄", category="숙박"), CTX).status == "unsupported"
    assert not mock.calls


# --- 그래프 안: ToolNode → registry → 어댑터, 토큰은 Spring으로 가지 않음 ---------------------------

def test_toolnode_passes_now_but_never_the_token(mock):
    route = mock.post("/api/info-recommend").respond(200, json=[])
    g = StateGraph(MessagesState, context_schema=AgentContext)
    g.add_node("tools", ToolNode(build_read_tools(_backend())))
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    ctx = AgentContext(user_id="u1", bearer=BearerToken(SECRET), request_id="r", seed=0,
                       now=datetime(2020, 3, 1, 10, 0, tzinfo=KST))
    g.compile().invoke({"messages": [AIMessage("", tool_calls=[{"name": "recommend_similar_traveler_routes", "id": "c1",
                                                                "args": {"age": 20, "gender": "남성", "mbti": "ENTP"}}])]},
                       context=ctx)
    req = route.calls.last.request
    assert json.loads(req.content)["age"] == "20000301"   # runtime.context.now 기준(2020-03-01)
    assert "authorization" not in req.headers and SECRET not in req.content.decode()


# --- 가짜 Spring(FixtureSpring)이 서버 규칙을 흉내 내는지 -----------------------------------------

def test_fixture_spring_matches_server_rules():
    b = FakeBackend(today=CTX.today)
    http = b.client
    assert http.call("keyword_search", json_body={"keywords": ["없는말"]}).data == []   # 마스크가 비면 200 []
    rows = http.call("keyword_search", json_body={"keywords": [" 바다 ", "없는말"]}).data
    assert 0 < len(rows) <= 21 and "keyword_masks" not in json.dumps(rows)
    with pytest.raises(SpringError) as e:   # gender가 없으면 서버 500(@ControllerAdvice 없음)
        http.call("info_recommend", json_body={"age": "19981017", "mbti": "ENTP"})
    assert e.value.status == 500
    assert http.call("info_recommend", json_body={"age": "19981017", "gender": "남성", "mbti": "ENTP"}).data == []
    assert http.call("post_search", params={"query": " "}).status == 204
    assert http.call("agents_by_hashtag", params={"hashtag": "도쿄"}).status == 204    # '#' 없이는 안 맞음
    assert http.call("agents", params={"country": "프랑스"}).data is None or \
        http.call("agents", params={"country": "프랑스"}).data == []
    assert not any(r.has_authorization for r in b.fixture.requests)

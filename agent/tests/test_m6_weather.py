"""M6 날씨(Open-Meteo): 클라이언트(respx만, 실제 네트워크 없음) · 위치 · 현지 날짜 · 템플릿 답 · 실패 · 토큰 위생 · 설정.

응답 모양은 2026-10-18 수동 요청 두 번(api-notes M6)의 실제 응답 그대로다. conftest가 루프백 밖 소켓 연결을 막는다.
"""
import io
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from glople_agent import cli
from glople_agent.answer import templates as tpl
from glople_agent.config import Settings, SettingsError
from glople_agent.llm.fake import tool_call_message
from glople_agent.tools.adapters import SpringBackend, weather as W
from glople_agent.tools.args import GetWeatherArgs
from glople_agent.tools.backend import ToolContext
from glople_agent.tools.envelope import ToolBackendError, tool_error_to_json
from glople_agent.tools.fake_backend import FakeBackend
from glople_agent.tools.fixture_weather import FixtureOpenMeteo, make_weather_client
from glople_agent.tools.registry import run_tool
from glople_agent.tools.spring import SpringClient
from glople_agent.tools.weather import (ATTRIBUTION, DAILY_VARIABLES, FORECAST_URL, GEOCODING_URL, OpenMeteoClient,
                                        WeatherError)

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=KST)   # 토요일
CTX = ToolContext(now=NOW, seed=1)

# 2026-10-18 실제 응답(수동 요청 1·2) 그대로
LIVE_GEOCODE = {"results": [{"id": 1843137, "name": "강릉시", "latitude": 37.75266, "longitude": 128.87239,
                             "elevation": 77.0, "feature_code": "PPLA3", "country_code": "KR", "admin1_id": 1843125,
                             "admin2_id": 1843136, "timezone": "Asia/Seoul", "population": 208161,
                             "country_id": 1835841, "country": "대한민국", "admin1": "강원도", "admin2": "강릉시"}],
                "generationtime_ms": 0.18703938}
LIVE_FORECAST = {"latitude": 37.75, "longitude": 128.875, "generationtime_ms": 0.12946128845214844,
                 "utc_offset_seconds": 32400, "timezone": "Asia/Seoul", "timezone_abbreviation": "GMT+9",
                 "elevation": 72.0, "daily_units": {"time": "iso8601", "weather_code": "wmo code",
                                                    "temperature_2m_max": "°C", "temperature_2m_min": "°C",
                                                    "precipitation_probability_max": "%"},
                 "daily": {"time": ["2026-10-18", "2026-10-19"], "weather_code": [61, 3],
                           "temperature_2m_max": [21.8, 19.8], "temperature_2m_min": [14.7, 14.2],
                           "precipitation_probability_max": [100, 2]}}
LIVE_NOW = datetime(2026, 10, 18, 14, 27, tzinfo=KST)


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        yield m


def _live_backend() -> SpringBackend:
    return SpringBackend(SpringClient("http://spring.test"), clock=lambda: LIVE_NOW,
                         weather=OpenMeteoClient(retries=1))


# --- 실제 응답 모양으로(respx) -------------------------------------------------------------------

def test_live_shapes_geocode_then_forecast_and_only_coordinates_are_sent(mock):
    geo = mock.get(GEOCODING_URL).respond(200, json=LIVE_GEOCODE)
    fc = mock.get(FORECAST_URL).respond(200, json=LIVE_FORECAST)
    ctx = ToolContext(now=LIVE_NOW, seed=0)
    content, card = run_tool("get_weather", _live_backend(), {"region": "강릉", "when": "내일"}, "c1", ctx)
    assert dict(geo.calls.last.request.url.params.items()) == {"name": "강릉시", "count": "5", "language": "ko",
                                                              "format": "json", "countryCode": "KR"}
    assert dict(fc.calls.last.request.url.params.items()) == {
        "latitude": "37.75", "longitude": "128.87", "daily": ",".join(DAILY_VARIABLES), "timezone": "auto",
        "forecast_days": "2"}
    for call in (*geo.calls, *fc.calls):
        assert "authorization" not in call.request.headers and "cookie" not in call.request.headers
    assert card["data"]["items"] == [{"date": "2026-10-19", "label": "10월 19일(월)", "summary": "흐림", "tmin": 14,
                                      "tmax": 20, "pop": 2, "code": 3}]
    assert card["data"]["attribution"] == ATTRIBUTION == "Weather data by Open-Meteo.com (CC BY 4.0)"
    assert card["data"]["location"] == {"name": "강원도 강릉시", "lat": 37.75266, "lng": 128.87239,
                                        "source": "geocoding"}
    assert card["data"]["provider"] == "open-meteo" and "오프라인" not in content   # 실제 응답이면 fixture note 없음
    assert tpl.answer_for_card(card) == "강릉 내일 날씨예요: 10월 19일(월) 흐림, 최저 14도·최고 20도, 강수 확률 2%."
    assert json.loads(content)["items"] == [{"date": "2026-10-19", "summary": "흐림", "tmin": 14, "tmax": 20, "pop": 2}]


def test_overseas_city_uses_table_coordinates_without_geocoding(mock):
    geo = mock.get(GEOCODING_URL).respond(200, json=LIVE_GEOCODE)
    fc = mock.get(FORECAST_URL).respond(200, json={**LIVE_FORECAST, "utc_offset_seconds": 10800,
                                                   "daily": {**LIVE_FORECAST["daily"], "time": ["2026-10-18",
                                                                                                "2026-10-19"]}})
    res = _live_backend().get_weather(GetWeatherArgs(region="헬싱키", when="오늘"), ToolContext(now=LIVE_NOW))
    assert geo.call_count == 0 and dict(fc.calls.last.request.url.params.items())["latitude"] == "60.17"
    assert res.status == "ok" and res.card_items[0]["date"] == "2026-10-18"


def test_geocoding_no_results_and_wrong_province_are_not_found(mock):
    mock.get(GEOCODING_URL).respond(200, json={"generationtime_ms": 0.1})   # 결과 없으면 results 키 없음
    fc = mock.get(FORECAST_URL).respond(200, json=LIVE_FORECAST)
    res = _live_backend().get_weather(GetWeatherArgs(region="영월"), CTX)
    assert res.status == "empty" and res.notes == [W.NOTE_NOT_FOUND] and fc.call_count == 0
    other = {"results": [{**LIVE_GEOCODE["results"][0], "admin1": "경상남도"},
                         {**LIVE_GEOCODE["results"][0], "admin1": "전라남도"}]}
    mock.get(GEOCODING_URL).respond(200, json=other)
    assert _live_backend().get_weather(GetWeatherArgs(region="강릉"), CTX).status == "empty"


# --- 실패 → 오류 봉투(정중한 안내) ------------------------------------------------------------------

@pytest.mark.parametrize(("effect", "code", "calls"), [
    (httpx.ReadTimeout("slow"), "timeout", 1),
    (httpx.ConnectError("down"), "upstream_unavailable", 2),                   # 연결 오류는 1회 더
    (httpx.Response(503), "upstream_unavailable", 2),
    (httpx.Response(429), "upstream_unavailable", 1),
    (httpx.Response(400, json={"error": True, "reason": "bad"}), "bad_request", 1),
    (httpx.Response(200, json={"latitude": 1}), "upstream_unavailable", 1),   # daily·utc_offset 없음
    (httpx.Response(200, content=b"<html>"), "upstream_unavailable", 1),
])
def test_failures_become_error_envelopes_with_a_polite_message(mock, effect, code, calls):
    route = mock.get(FORECAST_URL)
    route.side_effect = effect if isinstance(effect, Exception) else [effect, effect]
    with pytest.raises(ToolBackendError) as e:
        run_tool("get_weather", _live_backend(), {"region": "헬싱키"}, "c1", CTX)
    assert e.value.code == code and route.call_count == calls
    body = json.loads(tool_error_to_json(e.value))
    assert body == {"tool": "get_weather", "status": "error", "error": code,
                    "message_ko": tpl.error_message("get_weather", code)}
    assert "60.17" not in str(e.value) and e.value.__cause__ is None   # 좌표가 든 URL을 예외에 잇지 않음


def test_client_rejects_unknown_urls_and_bad_coordinates():
    client = OpenMeteoClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(PermissionError):
        client._get("https://evil.example/forecast", {}, "x")
    with pytest.raises(WeatherError) as e:
        client.forecast(123.0, 0.0, 1)
    assert e.value.code == "bad_request"


def test_geocode_is_cached_per_name():
    fixture = FixtureOpenMeteo(now=lambda: NOW)
    client = fixture.client()
    assert client.geocode("강릉시", country_code="KR") == client.geocode("강릉시", country_code="KR")
    assert len(fixture.requests) == 1


# --- 현지 날짜 -------------------------------------------------------------------------------------

@pytest.mark.parametrize(("today", "when", "expected"), [
    (date(2026, 10, 17), "오늘", ["2026-10-17"]),
    (date(2026, 10, 17), "내일", ["2026-10-18"]),
    (date(2026, 10, 17), "이번 주말", ["2026-10-17", "2026-10-18"]),   # 토요일이면 오늘·내일
    (date(2026, 10, 18), "이번 주말", ["2026-10-18"]),                 # 일요일이면 오늘만
    (date(2026, 10, 14), "이번 주말", ["2026-10-17", "2026-10-18"]),   # 수요일 → 토·일
    (date(2026, 10, 17), "다음 주", [f"2026-10-{d}" for d in range(19, 26)]),
    (date(2026, 10, 19), "다음 주", [f"2026-10-{d}" for d in range(26, 32)] + ["2026-11-01"]),   # 월요일 → 다음 월~일
])
def test_dates_for_when(today, when, expected):
    assert [d.isoformat() for d in W.dates_for(when, today)] == expected
    days = W.FORECAST_DAYS[when]
    assert (W.dates_for(when, today)[-1] - today).days < days


def test_local_today_uses_the_response_offset():
    assert W.local_today(NOW, 9 * 3600) == date(2026, 10, 17)
    assert W.local_today(NOW, -7 * 3600) == date(2026, 10, 16)   # KST 낮 12시 = 밴쿠버(PDT) 전날 저녁
    assert W.local_today(NOW.astimezone(timezone.utc), 13 * 3600) == date(2026, 10, 17)


def test_vancouver_today_is_the_local_previous_day_in_the_fixture():
    backend = FakeBackend(today=NOW.date())
    res = backend.get_weather(GetWeatherArgs(region="밴쿠버", when="오늘"), CTX)
    assert res.status == "ok" and res.extra["localToday"] == "2026-10-16" and res.card_items[0]["label"] == "10월 16일(금)"


# --- 위치 고르기 -----------------------------------------------------------------------------------

@pytest.mark.parametrize(("region", "status", "note"), [
    ("강원도", "empty", W.NOTE_TOO_WIDE_PROVINCE), ("일본", "empty", W.NOTE_TOO_WIDE_COUNTRY),
    ("국내", "empty", W.NOTE_TOO_WIDE_COUNTRY), ("모르는곳", "empty", W.NOTE_NOT_FOUND),
    ("고성", "empty", "강원 고성군과 경남 고성군 중 어느 곳을 말씀하시는 건가요?"),
])
def test_places_that_are_not_one_point_make_no_forecast_request(region, status, note):
    backend = FakeBackend(today=NOW.date())
    res = backend.get_weather(GetWeatherArgs(region=region), CTX)
    assert res.status == status and res.notes == [note]
    assert not [r for r in backend.weather_fixture.requests if r.path == "/v1/forecast"]


def test_same_name_county_is_picked_by_province_and_jeju_is_jeju_city():
    backend = FakeBackend(today=NOW.date())
    south = backend.get_weather(GetWeatherArgs(region="경남 고성"), CTX)
    north = backend.get_weather(GetWeatherArgs(region="강원 고성"), CTX)
    assert south.extra["location"]["lat"] == 34.973 and north.extra["location"]["lat"] == 38.3806
    jeju = backend.get_weather(GetWeatherArgs(region="제주"), CTX)
    assert jeju.extra["location"]["name"] == "제주특별자치도 제주시" and W.NOTE_JEJU in jeju.notes
    busan = backend.get_weather(GetWeatherArgs(region="부산", when="이번 주말"), CTX)
    assert [i["date"] for i in busan.card_items] == ["2026-10-17", "2026-10-18"] and W.NOTE_FIXTURE in busan.notes


# --- 그래프: 템플릿 답(숫자는 코드), 토큰·발화는 Open-Meteo로 가지 않음 ----------------------------------

def test_weather_in_graph_is_a_template_answer_and_sends_no_user_data(make_graph, agent_context, sentinel_raw):
    kit = make_graph(lambda m, k: tool_call_message([("get_weather", {"region": "부산", "when": "내일"})]))
    r = kit.turn("부산 내일 날씨 어때?", agent_context)
    [card] = r.cards
    assert r.reply_kind == "template" and card["status"] == "ok" and card["data"]["attribution"] == ATTRIBUTION
    item = card["data"]["items"][0]
    assert r.final_text.startswith(f"부산 내일 날씨예요: 10월 18일(일) {item['summary']}, 최저 {item['tmin']}도·"
                                   f"최고 {item['tmax']}도, 강수 확률 {item['pop']}%.")
    sent = kit.backend.weather_fixture.requests
    assert [r.path for r in sent] == ["/v1/search", "/v1/forecast"]
    assert not any(w.has_authorization or w.has_cookie for w in sent)
    blob = json.dumps([w.params for w in sent], ensure_ascii=False)
    for secret in (sentinel_raw, agent_context.user_id, agent_context.request_id, "날씨 어때"):
        assert secret not in blob
    assert set(sent[1].params) == {"latitude", "longitude", "daily", "timezone", "forecast_days"}


def test_weather_modes_and_settings(monkeypatch):
    assert make_weather_client("off") is None
    assert make_weather_client("fixture").provider == "fixture" and make_weather_client("live").provider == "open-meteo"
    with pytest.raises(ValueError):
        make_weather_client("mlx")
    assert Settings.from_env({}).weather == "live"
    assert Settings.from_env({"GLOPLE_WEATHER": "off"}).weather == "off"
    with pytest.raises(SettingsError):
        Settings.from_env({"GLOPLE_WEATHER": "always"})
    from glople_agent.graph.build import real_deps
    deps = real_deps("http://127.0.0.1:8090/v1", answer_mode="template", weather="off")   # 서버에 접속하지 않음
    assert deps.answer_mode == "template" and deps.answer_llm is None
    weather_tool = next(t for t in deps.read_tools if t.name == "get_weather")
    out = weather_tool.invoke({"type": "tool_call", "id": "c1", "name": "get_weather", "args": {"region": "부산"}})
    assert json.loads(out.content)["status"] == "unsupported"


def test_cli_chat_weather_flag_and_tool_report(monkeypatch):
    out = io.StringIO()
    session = cli.ChatSession("rules", user=cli.CLI_USER, inp=io.StringIO("안녕\n"), out=out, weather="off")
    assert session.run() == 0 and "M6: 날씨 끔(미지원 안내)" in out.getvalue()
    out = io.StringIO()
    code = cli.main(["tool", "get_weather", '{"region": "강릉", "when": "내일"}', "--now", "2026-10-17T12:00:00+09:00"],
                    out=out)
    text = out.getvalue()
    assert code == 0 and "GET geocoding-api.open-meteo.com/v1/search (fixture)" in text
    assert "GET api.open-meteo.com/v1/forecast (fixture)" in text and "Authorization 있음" not in text
    assert ATTRIBUTION in text


def test_tests_cannot_reach_the_real_open_meteo():
    # conftest의 소켓 가드: 실제 Open-Meteo 호스트 이름 풀이가 막혀 있음(respx·fixture 없이 부르면 바로 실패)
    client = OpenMeteoClient(retries=0, connect_timeout=0.5, read_timeout=0.5)
    with pytest.raises(WeatherError) as e:
        client.forecast(37.5, 127.0, 1)
    assert e.value.code == "upstream_unavailable"


def test_forecast_days_cover_every_window():
    for when, days in W.FORECAST_DAYS.items():
        for offset in range(7):
            today = date(2026, 10, 12) + timedelta(days=offset)   # 월~일
            assert all((d - today).days < days for d in W.dates_for(when, today)), (when, today)

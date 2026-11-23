"""가짜 Open-Meteo(M6): 지오코딩·예보 두 주소를 흉내 내는 httpx.MockTransport. 네트워크 없음.

- 응답 모양은 2026-10-18 실제 요청 두 번(api-notes M6)과 같다:
  지오코딩 `{"results": [{id, name, latitude, longitude, elevation, feature_code, country_code, timezone, country, admin1,
  admin2, …}], "generationtime_ms"}`(없으면 results 키 없음), 예보 `{latitude, longitude, generationtime_ms,
  utc_offset_seconds, timezone, timezone_abbreviation, elevation, daily_units, daily: {time, weather_code,
  temperature_2m_max, temperature_2m_min, precipitation_probability_max}}`. 잘못된 값은 400 `{"error": true, "reason"}`.
- 지오코딩 표는 tests/fixtures/backend/weather.json. 예보 값은 좌표·날짜의 CRC32로 만든 **결정적인 가짜 값**(실제 날씨가 아님)이고,
  daily.time은 실제처럼 현지 '오늘'부터 forecast_days일. 현지 시간대는 한국 좌표면 Asia/Seoul(+9), 그 밖은 경도/15로 어림한다.
- requests에 받은 요청(호스트·경로·query·Authorization 유무)을 남긴다(테스트가 좌표 말고 아무것도 안 갔는지 확인).
"""
import json
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from glople_agent.tools.backend import kst_now
from glople_agent.tools.fixture_spring import DEFAULT_FIXTURES_DIR
from glople_agent.tools.weather import DAILY_VARIABLES, MAX_FORECAST_DAYS, OpenMeteoClient, WeatherMode

FAKE_CODES = (0, 1, 2, 3, 3, 45, 61, 63, 80, 0, 1, 2, 95, 71)
FAKE_POP = {0: 0, 1: 5, 2: 10, 3: 20, 45: 15, 61: 70, 63: 85, 80: 60, 95: 80, 71: 65}


@dataclass
class SeenWeatherRequest:
    host: str
    path: str
    params: dict[str, str]
    has_authorization: bool
    has_cookie: bool


def _json(status: int, data: Any) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(data, ensure_ascii=False).encode(),
                          headers={"Content-Type": "application/json"})


def _bad(reason: str) -> httpx.Response:
    return _json(400, {"error": True, "reason": reason})


def utc_offset_for(lat: float, lng: float) -> tuple[int, str]:
    """(초, 시간대 이름). 한국 좌표는 Asia/Seoul, 그 밖은 경도로 어림한 고정 오프셋(fixture 전용 근사)."""
    if 33.0 <= lat <= 39.0 and 124.0 <= lng <= 132.0:
        return 9 * 3600, "Asia/Seoul"
    hours = max(-12, min(14, round(lng / 15)))
    return hours * 3600, f"GMT{hours:+d}"


def fake_day(lat: float, lng: float, day: date) -> tuple[int, float, float, int]:
    """(weather_code, 최고, 최저, 강수 확률) — 좌표·날짜로 정해지는 가짜 값."""
    h = zlib.crc32(f"{lat:.2f},{lng:.2f},{day.isoformat()}".encode())
    code = FAKE_CODES[h % len(FAKE_CODES)]
    base = 33.0 - 0.4 * abs(lat)   # 10월 낮 최고 기온 어림(위도만 봄): 부산 19도, 헬싱키 9도, 싱가포르 32도
    tmax = round(base + (h >> 8) % 7 - 3 + ((h >> 4) % 10) / 10, 1)
    tmin = round(tmax - 5 - (h >> 12) % 5 - ((h >> 20) % 10) / 10, 1)
    if code == 71 and tmax > 3:   # 따뜻한 날의 눈은 비로
        code = 61
    pop = min(100, FAKE_POP.get(code, 30) + (h >> 16) % 10)
    return code, tmax, tmin, pop


@dataclass
class FixtureOpenMeteo:
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR
    now: Callable[[], datetime] = field(default=kst_now)
    requests: list[SeenWeatherRequest] = field(default_factory=list)
    _geo: dict | None = None

    def geocoding_table(self) -> dict[str, list[dict]]:
        if self._geo is None:
            self._geo = json.loads((Path(self.fixtures_dir) / "weather.json").read_text(encoding="utf-8"))["geocoding"]
        return self._geo

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> OpenMeteoClient:
        return OpenMeteoClient(transport=self.transport(), provider="fixture")

    def handle(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params.items())
        self.requests.append(SeenWeatherRequest(request.url.host, request.url.path, params,
                                                "authorization" in request.headers, "cookie" in request.headers))
        if request.method != "GET":
            return _json(405, {"error": True, "reason": "method"})
        if (request.url.host, request.url.path) == ("geocoding-api.open-meteo.com", "/v1/search"):
            return self.geocoding(params)
        if (request.url.host, request.url.path) == ("api.open-meteo.com", "/v1/forecast"):
            return self.forecast(params)
        return _json(404, {"error": True, "reason": "Not Found"})

    def geocoding(self, params: dict[str, str]) -> httpx.Response:
        name = (params.get("name") or "").strip()
        if len(name) < 2:
            return _json(200, {"generationtime_ms": 0.1})
        rows = [r for r in self.geocoding_table().get(name, [])
                if not params.get("countryCode") or r.get("country_code") == params["countryCode"]]
        rows = rows[: int(params.get("count") or 10)]
        return _json(200, {"results": rows, "generationtime_ms": 0.2} if rows else {"generationtime_ms": 0.1})

    def forecast(self, params: dict[str, str]) -> httpx.Response:
        try:
            lat, lng = float(params["latitude"]), float(params["longitude"])
            days = int(params.get("forecast_days") or 7)
        except (KeyError, ValueError):
            return _bad("Parameter 'latitude' and 'longitude' must be numbers")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return _bad("Latitude must be in range of -90 to 90°.")
        if not 0 <= days <= MAX_FORECAST_DAYS:
            return _bad(f"Forecast days is invalid. Allowed range 0 to {MAX_FORECAST_DAYS}.")
        wanted = [v for v in (params.get("daily") or "").split(",") if v]
        if not wanted or any(v not in DAILY_VARIABLES for v in wanted):
            return _bad("Cannot initialize WeatherVariable from invalid String value")
        offset, tz = utc_offset_for(lat, lng)
        local_today = (self.now().astimezone(timezone.utc) + timedelta(seconds=offset)).date()
        dates = [local_today + timedelta(days=i) for i in range(days)]
        values = [fake_day(lat, lng, d) for d in dates]
        daily: dict[str, list] = {"time": [d.isoformat() for d in dates]}
        units = {"time": "iso8601"}
        columns = {"weather_code": (0, "wmo code"), "temperature_2m_max": (1, "°C"), "temperature_2m_min": (2, "°C"),
                   "precipitation_probability_max": (3, "%")}
        for v in wanted:
            i, unit = columns[v]
            daily[v] = [row[i] for row in values]
            units[v] = unit
        return _json(200, {"latitude": lat, "longitude": lng, "generationtime_ms": 0.1, "utc_offset_seconds": offset,
                           "timezone": tz, "timezone_abbreviation": tz if tz.startswith("GMT") else "GMT+9",
                           "elevation": 50.0, "daily_units": units, "daily": daily})


def make_weather_client(mode: "WeatherMode | OpenMeteoClient",
                        fixture: FixtureOpenMeteo | None = None) -> OpenMeteoClient | None:
    """날씨 모드 → 클라이언트. fixture = 가짜 Open-Meteo(MockTransport), live = 실제(https, 키 없음), off = None(부르지 않음)."""
    if isinstance(mode, OpenMeteoClient):
        return mode
    if mode == "fixture":
        return (fixture or FixtureOpenMeteo()).client()
    if mode == "live":
        return OpenMeteoClient()
    if mode == "off":
        return None
    raise ValueError(f"날씨 모드는 live·fixture·off 중 하나입니다: {mode!r}")


__all__ = ["FixtureOpenMeteo", "SeenWeatherRequest", "fake_day", "make_weather_client", "utc_offset_for"]

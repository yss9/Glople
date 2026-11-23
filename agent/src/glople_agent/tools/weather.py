"""Open-Meteo 날씨 클라이언트(M6, 오너 승인: API 키 없음, 비상업 무료, CC BY 4.0).

- 주소는 아래 두 상수뿐이다(예보·지오코딩). 모델 출력으로 URL을 만들지 않고, 값은 httpx가 인코딩하는 query로만 간다.
- 보내는 것: 예보는 **좌표(소수 2자리로 반올림, 약 1km)·일수·변수 이름·timezone=auto**뿐. 지오코딩은 지역 해석기 표의 이름
  (강릉시·부산광역시처럼 코드가 가진 글)과 나라 코드뿐이다. 사용자 토큰·사용자 id·발화는 보내지 않는다(Authorization 헤더를 만들
  방법이 없음: 이 클라이언트는 BearerToken을 받지 않는다). 쿠키·환경 변수 프록시도 쓰지 않음(trust_env=False).
- timeout 연결 2초·읽기 4초(design.md 12절 'Open-Meteo 4초'), 연결 오류·5xx는 1회 재시도, 응답 256KB 상한, 리다이렉트 안 따라감.
- 오류는 WeatherError(code). code는 도구 오류 봉투의 error 값(timeout·upstream_unavailable·bad_request)이다.
- 모드(WeatherMode): live = 실제 Open-Meteo, fixture = 가짜 Open-Meteo(tools/fixture_weather.py, 네트워크 없음), off = 부르지 않음
  (get_weather가 '아직 연결하지 않았어요'). 테스트는 늘 fixture이거나 respx다.
"""
import json
from collections.abc import Mapping
from typing import Any, Literal

import httpx

from glople_agent.config import WeatherMode

WeatherErrorCode = Literal["upstream_unavailable", "timeout", "bad_request"]

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
ALLOWED_URLS = frozenset({FORECAST_URL, GEOCODING_URL})
DAILY_VARIABLES = ("weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max")
MAX_FORECAST_DAYS = 16
CONNECT_TIMEOUT = 2.0
READ_TIMEOUT = 4.0
RETRIES = 1
MAX_RESPONSE_BYTES = 256 * 1024
COORD_DECIMALS = 2
USER_AGENT = "glople-agent/0.0.1"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"   # 날씨 카드에 늘 붙인다(라이선스 조건)
ATTRIBUTION_URL = "https://open-meteo.com/"


class WeatherError(Exception):
    """Open-Meteo 호출 실패. 메시지에는 엔드포인트 이름·상태 코드만(좌표·이름은 넣지 않음)."""

    def __init__(self, code: WeatherErrorCode, *, endpoint: str = "", status: int | None = None,
                 reason: str = "") -> None:
        detail = ", ".join(x for x in (endpoint, f"HTTP {status}" if status else "", reason) if x)
        super().__init__(f"Open-Meteo {code}" + (f" ({detail})" if detail else ""))
        self.code: WeatherErrorCode = code
        self.status = status
        self.reason = reason


class _Retryable(Exception):
    def __init__(self, status: int | None = None, reason: str = "") -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class OpenMeteoClient:
    """예보(forecast)·지오코딩(geocode). provider는 카드에 남기는 이름("open-meteo" | "fixture")."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None, provider: str = "open-meteo",
                 connect_timeout: float = CONNECT_TIMEOUT, read_timeout: float = READ_TIMEOUT,
                 retries: int = RETRIES, max_bytes: int = MAX_RESPONSE_BYTES) -> None:
        self.provider = provider
        self.retries = retries
        self.max_bytes = max_bytes
        self._geocode_cache: dict[tuple[str, str | None], list[dict]] = {}
        self._http = httpx.Client(transport=transport, follow_redirects=False, trust_env=False,
                                  timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
                                  headers={"Accept": "application/json", "User-Agent": USER_AGENT})

    @property
    def is_fixture(self) -> bool:
        return self.provider == "fixture"

    def close(self) -> None:
        self._http.close()

    def forecast(self, lat: float, lng: float, days: int) -> dict:
        """일별 예보(현지 날짜 기준, timezone=auto). 응답의 utc_offset_seconds로 호출부가 현지 '오늘'을 정한다."""
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise WeatherError("bad_request", endpoint="forecast", reason="coordinates")
        params = {"latitude": f"{round(lat, COORD_DECIMALS):.{COORD_DECIMALS}f}",
                  "longitude": f"{round(lng, COORD_DECIMALS):.{COORD_DECIMALS}f}",
                  "daily": ",".join(DAILY_VARIABLES), "timezone": "auto",
                  "forecast_days": str(max(1, min(int(days), MAX_FORECAST_DAYS)))}
        data = self._get(FORECAST_URL, params, "forecast")
        daily = data.get("daily") if isinstance(data, Mapping) else None
        if not isinstance(daily, Mapping) or not isinstance(daily.get("time"), list) \
                or not isinstance(data.get("utc_offset_seconds"), int):
            raise WeatherError("upstream_unavailable", endpoint="forecast", reason="bad_shape")
        return dict(data)

    def geocode(self, name: str, *, country_code: str | None = None, language: str = "ko",
                count: int = 5) -> list[dict]:
        """지역 이름 → 후보(Open-Meteo 지오코딩 results). 없으면 []. 같은 이름은 프로세스 안에서 한 번만 묻는다."""
        key = (name, country_code)
        if key in self._geocode_cache:
            return list(self._geocode_cache[key])
        params = {"name": name, "count": str(count), "language": language, "format": "json"}
        if country_code:
            params["countryCode"] = country_code
        data = self._get(GEOCODING_URL, params, "geocoding")
        if not isinstance(data, Mapping):
            raise WeatherError("upstream_unavailable", endpoint="geocoding", reason="bad_shape")
        results = [r for r in data.get("results") or [] if isinstance(r, Mapping)]   # 결과가 없으면 results 키가 없음
        self._geocode_cache[key] = [dict(r) for r in results]
        return [dict(r) for r in results]

    def _get(self, url: str, params: Mapping[str, str], endpoint: str) -> Any:
        if url not in ALLOWED_URLS:
            raise PermissionError(f"허용 목록에 없는 날씨 주소입니다: {endpoint}")
        for attempt in range(1 + self.retries):
            last = attempt == self.retries
            try:
                return self._once(url, params, endpoint)
            except _Retryable as e:
                if last:
                    raise WeatherError("upstream_unavailable", endpoint=endpoint, status=e.status,
                                       reason=e.reason) from None
        raise AssertionError("도달하지 않음")

    def _once(self, url: str, params: Mapping[str, str], endpoint: str) -> Any:
        try:
            with self._http.stream("GET", url, params=params) as resp:
                status = resp.status_code
                if status >= 500:
                    raise _Retryable(status, "server_error")
                if status == 429:
                    raise WeatherError("upstream_unavailable", endpoint=endpoint, status=status, reason="rate_limited")
                if 300 <= status < 400:
                    raise WeatherError("upstream_unavailable", endpoint=endpoint, status=status, reason="redirect")
                if status >= 400:
                    raise WeatherError("bad_request", endpoint=endpoint, status=status)
                body = self._read_limited(resp, endpoint)
        except httpx.ConnectTimeout:
            raise _Retryable(reason="connect_timeout") from None
        except httpx.TimeoutException as e:
            raise WeatherError("timeout", endpoint=endpoint, reason=type(e).__name__) from None
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError) as e:
            raise _Retryable(reason=type(e).__name__) from None
        except httpx.HTTPError as e:
            raise WeatherError("upstream_unavailable", endpoint=endpoint, reason=type(e).__name__) from None
        try:
            return json.loads(body)
        except ValueError:
            raise WeatherError("upstream_unavailable", endpoint=endpoint, reason="not_json") from None

    def _read_limited(self, resp: httpx.Response, endpoint: str) -> bytes:
        chunks, total = [], 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > self.max_bytes:
                raise WeatherError("upstream_unavailable", endpoint=endpoint, reason="too_large")
            chunks.append(chunk)
        return b"".join(chunks)


__all__ = ["ALLOWED_URLS", "ATTRIBUTION", "ATTRIBUTION_URL", "CONNECT_TIMEOUT", "DAILY_VARIABLES", "FORECAST_URL",
           "GEOCODING_URL", "MAX_FORECAST_DAYS", "OpenMeteoClient", "READ_TIMEOUT", "WeatherError", "WeatherMode"]

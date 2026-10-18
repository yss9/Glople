"""get_weather → Open-Meteo(M6, 오너 승인). tools/weather.py의 OpenMeteoClient만 통해 부른다.

1. 위치: 지역 해석기 → 좌표가 표에 있으면 그대로(해외 도시 82곳), 없으면 표의 이름으로 지오코딩(국내 시군 '강릉시', 광역시 '부산광역시',
   제주는 '제주시')하고 결과의 시도(admin1)가 해석기 시도와 같은 것을 고른다. 도(道)·나라 전체는 한 점 날씨가 뜻이 없어 부르지 않고
   시·군·도시 이름을 달라고 한다. 모호한 지역(광주)은 validate가 먼저 되묻고, 해석기가 모르는 이름은 밖으로 보내지 않는다.
2. 날짜: when(오늘·내일·이번 주말·다음 주, 없으면 오늘)을 **현지 날짜**로. 기준은 실행 문맥의 now(ToolContext.now, KST 시각)이고,
   예보 응답의 utc_offset_seconds로 그 순간의 현지 날짜를 구한다(밴쿠버는 KST 낮 12시면 아직 전날). 이번 주말 = 이번 토·일
   (오늘이 일요일이면 오늘만), 다음 주 = 다음 월~일.
3. 답: 수치는 모두 코드가 만든다(카드·템플릿 답). LLM 답변 경로는 날씨를 늘 뺀다(answer_input.TEMPLATE_ONLY_TOOLS).
   카드에는 출처 'Weather data by Open-Meteo.com (CC BY 4.0)'를 늘 붙인다.
4. 실패: 타임아웃·연결·5xx·응답 모양 → ToolBackendError(get_weather, 코드) → ToolNode가 오류 봉투(정해진 안내 문장)로.
"""
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from glople_agent.answer import templates as tpl
from glople_agent.tools.args import GetWeatherArgs
from glople_agent.tools.backend import BackendResult, ToolContext
from glople_agent.tools.envelope import ToolBackendError
from glople_agent.tools.regions import Region, RegionResolution, resolution_summary
from glople_agent.tools.regions import resolve as default_resolve
from glople_agent.tools.weather import ATTRIBUTION, ATTRIBUTION_URL, OpenMeteoClient, WeatherError

TOOL = "get_weather"
DEFAULT_WHEN = "오늘"
# 현지 '오늘'부터 받을 일수(현지 요일이 KST와 하루 달라도 덮도록 넉넉히)
FORECAST_DAYS = {"오늘": 1, "내일": 2, "이번 주말": 7, "다음 주": 14}
METRO_SUFFIXES = ("특별시", "광역시", "특별자치시")
JEJU = "제주특별자치도"
WEEKDAYS = "월화수목금토일"

WMO_KO: dict[int, str] = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림", 45: "안개", 48: "안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비", 56: "어는 이슬비", 57: "어는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 66: "어는 비", 67: "어는 비",
    71: "약한 눈", 73: "눈", 75: "강한 눈", 77: "싸락눈", 80: "약한 소나기", 81: "소나기", 82: "강한 소나기",
    85: "눈 소나기", 86: "강한 눈 소나기", 95: "뇌우", 96: "우박을 동반한 뇌우", 99: "우박을 동반한 뇌우",
}

NOTE_TOO_WIDE_PROVINCE = "도 전체 날씨는 한 곳으로 정하기 어려워요. 시·군 이름으로 물어봐 주세요."
NOTE_TOO_WIDE_COUNTRY = "나라 전체 날씨는 한 곳으로 정하기 어려워요. 도시 이름으로 물어봐 주세요."
NOTE_NOT_FOUND = "그 지역의 위치를 찾지 못해 날씨를 가져오지 못했어요."
NOTE_JEJU = "제주는 제주시 기준이에요."
NOTE_NO_DATES = "그 날짜의 예보가 아직 없어요."
NOTE_FIXTURE = "오프라인 가짜 날씨(fixture)라 실제 날씨가 아니에요."

Resolve = Callable[[str], RegionResolution]


@dataclass(frozen=True)
class WeatherPlace:
    name: str            # 카드에 보일 이름(해석기 이름 또는 지오코딩 이름 + 시도)
    lat: float
    lng: float
    source: str          # "regions"(해석기 표 좌표) | "geocoding"(Open-Meteo 지오코딩)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "lat": self.lat, "lng": self.lng, "source": self.source}


def wmo_summary(code: Any) -> str | None:
    return WMO_KO.get(code) if isinstance(code, int) and not isinstance(code, bool) else None


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def date_label(d: date) -> str:
    return f"{d.month}월 {d.day}일({WEEKDAYS[d.weekday()]})"


def dates_for(when: str, today: date) -> list[date]:
    """when → 현지 날짜 목록. today는 그 지역의 현지 오늘."""
    wd = today.weekday()   # 월 0 … 일 6
    if when == "내일":
        return [today + timedelta(days=1)]
    if when == "이번 주말":
        if wd == 6:
            return [today]
        start = today + timedelta(days=5 - wd) if wd < 5 else today
        return [start, start + timedelta(days=1)]
    if when == "다음 주":
        monday = today + timedelta(days=7 - wd)
        return [monday + timedelta(days=i) for i in range(7)]
    return [today]


def local_today(now: datetime, utc_offset_seconds: int) -> date:
    """실행 문맥 now(시간대 있는 시각)의 그 순간, utc_offset인 곳의 날짜."""
    aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone(timedelta(seconds=utc_offset_seconds))).date()


def _float(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _geocode_name(region: Region) -> tuple[str | None, list[str]]:
    """국내 지역 → 지오코딩에 보낼 표의 이름(코드가 가진 글)과 note. 도·나라 전체면 (None, 이유)."""
    if region.level == "SIGUNGU":
        return region.sigungu, []
    if region.level == "SIDO":
        if region.label.endswith(METRO_SUFFIXES):
            return region.label, []
        if region.label == JEJU:
            return "제주시", [NOTE_JEJU]
        return None, [NOTE_TOO_WIDE_PROVINCE]
    return None, [NOTE_TOO_WIDE_COUNTRY]


def _pick_domestic(results: list[dict], region: Region, resolve: Resolve) -> dict | None:
    kr = [r for r in results if r.get("country_code") == "KR"
          and _float(r.get("latitude")) is not None and _float(r.get("longitude")) is not None]
    for r in kr:   # 시도가 같은 것(같은 이름 고성군 두 곳 → 해석기 시도로). admin1 표기('강원도')는 해석기 별칭으로 맞춤
        admin1 = r.get("admin1")
        found = resolve(admin1) if isinstance(admin1, str) and admin1 else None
        if found is not None and found.region is not None and found.region.sido == region.sido:
            return r
    return kr[0] if len(kr) == 1 else None


def locate(client: OpenMeteoClient, res: RegionResolution,
           resolve: Resolve = default_resolve) -> tuple[WeatherPlace | None, list[str]]:
    if res.status == "ambiguous":
        return None, [tpl.clarify_region_choice(res.candidate_labels())]
    region = res.region
    if region is None:
        return None, [NOTE_NOT_FOUND]
    if region.lat is not None and region.lng is not None:
        return WeatherPlace(region.label, region.lat, region.lng, "regions"), []
    if region.kind != "DOMESTIC":
        return None, [NOTE_TOO_WIDE_COUNTRY] if region.level == "COUNTRY" else [NOTE_NOT_FOUND]
    name, notes = _geocode_name(region)
    if name is None:
        return None, notes
    picked = _pick_domestic(client.geocode(name, country_code="KR"), region, resolve)
    if picked is None:
        return None, [NOTE_NOT_FOUND]
    admin1 = picked.get("admin1") if isinstance(picked.get("admin1"), str) else None
    shown = " ".join(x for x in (admin1 if admin1 != picked.get("name") else None, picked.get("name")) if x)
    return WeatherPlace(shown or name, float(picked["latitude"]), float(picked["longitude"]), "geocoding"), notes


def _value(daily: Mapping[str, Any], key: str, i: int) -> Any:
    seq = daily.get(key)
    return seq[i] if isinstance(seq, list) and i < len(seq) else None


def day_items(data: Mapping[str, Any], wanted: list[date]) -> list[dict]:
    daily = data.get("daily") or {}
    times = [t for t in daily.get("time") or [] if isinstance(t, str)]
    out = []
    for d in wanted:
        iso = d.isoformat()
        if iso not in times:
            continue
        i = times.index(iso)
        tmax, tmin = _float(_value(daily, "temperature_2m_max", i)), _float(_value(daily, "temperature_2m_min", i))
        pop = _float(_value(daily, "precipitation_probability_max", i))
        code = _value(daily, "weather_code", i)
        out.append({"date": iso, "label": date_label(d), "summary": wmo_summary(code),
                    "tmin": round_half_up(tmin) if tmin is not None else None,
                    "tmax": round_half_up(tmax) if tmax is not None else None,
                    "pop": round_half_up(pop) if pop is not None else None,
                    "code": code if isinstance(code, int) and not isinstance(code, bool) else None})
    return out


def get_weather(client: OpenMeteoClient, args: GetWeatherArgs, ctx: ToolContext,
                resolve: Resolve = default_resolve) -> BackendResult:
    """날씨 모드 off(클라이언트 없음)는 SpringBackend가 not_ready.weather_off로 보낸다(여기 오지 않음)."""
    res = resolve(args.region)
    extra: dict[str, Any] = {"region": dict(resolution_summary(res))}
    when = args.when or DEFAULT_WHEN
    extra.update(when=when, provider=client.provider)
    fixture_notes = [NOTE_FIXTURE] if client.is_fixture else []
    try:
        place, notes = locate(client, res, resolve)
        if place is None:
            return BackendResult("empty", notes=notes, extra=extra)
        data = client.forecast(place.lat, place.lng, FORECAST_DAYS.get(when, 1))
    except WeatherError as e:   # 원래 예외(좌표·이름이 든 URL)는 잇지 않는다
        raise ToolBackendError(TOOL, e.code) from None
    today = local_today(ctx.now, int(data["utc_offset_seconds"]))
    wanted = dates_for(when, today)
    # 예보를 받았으면 출처를 늘 붙인다(Open-Meteo CC BY 4.0 조건, 카드에 그대로)
    extra.update(location=place.to_dict(), timezone=data.get("timezone"), localToday=today.isoformat(),
                 dates=[d.isoformat() for d in wanted], attribution=ATTRIBUTION, attributionUrl=ATTRIBUTION_URL)
    items = day_items(data, wanted)
    if not items:
        return BackendResult("empty", notes=[NOTE_NO_DATES, *notes, *fixture_notes], extra=extra)
    llm = [{k: i[k] for k in ("date", "summary", "tmin", "tmax", "pop")} for i in items]
    return BackendResult("ok", llm, items, [*notes, *fixture_notes], extra)


__all__ = ["DEFAULT_WHEN", "FORECAST_DAYS", "WMO_KO", "WeatherPlace", "date_label", "dates_for", "day_items",
           "get_weather", "local_today", "locate", "wmo_summary"]

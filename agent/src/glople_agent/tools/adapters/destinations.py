"""search_destinations · get_destination_detail → Spring S1(공개, docs/travel-api.md 'S1', M6).

| 도구 | 요청 | 어댑터가 하는 일 |
|---|---|---|
| search_destinations | GET /api/destinations?region=&category=&query=&limit=&seed= | 지역 해석 → region 글(시군은 '시도 시군'), 음식점(국내)·숙박은 부르지 않음, 넓은 지역은 같은 location 2곳까지, 해외 도시는 도심 반경으로 거름 |
| get_destination_detail | GET /api/destinations/lookup?name= → GET /api/destinations/{id} | 한 곳이면 상세, 같은 이름이 여러 지역이면 후보 카드 + 되묻기('어느 지역의 ○○인가요?'), '강화 마니산'처럼 지역 낱말이 붙으면 그 지역으로 좁힘 |

- 응답 항목 칸(오너가 2026-10-18 실제 서버로 확인): category, copyrightType, country, id, imageUrl, info, infoKind, lat, license,
  lng, location, name, source, sourceUrl. 상세는 address, regionCode, sigunguCode, summary를 더한다.
- Type3(text_policy): TOUR_API DESCRIPTION 원문(info)은 카드에만. LLM 항목은 이름·지역(where)·분류(type)와 코드가 만든 짧은 사실
  (상세의 summary 60자 = Glople가 수집 필드로 만든 소개, 주소)뿐이다. 새 dict를 허용 필드로만 만들어 서버가 칸을 늘려도 새지 않음.
- 해외 지역 표기는 고르지 않다(오너 확인: region=도쿄 → location '일본 도쿄도'라 가잔 열도·이즈오섬 같은 섬이 먼저). 그래서 해외
  도시는 지역 해석기의 도심 좌표·반경(overseas_cities.json radiusKm) 안의 행만 남기고, 도시 이름으로 맞는 행이 모자라면(삿포로 →
  '일본 홋카이도') 나라 이름으로 한 번 더 찾아 같은 반경으로 거른다. 반경 안이 하나도 없으면 서버 순서 그대로 + note.
- 모델 값은 query 파라미터(httpx 인코딩)로만 간다. region은 해석기 이름(코드 소유 글)이고, 해석기가 모르는 지역만 정제한 원래 글을
  Spring 규칙(1~3낱말, 글자·숫자·가운뎃점·하이픈·마침표, 50자)에 맞을 때 보낸다. 상세 id는 lookup 응답의 양의 정수만.
"""
import re
from collections.abc import Callable, Iterable
from typing import Any

from glople_agent.answer import templates as tpl
from glople_agent.sanitize import clean_text
from glople_agent.tools.adapters.common import rows_of
from glople_agent.tools.args import GetDestinationDetailArgs, SearchDestinationsArgs
from glople_agent.tools.backend import BackendResult, ToolContext
from glople_agent.tools.regions import (Region, RegionResolution, distance_km, location_in_region,
                                        region_query_label, resolution_summary)
from glople_agent.tools.regions import resolve as default_resolve
from glople_agent.tools.spring import SpringClient
from glople_agent.tools.text_policy import LLM_NAME_CHARS, LLM_WHERE_CHARS, SUMMARY_CHARS, place_items, text_policy

SEARCH = "destinations"
LOOKUP = "destination_lookup"
DETAIL = "destination_get"

DEFAULT_LIMIT = 5            # 인자에 limit이 없을 때(LLM 항목 상한과 같음)
MAX_LIMIT = 10               # 카드에 보일 최대(LLM에는 envelope가 5개까지)
SERVER_MAX_LIMIT = 50        # Spring limit 1~50
OVERFETCH = 4                # 같은 location 상한·반경으로 거를 때 더 받아 오는 배수
PLACES_PER_LOCATION = 2      # 넓은 지역(시도·나라)에서 같은 location(시군)은 2곳까지(recommend_by_keywords와 같음)
QUERY_CHARS = 50
NAME_CHARS = 100             # lookup name 상한(Spring 100자)
CANDIDATES_SHOWN = 5
ADDRESS_CHARS = 60

NOT_COLLECTED = ("음식점", "숙박")   # 국내는 관광 타입 12·14·15·28만 수집(domestic-tour-sync.md). 해외 숙박도 없음
WIDE_LEVELS = ("SIDO", "COUNTRY")
_SPRING_REGION = re.compile(r"^[0-9A-Za-z가-힣·.\-]+$")

Resolve = Callable[[str], RegionResolution]


# --- notes(사실로 된 제한 사항, 코드가 만든 문장) --------------------------------------------------

def note_limit(limit: int) -> str:
    return f"한 번에 {limit}곳까지 보여드려요."


def note_query_relaxed(query: str) -> str:
    return f"'{query}' 조건에 맞는 이름·소개를 찾지 못해 조건 없이 골랐어요."


def note_radius(city: str, radius: float) -> str:
    return f"{city} 도심 {radius:g}km 안의 여행지가 없어 {city} 이름이 붙은 지역 전체에서 골랐어요."


def note_country_widened(city: str, country: str) -> str:
    return f"{city} 이름으로 등록된 곳이 적어 {country} 전체에서 {city} 도심 가까운 곳을 함께 골랐어요."


def note_overseas_food() -> str:
    return "해외 음식점은 '음식' 키워드가 붙은 여행지로 고른 결과예요(맛집 목록이 아니에요)."


def note_ambiguous_region(labels: Iterable[str]) -> str:
    names = list(labels)
    return f"{tpl.josa(names[0], '과/와')} {', '.join(names[1:])} 모두에서 찾았어요." if len(names) > 1 else ""


NOTE_REGION_UNREADABLE = "지역 이름을 알아보지 못해 찾지 못했어요."


# --- 공용 ------------------------------------------------------------------------------------------

def _float(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _spring_region_text(text: str) -> str | None:
    """해석기가 모르는 지역의 원래 글을 Spring 규칙에 맞을 때만(1~3낱말, 50자, 허용 글자)."""
    t = clean_text(text, 50).strip()
    words = t.split()
    if not 1 <= len(words) <= 3 or not all(_SPRING_REGION.match(w) for w in words):
        return None
    return t


def destination_items(row: dict) -> tuple[dict, dict]:
    """S1 행 하나 → (LLM 항목, 카드 항목). place_items(Type3 판정)에 분류·이미지·좌표를 더한다."""
    llm, card = place_items(row)
    if isinstance(row.get("category"), str) and row["category"]:
        llm["type"] = row["category"]
    card.update(category=row.get("category"), imageUrl=row.get("imageUrl"), lat=_float(row.get("lat")),
                lng=_float(row.get("lng")))
    return llm, card


def _within(row: dict, center: Region) -> bool | None:
    lat, lng = _float(row.get("lat")), _float(row.get("lng"))
    if lat is None or lng is None or center.lat is None or center.lng is None or center.radius_km is None:
        return None
    return distance_km(center.lat, center.lng, lat, lng) <= center.radius_km


def cap_per_location(rows: list[dict], want: int, per_location: int = PLACES_PER_LOCATION) -> list[dict]:
    """같은 location은 per_location곳까지 순위대로 고르고, 자리가 남으면 건너뛴 행으로 순위대로 채운다(S0 지역 상한과 같은 규칙).
    고른 행은 원래 순위 순서로 돌려준다."""
    picked: list[int] = []
    skipped: list[int] = []
    count: dict[str, int] = {}
    for i, row in enumerate(rows):
        loc = row.get("location") or row.get("country") or ""
        if count.get(loc, 0) < per_location:
            count[loc] = count.get(loc, 0) + 1
            picked.append(i)
        else:
            skipped.append(i)
        if len(picked) == want:
            break
    picked += skipped[: max(0, want - len(picked))]
    return [rows[i] for i in sorted(picked)]


# --- search_destinations ---------------------------------------------------------------------------

def search_params(args: SearchDestinationsArgs, res: RegionResolution, ctx: ToolContext, *,
                  limit: int) -> dict[str, str] | None:
    """Spring query 파라미터. region은 해석기 이름(모호·모름이면 Spring 규칙에 맞는 원래 글), 못 만들면 None(부르지 않음)."""
    region = region_query_label(res.region) if res.region is not None else _spring_region_text(args.region)
    if not region:
        return None
    params = {"region": region, "limit": str(limit)}
    if args.category:
        params["category"] = args.category
    if args.query and (q := clean_text(args.query, QUERY_CHARS).strip()):
        params["query"] = q
    if ctx.seed:   # 대화마다 정한 seed(같은 대화 안에서 동점 순서가 흔들리지 않게). 0이면 서버 기본(오늘 날짜)
        params["seed"] = str(ctx.seed)
    return params


def search_destinations(client: SpringClient, args: SearchDestinationsArgs, ctx: ToolContext,
                        resolve: Resolve = default_resolve) -> BackendResult:
    res = resolve(args.region)
    region = res.region
    extra: dict[str, Any] = {"region": dict(resolution_summary(res)),
                             "categoryCollected": args.category not in NOT_COLLECTED}
    if args.category == "숙박" or (args.category == "음식점" and res.kind != "OVERSEAS"):
        return BackendResult("unsupported", extra=extra)   # 부르지 않음(국내 음식점·숙박은 수집 안 함, 해외 숙박도 없음)
    notes: list[str] = []
    want = min(args.limit or DEFAULT_LIMIT, MAX_LIMIT)
    if args.limit and args.limit > MAX_LIMIT:
        notes.append(note_limit(MAX_LIMIT))
    city = region if region is not None and region.level == "CITY" and region.radius_km else None
    wide = region is None or region.level in WIDE_LEVELS
    fetch = min(SERVER_MAX_LIMIT, want * OVERFETCH) if (wide or city) else want
    if res.status == "ambiguous":
        notes.append(note_ambiguous_region(res.candidate_labels()))
    params = search_params(args, res, ctx, limit=fetch)
    if params is None:
        return BackendResult("empty", notes=[NOTE_REGION_UNREADABLE], extra=extra)
    extra["request"] = {k: v for k, v in params.items() if k != "seed"}

    def fetch_rows(p: dict[str, str]) -> list[dict]:
        return rows_of(client.call(SEARCH, params=p).data)

    rows = fetch_rows(params)
    if not rows and "query" in params:   # 학습 query는 '반려견과 갈 수 있는' 같은 조건이라 이름·소개에 없을 때가 많음
        rows = fetch_rows({k: v for k, v in params.items() if k != "query"})
        if rows:
            notes.append(note_query_relaxed(params["query"]))
            params = {k: v for k, v in params.items() if k != "query"}
    if city is not None:
        rows, city_notes = _near_city(rows, city, want, params, fetch_rows)
        notes += city_notes
    picked = cap_per_location(rows, want) if wide else rows[:want]
    if not picked:
        return BackendResult("empty", notes=notes, extra=extra)
    if args.category == "음식점":   # 해외만 여기 옴(국내는 위에서 부르지 않음)
        notes.append(note_overseas_food())
    pairs = [destination_items(r) for r in picked]
    return BackendResult("ok", [p[0] for p in pairs], [p[1] for p in pairs], notes, {**extra, "found": len(rows)})


def _near_city(rows: list[dict], city: Region, want: int, params: dict[str, str],
               fetch_rows: Callable[[dict[str, str]], list[dict]]) -> tuple[list[dict], list[str]]:
    """해외 도시: 도심 반경 안 행만(좌표 없는 행은 뒤로). 모자라면 나라 이름으로 한 번 더 찾아 같은 반경으로 거른다."""
    notes: list[str] = []
    inside = [r for r in rows if _within(r, city) is True]
    unknown = [r for r in rows if _within(r, city) is None]
    if len(inside) < want and city.country:
        wider = fetch_rows({**params, "region": city.country, "limit": str(SERVER_MAX_LIMIT)})
        seen = {r.get("id") for r in inside}
        more = [r for r in wider if _within(r, city) is True and r.get("id") not in seen]
        if more:
            inside += more
            notes.append(note_country_widened(city.label, city.country))
    if inside:
        return inside + unknown, notes
    if rows:
        notes.append(note_radius(city.label, city.radius_km or 0))
    return rows, notes


# --- get_destination_detail ------------------------------------------------------------------------

def _norm(name: str) -> str:
    return "".join((name or "").split()).casefold()


def split_region_word(name: str, resolve: Resolve = default_resolve) -> tuple[Region, str] | None:
    """'강화 마니산'·'마니산 강화' → (강화군, '마니산'). 앞이나 뒤 낱말이 지역 해석기로 한 곳이면."""
    words = name.split()
    if len(words) < 2:
        return None
    for word, rest in ((words[0], words[1:]), (words[-1], words[:-1])):
        res = resolve(word)
        if res.status == "ok" and res.region is not None:
            return res.region, " ".join(rest)
    return None


def _lookup(client: SpringClient, name: str) -> list[dict]:
    rows = rows_of(client.call(LOOKUP, params={"name": name}).data)
    seen: set = set()
    out = []
    for r in rows:
        if r.get("id") in seen:
            continue
        seen.add(r.get("id"))
        out.append(r)
    return out


def _place_label(row: dict) -> str:
    return row.get("location") or row.get("country") or "위치 모름"


def candidates_result(name: str, rows: list[dict], extra: dict) -> BackendResult:
    """같은 이름이 여러 지역에 있음 → 후보 카드 + 되묻기(after_collect가 다른 결과가 없으면 clarify 노드로 보냄)."""
    shown = rows[:CANDIDATES_SHOWN]
    question = tpl.clarify_place_choice(name, [_place_label(r) for r in shown])
    items = [{"name": clean_text(r.get("name"), LLM_NAME_CHARS), "where": clean_text(_place_label(r), LLM_WHERE_CHARS),
              **({"type": r["category"]} if isinstance(r.get("category"), str) else {})} for r in shown]
    cards = [{"id": r.get("id"), "name": r.get("name"), "country": r.get("country"), "location": r.get("location"),
              "category": r.get("category"), "imageUrl": r.get("imageUrl"), "lat": _float(r.get("lat")),
              "lng": _float(r.get("lng")), "candidate": True} for r in shown]
    return BackendResult("ok", items, cards, [question],
                         {**extra, "needsClarification": True, "clarify": question, "found": len(rows),
                          "candidates": [{"id": r.get("id"), "name": r.get("name"), "location": r.get("location")}
                                         for r in shown]})


def detail_items(row: dict) -> tuple[dict, dict]:
    """상세 한 곳 → (LLM 항목, 카드 항목). LLM에는 이름·지역·분류 + 코드가 만든 사실(summary 60자, 주소)만."""
    llm, card = destination_items(row)
    summary = row.get("summary")
    # summary는 Glople가 수집 필드(지역·관광 타입·분류)로 만든 소개 = FREE_FACTS(11절 표: 틀 문장 60자까지 LLM 가능).
    # 출처를 모르는 행(source null)의 글은 판정할 수 없으니 넣지 않는다. infoKind가 SUMMARY면 note에 이미 같은 글이 있다
    if isinstance(summary, str) and summary and text_policy(row.get("source"), "SUMMARY", None) == "FREE_FACTS":
        short = clean_text(summary, SUMMARY_CHARS)
        if llm.get("note") != short:
            llm["summary"] = short
    if isinstance(row.get("address"), str) and row["address"]:
        llm["address"] = clean_text(row["address"], ADDRESS_CHARS)
    card.update(address=row.get("address"), summary=summary, regionCode=row.get("regionCode"),
                sigunguCode=row.get("sigunguCode"))
    return llm, card


def get_destination_detail(client: SpringClient, args: GetDestinationDetailArgs, ctx: ToolContext,
                           resolve: Resolve = default_resolve) -> BackendResult:
    name = clean_text(args.name, NAME_CHARS).strip()
    extra: dict[str, Any] = {}
    if not name:
        return BackendResult("empty", extra=extra)
    rows = _lookup(client, name)
    if not rows and (split := split_region_word(name, resolve)) is not None:
        region, rest = split   # '강화 마니산' → 마니산을 찾아 강화군 행만
        extra["regionHint"] = region.label
        rows = [r for r in _lookup(client, rest)
                if location_in_region(r.get("location"), region, country=r.get("country"))]
        name = rest
    if not rows:
        return BackendResult("empty", extra=extra)
    if len({(_norm(_place_label(r))) for r in rows}) > 1:
        return candidates_result(name, rows, extra)
    row = rows[0]
    rid = row.get("id")
    if isinstance(rid, int) and not isinstance(rid, bool) and rid > 0:
        resp = client.call(DETAIL, path_params={"id": rid}, accept={404})
        if resp.status == 200 and isinstance(resp.data, dict):
            row = resp.data
    llm, card = detail_items(row)
    return BackendResult("ok", [llm], [card], extra={**extra, "found": len(rows)})


__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "NOT_COLLECTED", "PLACES_PER_LOCATION", "cap_per_location",
           "candidates_result", "destination_items", "detail_items", "get_destination_detail", "search_destinations",
           "search_params", "split_region_word"]

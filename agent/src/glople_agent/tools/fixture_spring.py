"""가짜 Spring(M0 fixture → M1 HTTP 흉내): tests/fixtures/backend/*.json으로 공개 API 8개(M6에서 S1 셋 추가)를 흉내 내는
httpx.MockTransport.

SpringClient + 어댑터가 실제 서버와 같은 HTTP 길을 타게 한다(네트워크 없음, 8080에 접속하지 않음). 서버 동작은
Java 코드를 읽고 맞췄다(src/main/java/proj/travien/...).

| 경로 | 흉내 낸 동작 |
|---|---|
| POST /api/search | KeywordMask.fromKeywords(목록 밖 키워드는 조용히 버림, trim) → 마스크가 비면 200 [] → 일치 비트 수↓, id↑, 21건, 0점 제외 |
| POST /api/info-recommend | age·gender·mbti 중 하나라도 없거나 생년월일 형식이 틀리면 500(@ControllerAdvice 없음). 같은 gender 문자열, 생년월일 ±5년(문자열 BETWEEN), 6차원 코사인 유사도↓·id↓ 12명 |
| GET /api/posts/search?query= | 빈 query·결과 없음 → 204. title·body·AddressTitle LIKE(대소문자 무시) |
| GET /api/users/search-agents-by-hashtag?hashtag= | hashtags 목록에 완전 일치(# 포함), 없으면 204 |
| GET /api/users/agents[?country=] | agentCountry 완전 일치(없으면 200 []), country가 없으면 전체 |
| GET /api/destinations (M6 S1) | region(1~3낱말, 접미사·줄임말 규칙 RegionPatterns) · category(국내 = 관광 타입, 해외 = 근사, 숙박 해외 없음) · query(이름·소개 포함) · limit 1~50(기본 20) · seed(기본 오늘 yyyyMMdd). 순서: 이름 = query → 이름에 query → 설명문 → 이미지 → CRC32(id:seed) → id. 틀린 값은 400 errors JSON |
| GET /api/destinations/lookup?name= | 이름이 같음 · 이름 뒤 괄호 구분 · 공백 뺀 이름이 같음, 최대 10곳(같은 이름 먼저 → 설명문 → 이미지 → id). 빈 이름·100자 초과는 400 |
| GET /api/destinations/{id} | 목록 칸 + address·regionCode·sigunguCode·summary, 없는 id는 404 JSON |

S1 응답 칸은 오너가 2026-10-18 실제 서버로 확인한 모양 그대로다(docs/travel-api.md, api-notes M6). fixture 행은 DB 열처럼
description·summary를 따로 두고 여기서 info = COALESCE(description, summary)로 바꿔 내려 준다.
requests에 받은 요청(메서드·경로·query·본문·Authorization 유무)을 남긴다(cli tool이 보여 줌, 테스트가 토큰 없음을 확인).
"""
import json
import math
import re
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from glople_agent import contract
from glople_agent.config import AGENT_DIR

DEFAULT_FIXTURES_DIR = AGENT_DIR / "tests" / "fixtures" / "backend"
FIXTURE_BASE_URL = "http://spring.fixture"   # MockTransport가 가로챔(실제 주소 아님)
KEYWORD_LIMIT = 21
ROUTES_TOP_K = 12
BIRTH_YEAR_WINDOW = 5

# --- S1 여행지(docs/travel-api.md 'S1') ---------------------------------------------------------
DESTINATION_KEYS = ("category", "copyrightType", "country", "id", "imageUrl", "info", "infoKind", "lat", "license",
                    "lng", "location", "name", "source", "sourceUrl")                 # 목록·이름 찾기 항목
DETAIL_EXTRA_KEYS = ("address", "regionCode", "sigunguCode", "summary")               # 상세에만
CATEGORIES = ("관광지", "문화시설", "축제", "레포츠", "음식점", "숙박")
DESTINATIONS_DEFAULT_LIMIT = 20
LOOKUP_LIMIT = 10
# RegionPatterns: 바탕 이름 + (접미사 하나 또는 없음). 떼면 한 글자만 남으면 떼지 않음
REGION_SUFFIXES = ("특별자치도", "특별자치시", "특별시", "광역시", "자치도", "자치시", "도", "시", "군", "구", "부", "현", "주", "성")
REGION_ABBREVIATIONS = {"충북": "충청북", "충남": "충청남", "전북": "전라북", "전남": "전라남", "경북": "경상북", "경남": "경상남"}
REGION_COUNTRY_WORDS = {"한국": "대한민국", "국내": "대한민국"}
_REGION_WORD = re.compile(r"^[0-9A-Za-z가-힣·.\-]+$")
_DETAIL_PATH = re.compile(r"^/api/destinations/([0-9]+)$")


def _bad_request(path: str, message: str, errors: list[dict]) -> httpx.Response:
    return _json(400, {"status": 400, "error": "BAD_REQUEST", "message": message, "path": path, "errors": errors})


def _region_bases(word: str) -> set[str]:
    bases = {word}
    for suffix in REGION_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            bases.add(word[: -len(suffix)])
    for base in list(bases):
        if base in REGION_ABBREVIATIONS:
            bases.add(REGION_ABBREVIATIONS[base])
        for short, full in REGION_ABBREVIATIONS.items():
            if base == full:
                bases.add(short)
        if base in REGION_COUNTRY_WORDS:
            bases.add(REGION_COUNTRY_WORDS[base])
    return bases


@lru_cache(maxsize=512)
def _region_word_forms(given: str) -> frozenset[str]:
    """받은 낱말 하나가 맞을 수 있는 글 낱말들: 바탕 이름 + (접미사 하나 또는 없음)."""
    return frozenset(b + s for b in _region_bases(given) for s in ("", *REGION_SUFFIXES))


def region_matches(region: str, text: str) -> bool:
    """Spring RegionPatterns 흉내: region의 낱말마다 '나라 location' 글의 한 낱말이 바탕 이름 + (접미사 또는 없음)이어야 함."""
    words = set(text.split())
    return all(words & _region_word_forms(given) for given in region.split())


def destination_info(row: dict) -> tuple[str | None, str | None]:
    """(info, infoKind): 실제 설명문이 있으면 그것(DESCRIPTION), 없으면 분류 소개(SUMMARY) — COALESCE(info, summary)."""
    if row.get("description"):
        return row["description"], "DESCRIPTION"
    if row.get("summary"):
        return row["summary"], "SUMMARY"
    return None, None


def destination_item(row: dict, *, detail: bool = False) -> dict:
    info, kind = destination_info(row)
    out = {k: row.get(k) for k in DESTINATION_KEYS}
    out.update(info=info, infoKind=kind)
    if detail:
        out.update({k: row.get(k) for k in DETAIL_EXTRA_KEYS})
    return out


def tie_hash(row_id: int, seed: int) -> int:
    """MySQL CRC32(CONCAT(id, ':', seed)) = Java DestinationOrdering.tieHash(zlib.crc32와 같은 CRC-32)."""
    return zlib.crc32(f"{row_id}:{seed}".encode())


@dataclass
class SeenRequest:
    method: str
    path: str
    params: dict[str, str]
    body: Any
    has_authorization: bool


def _json(status: int, data: Any) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(data, ensure_ascii=False).encode(),
                          headers={"Content-Type": "application/json"})


def _parse_birth(value: str) -> tuple[date, str]:
    fmt = "%Y-%m-%d" if "-" in value else "%Y%m%d"
    return datetime.strptime(value, fmt).date(), fmt


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:   # 2월 29일 → 2월 28일(Java LocalDate.plusYears와 같음)
        return d.replace(year=d.year + years, day=28)


def _age(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


@dataclass
class FixtureSpring:
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR
    today: Callable[[], date] = field(default=lambda: date.today())
    requests: list[SeenRequest] = field(default_factory=list)
    _cache: dict[str, dict] = field(default_factory=dict)

    def fixture(self, name: str) -> dict:
        if name not in self._cache:
            self._cache[name] = json.loads((Path(self.fixtures_dir) / f"{name}.json").read_text(encoding="utf-8"))
        return self._cache[name]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        params = {k: v for k, v in request.url.params.items()}
        body = json.loads(request.content) if request.content else None
        self.requests.append(SeenRequest(request.method, request.url.path, params, body,
                                         "authorization" in request.headers))
        route = (request.method, request.url.path)
        handlers = {
            ("POST", "/api/search"): self.keyword_search,
            ("POST", "/api/info-recommend"): self.info_recommend,
            ("GET", "/api/posts/search"): self.post_search,
            ("GET", "/api/users/search-agents-by-hashtag"): self.agents_by_hashtag,
            ("GET", "/api/users/agents"): self.agents,
            ("GET", "/api/destinations"): self.destinations,                  # M6 S1
            ("GET", "/api/destinations/lookup"): self.destination_lookup,
        }
        handler = handlers.get(route)
        if handler is None and request.method == "GET" and (m := _DETAIL_PATH.match(request.url.path)):
            return self.destination_get(int(m.group(1)), request.url.path)
        if handler is None:
            return _json(404, {"status": 404, "error": "Not Found", "path": request.url.path})
        return handler(params, body)

    # --- 경로별 ---------------------------------------------------------------
    def keyword_search(self, params: dict, body: Any) -> httpx.Response:
        words = (body or {}).get("keywords") or []
        known = {w.strip() for w in words if isinstance(w, str) and w.strip() in contract.KEYWORDS}
        if not known:
            return _json(200, [])
        fx = self.fixture("recommend_by_keywords")
        masks = {int(k): set(v) for k, v in fx["keyword_masks"].items()}
        scored = [(len(masks.get(r["id"], set()) & known), r) for r in fx["rows"]]
        rows = [r for n, r in sorted(scored, key=lambda x: (-x[0], x[1]["id"])) if n > 0][:KEYWORD_LIMIT]
        return _json(200, rows)

    def info_recommend(self, params: dict, body: Any) -> httpx.Response:
        user = body or {}
        try:
            age, gender, mbti = user["age"], user["gender"], user["mbti"]
            if age is None or gender is None or mbti is None:
                raise KeyError("age, gender and mbti are required")
            birth, fmt = _parse_birth(age)
            mbti_n = mbti.strip().upper()
            if len(mbti_n) < 4:
                raise ValueError("MBTI must contain four dimensions")
        except (KeyError, ValueError, TypeError, AttributeError):
            return _json(500, {"status": 500, "error": "Internal Server Error", "path": "/api/info-recommend"})
        lo = _shift_years(birth, -BIRTH_YEAR_WINDOW).strftime(fmt)
        hi = _shift_years(birth, BIRTH_YEAR_WINDOW).strftime(fmt)
        today = self.today()

        def vector(b: date, g: str, m: str) -> list[float]:
            return [_age(b, today) / 100.0, 0.0 if g.upper() == "M" else 1.0, 0.0 if m[0] == "E" else 1.0,
                    0.0 if m[1] == "S" else 1.0, 0.0 if m[2] == "T" else 1.0, 0.0 if m[3] == "J" else 1.0]

        def cosine(a: list[float], b: list[float]) -> float:
            na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
            return 0.0 if na == 0 or nb == 0 else sum(x * y for x, y in zip(a, b)) / (na * nb)

        me = vector(birth, gender, mbti_n)
        scored = []
        for u in self.fixture("recommend_similar_traveler_routes")["rows"]:
            if u.get("gender") != gender or not (lo <= (u.get("age") or "") <= hi):
                continue
            try:
                ub, _ = _parse_birth(u["age"])
            except (KeyError, ValueError):
                continue
            scored.append((cosine(me, vector(ub, u["gender"], (u.get("mbti") or "XXXX").upper())), u.get("id") or 0, u))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)   # 유사도↓, 같으면 id↓(Comparator.reversed)
        return _json(200, [u for _, _, u in scored[:ROUTES_TOP_K]])

    def post_search(self, params: dict, body: Any) -> httpx.Response:
        q = (params.get("query") or "").strip()
        if not q:
            return httpx.Response(204)
        ql = q.lower()
        rows = [p for p in self.fixture("search_reviews")["rows"]
                if any(ql in (p.get(k) or "").lower() for k in ("title", "body", "addressTitle"))]
        return _json(200, rows) if rows else httpx.Response(204)

    def agents_by_hashtag(self, params: dict, body: Any) -> httpx.Response:
        tag = params.get("hashtag") or ""
        found = [a for a in self.fixture("search_gloplers")["rows"] if tag in (a.get("hashtags") or [])]
        return _json(200, found) if found else httpx.Response(204)

    def agents(self, params: dict, body: Any) -> httpx.Response:
        rows = self.fixture("search_gloplers")["rows"]
        country = params.get("country")
        return _json(200, rows if country is None else [a for a in rows if a.get("agentCountry") == country])

    # --- M6 S1 여행지 -------------------------------------------------------------------------
    def _destination_rows(self) -> list[dict]:
        return self.fixture("destinations")["rows"]

    @staticmethod
    def _category_ok(row: dict, category: str | None) -> bool:
        if category is None:
            return True
        domestic = row.get("source") == "TOUR_API" or row.get("country") == "대한민국"
        if domestic:
            return row.get("category") == category        # 국내는 관광 타입(음식점·숙박은 수집 안 해 행이 없음)
        if category == "관광지":
            return True                                    # 해외 관광지 = 해외 전체
        if category == "숙박":
            return False                                   # 해외 숙박 없음
        return row.get("category") == category            # 키워드 마스크 근사(fixture는 행의 category로)

    def destinations(self, params: dict, body: Any) -> httpx.Response:
        path = "/api/destinations"
        errors = []
        region = (params.get("region") or "").strip()
        category = params.get("category")
        query = (params.get("query") or "").strip()
        if region and (len(region) > 50 or not 1 <= len(region.split()) <= 3
                       or not all(_REGION_WORD.match(w) for w in region.split())):
            errors.append({"field": "region", "code": "invalid_value",
                           "message": "region은 지역 이름 단어 1~3개(50자까지)여야 합니다."})
        if category is not None and category not in CATEGORIES:
            errors.append({"field": "category", "code": "unknown_value",
                           "message": "category는 관광지, 문화시설, 축제, 레포츠, 음식점, 숙박 중 하나여야 합니다."})
        if len(query) > 50:
            errors.append({"field": "query", "code": "too_long", "message": "query는 50자까지입니다."})
        try:
            limit = int(params.get("limit", DESTINATIONS_DEFAULT_LIMIT))
            if not 1 <= limit <= 50:
                raise ValueError
        except ValueError:
            errors.append({"field": "limit", "code": "out_of_range", "message": "limit는 1~50이어야 합니다."})
            limit = 0
        try:
            seed = int(params["seed"]) if "seed" in params else int(self.today().strftime("%Y%m%d"))
        except ValueError:
            errors.append({"field": "seed", "code": "invalid_value", "message": "seed는 정수여야 합니다."})
            seed = 0
        if errors:
            return _bad_request(path, "여행지 조회 값이 올바르지 않습니다.", errors)
        q = query.casefold()
        rows = [r for r in self._destination_rows()
                if (not region or region_matches(region, f"{r.get('country') or ''} {r.get('location') or ''}"))
                and self._category_ok(r, category)
                and (not q or q in (r.get("name") or "").casefold() or q in (r.get("summary") or "").casefold())]

        def key(r: dict) -> tuple:
            name = (r.get("name") or "").casefold()
            match = (0 if name == q else 1 if q in name else 2) if q else 0
            return (match, 0 if r.get("description") else 1, 0 if r.get("imageUrl") else 1,
                    tie_hash(r["id"], seed), r["id"])

        return _json(200, [destination_item(r) for r in sorted(rows, key=key)[:limit]])

    def destination_lookup(self, params: dict, body: Any) -> httpx.Response:
        name = (params.get("name") or "").strip()
        if not name or len(name) > 100:
            return _bad_request("/api/destinations/lookup", "여행지 이름이 올바르지 않습니다.",
                                [{"field": "name", "code": "invalid_value", "message": "name은 1~100자여야 합니다."}])
        bracket = re.compile(re.escape(name) + r"\s*\(.+\)")
        squashed = name.replace(" ", "")
        found = [r for r in self._destination_rows()
                 if r.get("name") == name or bracket.fullmatch(r.get("name") or "")
                 or (r.get("name") or "").replace(" ", "") == squashed]
        found.sort(key=lambda r: (0 if r.get("name") == name else 1, 0 if r.get("description") else 1,
                                  0 if r.get("imageUrl") else 1, r["id"]))
        return _json(200, [destination_item(r) for r in found[:LOOKUP_LIMIT]])

    def destination_get(self, dest_id: int, path: str) -> httpx.Response:
        row = next((r for r in self._destination_rows() if r["id"] == dest_id), None)
        if row is None:
            return _json(404, {"status": 404, "error": "NOT_FOUND", "message": "여행지를 찾을 수 없습니다.", "path": path})
        return _json(200, destination_item(row, detail=True))


__all__ = ["DEFAULT_FIXTURES_DIR", "DESTINATION_KEYS", "DETAIL_EXTRA_KEYS", "FIXTURE_BASE_URL", "FixtureSpring",
           "SeenRequest", "destination_info", "destination_item", "region_matches", "tie_hash"]

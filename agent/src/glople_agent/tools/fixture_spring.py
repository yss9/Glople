"""가짜 Spring(M0 fixture → M1 HTTP 흉내): tests/fixtures/backend/*.json으로 공개 API 5개를 흉내 내는 httpx.MockTransport.

SpringClient + 어댑터가 실제 서버와 같은 HTTP 길을 타게 한다(네트워크 없음, 8080에 접속하지 않음). 서버 동작은
Java 코드를 읽고 맞췄다(src/main/java/proj/travien/...).

| 경로 | 흉내 낸 동작 |
|---|---|
| POST /api/search | KeywordMask.fromKeywords(목록 밖 키워드는 조용히 버림, trim) → 마스크가 비면 200 [] → 일치 비트 수↓, id↑, 21건, 0점 제외 |
| POST /api/info-recommend | age·gender·mbti 중 하나라도 없거나 생년월일 형식이 틀리면 500(@ControllerAdvice 없음). 같은 gender 문자열, 생년월일 ±5년(문자열 BETWEEN), 6차원 코사인 유사도↓·id↓ 12명 |
| GET /api/posts/search?query= | 빈 query·결과 없음 → 204. title·body·AddressTitle LIKE(대소문자 무시) |
| GET /api/users/search-agents-by-hashtag?hashtag= | hashtags 목록에 완전 일치(# 포함), 없으면 204 |
| GET /api/users/agents[?country=] | agentCountry 완전 일치(없으면 200 []), country가 없으면 전체 |

requests에 받은 요청(메서드·경로·query·본문·Authorization 유무)을 남긴다(cli tool이 보여 줌, 테스트가 토큰 없음을 확인).
"""
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
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
        }
        handler = handlers.get(route)
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


__all__ = ["DEFAULT_FIXTURES_DIR", "FIXTURE_BASE_URL", "FixtureSpring", "SeenRequest"]

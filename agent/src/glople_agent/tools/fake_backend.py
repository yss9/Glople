"""가짜 백엔드(M0): tests/fixtures/backend/*.json(실제 Glople 응답 모양)을 읽어 도구 결과를 돌려준다.

- 서버 동작(키워드 일치 수 정렬·21건, 후기 LIKE 검색·204, 해시태그 완전 일치, 유사 여행자 ±5년)을 흉내 낸다.
- 돌려줄 때는 이미 나뉜 결과를 준다: items(LLM용, 허용 필드만) / card_items(카드용, 원문·출처 포함).
  M1의 SpringBackend + adapters/*.py가 같은 BackendResult를 돌려주게 바꿔 끼운다.
- search_destinations·get_destination_detail은 Spring S1 전이라 pending_feature, get_weather는 1차 미지원(unsupported).
- 지역 해석기(M1) 전이라 글로플러의 국내/해외 판단은 아래 작은 표로만 한다.
"""
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

from glople_agent import contract
from glople_agent.answer.templates import attribution_text, normalize_copyright
from glople_agent.config import AGENT_DIR
from glople_agent.sanitize import clean_text
from glople_agent.state import ResultStatus
from glople_agent.tools.args import (GetDestinationDetailArgs, GetWeatherArgs, RecommendByKeywordsArgs,
                                     RecommendSimilarTravelerRoutesArgs, SearchDestinationsArgs,
                                     SearchGloplersArgs, SearchReviewsArgs)

DEFAULT_FIXTURES_DIR = AGENT_DIR / "tests" / "fixtures" / "backend"
KST = ZoneInfo("Asia/Seoul")

TextPolicy = Literal["VERBATIM_ONLY", "ATTRIBUTION", "FREE_FACTS", "SHARE_ALIKE", "UNKNOWN"]

SERVER_KEYWORD_LIMIT = 21      # /api/search의 LIMIT 21
PLACES_PER_LOCATION = 2        # 같은 location은 2곳까지
LLM_PLACES = 5                 # 장소 상위 5개
SUMMARY_CHARS = 60             # FREE_FACTS 틀 문장 상한
REVIEWS_SHOWN = 3              # 좋아요↓·최신순 3개
REVIEW_TITLE_CHARS = 40
REVIEW_EXCERPT_CHARS = 150
ROUTES_SERVER_LIMIT = 12       # info-recommend 상위 12명
ROUTES_SHOWN = 3
ROUTE_AGE_WINDOW = 5           # 생년월일 ±5년
GLOPLERS_SHOWN = 5

AGENT_COUNTRIES = ("미국", "이탈리아", "일본")  # 대행 국가 선택지(reservation.js:261-267)
# M0 임시 표(지역 해석기 M1 전). 해외 도시 → 대행 국가, 국내 지역명
_AGENT_COUNTRY_OF = {
    **{c: c for c in AGENT_COUNTRIES},
    **dict.fromkeys(["도쿄", "오사카", "교토", "삿포로", "오타루", "오키나와", "후쿠오카", "나고야", "가나자와",
                     "고베", "나라", "하코다테"], "일본"),
    **dict.fromkeys(["로마", "베네치아", "피렌체", "밀라노", "나폴리"], "이탈리아"),
    **dict.fromkeys(["뉴욕", "하와이", "로스앤젤레스", "샌프란시스코", "라스베이거스", "시애틀"], "미국"),
}
_DOMESTIC_REGIONS = frozenset([
    "한국", "국내", "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "제주", "제주도", "서귀포",
    "강릉", "속초", "양양", "춘천", "가평", "수원", "경주", "안동", "포항", "울릉도", "독도", "여수", "순천",
    "담양", "목포", "보성", "전주", "군산", "공주", "태안", "보령", "청송", "합천", "거제", "통영", "남해", "하동",
    "강화도", "고창", "삼척", "영월", "제천", "단양", "여주",
])

GENDER_CODES = {"남성": "M", "여성": "F"}
GENDER_LABELS = {"M": "남성", "F": "여성"}
VERBATIM_NOTE = "설명은 원문 카드"


@dataclass(frozen=True)
class BackendResult:
    """도구 하나의 결과. items는 LLM으로 갈 허용 필드만, card_items는 카드에만(원문 포함)."""

    status: ResultStatus
    items: list[dict] = field(default_factory=list)
    card_items: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # 사실로 된 제한 사항(반영 안 된 키워드 등)
    extra: dict = field(default_factory=dict)         # 카드 data에 더할 값


class ReadBackend(Protocol):
    """읽기 도구 7개의 실행 쪽. 메서드 이름 = 도구 이름(registry가 getattr로 찾음)."""

    def recommend_by_keywords(self, args: RecommendByKeywordsArgs) -> BackendResult: ...
    def search_destinations(self, args: SearchDestinationsArgs) -> BackendResult: ...
    def recommend_similar_traveler_routes(self, args: RecommendSimilarTravelerRoutesArgs) -> BackendResult: ...
    def get_destination_detail(self, args: GetDestinationDetailArgs) -> BackendResult: ...
    def search_reviews(self, args: SearchReviewsArgs) -> BackendResult: ...
    def search_gloplers(self, args: SearchGloplersArgs) -> BackendResult: ...
    def get_weather(self, args: GetWeatherArgs) -> BackendResult: ...


def text_policy(source: str | None, info_kind: str | None, copyright_type: str | None) -> TextPolicy:
    """design.md 11절 판정표(M0판, M1에서 tools/text_policy.py로 옮김). 모르는 값은 보수적으로."""
    if source == "TOUR_API":
        if info_kind == "SUMMARY":
            return "FREE_FACTS"
        if info_kind == "DESCRIPTION":
            return "ATTRIBUTION" if normalize_copyright(copyright_type) == "type1" else "VERBATIM_ONLY"
        return "UNKNOWN"
    if source == "WIKIDATA":
        return "FREE_FACTS" if info_kind == "SUMMARY" else "SHARE_ALIKE"
    return "UNKNOWN"


def _wiki_title(source_url: str | None) -> str | None:
    prefix = "https://ko.wikipedia.org/wiki/"
    if source_url and source_url.startswith(prefix):
        return unquote(source_url[len(prefix):].split("#")[0].split("?")[0]).replace("_", " ") or None
    return None


def _place_items(row: Mapping[str, Any]) -> tuple[dict, dict]:
    """KeywordResultDTO 한 행 → (LLM 항목, 카드 항목). LLM 쪽은 허용 필드만 새로 만든다(서버 필드가 늘어도 안 샘)."""
    name = row.get("destName") or ""
    where = row.get("location") or row.get("country") or ""
    policy = text_policy(row.get("source"), row.get("infoKind"), row.get("copyrightType"))
    llm: dict[str, Any] = {"name": name, "where": where}
    if policy == "VERBATIM_ONLY":
        llm["note"] = VERBATIM_NOTE
    elif policy == "FREE_FACTS" and row.get("info"):
        llm["note"] = clean_text(row["info"], SUMMARY_CHARS)
    # ATTRIBUTION·SHARE_ALIKE는 1차에 사실 필드만, UNKNOWN은 이름(과 지역)만
    card = {
        "id": row.get("id"),
        "name": name,
        "country": row.get("country"),
        "location": row.get("location"),
        "text": None if policy == "UNKNOWN" else row.get("info"),   # 원문 그대로(변경 금지)
        "textKind": row.get("infoKind"),
        "textPolicy": policy,
        "source": row.get("source"),
        "copyrightType": row.get("copyrightType"),
        "license": row.get("license"),
        "sourceUrl": row.get("sourceUrl"),
        "attribution": attribution_text(row.get("source"), row.get("infoKind"), row.get("copyrightType"),
                                        title=_wiki_title(row.get("sourceUrl")) or name),
        "detailPath": "/recomendation/information/" + quote(name),
    }
    return llm, card


def _date_part(create_date: str | None) -> str | None:
    return create_date[:10] if create_date else None


def _age_on(birth: str, today: date) -> int | None:
    try:
        b = datetime.strptime(birth, "%Y%m%d").date()
    except (TypeError, ValueError):
        return None
    return today.year - b.year - ((today.month, today.day) < (b.month, b.day))


def _age_band(age: int | None) -> str | None:
    return None if age is None else f"{age // 10 * 10}대"


class FakeBackend:
    """fixture를 읽는 ReadBackend. errors로 도구별 예외를 흉내 내고, calls에 받은 인자를 기록한다."""

    def __init__(self, fixtures_dir: Path | None = None, *, today: date | None = None,
                 errors: Mapping[str, BaseException] | None = None) -> None:
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else DEFAULT_FIXTURES_DIR
        self._today = today
        self.errors = dict(errors or {})
        self.calls: list[tuple[str, dict]] = []
        self._cache: dict[str, dict] = {}

    # --- 공통 -----------------------------------------------------------------
    def _fixture(self, name: str) -> dict:
        if name not in self._cache:
            path = self.fixtures_dir / f"{name}.json"
            self._cache[name] = json.loads(path.read_text(encoding="utf-8"))
        return self._cache[name]

    def _start(self, tool: str, args: Any) -> None:
        self.calls.append((tool, args.model_dump(exclude_none=True)))
        if tool in self.errors:
            raise self.errors[tool]

    def today(self) -> date:
        return self._today or datetime.now(KST).date()

    # --- 기존 공개 API 4개 --------------------------------------------------------
    def recommend_by_keywords(self, args: RecommendByKeywordsArgs) -> BackendResult:
        self._start("recommend_by_keywords", args)
        known = [k for k in dict.fromkeys(args.keywords) if k in contract.KEYWORDS]
        unknown = [k for k in dict.fromkeys(args.keywords) if k not in contract.KEYWORDS]
        notes = [f"목록에 없어 반영하지 못한 키워드: {', '.join(unknown)}"] if unknown else []
        extra = {"appliedKeywords": known}
        if not known:   # 서버가 모르는 키워드를 조용히 버리므로 부르지 않는다
            return BackendResult("empty", notes=notes, extra=extra)
        fx = self._fixture("recommend_by_keywords")
        masks = {int(k): set(v) for k, v in fx["keyword_masks"].items()}
        wanted = set(known)
        scored = [(len(masks.get(r["id"], set()) & wanted), r) for r in fx["rows"]]
        rows = [r for n, r in sorted(scored, key=lambda x: (-x[0], x[1]["id"])) if n > 0][:SERVER_KEYWORD_LIMIT]
        per_location: dict[str, int] = {}
        picked = []
        for r in rows:
            loc = r.get("location") or r.get("country") or ""
            if per_location.get(loc, 0) >= PLACES_PER_LOCATION:
                continue
            per_location[loc] = per_location.get(loc, 0) + 1
            picked.append(r)
            if len(picked) == LLM_PLACES:
                break
        if not picked:
            return BackendResult("empty", notes=notes, extra=extra)
        pairs = [_place_items(r) for r in picked]
        return BackendResult("ok", [p[0] for p in pairs], [p[1] for p in pairs], notes, extra)

    def search_reviews(self, args: SearchReviewsArgs) -> BackendResult:
        self._start("search_reviews", args)
        q = args.query.lower()
        rows = [p for p in self._fixture("search_reviews")["rows"]
                if any(q in (p.get(k) or "").lower() for k in ("title", "body", "addressTitle"))]
        if not rows:   # 서버는 204
            return BackendResult("empty")
        rows.sort(key=lambda p: (p.get("likesCount") or 0, p.get("createDate") or ""), reverse=True)
        items, cards = [], []
        for p in rows[:REVIEWS_SHOWN]:
            items.append({"title": clean_text(p.get("title"), REVIEW_TITLE_CHARS),
                          "likes": p.get("likesCount") or 0, "date": _date_part(p.get("createDate"))})
            cards.append({"boardId": p.get("boardID"), "title": clean_text(p.get("title"), 100),
                          "excerpt": clean_text(p.get("body"), REVIEW_EXCERPT_CHARS),
                          "likes": p.get("likesCount") or 0, "date": _date_part(p.get("createDate")),
                          "nickname": clean_text(p.get("nickname"), 30),
                          "imageUrl": p.get("imageUrl") or p.get("thumbnail"),
                          "link": f"/board/{p.get('boardID')}"})
        return BackendResult("ok", items, cards, extra={"found": len(rows)})

    def search_gloplers(self, args: SearchGloplersArgs) -> BackendResult:
        self._start("search_gloplers", args)
        word = args.hashtag.lstrip("#").strip()
        if word in _DOMESTIC_REGIONS:   # 국내 대행자가 없어 부르지 않음
            return BackendResult("empty", notes=["국내 글로플러는 아직 없어요(지금은 미국·이탈리아·일본)."])
        agents = self._fixture("search_gloplers")["rows"]
        tag = "#" + word
        found = [a for a in agents if tag in (a.get("hashtags") or [])]   # 서버처럼 완전 일치
        notes: list[str] = []
        if not found and word in _AGENT_COUNTRY_OF:   # 204 → GET /api/users/agents?country=
            country = _AGENT_COUNTRY_OF[word]
            found = [a for a in agents if a.get("agentCountry") == country]
            if found and country != word:
                notes.append(f"{word} 해시태그 글로플러가 없어 {country} 글로플러를 보여드려요.")
        if not found:
            return BackendResult("empty", notes=["지금은 미국·이탈리아·일본 글로플러만 있어요."])
        found.sort(key=lambda a: -(a.get("averageReviewRating") or 0))
        items, cards = [], []
        for a in found[:GLOPLERS_SHOWN]:
            tags = [t.lstrip("#") for t in a.get("hashtags") or []]
            items.append({"nickname": clean_text(a.get("nickname"), 20), "country": a.get("agentCountry"),
                          "hashtags": tags[:3], "rating": a.get("averageReviewRating")})
            cards.append({"nickname": clean_text(a.get("nickname"), 30), "country": a.get("agentCountry"),
                          "hashtags": tags, "rating": a.get("averageReviewRating"),
                          "introduction": clean_text(a.get("introduction"), 300),
                          "specIntroduction": [clean_text(s, 100) for s in a.get("specIntroduction") or []],
                          "profilePicture": a.get("profilePicture")})
        return BackendResult("ok", items, cards, notes)

    def recommend_similar_traveler_routes(self, args: RecommendSimilarTravelerRoutesArgs) -> BackendResult:
        self._start("recommend_similar_traveler_routes", args)
        today = self.today()
        code = GENDER_CODES[args.gender]
        birth_year = today.year - args.age
        lo = f"{birth_year - ROUTE_AGE_WINDOW}{today:%m%d}"   # 서버처럼 문자열 BETWEEN
        hi = f"{birth_year + ROUTE_AGE_WINDOW}{today:%m%d}"
        cands = [u for u in self._fixture("recommend_similar_traveler_routes")["rows"]
                 if u.get("gender") == code and lo <= (u.get("age") or "") <= hi]

        def rank(u: Mapping[str, Any]) -> tuple:
            same = sum(a == b for a, b in zip(u.get("mbti") or "", args.mbti))
            age = _age_on(u.get("age") or "", today)
            return (-same, abs((age or 0) - args.age), u.get("id") or 0)

        cands = sorted(cands, key=rank)[:ROUTES_SERVER_LIMIT]
        if not cands:
            return BackendResult("empty")
        items, cards = [], []
        for u in cands[:ROUTES_SHOWN]:
            band = _age_band(_age_on(u.get("age") or "", today))   # 생년월일은 넘기지 않고 나이대만
            dests = [clean_text(d, 30) for d in u.get("travelDestinations") or []]
            base = {"ageBand": band, "gender": GENDER_LABELS.get(u.get("gender")), "mbti": u.get("mbti")}
            items.append({**base, "destinations": dests[:3]})
            cards.append({"id": u.get("id"), **base, "destinations": dests})
        return BackendResult("ok", items, cards)

    # --- 아직 없는 기능 -----------------------------------------------------------
    def search_destinations(self, args: SearchDestinationsArgs) -> BackendResult:
        self._start("search_destinations", args)
        return BackendResult("pending_feature")   # Spring S1(GET /api/destinations) 전

    def get_destination_detail(self, args: GetDestinationDetailArgs) -> BackendResult:
        self._start("get_destination_detail", args)
        return BackendResult("pending_feature")   # Spring S1(GET /api/destinations/lookup) 전

    def get_weather(self, args: GetWeatherArgs) -> BackendResult:
        self._start("get_weather", args)
        return BackendResult("unsupported")       # 1차 미지원(Open-Meteo는 오너 승인 뒤 M6)


def all_rows(fixture: str, fixtures_dir: Path | None = None) -> Sequence[dict]:
    """테스트용: fixture의 rows 그대로."""
    path = (Path(fixtures_dir) if fixtures_dir else DEFAULT_FIXTURES_DIR) / f"{fixture}.json"
    return json.loads(path.read_text(encoding="utf-8"))["rows"]

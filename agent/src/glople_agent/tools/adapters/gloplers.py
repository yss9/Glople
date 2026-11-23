"""search_gloplers → GET /api/users/search-agents-by-hashtag?hashtag=#값(공개, UserController.searchAgentsByHashtag).

서버는 승인된 대행자의 hashtags 목록에 **'#'을 붙인 값이 완전 일치**할 때만 돌려주고, 없으면 204다(UserService:375-378).
해시태그는 '#'을 붙여 저장하므로(agentprofileset.js:214) 도구 인자('#' 없음)에 어댑터가 '#'을 붙인다.
학습 분포는 지역명 80%·해시태그 20%라 대체 경로를 둔다(tools-backend-mapping 2.6, design.md 8.2):

1. 지역 해석기가 국내라고 하면 **부르지 않는다**(대행 국가는 미국·이탈리아·일본뿐, reservation.js:261-267).
2. GET /search-agents-by-hashtag?hashtag=#값
3. 204이고 해외 지역이며 대행 국가가 있으면 GET /api/users/agents?country=<대행 국가>(agentCountry 완전 일치, 없으면 200 [])
4. 그래도 없으면 GET /api/users/agents(전체, 승인된 대행자만이라 적음)에서 해시태그·소개에 값이 들어 있는 사람
소개글·스펙은 사용자 글이라 카드에만 넣고(정제), LLM에는 닉네임·국가·해시태그 3개·별점만.
"""
from glople_agent.sanitize import clean_text
from glople_agent.tools.adapters.common import first_texts, rows_of
from glople_agent.tools.args import SearchGloplersArgs
from glople_agent.tools.backend import BackendResult, ToolContext
from glople_agent.tools.regions import RegionResolution
from glople_agent.tools.regions import resolve as default_resolve
from glople_agent.tools.spring import SpringClient

GLOPLERS_SHOWN = 5
AGENT_COUNTRIES = ("미국", "이탈리아", "일본")
NOTE_DOMESTIC = "국내 글로플러는 아직 없어요(지금은 미국·이탈리아·일본)."
NOTE_NONE = "지금은 미국·이탈리아·일본 글로플러만 있어요."
NICKNAME_CHARS = 20
TAG_CHARS = 30


def note_country(word: str, country: str) -> str:
    return f"{word} 해시태그 글로플러가 없어 {country} 글로플러를 보여드려요."


def note_partial(word: str) -> str:
    return f"'{word}' 해시태그가 정확히 같은 글로플러가 없어 해시태그·소개에 '{word}'가 들어간 글로플러를 보여드려요."


def _rating(a: dict) -> float:
    r = a.get("averageReviewRating")
    return float(r) if isinstance(r, int | float) and not isinstance(r, bool) else 0.0


def _tags(a: dict) -> list[str]:
    return [clean_text(t, TAG_CHARS).lstrip("#") for t in first_texts(a.get("hashtags") or [], 30)]


def to_result(rows: list[dict], notes: list[str]) -> BackendResult:
    if not rows:
        return BackendResult("empty", notes=notes or [NOTE_NONE])
    rows = sorted(rows, key=lambda a: -_rating(a))
    items, cards = [], []
    for a in rows[:GLOPLERS_SHOWN]:
        tags = _tags(a)
        country = a.get("agentCountry") if isinstance(a.get("agentCountry"), str) else None
        items.append({"nickname": clean_text(a.get("nickname"), NICKNAME_CHARS), "country": country,
                      "hashtags": tags[:3], "rating": _rating(a)})
        cards.append({"nickname": clean_text(a.get("nickname"), 30), "country": country, "hashtags": tags,
                      "rating": _rating(a), "introduction": clean_text(a.get("introduction"), 300),
                      "specIntroduction": [clean_text(s, 100) for s in first_texts(a.get("specIntroduction") or [], 10)],
                      "profilePicture": a.get("profilePicture")})
    return BackendResult("ok", items, cards, notes, extra={"found": len(rows)})


def run(client: SpringClient, args: SearchGloplersArgs, ctx: ToolContext,
        resolve=default_resolve) -> BackendResult:
    word = clean_text(args.hashtag, TAG_CHARS).lstrip("#").strip()
    region: RegionResolution = resolve(word)
    extra = {"region": {"status": region.status, "kind": region.kind, "label": region.label}}
    if region.kind == "DOMESTIC":   # '광주'처럼 모호해도 둘 다 국내면 같은 답
        result = BackendResult("empty", notes=[NOTE_DOMESTIC], extra=extra)
        return result
    found = rows_of(client.call("agents_by_hashtag", params={"hashtag": "#" + word}).data)
    notes: list[str] = []
    if not found and region.region is not None and region.region.agent_country in AGENT_COUNTRIES:
        country = region.region.agent_country
        found = rows_of(client.call("agents", params={"country": country}).data)
        if found and country != word:
            notes.append(note_country(word, country))
    if not found and word:
        everyone = rows_of(client.call("agents").data)
        found = [a for a in everyone
                 if any(word in t for t in _tags(a)) or word in clean_text(a.get("introduction"), 300)]
        if found:
            notes.append(note_partial(word))
    out = to_result(found, notes)
    return BackendResult(out.status, out.items, out.card_items, out.notes, {**out.extra, **extra})

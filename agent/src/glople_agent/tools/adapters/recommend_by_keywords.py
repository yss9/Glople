"""recommend_by_keywords → POST /api/search(공개, RecommendController.search).

요청 {"keywords": [...]} → 응답 KeywordResultDTO 배열(최대 21건: id, country, location, destName, info, source,
copyrightType, sourceUrl, license, infoKind).

- 서버는 목록 밖 키워드를 **조용히 버린다**(KeywordMask.fromKeywords). 그래서 먼저 94개 목록과 맞춰(tools/keywords.py)
  목록 표기만 보내고, 바꿀 수 없는 말은 notes에 '반영하지 못한 키워드'로 남긴다. 하나도 없으면 부르지 않는다.
- 같은 location은 2곳까지, LLM에는 상위 5곳(이름·지역·note만, text_policy.place_items). Type3 원문은 카드에만.
- 동점이면 서버가 id 순이라 '가'로 시작하는 국내 관광지에 쏠린다(Spring S0에서 seed·limit·scope로 고칠 예정).
"""
from glople_agent.tools.adapters.common import rows_of
from glople_agent.tools.args import RecommendByKeywordsArgs
from glople_agent.tools.backend import BackendResult, ToolContext
from glople_agent.tools.keywords import resolve_keywords, unknown_note
from glople_agent.tools.spring import SpringClient
from glople_agent.tools.text_policy import place_items

ENDPOINT = "keyword_search"
PLACES_PER_LOCATION = 2
LLM_PLACES = 5


def request_body(args: RecommendByKeywordsArgs) -> tuple[dict | None, dict, list[str]]:
    """(보낼 본문 또는 None, 카드 extra, notes)."""
    res = resolve_keywords(args.keywords)
    known = res.applied
    notes = [n for n in [unknown_note(res.unknown)] if n]
    extra: dict = {"appliedKeywords": known}
    if res.mapped:
        extra["mappedKeywords"] = [{"given": g, "keyword": k} for g, k in res.mapped]
    return ({"keywords": known} if known else None), extra, notes


def to_result(data: object, extra: dict, notes: list[str]) -> BackendResult:
    picked, per_location = [], {}
    for row in rows_of(data):
        loc = row.get("location") or row.get("country") or ""
        if per_location.get(loc, 0) >= PLACES_PER_LOCATION:
            continue
        per_location[loc] = per_location.get(loc, 0) + 1
        picked.append(row)
        if len(picked) == LLM_PLACES:
            break
    if not picked:
        return BackendResult("empty", notes=notes, extra=extra)
    pairs = [place_items(r) for r in picked]
    return BackendResult("ok", [p[0] for p in pairs], [p[1] for p in pairs], notes, extra)


def run(client: SpringClient, args: RecommendByKeywordsArgs, ctx: ToolContext) -> BackendResult:
    body, extra, notes = request_body(args)
    if body is None:   # 서버가 모두 버릴 키워드뿐 → 부르지 않음
        return BackendResult("empty", notes=notes, extra=extra)
    return to_result(client.call(ENDPOINT, json_body=body).data, extra, notes)

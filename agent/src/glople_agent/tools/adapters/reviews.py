"""search_reviews → GET /api/posts/search?query=(공개, PostController.searchPosts).

응답은 Post 엔티티 배열(boardID, title, body(HTML LONGTEXT), imageUrl, createDate, addressTitle, likesCount, nickname …),
결과가 없으면 **204**(본문 없음). 개수 제한·정렬이 없고 본문이 HTML이라 어댑터가:
- 204·빈 배열 → empty("아직 ○○ 후기가 없어요")
- 좋아요↓, 같으면 최신순으로 3개
- LLM에는 제목 40자·좋아요 수·날짜만(정제: HTML·특수 토큰 제거). 본문 발췌 150자·닉네임·이미지·링크는 카드에만
- 검색어는 정제해 50자까지만 보낸다(LIKE %q% 검색이라 긴 문장은 의미 없음)
"""
from glople_agent.sanitize import clean_text
from glople_agent.tools.adapters.common import iso_or_none, kst_date, rows_of
from glople_agent.tools.args import SearchReviewsArgs
from glople_agent.tools.backend import BackendResult, ToolContext
from glople_agent.tools.spring import SpringClient

ENDPOINT = "post_search"
QUERY_CHARS = 50
REVIEWS_SHOWN = 3
TITLE_CHARS = 40
CARD_TITLE_CHARS = 100
EXCERPT_CHARS = 150
NICKNAME_CHARS = 30


def query_params(args: SearchReviewsArgs) -> dict[str, str]:
    return {"query": clean_text(args.query, QUERY_CHARS)}


def to_result(data: object) -> BackendResult:
    rows = rows_of(data)
    if not rows:   # 204
        return BackendResult("empty")

    def key(p: dict) -> tuple:
        likes = p.get("likesCount")
        return (likes if isinstance(likes, int) and not isinstance(likes, bool) else 0,
                iso_or_none(kst_date(p.get("createDate"))) or "")

    rows.sort(key=key, reverse=True)
    items, cards = [], []
    for p in rows[:REVIEWS_SHOWN]:
        likes = key(p)[0]
        day = iso_or_none(kst_date(p.get("createDate")))
        board = p.get("boardID")
        items.append({"title": clean_text(p.get("title"), TITLE_CHARS), "likes": likes, "date": day})
        cards.append({"boardId": board, "title": clean_text(p.get("title"), CARD_TITLE_CHARS),
                      "excerpt": clean_text(p.get("body"), EXCERPT_CHARS), "likes": likes, "date": day,
                      "nickname": clean_text(p.get("nickname"), NICKNAME_CHARS),
                      "imageUrl": p.get("imageUrl") or p.get("thumbnail"),
                      "link": f"/board/{board}" if isinstance(board, int) else None})
    return BackendResult("ok", items, cards, extra={"found": len(rows)})


def run(client: SpringClient, args: SearchReviewsArgs, ctx: ToolContext) -> BackendResult:
    params = query_params(args)
    if not params["query"]:   # 정제하고 남은 검색어가 없음(서버도 빈 query는 204)
        return BackendResult("empty")
    return to_result(client.call(ENDPOINT, params=params).data)

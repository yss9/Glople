"""도구별 어댑터(design.md 8.2)와 SpringBackend: 도구 인자 → Spring 요청 → 허용 필드로 줄인 BackendResult.

| 도구 | 방식 | 모듈 |
|---|---|---|
| recommend_by_keywords | POST /api/search | recommend_by_keywords.py |
| recommend_similar_traveler_routes | POST /api/info-recommend | similar_routes.py |
| search_reviews | GET /api/posts/search | reviews.py |
| search_gloplers | GET /api/users/search-agents-by-hashtag → /api/users/agents | gloplers.py |
| search_destinations · get_destination_detail · get_weather | 부르지 않음(준비 중·미지원) | not_ready.py |

모든 HTTP는 SpringClient(경로 허용 목록, 공개 경로엔 토큰 없음)를 지난다. 테스트·CLI fixture는 같은 SpringClient에
httpx.MockTransport(tools/fixture_spring.py)를 끼우고, 실제 서버는 base_url만 바꾼다(오너가 IDOR 점검 뒤).
"""
from collections.abc import Callable

from glople_agent.tools.adapters import gloplers, not_ready, recommend_by_keywords, reviews, similar_routes
from glople_agent.tools.args import (GetDestinationDetailArgs, GetWeatherArgs, RecommendByKeywordsArgs,
                                     RecommendSimilarTravelerRoutesArgs, SearchDestinationsArgs,
                                     SearchGloplersArgs, SearchReviewsArgs)
from glople_agent.tools.backend import BackendResult, Clock, ToolContext, kst_now
from glople_agent.tools.regions import resolve as default_resolve
from glople_agent.tools.spring import SpringClient


class SpringBackend:
    """ReadBackend 구현: 공개 API 4개는 Spring으로, 나머지 3개는 준비 중·미지원 봉투."""

    def __init__(self, client: SpringClient, *, clock: Clock = kst_now,
                 resolve: Callable = default_resolve) -> None:
        self.client = client
        self.clock = clock
        self.resolve = resolve

    def _ctx(self, ctx: ToolContext | None) -> ToolContext:
        return ctx if ctx is not None else ToolContext(now=self.clock())

    def _start(self, tool: str, args) -> None:
        """하위 클래스용 훅(FakeBackend가 호출 기록·오류 흉내에 씀)."""

    # --- 기존 공개 API 4개 ---------------------------------------------------------
    def recommend_by_keywords(self, args: RecommendByKeywordsArgs, ctx: ToolContext | None = None) -> BackendResult:
        self._start("recommend_by_keywords", args)
        return recommend_by_keywords.run(self.client, args, self._ctx(ctx))

    def recommend_similar_traveler_routes(self, args: RecommendSimilarTravelerRoutesArgs,
                                          ctx: ToolContext | None = None) -> BackendResult:
        self._start("recommend_similar_traveler_routes", args)
        return similar_routes.run(self.client, args, self._ctx(ctx))

    def search_reviews(self, args: SearchReviewsArgs, ctx: ToolContext | None = None) -> BackendResult:
        self._start("search_reviews", args)
        return reviews.run(self.client, args, self._ctx(ctx))

    def search_gloplers(self, args: SearchGloplersArgs, ctx: ToolContext | None = None) -> BackendResult:
        self._start("search_gloplers", args)
        return gloplers.run(self.client, args, self._ctx(ctx), resolve=self.resolve)

    # --- 아직 없는 기능 ---------------------------------------------------------------
    def search_destinations(self, args: SearchDestinationsArgs, ctx: ToolContext | None = None) -> BackendResult:
        self._start("search_destinations", args)
        return not_ready.search_destinations(args)

    def get_destination_detail(self, args: GetDestinationDetailArgs, ctx: ToolContext | None = None) -> BackendResult:
        self._start("get_destination_detail", args)
        return not_ready.get_destination_detail(args)

    def get_weather(self, args: GetWeatherArgs, ctx: ToolContext | None = None) -> BackendResult:
        self._start("get_weather", args)
        return not_ready.get_weather(args)


def live_backend(base_url: str) -> SpringBackend:
    """실제 Spring(설정 base_url)에 붙는 백엔드. 테스트에서는 쓰지 않는다(8080 접속 금지)."""
    return SpringBackend(SpringClient(base_url))


__all__ = ["SpringBackend", "gloplers", "live_backend", "not_ready", "recommend_by_keywords", "reviews",
           "similar_routes"]

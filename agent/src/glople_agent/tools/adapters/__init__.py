"""도구별 어댑터(design.md 8.2)와 SpringBackend: 도구 인자 → Spring(또는 Open-Meteo) 요청 → 허용 필드로 줄인 BackendResult.

| 도구 | 방식 | 모듈 |
|---|---|---|
| recommend_by_keywords | POST /api/search | recommend_by_keywords.py |
| recommend_similar_traveler_routes | POST /api/info-recommend | similar_routes.py |
| search_reviews | GET /api/posts/search | reviews.py |
| search_gloplers | GET /api/users/search-agents-by-hashtag → /api/users/agents | gloplers.py |
| search_destinations · get_destination_detail (M6) | GET /api/destinations · /lookup?name= → /{id} (S1) | destinations.py |
| get_weather (M6) | Open-Meteo 예보(+ 지오코딩), 날씨 모드 off면 부르지 않음 | weather.py · not_ready.py |

Spring HTTP는 모두 SpringClient(경로 허용 목록, 공개 경로엔 토큰 없음)를, 날씨는 OpenMeteoClient(주소 두 개, 좌표·표 이름만)를
지난다. 테스트·CLI fixture는 같은 클라이언트에 httpx.MockTransport(tools/fixture_spring.py, tools/fixture_weather.py)를 끼우고,
실제 서버는 base_url(Spring)·transport 없음(Open-Meteo)으로 바꾼다.
"""
from collections.abc import Callable

from glople_agent.tools.adapters import (destinations, gloplers, not_ready, recommend_by_keywords, reviews,
                                        similar_routes, weather)
from glople_agent.tools.args import (GetDestinationDetailArgs, GetWeatherArgs, RecommendByKeywordsArgs,
                                     RecommendSimilarTravelerRoutesArgs, SearchDestinationsArgs,
                                     SearchGloplersArgs, SearchReviewsArgs)
from glople_agent.tools.backend import BackendResult, Clock, ToolContext, kst_now
from glople_agent.tools.regions import resolve as default_resolve
from glople_agent.tools.spring import SpringClient
from glople_agent.tools.weather import OpenMeteoClient, WeatherMode


class SpringBackend:
    """ReadBackend 구현: 읽기 도구 6개는 Spring 공개 API로, 날씨는 Open-Meteo로(weather=None이면 날씨 끔 → 미지원 봉투)."""

    def __init__(self, client: SpringClient, *, clock: Clock = kst_now,
                 resolve: Callable = default_resolve, weather: OpenMeteoClient | None = None) -> None:
        self.client = client
        self.clock = clock
        self.resolve = resolve
        self.weather = weather

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

    # --- M6: S1 여행지 · 날씨 ---------------------------------------------------------------
    def search_destinations(self, args: SearchDestinationsArgs, ctx: ToolContext | None = None) -> BackendResult:
        self._start("search_destinations", args)
        return destinations.search_destinations(self.client, args, self._ctx(ctx), resolve=self.resolve)

    def get_destination_detail(self, args: GetDestinationDetailArgs, ctx: ToolContext | None = None) -> BackendResult:
        self._start("get_destination_detail", args)
        return destinations.get_destination_detail(self.client, args, self._ctx(ctx), resolve=self.resolve)

    def get_weather(self, args: GetWeatherArgs, ctx: ToolContext | None = None) -> BackendResult:
        self._start("get_weather", args)
        if self.weather is None:
            return not_ready.weather_off(args)
        return weather.get_weather(self.weather, args, self._ctx(ctx), resolve=self.resolve)


def live_backend(base_url: str, *, weather: "WeatherMode | OpenMeteoClient | None" = None) -> SpringBackend:
    """실제 Spring(설정 base_url)에 붙는 백엔드. 테스트에서는 쓰지 않는다(8080 접속 금지).
    weather(M6): 날씨 모드(live·fixture·off) 또는 OpenMeteoClient. 없으면 설정 GLOPLE_WEATHER(기본 live)."""
    from glople_agent.config import load_settings
    from glople_agent.tools.fixture_weather import make_weather_client

    mode = weather if weather is not None else load_settings().weather
    return SpringBackend(SpringClient(base_url), weather=make_weather_client(mode))


__all__ = ["SpringBackend", "destinations", "gloplers", "live_backend", "not_ready", "recommend_by_keywords", "reviews",
           "similar_routes", "weather"]

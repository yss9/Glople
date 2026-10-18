"""읽기 도구 백엔드의 공통 타입: 결과(BackendResult), 실행 문맥(ToolContext), 프로토콜(ReadBackend).

- BackendResult.items는 LLM으로 갈 허용 필드만, card_items는 카드에만(원문·출처·이미지 포함).
- ToolContext는 도구가 runtime.context(AgentContext)에서 받는 값 중 **now·seed만** 옮긴 것이다. 토큰(bearer)·사용자 id는
  넘기지 않는다(공개 API만 부르는 읽기 도구에는 필요 없음, 최소 권한). registry가 ToolRuntime에서 만든다.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from glople_agent.state import ResultStatus
from glople_agent.tools.args import (GetDestinationDetailArgs, GetWeatherArgs, RecommendByKeywordsArgs,
                                     RecommendSimilarTravelerRoutesArgs, SearchDestinationsArgs,
                                     SearchGloplersArgs, SearchReviewsArgs)

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class BackendResult:
    """도구 하나의 결과. items는 LLM으로 갈 허용 필드만, card_items는 카드에만(원문 포함)."""

    status: ResultStatus
    items: list[dict] = field(default_factory=list)
    card_items: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # 사실로 된 제한 사항(반영 안 된 키워드 등)
    extra: dict = field(default_factory=dict)         # 카드 data에 더할 값


@dataclass(frozen=True)
class ToolContext:
    """도구 실행 문맥: 현지 시각(나이 → 생년월일, 날짜 계산)과 seed(S0 동점 섞기). 토큰은 없다."""

    now: datetime
    seed: int = 0

    @property
    def today(self) -> date:
        return self.now.astimezone(KST).date() if self.now.tzinfo else self.now.date()

    @classmethod
    def from_agent_context(cls, ctx: Any) -> "ToolContext | None":
        now = getattr(ctx, "now", None)
        if not isinstance(now, datetime):
            return None
        return cls(now=now, seed=int(getattr(ctx, "seed", 0) or 0))


Clock = Callable[[], datetime]


def kst_now() -> datetime:
    return datetime.now(KST)


class ReadBackend(Protocol):
    """읽기 도구 7개의 실행 쪽. 메서드 이름 = 도구 이름(registry가 getattr로 찾음). ctx는 없을 수 있다(직접 호출)."""

    def recommend_by_keywords(self, args: RecommendByKeywordsArgs, ctx: ToolContext | None = None) -> BackendResult: ...
    def search_destinations(self, args: SearchDestinationsArgs, ctx: ToolContext | None = None) -> BackendResult: ...
    def recommend_similar_traveler_routes(self, args: RecommendSimilarTravelerRoutesArgs,
                                          ctx: ToolContext | None = None) -> BackendResult: ...
    def get_destination_detail(self, args: GetDestinationDetailArgs, ctx: ToolContext | None = None) -> BackendResult: ...
    def search_reviews(self, args: SearchReviewsArgs, ctx: ToolContext | None = None) -> BackendResult: ...
    def search_gloplers(self, args: SearchGloplersArgs, ctx: ToolContext | None = None) -> BackendResult: ...
    def get_weather(self, args: GetWeatherArgs, ctx: ToolContext | None = None) -> BackendResult: ...


__all__ = ["KST", "BackendResult", "Clock", "ReadBackend", "ToolContext", "kst_now"]

"""아직 백엔드가 없는 도구 3개(design.md 8.2). 부르지 않고 정해진 봉투를 돌려준다.

- search_destinations: Spring S1(`GET /api/destinations`) 전이라 pending_feature. S1 뒤 어댑터는 지역 해석 →
  regionCode/sigunguCode 또는 countryCode + 반경, 음식점·숙박은 수집하지 않아 부르지 않고 정해진 답(학습 호출의 33%).
- get_destination_detail: S1(`/api/destinations/lookup?name=`) 전이라 pending_feature.
- get_weather: 1차 미지원(unsupported). Open-Meteo는 오너 승인 뒤 M6. 병렬로 부른 다른 결과는 그대로 나간다.
문장은 templates.status_message가 status로 고른다(registry가 notes 맨 앞에 넣음).
"""
from glople_agent.tools.args import GetDestinationDetailArgs, GetWeatherArgs, SearchDestinationsArgs
from glople_agent.tools.backend import BackendResult
from glople_agent.tools.regions import resolution_summary, resolve

NOT_COLLECTED_CATEGORIES = ("음식점", "숙박")   # S1 어댑터가 부르지 않을 카테고리(수집 안 함)


def search_destinations(args: SearchDestinationsArgs) -> BackendResult:
    region = resolve(args.region)
    extra = {"region": dict(resolution_summary(region)), "categoryCollected": args.category not in NOT_COLLECTED_CATEGORIES}
    return BackendResult("pending_feature", extra=extra)


def get_destination_detail(args: GetDestinationDetailArgs) -> BackendResult:
    return BackendResult("pending_feature")


def get_weather(args: GetWeatherArgs) -> BackendResult:
    return BackendResult("unsupported", extra={"region": dict(resolution_summary(resolve(args.region)))})

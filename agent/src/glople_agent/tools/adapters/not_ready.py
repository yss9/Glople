"""꺼 둔 도구의 정해진 봉투(부르지 않음). M6부터 search_destinations·get_destination_detail은 S1 어댑터(destinations.py),
get_weather는 Open-Meteo 어댑터(weather.py)라서, 여기에는 설정으로 끈 경우만 남았다.

- get_weather: 날씨 모드 off(GLOPLE_WEATHER=off, `chat --weather off`) → unsupported("날씨 정보는 아직 연결하지 않았어요").
  병렬로 부른 다른 결과는 그대로 나간다. 문장은 templates.status_message가 status로 고른다(registry가 notes 맨 앞에 넣음).
"""
from glople_agent.tools.args import GetWeatherArgs
from glople_agent.tools.backend import BackendResult
from glople_agent.tools.regions import resolution_summary, resolve


def weather_off(args: GetWeatherArgs) -> BackendResult:
    return BackendResult("unsupported", extra={"region": dict(resolution_summary(resolve(args.region)))})


__all__ = ["weather_off"]

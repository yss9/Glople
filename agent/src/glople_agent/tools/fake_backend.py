"""가짜 백엔드: tests/fixtures/backend/*.json으로 Spring 공개 API를 흉내 내는 SpringBackend(M0 → M1).

M0에서는 이 파일이 서버 흉내와 결과 압축을 함께 했다. M1부터는 실제와 같은 길을 탄다:
FakeBackend = SpringBackend(어댑터) + SpringClient(경로 허용 목록·토큰 규칙) + httpx.MockTransport(FixtureSpring).
그래서 그래프 테스트·CLI·정답 재생이 모두 실제 어댑터 코드와 HTTP 처리(204·JSON·크기 상한)를 거친다.

- (M6) 날씨는 가짜 Open-Meteo(tools/fixture_weather.py, 네트워크 없음)가 기본이다. weather="live"는 실제 Open-Meteo
  (`chat --llm real`의 기본, 오너 승인), "off"는 부르지 않음(미지원 봉투), OpenMeteoClient를 직접 줘도 된다(respx 테스트).
  weather_fixture: 가짜 Open-Meteo가 받은 요청 기록(좌표 말고 아무것도 안 갔는지 확인).
- errors={"도구": 예외}: 그 도구를 부를 때 예외를 낸다(registry가 ToolBackendError로 감쌈).
- calls: 받은 (도구, 인자) 기록. fixture.requests: 실제로 나간 HTTP 요청 기록(cli tool, 토큰 검사).
- today: 나이 → 생년월일 기준일. 그래프 안에서는 도구가 runtime.context.now를 넘기므로 그것이 우선이다.
"""
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from glople_agent.tools.adapters import SpringBackend
from glople_agent.tools.backend import KST, BackendResult, ReadBackend, ToolContext, kst_now
from glople_agent.tools.fixture_spring import DEFAULT_FIXTURES_DIR, FIXTURE_BASE_URL, FixtureSpring
from glople_agent.tools.spring import SpringClient
from glople_agent.tools.fixture_weather import FixtureOpenMeteo, make_weather_client
from glople_agent.tools.text_policy import TextPolicy, text_policy   # M0 테스트가 여기서 가져다 씀
from glople_agent.tools.weather import OpenMeteoClient, WeatherMode


class FakeBackend(SpringBackend):
    """fixture를 읽는 ReadBackend. errors로 도구별 예외를 흉내 내고, calls에 받은 인자를 기록한다."""

    def __init__(self, fixtures_dir: Path | None = None, *, today: date | None = None,
                 errors: Mapping[str, BaseException] | None = None,
                 weather: WeatherMode | OpenMeteoClient = "fixture") -> None:
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else DEFAULT_FIXTURES_DIR
        self._today = today
        self.errors = dict(errors or {})
        self.calls: list[tuple[str, dict]] = []
        self.fixture = FixtureSpring(self.fixtures_dir, today=self.today)
        self.weather_fixture = FixtureOpenMeteo(self.fixtures_dir, now=self._clock)
        super().__init__(SpringClient(FIXTURE_BASE_URL, transport=self.fixture.transport()), clock=self._clock,
                         weather=make_weather_client(weather, self.weather_fixture))

    def _clock(self) -> datetime:
        return datetime.combine(self._today, time(12, 0), tzinfo=KST) if self._today else kst_now()

    def today(self) -> date:
        return self._today or kst_now().date()

    def _start(self, tool: str, args: Any) -> None:
        self.calls.append((tool, args.model_dump(exclude_none=True)))
        if tool in self.errors:
            raise self.errors[tool]


def all_rows(fixture: str, fixtures_dir: Path | None = None) -> Sequence[dict]:
    """테스트용: fixture의 rows 그대로."""
    path = (Path(fixtures_dir) if fixtures_dir else DEFAULT_FIXTURES_DIR) / f"{fixture}.json"
    return json.loads(path.read_text(encoding="utf-8"))["rows"]


__all__ = ["DEFAULT_FIXTURES_DIR", "BackendResult", "FakeBackend", "ReadBackend", "TextPolicy",
           "ToolContext", "all_rows", "make_weather_client", "text_policy"]

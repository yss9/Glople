"""실행 설정. 환경변수만 읽는다(어떤 .env 파일도 읽지 않음)."""
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

# config.py → glople_agent → src → agent → 저장소 루트
AGENT_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = AGENT_DIR.parent

AnswerMode = Literal["template", "llm"]
LlmMode = Literal["fake", "live"]
AnswerPrompt = Literal["v1", "v2"]          # M4: 답변 system 문구(llm/prompts.py). v1 = design.md 6.4 그대로
PlanGraphMode = Literal["flat", "subgraph"]  # M3: 일정 노드를 평평하게 / 서브그래프로(graph/plan_subgraph.py)
PlanStoreKind = Literal["memory", "spring"]  # M3: 일정 저장소. spring은 오너가 S2 실제 API를 확인한 뒤에 켬
WeatherMode = Literal["live", "fixture", "off"]  # M6: 날씨(Open-Meteo 실제 / 가짜 fixture / 부르지 않음). tools/weather.py와 같은 값
# M7: 도구 결과 뒤. answer = 지금 그대로(collect → answer_llm·answer_template), loop = 라우터 모델에 결과를 보내 답하거나
# 한 번 더 부르게 함(collect → route_after_tools, 최대 2라운드, graph/after_tools.py)
AfterToolsMode = Literal["answer", "loop"]

ENV_TRAINING_DIR = "GLOPLE_TRAINING_DIR"
ENV_ANSWER_MODE = "GLOPLE_ANSWER_MODE"
ENV_LLM = "GLOPLE_LLM"
ENV_MODEL_BASE_URL = "GLOPLE_MODEL_BASE_URL"
ENV_SPRING_BASE_URL = "GLOPLE_SPRING_BASE_URL"
ENV_ANSWER_PROMPT = "GLOPLE_ANSWER_PROMPT"
ENV_PLAN_STORE = "GLOPLE_PLAN_STORE"
ENV_WEATHER = "GLOPLE_WEATHER"
ENV_AFTER_TOOLS = "GLOPLE_AFTER_TOOLS"
ENV_AFTER_TOOLS_BASE_URL = "GLOPLE_AFTER_TOOLS_BASE_URL"

DEFAULT_MODEL_BASE_URL = "http://127.0.0.1:8090/v1"  # mlx_lm.server (v2 LoRA)
DEFAULT_SPRING_BASE_URL = "http://127.0.0.1:8080"


class SettingsError(ValueError):
    """환경 변수·설정 값이 허용 목록 밖(CLI는 이것을 받아 종료 코드 2로 알림)."""


@dataclass(frozen=True)
class Settings:
    training_dir: Path = REPO_ROOT / "training"
    answer_mode: AnswerMode = "template"  # M2부터 llm
    llm: LlmMode = "fake"                 # M0는 가짜 모델만
    model_base_url: str = DEFAULT_MODEL_BASE_URL
    spring_base_url: str = DEFAULT_SPRING_BASE_URL
    # M4 A/B(2026-10-18, docs/m4-ab.md)로 v2: 라우터 v3·v4 모두에서 가드 통과가 같거나 높고 이름만 나열이 0%.
    # 그래프 단위 기본(Deps·fake_deps·real_deps(URL 문자열))은 테스트 기준이라 v1 그대로다
    answer_prompt: AnswerPrompt = "v2"
    plan_store: PlanStoreKind = "memory"  # 오너가 S2 확인 전까지 메모리
    # M6: real_deps·`chat --llm real`·`tool --live`의 날씨. 기본 live(오너 승인). fixture·off로 끔. 가짜 경로(fake_deps·
    # `chat --fake`·replay·abtest·테스트)는 이 값과 상관없이 늘 fixture다(네트워크 없음)
    weather: WeatherMode = "live"
    # M7: 기본은 answer(지금 그대로). loop는 v4 실측(README 'M7 도구 결과 뒤 루프')으로 정할 때까지 켜서 비교만
    after_tools: AfterToolsMode = "answer"
    # M7: loop에서 도구 결과 뒤 요청만 다른 모델 서버로(예: 첫 판단은 v3 8090, 결과 뒤는 v4 8092). 없으면 라우터와 같은 서버
    after_tools_base_url: str | None = None

    def __post_init__(self) -> None:
        for name, kind in (("answer_mode", AnswerMode), ("llm", LlmMode), ("answer_prompt", AnswerPrompt),
                           ("plan_store", PlanStoreKind), ("weather", WeatherMode), ("after_tools", AfterToolsMode)):
            if getattr(self, name) not in get_args(kind):
                raise SettingsError(f"{name}은 {get_args(kind)} 중 하나여야 합니다: {getattr(self, name)!r}")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        default = cls()
        training_dir = env.get(ENV_TRAINING_DIR)
        return cls(
            training_dir=Path(training_dir).expanduser().resolve() if training_dir else default.training_dir,
            answer_mode=env.get(ENV_ANSWER_MODE, default.answer_mode),
            llm=env.get(ENV_LLM, default.llm),
            model_base_url=env.get(ENV_MODEL_BASE_URL, default.model_base_url),
            spring_base_url=env.get(ENV_SPRING_BASE_URL, default.spring_base_url),
            answer_prompt=env.get(ENV_ANSWER_PROMPT, default.answer_prompt),
            plan_store=env.get(ENV_PLAN_STORE, default.plan_store),
            weather=env.get(ENV_WEATHER, default.weather),
            after_tools=env.get(ENV_AFTER_TOOLS, default.after_tools),
            after_tools_base_url=env.get(ENV_AFTER_TOOLS_BASE_URL) or default.after_tools_base_url,
        )


def load_settings() -> Settings:
    return Settings.from_env()

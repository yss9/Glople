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

ENV_TRAINING_DIR = "GLOPLE_TRAINING_DIR"
ENV_ANSWER_MODE = "GLOPLE_ANSWER_MODE"
ENV_LLM = "GLOPLE_LLM"
ENV_MODEL_BASE_URL = "GLOPLE_MODEL_BASE_URL"
ENV_SPRING_BASE_URL = "GLOPLE_SPRING_BASE_URL"

DEFAULT_MODEL_BASE_URL = "http://127.0.0.1:8090/v1"  # mlx_lm.server (v2 LoRA)
DEFAULT_SPRING_BASE_URL = "http://127.0.0.1:8080"


@dataclass(frozen=True)
class Settings:
    training_dir: Path = REPO_ROOT / "training"
    answer_mode: AnswerMode = "template"  # M2부터 llm
    llm: LlmMode = "fake"                 # M0는 가짜 모델만
    model_base_url: str = DEFAULT_MODEL_BASE_URL
    spring_base_url: str = DEFAULT_SPRING_BASE_URL

    def __post_init__(self) -> None:
        if self.answer_mode not in get_args(AnswerMode):
            raise ValueError(f"answer_mode는 {get_args(AnswerMode)} 중 하나여야 합니다: {self.answer_mode!r}")
        if self.llm not in get_args(LlmMode):
            raise ValueError(f"llm은 {get_args(LlmMode)} 중 하나여야 합니다: {self.llm!r}")

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
        )


def load_settings() -> Settings:
    return Settings.from_env()

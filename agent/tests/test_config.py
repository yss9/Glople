"""Settings: 환경변수만 읽고 기본 경로는 이 저장소의 training/."""
from pathlib import Path

import pytest

from glople_agent import config
from glople_agent.config import Settings


def test_defaults_point_to_repo_training():
    s = Settings.from_env({})
    assert s.training_dir == config.REPO_ROOT / "training"
    assert config.AGENT_DIR == Path(__file__).resolve().parents[1]
    assert (s.answer_mode, s.llm) == ("template", "fake")
    assert s.model_base_url == "http://127.0.0.1:8090/v1"
    assert s.spring_base_url == "http://127.0.0.1:8080"


def test_env_overrides(tmp_path):
    s = Settings.from_env({
        "GLOPLE_TRAINING_DIR": str(tmp_path),
        "GLOPLE_ANSWER_MODE": "llm",
        "GLOPLE_LLM": "live",
        "GLOPLE_MODEL_BASE_URL": "http://127.0.0.1:9999/v1",
        "GLOPLE_SPRING_BASE_URL": "http://127.0.0.1:9998",
    })
    assert s.training_dir == tmp_path.resolve()
    assert (s.answer_mode, s.llm) == ("llm", "live")
    assert s.model_base_url.endswith(":9999/v1") and s.spring_base_url.endswith(":9998")


def test_empty_training_dir_env_uses_default():
    assert Settings.from_env({"GLOPLE_TRAINING_DIR": ""}).training_dir == config.REPO_ROOT / "training"


@pytest.mark.parametrize("key,value", [("GLOPLE_ANSWER_MODE", "gpt"), ("GLOPLE_LLM", "openai")])
def test_rejects_unknown_modes(key, value):
    with pytest.raises(ValueError):
        Settings.from_env({key: value})


def test_settings_are_frozen():
    with pytest.raises(AttributeError):
        Settings().llm = "live"  # type: ignore[misc]

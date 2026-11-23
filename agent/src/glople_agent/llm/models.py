"""실제 모델 클라이언트(design.md 6.2): mlx_lm.server(OpenAI 호환, 기본 http://127.0.0.1:8090/v1)에 붙는 ChatOpenAI.

라우터와 답변이 같은 서버(v2 LoRA)를 쓰고, 설정만 다르다.

| | 라우터 | 답변(M2, a3) |
|---|---|---|
| 요청 | [System(SYSTEM_PROMPT), Human(한 마디)] + bind_tools(glople_tools.json dict 그대로) | 답변 전용 system + 이번 턴, 도구 없음 |
| 생성 | temperature 0, max_tokens 384(재시도는 route가 768·0.2로 덮음) | temperature 0.3, top_p 0.8, top_k 20, max_tokens 400 |
| 스트리밍 | 끔(disable_streaming, tags=["nostream"]) | 켬 |
| 기타 | tool_choice·strict·parallel_tool_calls를 넘기지 않음 | logit_bias로 <tool_call>(151657)·</tool_call>(151658) 막기 |

- model="default_model"은 코드 상수다. 다른 이름이면 서버가 그 이름으로 모델을 새로 로드하려 하고 LoRA가 빠진다.
- api_key는 쓰지 않는 더미 값이다. 모델 서버에는 사용자 토큰을 절대 붙이지 않는다(design.md 10.1). 값과 base_url을
  명시해서 OPENAI_API_KEY·OPENAI_BASE_URL·LANGSMITH_GATEWAY 환경 변수가 끼어들지 않게 한다(test_pins_openai_http).
- use_responses_api=False(mlx 서버는 /v1/chat/completions만), max_retries=0(서버가 연결을 끊었을 때 SDK가 같은 생성을
  다시 보내지 않게, 재시도는 그래프의 RetryPolicy·자기 루프가 함), timeout 20초.
- openai 3.24.0은 httpx 대신 httpx2를 쓴다. 그래서 respx로는 가로챌 수 없고 가짜 서버(scripts/fake_openai_server.py)로 시험한다.
- 이 모듈은 모델 서버를 띄우지 않는다. 학습 중에는 8090에 아무것도 없다(GPU 경쟁, design.md 4절).
"""
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from glople_agent import contract
from glople_agent.config import Settings

MODEL_NAME = "default_model"
DUMMY_API_KEY = "local-not-used"
DEFAULT_TIMEOUT = 20.0
ROUTER_MAX_TOKENS = 384
ANSWER_MAX_TOKENS = 400
TOOL_CALL_TOKEN_IDS = (151657, 151658)   # Qwen3 <tool_call>, </tool_call> (tokenizer.json)
ANSWER_LOGIT_BIAS: dict[int, int] = {t: -100 for t in TOOL_CALL_TOKEN_IDS}


def _common(base_url: str, timeout: float) -> dict[str, Any]:
    return {"model": MODEL_NAME, "base_url": base_url, "api_key": DUMMY_API_KEY, "use_responses_api": False,
            "timeout": timeout, "max_retries": 0}


def router_model(base_url: str, *, timeout: float = DEFAULT_TIMEOUT, **overrides: Any) -> ChatOpenAI:
    """라우터: 결정적(temperature 0)·비스트리밍. route 노드가 bind_tools(contract.TOOLS)로 묶는다."""
    params: dict[str, Any] = {**_common(base_url, timeout), "temperature": 0, "max_tokens": ROUTER_MAX_TOKENS,
                              "disable_streaming": True, "tags": ["nostream"]}
    params.update(overrides)
    return ChatOpenAI(**params)


def answer_model(base_url: str, *, timeout: float = DEFAULT_TIMEOUT, **overrides: Any) -> ChatOpenAI:
    """답변(M2 a3): 스트리밍, 도구 없음, <tool_call> 토큰을 logit_bias로 막음."""
    params: dict[str, Any] = {**_common(base_url, timeout), "temperature": 0.3, "top_p": 0.8,
                              "max_tokens": ANSWER_MAX_TOKENS, "streaming": True, "extra_body": {"top_k": 20},
                              "logit_bias": dict(ANSWER_LOGIT_BIAS)}
    params.update(overrides)
    return ChatOpenAI(**params)


def bind_router(model: BaseChatModel) -> Runnable:
    """route 노드와 같은 묶음: 도구 dict를 그대로(키 순서 포함) 넘기고 tool_choice·parallel_tool_calls는 넘기지 않는다."""
    return model.bind_tools(contract.TOOLS)


def models_from_settings(settings: Settings) -> tuple[ChatOpenAI, ChatOpenAI]:
    """(라우터, 답변) — Settings.model_base_url(GLOPLE_MODEL_BASE_URL) 하나를 함께 쓴다."""
    return router_model(settings.model_base_url), answer_model(settings.model_base_url)


__all__ = [
    "ANSWER_LOGIT_BIAS", "ANSWER_MAX_TOKENS", "DEFAULT_TIMEOUT", "DUMMY_API_KEY", "MODEL_NAME", "ROUTER_MAX_TOKENS",
    "TOOL_CALL_TOKEN_IDS", "answer_model", "bind_router", "models_from_settings", "router_model",
]

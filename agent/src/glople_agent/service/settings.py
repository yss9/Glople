"""서비스 설정(M5). 환경 변수만 읽는다(어떤 .env 파일도 읽지 않음, config.py와 같은 규칙).

| 환경 변수 | 기본값 | 뜻 |
|---|---|---|
| GLOPLE_SERVICE_HOST / GLOPLE_SERVICE_PORT | 127.0.0.1 / 8095 | 묶을 주소(127.0.0.1만 권장, design.md 4절) |
| GLOPLE_SERVICE_LLM | real | real = 모델 서버(GLOPLE_MODEL_BASE_URL), fake = 가짜 라우터(RuleScript)·가짜 답변 |
| GLOPLE_SERVICE_PLAN_STORE | real이면 spring, fake면 memory | 일정 저장소. spring = S2 /api/travel-plans(사용자 토큰) |
| GLOPLE_SERVICE_READ_BACKEND | fixture | 읽기 도구: fixture(real_deps·fake_deps의 기본 백엔드를 그대로, 지금은 가짜 Spring) / spring(GLOPLE_SPRING_BASE_URL 공개 API, M6 뒤 기본 후보) |
| GLOPLE_ANSWER_MODE | llm | 답변: llm(a3 + 가드) / template |
| GLOPLE_ANSWER_BASE_URL | 없음(라우터와 같은 서버) | 답변·일정 검토만 다른 서버로(a1) |
| GLOPLE_ANSWER_PROMPT | v2 | 답변 system 문구(M4 A/B로 정함) |
| GLOPLE_AFTER_TOOLS | answer | 도구 결과 뒤(M7): answer(답변 전용 경로) / loop(라우터가 답하거나 한 번 더, 최대 2라운드) |
| GLOPLE_AFTER_TOOLS_BASE_URL | 없음(라우터와 같은 서버) | loop의 결과 뒤 요청만 다른 서버로. 권장: 첫 판단 v3(8090) + 결과 뒤 v4(8092) |
| GLOPLE_SERVICE_DATA_DIR | agent/var/service | 체크포인트(checkpoints.sqlite)·스레드 주인 표(threads.sqlite) |
| GLOPLE_SERVICE_DEBUG | 0 | 1이면 오류 로그에 traceback(기본은 예외 이름만) |

모델 서버·Spring 주소는 config.Settings(GLOPLE_MODEL_BASE_URL·GLOPLE_SPRING_BASE_URL)를 그대로 쓴다.
"""
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, get_args

from glople_agent.config import (AGENT_DIR, AfterToolsMode, AnswerMode, AnswerPrompt, PlanStoreKind, Settings,
                                 SettingsError)

ServiceLlm = Literal["fake", "real"]
ReadBackendKind = Literal["fixture", "spring"]

BASE_PATH = "/ai-api/v1"
DEFAULT_DATA_DIR = AGENT_DIR / "var" / "service"


@dataclass(frozen=True)
class ServiceSettings:
    host: str = "127.0.0.1"
    port: int = 8095
    llm: ServiceLlm = "real"
    answer_mode: AnswerMode = "llm"
    answer_prompt: AnswerPrompt = "v2"   # M4 A/B로 정함(config.Settings와 같음)
    # M7: 기본은 answer(서버 하나로도 안전). 권장은 loop + GLOPLE_AFTER_TOOLS_BASE_URL(v4 서버): docs/m7-ab-hybrid.md
    after_tools: AfterToolsMode = "answer"
    plan_store: PlanStoreKind = "spring"
    read_backend: ReadBackendKind = "fixture"
    model_base_url: str = "http://127.0.0.1:8090/v1"
    answer_base_url: str | None = None
    spring_base_url: str = "http://127.0.0.1:8080"
    data_dir: Path = DEFAULT_DATA_DIR
    debug: bool = False
    # --- 인증(design.md 10.2) ---
    auth_cache_seconds: float = 60.0     # sha256(token) → userId 메모리 캐시
    auth_cache_max: int = 1024
    auth_timeout: float = 3.0            # my-info 읽기 시간 예산
    # --- 요청 크기 ---
    max_body_bytes: int = 16 * 1024
    max_text_chars: int = 500            # 메시지 한 개(start_turn의 정제 상한과 같음)
    max_token_chars: int = 4096          # Authorization 토큰 길이 상한
    max_threads_per_user: int = 100
    # --- 실행 수명(design.md 12) ---
    turn_timeout: float = 60.0           # 메시지·재개 한 번
    review_timeout: float = 30.0         # 일정 검토 답변 한 번
    model_wait_seconds: float = 20.0     # 모델 자리(세마포어)를 기다리는 최대 시간 → MODEL_BUSY
    model_concurrency: int = 1           # 동시에 모델을 쓰는 실행 수(mlx 서버 하나)
    ping_seconds: float = 10.0           # SSE `: ping` 간격
    rate_limit_turns: int = 20           # 사용자별 rate_limit_window초에 메시지·검토 수
    rate_limit_window: float = 600.0
    confirm_ttl_seconds: float = 24 * 3600   # 확인 카드 유효 시간 → CONFIRM_EXPIRED

    def __post_init__(self) -> None:
        for name, kind in (("llm", ServiceLlm), ("answer_mode", AnswerMode), ("answer_prompt", AnswerPrompt),
                           ("after_tools", AfterToolsMode), ("plan_store", PlanStoreKind),
                           ("read_backend", ReadBackendKind)):
            if getattr(self, name) not in get_args(kind):
                raise SettingsError(f"{name}은 {get_args(kind)} 중 하나여야 합니다: {getattr(self, name)!r}")
        if not 0 < self.port < 65536:
            raise SettingsError(f"port가 범위 밖입니다: {self.port}")

    @property
    def checkpoint_db(self) -> Path:
        return Path(self.data_dir) / "checkpoints.sqlite"

    @property
    def threads_db(self) -> Path:
        return Path(self.data_dir) / "threads.sqlite"

    def with_(self, **changes) -> "ServiceSettings":
        return replace(self, **changes)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ServiceSettings":
        env = os.environ if environ is None else environ
        base = Settings.from_env(env)
        llm = env.get("GLOPLE_SERVICE_LLM", "real")
        plan_store = env.get("GLOPLE_SERVICE_PLAN_STORE", "spring" if llm == "real" else "memory")
        try:
            port = int(env.get("GLOPLE_SERVICE_PORT", "8095"))
        except ValueError:
            raise SettingsError("GLOPLE_SERVICE_PORT는 숫자여야 합니다") from None
        data_dir = env.get("GLOPLE_SERVICE_DATA_DIR")
        return cls(
            host=env.get("GLOPLE_SERVICE_HOST", "127.0.0.1"),
            port=port,
            llm=llm,
            answer_mode=env.get("GLOPLE_ANSWER_MODE", "llm"),
            answer_prompt=base.answer_prompt,
            after_tools=base.after_tools,
            plan_store=plan_store,
            read_backend=env.get("GLOPLE_SERVICE_READ_BACKEND", "fixture"),
            model_base_url=base.model_base_url,
            answer_base_url=env.get("GLOPLE_ANSWER_BASE_URL") or None,
            spring_base_url=base.spring_base_url,
            data_dir=Path(data_dir).expanduser().resolve() if data_dir else DEFAULT_DATA_DIR,
            debug=env.get("GLOPLE_SERVICE_DEBUG", "0") == "1",
        )


__all__ = ["BASE_PATH", "DEFAULT_DATA_DIR", "ReadBackendKind", "ServiceLlm", "ServiceSettings"]

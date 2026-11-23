"""서비스가 쓸 그래프 의존성(design.md 12): real = real_deps(설정의 모델 주소·Spring 일정 저장소), fake = 가짜 모델.

- real: 라우터·답변 ChatOpenAI(GLOPLE_MODEL_BASE_URL, 답변만 GLOPLE_ANSWER_BASE_URL로 바꿀 수 있음),
  일정 저장소 SpringPlanStore(토큰은 저장할 때 AgentContext에서 받아 그 요청에만 붙음), 읽기 도구는 설정대로
  (fixture = real_deps 기본인 가짜 Spring, spring = 공개 API 실제 호출. M6 어댑터가 들어오면 spring을 기본으로 바꿀 후보).
- fake: RuleScript 라우터 + ResultEchoScript 답변 + 가짜 Spring(fixture) + 메모리 저장소(설정이 spring이면 Spring 저장소).
  모델 서버에 접속하지 않는다(테스트·화면 개발용).
- 일정 검토 모델은 답변 모델과 같은 것(a3 서버). 답변이 template 모드면 검토도 템플릿만.
- (M7) after_tools=loop면 도구 결과 뒤에 라우터 모델이 답하거나 한 번 더 부른다(graph/after_tools.py). fake는 결과 뒤 요청을
  LoopAnswerScript가 받는다(AfterToolsScript로 첫 판단과 나눔). 일정 검토 모델은 그대로(answer_mode대로).
"""
from dataclasses import dataclass, replace

from langchain_core.language_models import BaseChatModel

from glople_agent.config import Settings
from glople_agent.graph.build import Deps, fake_deps, make_plan_store, real_deps
from glople_agent.llm.fake import AfterToolsScript, ResultEchoScript, RuleScript, fake_answer_model
from glople_agent.service.review import ReviewEchoScript
from glople_agent.service.settings import ServiceSettings


@dataclass
class ServiceDeps:
    graph: Deps
    review_model: BaseChatModel | None


def read_backend(settings: ServiceSettings):
    """None = real_deps·fake_deps 기본(가짜 Spring fixture)."""
    if settings.read_backend == "spring":
        from glople_agent.tools.adapters import live_backend

        return live_backend(settings.spring_base_url)
    return None


def build_service_deps(settings: ServiceSettings) -> ServiceDeps:
    store = make_plan_store(settings.plan_store, spring_base_url=settings.spring_base_url)
    if settings.llm == "fake":
        answer = fake_answer_model(ResultEchoScript()) if settings.answer_mode == "llm" else None
        router = AfterToolsScript(RuleScript()) if settings.after_tools == "loop" else RuleScript()
        deps = fake_deps(router, backend=read_backend(settings), store=store, answer_mode=settings.answer_mode,
                         answer_llm=answer, answer_prompt=settings.answer_prompt, retry_initial_interval=0.5,
                         after_tools=settings.after_tools)
        review = fake_answer_model(ReviewEchoScript()) if settings.answer_mode == "llm" else None
        return ServiceDeps(deps, review)
    # 환경 변수 설정(GLOPLE_*, M6 날씨 같은 새 칸 포함)에 서비스 설정만 덮는다
    base = replace(Settings.from_env(), model_base_url=settings.model_base_url,
                   spring_base_url=settings.spring_base_url, answer_mode=settings.answer_mode,
                   answer_prompt=settings.answer_prompt, plan_store=settings.plan_store,
                   after_tools=settings.after_tools)
    deps = real_deps(base, answer_mode=settings.answer_mode, backend=read_backend(settings), store=store,
                     answer_url=settings.answer_base_url, answer_prompt=settings.answer_prompt,
                     after_tools=settings.after_tools)
    return ServiceDeps(deps, deps.answer_llm)


__all__ = ["ServiceDeps", "build_service_deps", "read_backend"]

"""그래프 조립(design.md 5.4)과 호출부 도우미(run_turn·resume).

- 실행 config는 항상 {"configurable": {"thread_id"}, "recursion_limit": 25}. 토큰은 context=AgentContext로만.
- route·validate·collect·answer_llm은 add_conditional_edges(경로 목록), plan_prepare·review·save_plan은
  Command(goto) + destinations로 잇는다. Command를 내는 노드에는 정적 엣지를 달지 않는다(둘 다 실행되므로).
- tools → route 엣지는 없다. 도구 결과를 받은 모델이 다시 부르는 루프가 구조적으로 생기지 않는다.
"""
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, Interrupt, RetryPolicy
from pydantic import ValidationError

from glople_agent.answer.templates import fallback_text
from glople_agent.config import AnswerMode
from glople_agent.context import AgentContext
from glople_agent.graph import nodes
from glople_agent.graph.edges import (ROUTE_RETRY_ATTEMPTS, after_answer, after_validate, make_after_collect,
                                      make_after_route)
from glople_agent.llm.fake import fake_router_model
from glople_agent.llm.normalize import is_model_connection_error
from glople_agent.plan import InMemoryPlanStore, PlanDecision, PlanStore
from glople_agent.state import AgentState
from glople_agent.tools.envelope import tool_error_to_json
from glople_agent.tools.fake_backend import FakeBackend
from glople_agent.tools.registry import build_read_tools

RECURSION_LIMIT = 25   # 기본값 10007(사실상 무제한)이라 항상 최상위에 넣는다

NODE_NAMES = ("start_turn", "route", "validate", "tools", "collect", "answer_template", "answer_llm", "clarify",
              "direct_reply", "fallback", "plan_prepare", "review", "save_plan", "finish")


@dataclass
class Deps:
    """그래프 밖에서 주입하는 것(state에 넣지 않음). 테스트는 가짜로 바꿔 끼운다."""

    router_llm: BaseChatModel
    read_tools: Sequence[BaseTool]
    plan_store: PlanStore
    answer_llm: BaseChatModel | None = None          # M2
    sink: nodes.TrajectorySink | None = None         # 궤적 한 줄(M1부터 파일)
    answer_mode: AnswerMode = "template"
    retry_initial_interval: float = 0.5              # RetryPolicy 대기(테스트는 0)
    # 지역이 모호하면(광주) 후보를 되물을 도구. 지역을 실제로 쓰는 백엔드가 없는 M1은 비움(S1·M6에서 켬)
    region_clarify_tools: frozenset[str] = frozenset()


def route_retry_policy(initial_interval: float = 0.5) -> RetryPolicy:
    # 연결 끊김만 1회 더(타임아웃은 APIConnectionError 하위 클래스라 is_model_connection_error가 뺌)
    return RetryPolicy(max_attempts=ROUTE_RETRY_ATTEMPTS, initial_interval=initial_interval, jitter=False,
                       retry_on=is_model_connection_error)


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    tool_names = [t.name for t in deps.read_tools]
    g = StateGraph(AgentState, context_schema=AgentContext)
    g.add_node("start_turn", nodes.start_turn)
    g.add_node("route", nodes.make_route(deps.router_llm),
               retry_policy=route_retry_policy(deps.retry_initial_interval))
    g.add_node("validate", nodes.make_validate(tool_names, region_clarify_tools=deps.region_clarify_tools))
    g.add_node("tools", ToolNode(list(deps.read_tools), handle_tool_errors=tool_error_to_json))
    g.add_node("collect", nodes.collect)
    g.add_node("answer_template", nodes.answer_template)
    g.add_node("answer_llm", nodes.make_answer_llm(deps.answer_llm))
    g.add_node("clarify", nodes.clarify)
    g.add_node("direct_reply", nodes.direct_reply)
    g.add_node("fallback", nodes.fallback)
    g.add_node("plan_prepare", nodes.plan_prepare, destinations=("review", "finish"))
    g.add_node("review", nodes.review, destinations=("save_plan", "plan_prepare", "finish"))
    g.add_node("save_plan", nodes.make_save_plan(deps.plan_store), destinations=("finish", "review"))
    g.add_node("finish", nodes.make_finish(deps.sink))

    g.add_edge(START, "start_turn")
    g.add_edge("start_turn", "route")
    g.add_conditional_edges("route", make_after_route(deps.answer_mode),
                            ["validate", "direct_reply", "route", "fallback"])
    g.add_conditional_edges("validate", after_validate, ["tools", "plan_prepare", "clarify", "fallback"])
    g.add_edge("tools", "collect")
    g.add_conditional_edges("collect", make_after_collect(deps.answer_mode),
                            ["answer_llm", "answer_template", "clarify"])
    g.add_conditional_edges("answer_llm", after_answer, ["finish", "answer_template"])
    for name in ("answer_template", "clarify", "direct_reply", "fallback"):
        g.add_edge(name, "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


def mermaid(graph: CompiledStateGraph) -> str:
    """그래프 그림(텍스트). draw_mermaid_png()는 외부 mermaid.ink를 부르므로 쓰지 않는다."""
    return graph.get_graph().draw_mermaid()


# --- 가짜 의존성(M0 CLI·테스트) -----------------------------------------------------------

def fake_deps(script: Callable, *, backend: Any = None, store: PlanStore | None = None,
              answer_mode: AnswerMode = "template", sink: nodes.TrajectorySink | None = None,
              retry_initial_interval: float = 0.0) -> Deps:
    """가짜 라우터(script) + 가짜 백엔드 + 메모리 저장소."""
    return Deps(router_llm=fake_router_model(script), read_tools=build_read_tools(backend or FakeBackend()),
                plan_store=store if store is not None else InMemoryPlanStore(), sink=sink,
                answer_mode=answer_mode, retry_initial_interval=retry_initial_interval)


# --- 호출부 도우미 ----------------------------------------------------------------------

def new_thread_id() -> str:
    return str(uuid.uuid4())


def run_config(thread_id: str, *, recursion_limit: int = RECURSION_LIMIT) -> dict:
    # configurable에는 thread_id만(문자열·숫자 값은 체크포인트 메타데이터에 평문으로 복사됨)
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit}


class InterruptNotPending(Exception):
    """대기 중인 확인 카드가 없거나 id가 다름(9.2 첫 번째 겹, 서비스는 409 INTERRUPT_NOT_PENDING)."""

    code = "INTERRUPT_NOT_PENDING"


class InvalidResume(ValueError):
    """재개 값이 PlanDecision 모양이 아님(서비스는 422). 같은 카드에 다시 답할 수 있다."""

    def __init__(self, error: ValidationError) -> None:
        super().__init__(f"재개 값이 확인 카드 형식에 맞지 않아요({error.error_count()}곳)")
        self.errors = error.errors(include_url=False, include_input=False)


def is_invalid_resume(e: BaseException) -> bool:
    """langgraph 1.2.12에는 errors.is_invalid_resume이 없어 대신 쓴다(api-notes).
    interrupt(response_schema=PlanDecision)는 TypeAdapter 검증 오류(title 'PlanDecision')를 그대로 올린다."""
    return isinstance(e, InvalidResume) or (isinstance(e, ValidationError) and e.title == PlanDecision.__name__)


@dataclass
class TurnResult:
    thread_id: str
    path: list[str] = field(default_factory=list)       # 이번 invoke에서 돈 노드(updates 순서)
    cards: list[dict] = field(default_factory=list)     # custom 스트림의 card 이벤트
    interrupts: tuple[Interrupt, ...] = ()               # 대기 중인 확인 카드(get_state 기준)
    values: dict = field(default_factory=dict)           # 끝난 뒤 state 값
    error: str | None = None                             # "recursion" 등 호출부가 잡은 오류

    @property
    def pending(self) -> Interrupt | None:
        return self.interrupts[0] if self.interrupts else None

    @property
    def done(self) -> bool:
        return self.pending is None

    @property
    def final_text(self) -> str | None:
        if self.error == "recursion":
            return fallback_text("steps")
        return self.values.get("final_text") if self.done else None

    @property
    def reply_kind(self) -> str | None:
        if self.error == "recursion":
            return "fallback"
        return self.values.get("reply_kind") if self.done else None


EventCallback = Callable[[str, Any], None]   # ("node", 이름) / ("card", 카드)


def _stream(graph: CompiledStateGraph, inp: Any, config: dict, context: AgentContext | None,
            on_event: EventCallback | None) -> TurnResult:
    result = TurnResult(thread_id=config["configurable"]["thread_id"])
    try:
        for part in graph.stream(inp, config, context=context, stream_mode=["updates", "custom"], version="v2"):
            if part["type"] == "updates":
                for name in part["data"]:
                    if name != "__interrupt__":
                        result.path.append(name)
                        if on_event:
                            on_event("node", name)
            elif part["type"] == "custom" and isinstance(part["data"], Mapping) \
                    and part["data"].get("event") == "card":
                result.cards.append(part["data"]["card"])
                if on_event:
                    on_event("card", part["data"]["card"])
    except GraphRecursionError:
        result.error = "recursion"   # 정해진 사과 문장으로(design.md 5.4)
    snapshot = graph.get_state(config)
    result.interrupts = tuple(snapshot.interrupts)   # 스트림 조각 모양에 기대지 않고 get_state로 읽음
    result.values = dict(snapshot.values)
    if result.error == "recursion" and not snapshot.next and result.path[-1:] == ["finish"]:
        # 1.2.12는 마지막 노드가 remaining_steps 0에서 END에 닿아도 그 뒤에 오류를 낸다(api-notes).
        # finish가 이미 답을 저장했으므로 성공으로 본다(보여 주는 답 = 대화 기록의 답)
        result.error = None
    return result


def run_turn(graph: CompiledStateGraph, thread_id: str, text: str, context: AgentContext | None, *,
             recursion_limit: int = RECURSION_LIMIT, on_event: EventCallback | None = None) -> TurnResult:
    """사용자 한 마디로 한 턴. 입력은 {"messages": [HumanMessage]}뿐이고 나머지는 start_turn이 정한다."""
    config = run_config(thread_id, recursion_limit=recursion_limit)
    return _stream(graph, {"messages": [HumanMessage(content=text)]}, config, context, on_event)


def pending_interrupt(graph: CompiledStateGraph, thread_id: str) -> Interrupt | None:
    interrupts = graph.get_state(run_config(thread_id)).interrupts
    return interrupts[0] if interrupts else None


def resume(graph: CompiledStateGraph, thread_id: str, decision: PlanDecision | Mapping[str, Any],
           context: AgentContext | None, *, interrupt_id: str,
           recursion_limit: int = RECURSION_LIMIT, on_event: EventCallback | None = None) -> TurnResult:
    """확인 카드에 답한다. interrupt_id는 호출부가 화면에 띄운 카드의 id(TurnResult.pending.id)이고 필수다.

    대기 카드가 없거나 id가 다르면 InterruptNotPending(같은 재개 두 번, edit·401 뒤 새 카드에 예전 답 →
    거절), 모양이 틀리면 InvalidResume(그래프를 부르기 전에 검사, 카드는 그대로). 재개는 {interrupt_id: dict}로 보낸다.
    """
    pending = pending_interrupt(graph, thread_id)
    if pending is None or interrupt_id != pending.id:
        raise InterruptNotPending("대기 중인 확인 카드가 없거나 이미 답했어요.")
    try:
        answer = decision if isinstance(decision, PlanDecision) else PlanDecision.model_validate(decision)
    except ValidationError as e:
        raise InvalidResume(e) from None
    config = run_config(thread_id, recursion_limit=recursion_limit)
    # 모델 객체를 넣으면 STRICT 역직렬화에서 막혀 경고가 나므로 dict(as_resume)로
    return _stream(graph, Command(resume={pending.id: answer.as_resume()}), config, context, on_event)


def cancel_pending(graph: CompiledStateGraph, thread_id: str, context: AgentContext | None) -> TurnResult | None:
    """확인 카드가 떠 있는 스레드에 새 메시지가 오면 먼저 reject로 닫는다(9.3). 대기 카드가 없으면 None.
    화면에 보인 id 대신 지금 대기 중인 id로 닫는 곳은 여기뿐이다(어떤 카드든 취소는 안전함)."""
    pending = pending_interrupt(graph, thread_id)
    if pending is None:
        return None
    return resume(graph, thread_id, {"decision": "reject"}, context, interrupt_id=pending.id)


__all__ = [
    "Deps", "EventCallback", "InterruptNotPending", "InvalidResume", "NODE_NAMES", "RECURSION_LIMIT",
    "TurnResult", "build_graph", "cancel_pending", "fake_deps", "is_invalid_resume", "mermaid", "new_thread_id",
    "pending_interrupt", "resume", "route_retry_policy", "run_config", "run_turn",
]

"""그래프 조립(design.md 5.4)과 호출부 도우미(run_turn·resume).

- 실행 config는 항상 {"configurable": {"thread_id"}, "recursion_limit": 25}. 토큰은 context=AgentContext로만.
- route·validate·collect·answer_llm은 add_conditional_edges(경로 목록), plan_prepare·review·save_plan은
  Command(goto) + destinations로 잇는다. Command를 내는 노드에는 정적 엣지를 달지 않는다(둘 다 실행되므로).
- tools → route 엣지는 없다. 기본(after_tools="answer")에서는 도구 결과를 받은 모델이 다시 부르는 루프가 구조적으로 생기지 않는다.
  (M7) after_tools="loop"면 collect → route_after_tools(라우터 모델에 결과를 보냄) → validate_after_tools → tools로 한 라운드
  더 갈 수 있다(최대 2라운드, graph/after_tools.py). 이때 answer_llm 노드는 붙이지 않는다(글 답은 라우터가 쓰고 가드가 봄).
- (M3) Deps.plan_graph: "flat"(기본, M0~M2와 같음)이면 일정 노드 셋을 부모에 그대로, "subgraph"면 일정 서브그래프
  (graph/plan_subgraph.py)를 부모 노드 "plan" 하나로 붙인다(design.md 9.3). 호출부 도우미(run_turn·resume·TurnResult)는
  두 판에서 같은 값을 돌려준다: 스트림을 subgraphs=True로 받아 path에는 안쪽 노드 이름을 그대로 넣고 부모의 "plan"은 뺀다.
"""
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, Interrupt, RetryPolicy, StateSnapshot
from pydantic import ValidationError

from glople_agent.answer.templates import fallback_text
from glople_agent.config import (AfterToolsMode, AnswerMode, AnswerPrompt, PlanGraphMode, PlanStoreKind, Settings,
                                 WeatherMode, load_settings)
from glople_agent.context import AgentContext
from glople_agent.graph import after_tools, nodes
from glople_agent.graph.edges import (ROUTE_RETRY_ATTEMPTS, after_answer, after_route_after_tools, after_validate,
                                      after_validate_after_tools, make_after_collect, make_after_route)
from glople_agent.graph.grounding import REGION_TOOLS
from glople_agent.graph.plan_subgraph import PLAN_NODE, add_plan_nodes, build_plan_subgraph
from glople_agent.llm.fake import fake_router_model
from glople_agent.llm.normalize import is_model_connection_error
from glople_agent.plan import InMemoryPlanStore, PlanDecision, PlanStore
from glople_agent.state import AgentState, ParentState
from glople_agent.tools.envelope import tool_error_to_json
from glople_agent.tools.fake_backend import FakeBackend
from glople_agent.tools.registry import build_read_tools

if TYPE_CHECKING:
    from glople_agent.llm.cassette import Cassette

RECURSION_LIMIT = 25   # 기본값 10007(사실상 무제한)이라 항상 최상위에 넣는다

NODE_NAMES = ("start_turn", "route", "validate", "tools", "collect", "answer_template", "answer_llm", "clarify",
              "direct_reply", "fallback", "plan_prepare", "review", "save_plan", "finish")
# 서브그래프 판(M3)의 부모 노드: 일정 노드 셋 대신 "plan" 하나(안은 plan_subgraph.PLAN_NODES)
SUBGRAPH_NODE_NAMES = ("start_turn", "route", "validate", "tools", "collect", "answer_template", "answer_llm",
                       "clarify", "direct_reply", "fallback", PLAN_NODE, "finish")
NS_SEP = "/"   # TurnResult.qualified_path에서 서브그래프 이름과 안쪽 노드를 잇는 글자(plan/review)
# M7 loop 판: answer_llm 대신 도구 결과 뒤 루프 노드 둘
LOOP_NODES = ("route_after_tools", "validate_after_tools")
LOOP_NODE_NAMES = tuple(n for n in NODE_NAMES if n != "answer_llm") + LOOP_NODES


@dataclass
class Deps:
    """그래프 밖에서 주입하는 것(state에 넣지 않음). 테스트는 가짜로 바꿔 끼운다."""

    router_llm: BaseChatModel
    read_tools: Sequence[BaseTool]
    plan_store: PlanStore
    answer_llm: BaseChatModel | None = None          # M2 a3(answer_mode="llm"일 때만 씀). 도구를 묶지 않는다
    sink: nodes.TrajectorySink | None = None         # 궤적 한 줄(M1부터 파일)
    answer_mode: AnswerMode = "template"
    retry_initial_interval: float = 0.5              # RetryPolicy 대기(테스트는 0)
    # 지역이 모호하면(광주) 후보를 되물을 도구. M1은 비웠고(지역을 쓰는 백엔드가 없었음) M6부터 S1 검색·날씨(grounding.REGION_TOOLS)
    region_clarify_tools: frozenset[str] = REGION_TOOLS
    plan_graph: PlanGraphMode = "flat"               # M3: 일정 노드를 평평하게(기본) / 서브그래프로
    answer_prompt: AnswerPrompt = "v1"               # M4: 답변 system 문구(llm/prompts.py)
    after_tools: AfterToolsMode = "answer"           # M7: 도구 결과 뒤 answer(지금 그대로) / loop(라우터가 답하거나 한 번 더)
    after_tools_llm: BaseChatModel | None = None     # M7 loop: 결과 뒤 요청만 받을 모델(없으면 router_llm). 예: v3 첫 판단 + v4 결과 뒤


def route_retry_policy(initial_interval: float = 0.5) -> RetryPolicy:
    # 연결 끊김만 1회 더(타임아웃은 APIConnectionError 하위 클래스라 is_model_connection_error가 뺌)
    return RetryPolicy(max_attempts=ROUTE_RETRY_ATTEMPTS, initial_interval=initial_interval, jitter=False,
                       retry_on=is_model_connection_error)


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver | None = None, *,
                plan_graph: PlanGraphMode | None = None) -> CompiledStateGraph:
    """plan_graph(없으면 deps.plan_graph): "flat"은 M0~M2 그대로, "subgraph"는 일정 노드를 서브그래프 "plan"으로."""
    mode = plan_graph or deps.plan_graph
    if mode not in ("flat", "subgraph"):
        raise ValueError(f"plan_graph는 flat 또는 subgraph입니다: {mode!r}")
    if deps.after_tools not in ("answer", "loop"):
        raise ValueError(f"after_tools는 answer 또는 loop입니다: {deps.after_tools!r}")
    subgraph = mode == "subgraph"
    loop = deps.after_tools == "loop"
    tool_names = [t.name for t in deps.read_tools]
    # 서브그래프 판의 부모에는 plan_* 키가 없다(서브그래프 자기 키, design.md 9.3)
    g = StateGraph(ParentState if subgraph else AgentState, context_schema=AgentContext)
    g.add_node("start_turn", nodes.start_turn)
    g.add_node("route", nodes.make_route(deps.router_llm),
               retry_policy=route_retry_policy(deps.retry_initial_interval))
    g.add_node("validate", nodes.make_validate(tool_names, region_clarify_tools=deps.region_clarify_tools))
    g.add_node("tools", ToolNode(list(deps.read_tools), handle_tool_errors=tool_error_to_json))
    g.add_node("collect", nodes.collect)
    g.add_node("answer_template", nodes.answer_template)
    if loop:   # M7: 라우터 모델이 결과를 보고 답하거나 한 번 더 부름(연결 오류 재시도는 route와 같음)
        g.add_node("route_after_tools", after_tools.make_route_after_tools(deps.after_tools_llm or deps.router_llm),
                   retry_policy=route_retry_policy(deps.retry_initial_interval))
        g.add_node("validate_after_tools",
                   after_tools.make_validate_after_tools(tool_names, region_clarify_tools=deps.region_clarify_tools))
    else:
        g.add_node("answer_llm", nodes.make_answer_llm(deps.answer_llm, prompt=deps.answer_prompt))
    g.add_node("clarify", nodes.clarify)
    g.add_node("direct_reply", nodes.direct_reply)
    g.add_node("fallback", nodes.fallback)
    if subgraph:
        g.add_node(PLAN_NODE, build_plan_subgraph(deps.plan_store))   # 서브그래프가 END에 닿으면 정적 엣지로 finish
        g.add_edge(PLAN_NODE, "finish")
    else:
        add_plan_nodes(g, deps.plan_store, done="finish")
    g.add_node("finish", nodes.make_finish(deps.sink))

    g.add_edge(START, "start_turn")
    g.add_edge("start_turn", "route")
    g.add_conditional_edges("route", make_after_route(deps.answer_mode),
                            ["validate", "direct_reply", "route", "fallback"])
    if subgraph:   # after_validate가 고른 "plan_prepare"를 서브그래프 노드로(엣지 함수는 두 판이 같음)
        g.add_conditional_edges("validate", after_validate, {"tools": "tools", "plan_prepare": PLAN_NODE,
                                                             "clarify": "clarify", "fallback": "fallback"})
    else:
        g.add_conditional_edges("validate", after_validate, ["tools", "plan_prepare", "clarify", "fallback"])
    g.add_edge("tools", "collect")
    if loop:
        g.add_conditional_edges("collect", make_after_collect(deps.answer_mode, "loop"),
                                ["route_after_tools", "answer_template", "clarify"])
        g.add_conditional_edges("route_after_tools", after_route_after_tools,
                                ["validate_after_tools", "answer_template", "finish"])
        g.add_conditional_edges("validate_after_tools", after_validate_after_tools, ["tools", "answer_template"])
    else:
        g.add_conditional_edges("collect", make_after_collect(deps.answer_mode),
                                ["answer_llm", "answer_template", "clarify"])
        g.add_conditional_edges("answer_llm", after_answer, ["finish", "answer_template"])
    for name in ("answer_template", "clarify", "direct_reply", "fallback"):
        g.add_edge(name, "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


def mermaid(graph: CompiledStateGraph, *, xray: bool = False) -> str:
    """그래프 그림(텍스트). draw_mermaid_png()는 외부 mermaid.ink를 부르므로 쓰지 않는다.
    xray=True면 서브그래프 안(plan:plan_prepare …)까지 그린다(서브그래프 판)."""
    return graph.get_graph(xray=xray).draw_mermaid()


def subgraph_nodes(graph: CompiledStateGraph) -> frozenset[str]:
    """부모 노드 중 서브그래프인 것(서브그래프 판이면 {"plan"}, 평평한 판이면 빈 집합)."""
    return frozenset({PLAN_NODE} & set(graph.nodes))


PLAN_GRAPH_META = "plan_graph"   # 체크포인트 metadata 키: 그 체크포인트를 만든 판("flat" | "subgraph")


def plan_graph_of(graph: CompiledStateGraph) -> PlanGraphMode:
    return "subgraph" if subgraph_nodes(graph) else "flat"


# --- 가짜 의존성(M0 CLI·테스트) -----------------------------------------------------------

def fake_deps(script: Callable, *, backend: Any = None, store: PlanStore | None = None,
              answer_mode: AnswerMode = "template", sink: nodes.TrajectorySink | None = None,
              retry_initial_interval: float = 0.0, answer_llm: BaseChatModel | None = None,
              plan_graph: PlanGraphMode = "flat", answer_prompt: AnswerPrompt = "v1",
              after_tools: AfterToolsMode = "answer") -> Deps:
    """가짜 라우터(script) + 가짜 백엔드 + 메모리 저장소. answer_llm(M2)은 llm 모드에서만 쓴다
    (예: fake_answer_model(ResultEchoScript()) 또는 CassetteScript). plan_graph(M3)·answer_prompt(M4)·after_tools(M7)는
    Deps 그대로.
    after_tools="loop"이면 script가 도구 결과 뒤 요청도 받는다(llm.fake.AfterToolsScript로 첫 판단과 나눠 쓸 수 있음)."""
    return Deps(router_llm=fake_router_model(script), read_tools=build_read_tools(backend or FakeBackend()),
                plan_store=store if store is not None else InMemoryPlanStore(), sink=sink,
                answer_mode=answer_mode, retry_initial_interval=retry_initial_interval, answer_llm=answer_llm,
                plan_graph=plan_graph, answer_prompt=answer_prompt, after_tools=after_tools)


def make_plan_store(kind: PlanStoreKind = "memory", *, spring_base_url: str | None = None,
                    transport: Any = None) -> PlanStore:
    """일정 저장소: "memory"(기본, InMemoryPlanStore) 또는 "spring"(plan/spring_store.py, S2 /api/travel-plans).
    spring은 이 함수가 접속하지 않는다(첫 요청은 save_plan이 승인 뒤에 보냄). transport는 테스트용(respx·MockTransport)."""
    if kind == "memory":
        return InMemoryPlanStore()
    if kind == "spring":
        from glople_agent.plan.spring_store import SpringPlanStore
        from glople_agent.tools.spring import SpringClient

        base = spring_base_url or load_settings().spring_base_url
        return SpringPlanStore(SpringClient(base, transport=transport))
    raise ValueError(f"모르는 일정 저장소: {kind!r} (memory 또는 spring)")


def real_deps(target: Settings | str | None = None, *, answer_mode: AnswerMode | None = None, backend: Any = None,
              store: PlanStore | None = None, sink: nodes.TrajectorySink | None = None,
              retry_initial_interval: float = 0.5, timeout: float | None = None,
              record: "Cassette | str | Path | None" = None, answer_url: str | None = None,
              answer_prompt: AnswerPrompt | None = None, plan_graph: PlanGraphMode = "flat",
              plan_store: PlanStoreKind | None = None, spring_base_url: str | None = None,
              weather: WeatherMode | None = None, after_tools: AfterToolsMode | None = None,
              after_tools_url: str | None = None) -> Deps:
    """실제 모델 서버(mlx_lm.server, OpenAI 호환)에 붙는 의존성(M2): 라우터·답변 ChatOpenAI(llm/models.py, 같은 서버)
    + 가짜 Spring(FakeBackend: fixture + MockTransport, 네트워크 없음) + 메모리 일정 저장소.

    target: Settings(model_base_url·answer_mode·answer_prompt·plan_store를 씀) 또는 base_url 문자열
    (예: "http://127.0.0.1:8090/v1"). None이면 환경 변수 설정(GLOPLE_MODEL_BASE_URL, 기본 8090). answer_mode를 주지
    않으면 Settings의 값, 문자열·None이면 "llm". template 모드면 답변 모델을 만들지 않는다(라우터만 실제).
    answer_url(M4): 답변만 다른 서버로(a1: 기본 모델 8091). 없으면 라우터와 같은 서버(a3).
    answer_prompt(M4): 답변 system 문구 "v1"(기본, design.md 6.4) / "v2"(llm/prompts.ANSWER_SYSTEM_PROMPT_V2).
    plan_store(M3): "memory"(기본) / "spring"(S2 /api/travel-plans, 사용자 토큰). store를 직접 주면 그것이 이긴다.
    weather(M6): backend를 주지 않을 때 가짜 Spring에 붙일 날씨 — "live"(Open-Meteo, Settings 기본) / "fixture" / "off".
    없으면 Settings(GLOPLE_WEATHER)의 값. 테스트는 conftest가 GLOPLE_WEATHER=fixture로 둔다.
    after_tools(M7): "answer"(기본) / "loop"(도구 결과 뒤 라우터가 답하거나 한 번 더 부름). 없으면 Settings의 값, 문자열·None이면
    answer(환경 변수는 Settings로만 읽음). loop에서도 답변 모델은 answer_mode대로 만들지만(일정 검토가 씀) 그래프에는 붙지 않는다.
    after_tools_url(M7): loop에서 결과 뒤 요청만 다른 서버로(없으면 Settings.after_tools_base_url, 그것도 없으면 라우터 서버).
    record: 카세트(또는 경로)를 주면 두 모델을 RecordingChatModel로 감싸 요청·응답을 녹화한다(llm/cassette.py).
    이 함수는 모델을 만들기만 하고 서버에 접속하지 않는다(첫 요청은 그래프가 돌 때). 서버는 오너가 serve.sh로 띄운다.
    """
    from glople_agent.llm.models import DEFAULT_TIMEOUT, answer_model, router_model   # langchain-openai는 쓸 때만

    settings = target if isinstance(target, Settings) else None
    if settings is not None:
        base_url, mode = settings.model_base_url, answer_mode or settings.answer_mode
    else:
        base_url, mode = target or load_settings().model_base_url, answer_mode or "llm"
    prompt = answer_prompt or (settings.answer_prompt if settings is not None else "v1")
    loop_mode = after_tools or (settings.after_tools if settings is not None else "answer")
    store_kind = plan_store or (settings.plan_store if settings is not None else "memory")
    seconds = DEFAULT_TIMEOUT if timeout is None else timeout
    router: BaseChatModel = router_model(base_url, timeout=seconds)
    answer: BaseChatModel | None = (answer_model(answer_url or base_url, timeout=seconds) if mode == "llm"
                                    else None)
    loop_url = after_tools_url or (settings.after_tools_base_url if settings is not None else None)
    after: BaseChatModel | None = (router_model(loop_url, timeout=seconds)
                                   if loop_mode == "loop" and loop_url and loop_url != base_url else None)
    if record is not None:
        from glople_agent.llm.cassette import Cassette, recording_model

        cassette = record if isinstance(record, Cassette) else Cassette.open(Path(record))
        router = recording_model(router, cassette, profile="router")
        answer = recording_model(answer, cassette, profile="answer") if answer is not None else None
        after = recording_model(after, cassette, profile="after_tools") if after is not None else None
    if store is None:
        spring = spring_base_url or (settings.spring_base_url if settings is not None else None)
        store = make_plan_store(store_kind, spring_base_url=spring)
    if backend is None:
        weather_mode = weather or (settings.weather if settings is not None else load_settings().weather)
        backend = FakeBackend(weather=weather_mode)
    return Deps(router_llm=router, read_tools=build_read_tools(backend), plan_store=store,
                answer_llm=answer, sink=sink, answer_mode=mode, retry_initial_interval=retry_initial_interval,
                plan_graph=plan_graph, answer_prompt=prompt, after_tools=loop_mode, after_tools_llm=after)


# --- 호출부 도우미 ----------------------------------------------------------------------

def new_thread_id() -> str:
    return str(uuid.uuid4())


def run_config(thread_id: str, *, recursion_limit: int = RECURSION_LIMIT, checkpoint_id: str | None = None) -> dict:
    # configurable에는 thread_id만(문자열·숫자 값은 체크포인트 메타데이터에 평문으로 복사됨).
    # checkpoint_id(M3 시간 여행)를 주면 그 체크포인트에서 이어 새 갈래를 만든다(체크포인트 id는 비밀이 아님)
    configurable = {"thread_id": thread_id}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable, "recursion_limit": recursion_limit}


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
    tokens: list[str] = field(default_factory=list)      # M2: custom 스트림의 답변 미리보기 조각(answer_llm)
    # M3: 서브그래프 판에서도 path는 안쪽 노드 이름 그대로(부모의 "plan"은 뺌). 아래 둘은 판을 드러내는 값
    qualified_path: list[str] = field(default_factory=list)   # 안쪽 노드는 "plan/review"처럼(평평한 판은 path와 같음)
    graph_path: list[str] = field(default_factory=list)       # 부모 그래프에서 끝난 노드("plan" 포함)
    waiting: tuple[str, ...] = ()                             # 멈춘 자리(가장 안쪽 next, 예: ("plan/review",))

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

    @property
    def preview(self) -> str:
        """스트리밍한 미리보기 글(가드 전). 가드가 바꾸면 final_text와 다르다."""
        return "".join(self.tokens)

    @property
    def answer_trace(self) -> dict:
        """answer_llm 가드 판정(trace["answer_llm"]): guard·reasons·reply_kind·replaced …. llm 모드가 아니면 {}."""
        return dict((self.values.get("trace") or {}).get("answer_llm") or {})

    @property
    def after_tools(self) -> list[dict]:
        """(M7 loop) 도구 결과 뒤 라운드마다의 판단(trace["after_tools"]: round·kind·decision·next). answer 모드는 []."""
        return list((self.values.get("trace") or {}).get("after_tools") or [])

    @property
    def tool_rounds(self) -> int:
        """이번 턴에 도구가 실행된 라운드 수(answer 모드는 0 또는 1, loop는 최대 2)."""
        from glople_agent.graph.after_tools import tool_rounds

        return len(tool_rounds(self.values.get("messages") or []))

    @property
    def replaced(self) -> bool:
        """미리보기를 보낸 뒤 가드가 템플릿 답으로 갈아끼웠는지(SSE message{replaced}, design.md 6.5)."""
        return bool(self.answer_trace.get("replaced")) and self.done


EventCallback = Callable[[str, Any], None]   # ("node", 이름) / ("card", 카드) / ("token", 미리보기 조각, M2)


def _ns_names(ns: Sequence[str]) -> list[str]:
    """스트림 namespace(("plan:<task id>",)) → 노드 이름(["plan"])."""
    return [part.split(":", 1)[0] for part in ns]


def waiting_nodes(graph: CompiledStateGraph, config: dict) -> tuple[str, ...]:
    """멈춘 자리: 서브그래프 안에서 멈췄으면 "plan/review"처럼 가장 안쪽 next, 아니면 부모 next."""
    snap = graph.get_state(config, subgraphs=True)
    for task in snap.tasks:
        state = task.state
        if isinstance(state, StateSnapshot) and state.next:
            return tuple(f"{task.name}{NS_SEP}{n}" for n in state.next)
    return tuple(snap.next)


def _stream(graph: CompiledStateGraph, inp: Any, config: dict, context: AgentContext | None,
            on_event: EventCallback | None) -> TurnResult:
    thread_id = config["configurable"]["thread_id"]
    result = TurnResult(thread_id=thread_id)
    containers = subgraph_nodes(graph)
    # 체크포인트 metadata에 판을 남긴다(다른 --plan-graph로 이어 가면 대기 카드를 잃으므로 CLI가 먼저 확인, 비밀 아님)
    config = {**config, "metadata": {**(config.get("metadata") or {}), PLAN_GRAPH_META: plan_graph_of(graph)}}
    try:
        for part in graph.stream(inp, config, context=context, stream_mode=["updates", "custom"], subgraphs=True,
                                 version="v2"):
            ns = _ns_names(part.get("ns") or ())
            if part["type"] == "updates":
                for name in part["data"]:
                    if name == "__interrupt__":
                        continue
                    if not ns:
                        result.graph_path.append(name)
                        if name in containers:   # 서브그래프가 END에 닿아 부모에 쓴 것(안쪽 노드는 이미 셈)
                            continue
                    result.path.append(name)
                    result.qualified_path.append(NS_SEP.join([*ns, name]))
                    if on_event:
                        on_event("node", name)
            elif part["type"] == "custom" and isinstance(part["data"], Mapping):
                event = part["data"].get("event")
                if event == "card":
                    result.cards.append(part["data"]["card"])
                    if on_event:
                        on_event("card", part["data"]["card"])
                elif event == "token":   # answer_llm 미리보기(가드 전)
                    result.tokens.append(part["data"]["text"])
                    if on_event:
                        on_event("token", part["data"]["text"])
    except GraphRecursionError:
        result.error = "recursion"   # 정해진 사과 문장으로(design.md 5.4)
    head = run_config(thread_id)     # 시간 여행(checkpoint_id)으로 돌았어도 결과는 스레드의 머리(새 갈래)에서 읽음
    snapshot = graph.get_state(head)
    result.interrupts = tuple(snapshot.interrupts)   # 스트림 조각 모양에 기대지 않고 get_state로 읽음
    result.values = dict(snapshot.values)
    result.waiting = waiting_nodes(graph, head) if snapshot.interrupts else ()
    if result.error == "recursion" and not snapshot.next and result.path[-1:] == ["finish"]:
        # 1.2.12는 마지막 노드가 remaining_steps 0에서 END에 닿아도 그 뒤에 오류를 낸다(api-notes).
        # finish가 이미 답을 저장했으므로 성공으로 본다(보여 주는 답 = 대화 기록의 답)
        result.error = None
    return result


def run_turn(graph: CompiledStateGraph, thread_id: str, text: str, context: AgentContext | None, *,
             recursion_limit: int = RECURSION_LIMIT, on_event: EventCallback | None = None,
             checkpoint_id: str | None = None) -> TurnResult:
    """사용자 한 마디로 한 턴. 입력은 {"messages": [HumanMessage]}뿐이고 나머지는 start_turn이 정한다.
    checkpoint_id(M3 /fork)를 주면 머리 대신 그 체크포인트 뒤에 이 말을 붙여 새 갈래를 만든다."""
    config = run_config(thread_id, recursion_limit=recursion_limit, checkpoint_id=checkpoint_id)
    return _stream(graph, {"messages": [HumanMessage(content=text)]}, config, context, on_event)


def pending_interrupt(graph: CompiledStateGraph, thread_id: str) -> Interrupt | None:
    interrupts = graph.get_state(run_config(thread_id)).interrupts
    return interrupts[0] if interrupts else None


def plan_state(graph: CompiledStateGraph, thread_id: str) -> dict:
    """일정 키(plan_draft·plan_edits·plan_notice)를 볼 수 있는 state 값.

    평평한 판은 부모 state 그대로다. 서브그래프 판은 확인 카드가 떠 있는 동안 get_state(subgraphs=True)의 plan 작업
    state(서브그래프 체크포인트)를 돌려주고, 서브그래프가 끝난 뒤에는 부모 값(일정 키 없음)을 돌려준다."""
    snap = graph.get_state(run_config(thread_id), subgraphs=True)
    for task in snap.tasks:
        if task.name == PLAN_NODE and isinstance(task.state, StateSnapshot):
            return dict(task.state.values)
    return dict(snap.values)


def fork(graph: CompiledStateGraph, checkpoint_config: Mapping[str, Any], context: AgentContext | None, *,
         recursion_limit: int = RECURSION_LIMIT, on_event: EventCallback | None = None) -> TurnResult:
    """시간 여행(M3 /fork): 지난 체크포인트(get_state_history의 snapshot.config)에서 다시 실행해 새 갈래를 만든다.

    - next가 있는 체크포인트면 그 노드부터 다시 돈다. 확인 카드를 기다리던 자리면 review가 다시 돌아 **새 id**의 카드가 뜨고
      (예전 재개 값은 쓰지 않음), 다른 결정을 할 수 있다. 서브그래프 판은 부모 체크포인트 단위라 plan_prepare부터 다시 돈다.
    - next가 없는(턴이 끝난) 체크포인트면 아무것도 돌지 않는다. 그 자리에 새 말을 붙이려면 run_turn(checkpoint_id=…).
    - 결과(TurnResult.values·interrupts)는 새 갈래의 머리 기준이다. 지난 체크포인트에 그대로 재개(Command(resume))를
      보내지 않는다: 그 체크포인트에 남은 예전 재개 값이 먼저 쓰여 예전 결정이 되풀이된다(api-notes M3)."""
    cfg = dict(checkpoint_config.get("configurable") or {})
    if not cfg.get("thread_id") or not cfg.get("checkpoint_id"):
        raise ValueError("fork에는 thread_id·checkpoint_id가 든 체크포인트 config가 필요합니다")
    if cfg.get("checkpoint_ns"):
        raise ValueError("서브그래프 안 체크포인트로는 fork하지 않습니다(부모 체크포인트를 고르세요)")
    config = run_config(cfg["thread_id"], recursion_limit=recursion_limit, checkpoint_id=cfg["checkpoint_id"])
    return _stream(graph, None, config, context, on_event)


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
    "Deps", "EventCallback", "InterruptNotPending", "InvalidResume", "LOOP_NODES", "LOOP_NODE_NAMES", "NODE_NAMES",
    "NS_SEP", "PLAN_GRAPH_META",
    "RECURSION_LIMIT", "plan_graph_of",
    "SUBGRAPH_NODE_NAMES", "TurnResult", "build_graph", "cancel_pending", "fake_deps", "fork", "is_invalid_resume",
    "make_plan_store", "mermaid", "new_thread_id", "pending_interrupt", "plan_state", "real_deps", "resume",
    "route_retry_policy", "run_config", "run_turn", "subgraph_nodes", "waiting_nodes",
]

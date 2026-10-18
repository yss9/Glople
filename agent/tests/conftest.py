"""공용 픽스처. 실행 환경변수는 langgraph를 import하기 전에 맞춘다(design.md 3.3)."""
import os

os.environ["LANGSMITH_TRACING"] = "false"            # 테스트는 어떤 경우에도 트레이싱을 보내지 않음
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")  # 역직렬화 허용 목록(실행 때와 같게)
os.environ["GLOPLE_WEATHER"] = "fixture"             # M6: real_deps·CLI 기본 날씨(live)를 테스트에서는 늘 가짜 Open-Meteo로
os.environ.pop("GLOPLE_AFTER_TOOLS", None)           # M7: 셸에서 loop를 켜 두었어도 기존 테스트는 기본(answer)으로

# M6: 테스트는 네트워크 없이 돈다. 루프백(가짜 OpenAI 서버 127.0.0.1 빈 포트) 밖으로의 이름 풀이·연결은 막는다
# (Open-Meteo·Spring 8080 밖 호스트에 실수로 요청이 나가면 바로 실패). httpx.MockTransport·respx는 소켓을 쓰지 않음.
import ipaddress  # noqa: E402
import socket  # noqa: E402

_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex


def _is_loopback(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    h = str(host).strip("[]").lower()
    if h in ("", "localhost", "localhost.") or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h.split("%")[0])
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def _guarded_getaddrinfo(host, *args, **kwargs):
    if not _is_loopback(host):
        raise socket.gaierror(socket.EAI_NONAME, f"테스트는 네트워크를 쓰지 않습니다: {host}")
    return _REAL_GETADDRINFO(host, *args, **kwargs)


def _check_address(sock, address) -> None:
    if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and not _is_loopback(address[0]):
        raise OSError(f"테스트는 네트워크를 쓰지 않습니다: {address[0]}")


def _guarded_connect(self, address):
    _check_address(self, address)
    return _REAL_CONNECT(self, address)


def _guarded_connect_ex(self, address):
    _check_address(self, address)
    return _REAL_CONNECT_EX(self, address)


socket.getaddrinfo = _guarded_getaddrinfo
socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex

from datetime import datetime  # noqa: E402

import pytest  # noqa: E402

from glople_agent import contract  # noqa: E402
from glople_agent.context import AgentContext, BearerToken  # noqa: E402

FIXED_NOW = datetime.fromisoformat("2026-10-17T12:00:00+09:00")  # 토요일
FIXED_SEED = 20261017
SENTINEL_TOKEN = "SENTINEL-TOKEN-7c41e9a2d0b35f68"  # 저장소·로그·요청에서 0건이어야 하는 가짜 JWT
TEST_USER_ID = "test-user-1"

TRAINING_DATA_DIR = contract.TRAINING_DIR / "data"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "needs_training_data(*paths): training/data 아래 파일이 없으면 skip (기본 tool_calling_v2/test.jsonl)",
    )


def pytest_runtest_setup(item):
    # training/data는 gitignore 대상(시드로 다시 만듦)이라 없으면 그 테스트만 skip
    for marker in item.iter_markers(name="needs_training_data"):
        for rel in marker.args or ("tool_calling_v2/test.jsonl",):
            if not (TRAINING_DATA_DIR / rel).exists():
                pytest.skip(f"training/data/{rel} 없음 (build_tool_dataset.py로 다시 만들 수 있음)")


@pytest.fixture
def fixed_now() -> datetime:
    return FIXED_NOW


@pytest.fixture
def seed() -> int:
    return FIXED_SEED


@pytest.fixture
def sentinel_raw() -> str:
    return SENTINEL_TOKEN


@pytest.fixture
def sentinel_token() -> BearerToken:
    return BearerToken(SENTINEL_TOKEN)


@pytest.fixture
def agent_context(sentinel_token, fixed_now, seed) -> AgentContext:
    return AgentContext(user_id=TEST_USER_ID, bearer=sentinel_token, request_id="req-test-1",
                        now=fixed_now, seed=seed)


@pytest.fixture
def anon_context(fixed_now, seed) -> AgentContext:
    return AgentContext(user_id=None, bearer=None, request_id="req-test-anon", now=fixed_now, seed=seed)


# --- 그래프 팩토리(design.md 14.2: make_graph(script, store), InMemorySaver) ---------------------

from dataclasses import dataclass, field  # noqa: E402

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from glople_agent.graph import (NS_SEP, Deps, TurnResult, build_graph, fake_deps, new_thread_id,  # noqa: E402
                                plan_state, resume, run_config, run_turn, waiting_nodes)
from glople_agent.plan import InMemoryPlanStore  # noqa: E402
from glople_agent.tools.fake_backend import FakeBackend  # noqa: E402


@dataclass
class GraphKit:
    """가짜 라우터·가짜 백엔드·메모리 저장소로 조립한 그래프와 그 부품. thread 하나를 들고 다닌다."""

    graph: object
    deps: Deps
    store: InMemoryPlanStore
    backend: FakeBackend
    saver: InMemorySaver
    thread: str = field(default_factory=new_thread_id)
    sink: list = field(default_factory=list)
    last: TurnResult | None = None   # 마지막으로 받은 결과(호출부 화면에 떠 있는 것)
    plan_graph: str = "flat"         # M3: "flat" | "subgraph"

    @property
    def model(self):
        return self.deps.router_llm   # ScriptedChatModel: .calls에 받은 (messages, kwargs)

    @property
    def config(self) -> dict:
        return run_config(self.thread)

    def turn(self, text: str, ctx, **kw) -> TurnResult:
        self.last = run_turn(self.graph, self.thread, text, ctx, **kw)
        return self.last

    def resume(self, decision, ctx, *, interrupt_id: str | None = None, **kw) -> TurnResult:
        """CLI처럼 마지막 결과에 띄운 카드의 id로 답한다. 다른 id(예전 카드)는 interrupt_id로 직접 넘긴다."""
        if interrupt_id is None:
            shown = self.last.pending if self.last is not None else None
            interrupt_id = shown.id if shown is not None else ""
        self.last = resume(self.graph, self.thread, decision, ctx, interrupt_id=interrupt_id, **kw)
        return self.last

    def values(self) -> dict:
        return self.graph.get_state(self.config).values

    def plan_values(self) -> dict:
        """일정 키를 볼 수 있는 값(서브그래프 판은 대기 중인 서브그래프 state, build.plan_state)."""
        return plan_state(self.graph, self.thread)

    def waiting_at(self) -> tuple[str, ...]:
        """멈춘 노드 이름(서브그래프 판의 'plan/review'도 'review'로)."""
        return tuple(n.rsplit(NS_SEP, 1)[-1] for n in waiting_nodes(self.graph, self.config))


@pytest.fixture
def make_graph():
    def factory(script, *, store: InMemoryPlanStore | None = None, backend: FakeBackend | None = None,
                answer_mode: str = "template", saver=None, answer_llm=None, plan_graph: str = "flat",
                answer_prompt: str = "v1", after_tools: str = "answer") -> GraphKit:
        store = store if store is not None else InMemoryPlanStore()
        backend = backend if backend is not None else FakeBackend(today=FIXED_NOW.date())
        sink: list = []
        deps = fake_deps(script, backend=backend, store=store, answer_mode=answer_mode, sink=sink.append,
                         answer_llm=answer_llm,   # M2: llm 모드의 가짜 답변 모델(ScriptedChatModel)
                         plan_graph=plan_graph, answer_prompt=answer_prompt,   # M3 판, M4 답변 문구
                         after_tools=after_tools)                              # M7 도구 결과 뒤(answer|loop)
        saver = saver if saver is not None else InMemorySaver()   # M3: SqliteSaver도 받음
        return GraphKit(build_graph(deps, saver), deps, store, backend, saver, sink=sink, plan_graph=plan_graph)

    return factory

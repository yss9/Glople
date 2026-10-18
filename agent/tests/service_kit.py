"""서비스 테스트 공용(M5): 가짜 Spring(respx, http://spring.test) + 가짜 모델 + 임시 폴더 sqlite로 앱을 띄운다.

- Spring my-info는 토큰 → 사용자 표(USERS)로 답한다. 표에 없는 토큰은 401. 8080·8090에는 접속하지 않는다.
- SSE는 httpx_sse.connect_sse로 읽는다(TestClient는 httpx.Client라 그대로 됨). TestClient는 응답을 끝까지 받은 뒤 돌려주므로
  동시 요청은 스레드로 만든다(Blocker).
"""
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient
from httpx_sse import connect_sse
from langchain_core.messages import AIMessage

from glople_agent.graph.build import fake_deps
from glople_agent.llm.fake import ResultEchoScript, RuleScript, ScriptedChatModel, fake_answer_model
from glople_agent.plan import InMemoryPlanStore, PlanStore
from glople_agent.service.app import create_app
from glople_agent.service.deps import ServiceDeps
from glople_agent.service.review import ReviewEchoScript
from glople_agent.service.settings import ServiceSettings
from glople_agent.tools.fake_backend import FakeBackend

SPRING = "http://spring.test"
API = "/ai-api/v1"
ALICE_TOKEN = "tok-alice-JWTMARK-9f2c4b7e1a"
BOB_TOKEN = "tok-bob-5e8d"
USERS = {ALICE_TOKEN: "alice-uid-7731", BOB_TOKEN: "bob-uid-2290"}


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


ALICE = auth(ALICE_TOKEN)
BOB = auth(BOB_TOKEN)


@dataclass
class Sse:
    status: int
    headers: dict
    events: list[tuple[str, Any]] = field(default_factory=list)
    body: Any = None   # SSE가 아닐 때(오류 JSON)
    raw: str = ""

    def names(self) -> list[str]:
        return [e for e, _ in self.events]

    def first(self, name: str) -> Any:
        return next(d for e, d in self.events if e == name)

    def all(self, name: str) -> list[Any]:
        return [d for e, d in self.events if e == name]


@dataclass
class Kit:
    client: TestClient
    spring: respx.MockRouter
    settings: ServiceSettings
    store: PlanStore
    router_model: ScriptedChatModel
    review_model: ScriptedChatModel | None
    answer_model: ScriptedChatModel | None = None

    @property
    def svc(self):
        return self.client.app.state.svc

    def new_thread(self, headers=ALICE) -> str:
        r = self.client.post(f"{API}/threads", headers=headers)
        assert r.status_code == 201, r.text
        return r.json()["threadId"]

    def sse(self, path: str, body: dict, headers=ALICE) -> Sse:
        with connect_sse(self.client, "POST", f"{API}{path}", json=body, headers=headers) as es:
            resp = es.response
            if not resp.headers.get("content-type", "").startswith("text/event-stream"):
                resp.read()
                return Sse(resp.status_code, dict(resp.headers), body=resp.json(), raw=resp.text)
            events = [(ev.event, json.loads(ev.data)) for ev in es.iter_sse()]
            return Sse(resp.status_code, dict(resp.headers), events,
                       raw="".join(f"{e}{json.dumps(d, ensure_ascii=False)}" for e, d in events))

    def say(self, thread_id: str, text: str, headers=ALICE, **extra) -> Sse:
        return self.sse(f"/threads/{thread_id}/messages", {"text": text, **extra}, headers)

    def answer(self, thread_id: str, interrupt_id: str, decision: str, headers=ALICE, **extra) -> Sse:
        return self.sse(f"/threads/{thread_id}/resume", {"interruptId": interrupt_id, "decision": decision, **extra},
                        headers)


def my_info(request: httpx.Request) -> httpx.Response:
    header = request.headers.get("authorization") or ""
    user = USERS.get(header.removeprefix("Bearer "))
    if user is None:
        return httpx.Response(401, json={"status": 401, "error": "UNAUTHORIZED", "message": "로그인이 필요합니다.",
                                         "path": "/api/users/my-info"})
    return httpx.Response(200, json={"userId": user, "nickname": "닉네임", "dateOfBirth": "19990101",
                                     "mbti": "ENTP", "points": 10})


async def no_probe(url: str) -> bool:
    return False


@contextmanager
def service_kit(tmp_path: Path, *, router: Callable | None = None, store: PlanStore | None = None,
                answer_mode: str = "template", answer_script: Callable | None = None,
                review_script: Callable | None = ReviewEchoScript(), backend: FakeBackend | None = None,
                after_tools: str = "answer", **settings_changes) -> Iterator[Kit]:
    store = store if store is not None else InMemoryPlanStore()
    answer = fake_answer_model(answer_script or ResultEchoScript()) if answer_mode == "llm" else None
    deps = fake_deps(router or RuleScript(), backend=backend or FakeBackend(), store=store, answer_mode=answer_mode,
                     answer_llm=answer, after_tools=after_tools)   # M7: loop면 router가 도구 결과 뒤 요청도 받음
    review = fake_answer_model(review_script) if review_script is not None else None
    settings = ServiceSettings(llm="fake", answer_mode=answer_mode, plan_store="memory", spring_base_url=SPRING,
                               data_dir=tmp_path / "service", after_tools=after_tools, **settings_changes)
    with respx.mock(base_url=SPRING, assert_all_called=False) as spring:
        spring.get("/api/users/my-info").mock(side_effect=my_info)
        app = create_app(settings, deps=ServiceDeps(deps, review), probe=no_probe)
        with TestClient(app) as client:
            yield Kit(client, spring, settings, store, deps.router_llm, review, answer)


class Blocker:
    """동기 노드 안에서 멈추는 가짜 라우터(스레드에서 돎). release() 전까지 route가 끝나지 않는다."""

    def __init__(self, then: Callable | None = None, *, timeout: float = 5.0) -> None:
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.then = then or RuleScript()
        self.timeout = timeout

    def __call__(self, messages, kwargs) -> AIMessage:
        self.entered.set()
        self.gate.wait(self.timeout)
        return self.then(messages, kwargs)

    def release(self) -> None:
        self.gate.set()


def in_thread(fn: Callable[[], Any]) -> tuple[threading.Thread, dict]:
    out: dict = {}

    def run():
        out["value"] = fn()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out

"""FastAPI 앱(M5, design.md 10.2·12): 127.0.0.1:8095, 접두사 `/ai-api/v1`.

| ID | 메서드·경로 | 접근 | 응답 |
|---|---|---|---|
| AI1 | GET /health | 공개 | {status, llm: up·down·rule, spring: up·down} (내부 주소 없음) |
| AI2 | POST /threads | 로그인 | 201 {threadId, title, createdAt, updatedAt} |
| AI3 | GET /threads | 본인 것만 | {threads: [...]} |
| AI4 | GET /threads/{id} | 본인 | {threadId, …, turns:[{turnId, user, cards, answer, kind?}], pendingConfirm, running} |
| AI5 | DELETE /threads/{id} | 본인 | 204(체크포인트·주인 표 행 함께) |
| AI6 | POST /threads/{id}/messages | 본인 | text/event-stream, 본문 {text(1~500자), clientMessageId?} |
| AI7 | POST /threads/{id}/resume | 본인 | text/event-stream, 본문 {interruptId, decision, draft?} |
| AI8 | POST /threads/{id}/cancel | 본인 | 202 {cancelled} |
| AI9 | POST /plans/{planId}/review | 본인(Spring이 판정) | {planId, text, kind, fallbackReasons, dayNotes, planNotes} |

- 인증: 모든 요청(health 빼고)에서 Authorization 헤더를 Spring my-info로 확인(auth.py). 실패 401, Spring 장애 503.
- 본문 모델은 모두 extra="forbid": userId·nickname 같은 행위자 칸을 받지 않는다(422). 본문 16KB 상한(413).
- 스레드: 주인 표(threads.py)로 검사. 남의 것·없는 것·모양이 틀린 id는 모두 403(같은 응답).
- SSE 이벤트: meta{threadId, turnId} → node{node} … card{card} … token{text} … → final{text, kind, replaced} 또는
  confirm{interruptId, draft, message, notice, noticeText, editsLeft, problems} → done{reason}. 실패는 error{code, message,
  retryable} 다음 done. 10초마다 `: ping`. 헤더 Cache-Control: no-cache, no-transform(CRA 개발 서버 압축이 버퍼링하지 않게),
  X-Accel-Buffering: no.
- 실행 수명: 스레드당 실행 하나(409 THREAD_BUSY), 모델 자리(기본 1, 20초 넘게 기다리면 error MODEL_BUSY), 턴 60초
  (error TURN_TIMEOUT), 사용자별 10분 20번(429), 연결이 끊기면 실행 태스크 취소(다음 턴 start_turn이 짝 없는 호출을 닫음),
  확인 카드 24시간(409 CONFIRM_EXPIRED).
- 토큰: Principal.bearer → AgentContext(bearer)로만 그래프에 들어간다. state·체크포인트·configurable·주인 표·로그·SSE에 없음.
"""
import asyncio
import json
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import anyio
from fastapi import APIRouter, Depends, FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from glople_agent.context import AgentContext
from glople_agent.graph.build import InterruptNotPending, InvalidResume, build_graph, run_config
from glople_agent.plan import PlanDecision
from glople_agent.plan.spring_store import PlanBadResponse, PlanForbidden, SpringPlanStore
from glople_agent.plan.store import AuthExpired, PlanStoreError
from glople_agent.service.auth import Authenticator, Principal
from glople_agent.service.deps import ServiceDeps, build_service_deps
from glople_agent.service.errors import ApiError, error_response, forbidden, validation_errors
from glople_agent.service.limits import ModelSlot, RateLimiter, RunRegistry
from glople_agent.service.review import PlanReviewer
from glople_agent.service.runner import (RunOutcome, astream_run, check_decision, final_payload, message_input,
                                         pending_interrupt, public_confirm, resume_input, thread_view)
from glople_agent.service.settings import BASE_PATH, ServiceSettings
from glople_agent.service.threads import ThreadRow, ThreadStore
from glople_agent.tools.spring import SpringClient

logger = logging.getLogger("glople_agent.service")
KST = ZoneInfo("Asia/Seoul")
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
_END = object()

Probe = Callable[[str], Awaitable[bool]]


# --- 본문 모델(행위자 칸 없음, extra=forbid) -------------------------------------------------

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateThreadIn(Strict):
    pass


class MessageIn(Strict):
    text: str = Field(min_length=1, max_length=500)
    clientMessageId: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("빈 메시지")
        return v


class DraftIn(Strict):
    """확인 카드에서 고친 일정(plan.PlanDraftIn과 같은 칸). 범위·길이 검사는 그래프(check_edit)가 카드에 problems로."""

    region: str = Field(max_length=200)
    days: StrictInt
    places: list[Annotated[str, Field(max_length=200)]] = Field(max_length=60)
    title: str | None = Field(default=None, max_length=400)


class ResumeIn(Strict):
    interruptId: str = Field(min_length=1, max_length=64)
    decision: Literal["approve", "edit", "reject"]
    draft: DraftIn | None = None

    def as_decision(self) -> dict:
        out: dict[str, Any] = {"decision": self.decision}
        if self.draft is not None:
            out["draft"] = self.draft.model_dump(exclude_none=True)
        return out


class ReviewIn(Strict):
    pass


# --- 서비스 상태 ---------------------------------------------------------------------------

@dataclass
class Service:
    settings: ServiceSettings
    graph: CompiledStateGraph
    saver: AsyncSqliteSaver
    threads: ThreadStore
    auth: Authenticator
    registry: RunRegistry
    slot: ModelSlot
    limiter: RateLimiter
    reviewer: PlanReviewer
    plans: SpringPlanStore
    probe: Probe

    def context(self, principal: Principal, thread_id: str | None) -> AgentContext:
        seed = uuid.UUID(thread_id).int % 100_000_000 if thread_id else 0   # 대화마다 같은 seed(동점 순서가 안 흔들림)
        return AgentContext(user_id=principal.user_id, bearer=principal.bearer, request_id=str(uuid.uuid4()),
                            now=datetime.now(KST), seed=seed)


def svc_of(request: Request) -> Service:
    return request.app.state.svc


async def current_user(request: Request) -> Principal:
    return await svc_of(request).auth.authenticate(request.headers.get("authorization"))


User = Annotated[Principal, Depends(current_user)]
ThreadId = Annotated[str, Path(max_length=64)]


async def owned_thread(svc: Service, thread_id: str, principal: Principal) -> ThreadRow:
    row = await svc.threads.owned(thread_id, principal.user_id)
    if row is None:
        raise forbidden()   # 남의 것·없는 것 같은 응답
    return row


async def tcp_probe(url: str, timeout: float = 0.5) -> bool:
    """주소의 host:port에 TCP 연결만 해 본다(요청·토큰 없음)."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with anyio.fail_after(timeout):
            stream = await anyio.connect_tcp(parts.hostname or "127.0.0.1", port)
            await stream.aclose()
        return True
    except (OSError, TimeoutError, socket.gaierror):
        return False


# --- SSE ---------------------------------------------------------------------------------

def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def emit_result(outcome: RunOutcome, emit) -> None:
    if outcome.pending is not None:
        await emit("confirm", public_confirm(outcome.pending))
        await emit("done", {"reason": "confirm"})
    else:
        await emit("final", final_payload(outcome))
        await emit("done", {"reason": "final"})


def start_stream(request: Request, svc: Service, thread_id: str,
                 job: Callable[[Callable[[str, dict], Awaitable[None]]], Awaitable[None]]) -> StreamingResponse:
    """job을 태스크로 돌리고 그 이벤트를 SSE로 흘린다. 자리(registry)는 태스크가 끝날 때 풀린다."""
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: str, data: dict) -> None:
        queue.put_nowait((event, data))

    async def runner() -> None:
        try:
            await job(emit)
        except asyncio.CancelledError:
            queue.put_nowait(("error", {"code": "CANCELLED", "message": "요청을 멈췄어요.", "retryable": True}))
            queue.put_nowait(("done", {"reason": "cancelled"}))
            raise
        except ApiError as e:
            queue.put_nowait(("error", {"code": e.code, "message": e.message, "retryable": e.retryable}))
            queue.put_nowait(("done", {"reason": "error"}))
        except TimeoutError:
            queue.put_nowait(("error", {"code": "TURN_TIMEOUT", "message": "답을 만드는 데 너무 오래 걸렸어요. 다시 시도해 주세요.",
                                        "retryable": True}))
            queue.put_nowait(("done", {"reason": "error"}))
        except Exception as e:  # noqa: BLE001 - 어떤 실패든 화면에는 정해진 문장만(메시지·traceback은 로그에만, 토큰 없음)
            logger.error("run failed: %s", type(e).__name__, exc_info=svc.settings.debug)
            queue.put_nowait(("error", {"code": "INTERNAL", "message": "답을 만들지 못했어요. 잠시 뒤 다시 시도해 주세요.",
                                        "retryable": True}))
            queue.put_nowait(("done", {"reason": "error"}))
        finally:
            svc.registry.end(thread_id, asyncio.current_task())   # 다음 요청이 바로 와도 409가 나지 않게 먼저 푼다
            queue.put_nowait(_END)

    task = asyncio.create_task(runner())
    svc.registry.attach(thread_id, task)   # 시작 전에 취소돼도 done 콜백이 자리를 푼다

    async def body():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), svc.settings.ping_seconds)
                except TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                    continue
                if item is _END:
                    break
                yield sse_event(*item)
        finally:
            if not task.done():   # 연결이 끊김 → 실행 취소(design.md 12)
                task.cancel()

    return StreamingResponse(body(), media_type="text/event-stream", headers=SSE_HEADERS)


# --- 라우트 -------------------------------------------------------------------------------

router = APIRouter(prefix=BASE_PATH)


@router.get("/health")
async def health(request: Request) -> dict:
    svc = svc_of(request)
    s = svc.settings
    llm = "rule" if s.llm == "fake" else ("up" if await svc.probe(s.model_base_url) else "down")
    spring = "up" if await svc.probe(s.spring_base_url) else "down"
    return {"status": "ok", "llm": llm, "spring": spring}


@router.post("/threads", status_code=201)
async def create_thread(request: Request, principal: User, body: CreateThreadIn | None = None) -> dict:
    svc = svc_of(request)
    if await svc.threads.count_for(principal.user_id) >= svc.settings.max_threads_per_user:
        raise ApiError(409, "THREAD_LIMIT", "대화가 너무 많아요. 지난 대화를 지운 뒤 다시 만들어 주세요.")
    row = await svc.threads.create(principal.user_id)
    return row.public()


@router.get("/threads")
async def list_threads(request: Request, principal: User) -> dict:
    svc = svc_of(request)
    rows = await svc.threads.list_for(principal.user_id, limit=svc.settings.max_threads_per_user)
    return {"threads": [{**r.public(), "running": svc.registry.busy(r.thread_id)} for r in rows]}


@router.get("/threads/{thread_id}")
async def get_thread(request: Request, thread_id: ThreadId, principal: User) -> dict:
    svc = svc_of(request)
    row = await owned_thread(svc, thread_id, principal)
    snapshot = await svc.graph.aget_state(run_config(thread_id))
    return {**row.public(), **thread_view(snapshot), "running": svc.registry.busy(thread_id)}


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(request: Request, thread_id: ThreadId, principal: User) -> Response:
    svc = svc_of(request)
    await owned_thread(svc, thread_id, principal)
    svc.registry.begin(thread_id)   # 실행 중이면 409, 지우는 동안 새 실행도 막음
    try:
        await svc.saver.adelete_thread(thread_id)
        await svc.threads.delete(thread_id)
    finally:
        svc.registry.end(thread_id)
    return Response(status_code=204)


@router.post("/threads/{thread_id}/cancel", status_code=202)
async def cancel_thread(request: Request, thread_id: ThreadId, principal: User) -> dict:
    svc = svc_of(request)
    await owned_thread(svc, thread_id, principal)
    return {"cancelled": svc.registry.cancel(thread_id)}


@router.post("/threads/{thread_id}/messages")
async def post_message(request: Request, thread_id: ThreadId, body: MessageIn, principal: User) -> StreamingResponse:
    svc = svc_of(request)
    row = await owned_thread(svc, thread_id, principal)
    if body.clientMessageId and body.clientMessageId == row.last_client_message_id:
        raise ApiError(409, "DUPLICATE_MESSAGE", "이미 받은 메시지예요.")
    svc.registry.begin(thread_id)   # 실행 중이면 409(요청 수에 세지 않음)
    try:
        svc.limiter.check(principal.user_id)
        await svc.threads.touch(thread_id, first_text=body.text, client_message_id=body.clientMessageId)
    except BaseException:
        svc.registry.end(thread_id)
        raise
    turn_id = f"turn-{uuid.uuid4()}"
    graph = svc.graph

    async def job(emit) -> None:
        await emit("meta", {"threadId": thread_id, "turnId": turn_id})
        async with svc.slot.hold() as got:
            if not got:
                raise ApiError(503, "MODEL_BUSY", "지금은 다른 답을 만드는 중이에요. 잠시 뒤 다시 보내 주세요.", retryable=True)
            async with asyncio.timeout(svc.settings.turn_timeout):
                ctx = svc.context(principal, thread_id)
                pending, _ = await pending_interrupt(graph, thread_id)
                if pending is not None:   # 확인 카드가 떠 있는데 새 말 → 먼저 취소로 닫음(design.md 9.3, CLI와 같음)
                    await astream_run(graph, resume_input(pending, PlanDecision(decision="reject")), thread_id, ctx,
                                      emit, only=("card",))
                outcome = await astream_run(graph, message_input(body.text, turn_id), thread_id, ctx, emit)
                await emit_result(outcome, emit)

    return start_stream(request, svc, thread_id, job)


@router.post("/threads/{thread_id}/resume")
async def post_resume(request: Request, thread_id: ThreadId, body: ResumeIn, principal: User) -> StreamingResponse:
    svc = svc_of(request)
    await owned_thread(svc, thread_id, principal)
    if body.decision == "edit" and body.draft is None:
        raise ApiError(422, "UNPROCESSABLE", "고친 일정(draft)이 필요합니다.",
                       errors=[{"field": "draft", "code": "missing", "message": "필요한 값이 없습니다."}])
    svc.registry.begin(thread_id)
    try:
        pending, snapshot = await pending_interrupt(svc.graph, thread_id)
        try:
            decision = check_decision(body.interruptId, pending, body.as_decision())
        except InterruptNotPending:
            raise ApiError(409, "INTERRUPT_NOT_PENDING", "대기 중인 확인 카드가 없거나 이미 답했어요.") from None
        except InvalidResume as e:
            raise ApiError(422, "UNPROCESSABLE", str(e)) from None
        if _expired(snapshot.created_at, svc.settings.confirm_ttl_seconds):
            raise ApiError(409, "CONFIRM_EXPIRED", "확인 카드가 오래되어 저장할 수 없어요. 다시 요청해 주세요.")
    except BaseException:
        svc.registry.end(thread_id)
        raise
    assert pending is not None
    turn_id = (snapshot.values or {}).get("turn_id")
    graph = svc.graph

    async def job(emit) -> None:
        await emit("meta", {"threadId": thread_id, "turnId": turn_id})
        async with asyncio.timeout(svc.settings.turn_timeout):   # 재개는 모델을 부르지 않음(저장·다시 띄우기)
            ctx = svc.context(principal, thread_id)
            outcome = await astream_run(graph, resume_input(pending, decision), thread_id, ctx, emit)
            await emit_result(outcome, emit)

    return start_stream(request, svc, thread_id, job)


def _expired(created_at: str | None, ttl: float) -> bool:
    if not created_at:
        return False
    try:
        stamp = datetime.fromisoformat(created_at)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return (datetime.now(UTC) - stamp).total_seconds() > ttl


@router.post("/plans/{plan_id}/review")
async def review_plan(request: Request, principal: User, plan_id: Annotated[int, Path(gt=0, lt=10**18)],
                      body: ReviewIn | None = None) -> dict:
    svc = svc_of(request)
    svc.limiter.check(principal.user_id)
    try:
        stored = await anyio.to_thread.run_sync(lambda: svc.plans.get(plan_id, auth=principal.bearer))
    except AuthExpired:
        raise ApiError(401, "UNAUTHORIZED", "로그인이 필요합니다.") from None
    except PlanForbidden:
        raise forbidden() from None
    except PlanBadResponse:
        raise ApiError(502, "BAD_UPSTREAM", "일정 저장소의 응답을 읽지 못했어요.") from None
    except PlanStoreError as e:
        raise ApiError(503, "PLAN_UNAVAILABLE", "일정 저장소에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요.",
                       retryable=e.retryable) from None
    if stored is None:
        raise forbidden()   # 남의 일정·없는 일정(Spring 403)
    review = await svc.reviewer.review(stored.plan_id, stored.plan)
    return review.public()


# --- 앱 ----------------------------------------------------------------------------------

class BodyLimit:
    """본문 상한(ASGI). Content-Length가 넘으면 바로, 없으면 읽으면서 세어 413."""

    def __init__(self, app, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.limit):
            await self._reject(scope, receive, send)
            return
        chunks: list[bytes] = []
        total = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.limit:
                await self._reject(scope, receive, send)
                return
            chunks.append(chunk)
            more = message.get("more_body", False)
        body = b"".join(chunks)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, scope, receive, send) -> None:
        resp = error_response(413, "PAYLOAD_TOO_LARGE", "요청 본문이 너무 큽니다.", scope.get("path", ""))
        await resp(scope, receive, send)


def create_app(settings: ServiceSettings | None = None, *, deps: ServiceDeps | None = None,
               spring_transport: Any = None, probe: Probe | None = None) -> FastAPI:
    """settings 없으면 환경 변수(ServiceSettings.from_env). deps·spring_transport·probe는 테스트용."""
    settings = settings or ServiceSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service_deps = deps or build_service_deps(settings)
        settings.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
        auth_client = SpringClient(settings.spring_base_url, transport=spring_transport,
                                   read_timeout=settings.auth_timeout, connect_timeout=min(2.0, settings.auth_timeout))
        plans_client = SpringClient(settings.spring_base_url, transport=spring_transport)
        async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db)) as saver:
            await saver.setup()
            threads = await ThreadStore.open(settings.threads_db)
            slot = ModelSlot(settings.model_concurrency, wait=settings.model_wait_seconds)
            app.state.svc = Service(
                settings=settings, graph=build_graph(service_deps.graph, saver), saver=saver, threads=threads,
                auth=Authenticator(auth_client, ttl=settings.auth_cache_seconds, max_entries=settings.auth_cache_max,
                                   max_token_chars=settings.max_token_chars),
                registry=RunRegistry(), slot=slot,
                limiter=RateLimiter(settings.rate_limit_turns, settings.rate_limit_window),
                reviewer=PlanReviewer(service_deps.review_model, slot=slot, timeout=settings.review_timeout),
                plans=SpringPlanStore(plans_client), probe=probe or tcp_probe)
            try:
                yield
            finally:
                app.state.svc.registry.cancel_all()
                await threads.close()
                auth_client.close()
                plans_client.close()

    app = FastAPI(title="Glople 여행 도우미 서비스", version="0.0.1", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.include_router(router)

    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, request.url.path, errors=exc.errors,
                              retryable=exc.retryable or None)

    @app.exception_handler(RequestValidationError)
    async def on_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, "UNPROCESSABLE", "요청 값이 올바르지 않습니다.", request.url.path,
                              errors=validation_errors(list(exc.errors())))

    @app.exception_handler(StarletteHTTPException)
    async def on_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = exc.status_code
        return error_response(status, HTTPStatus(status).name, HTTPStatus(status).phrase, request.url.path)

    @app.exception_handler(Exception)
    async def on_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("request failed: %s", type(exc).__name__, exc_info=settings.debug)
        return error_response(500, "INTERNAL", "요청을 처리하지 못했어요.", request.url.path)

    return BodyLimitApp(app, settings.max_body_bytes)


class BodyLimitApp(BodyLimit):
    """create_app이 돌려주는 ASGI 앱: 본문 상한을 FastAPI 앞에 둔다(.fastapi로 안쪽 앱, TestClient는 둘 다 받음)."""

    @property
    def fastapi(self) -> FastAPI:
        return self.app

    @property
    def state(self):
        return self.app.state


def app_factory():
    """`uvicorn --factory glople_agent.service.app:app_factory` 용(환경 변수 설정)."""
    return create_app()


__all__ = ["BodyLimit", "CreateThreadIn", "DraftIn", "MessageIn", "ResumeIn", "Service", "SSE_HEADERS", "app_factory",
           "create_app", "sse_event", "tcp_probe"]

"""실행 수명(design.md 12): 스레드당 실행 하나, 모델 자리(세마포어), 사용자별 요청 수.

모두 한 프로세스·한 이벤트 루프 안의 메모리 상태다(uvicorn 워커 하나, 127.0.0.1). 워커를 늘리려면 이 상태를 밖으로 옮겨야 한다.
"""
import asyncio
import time
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager

from glople_agent.service.errors import ApiError


class RunRegistry:
    """thread_id → 실행 중인 태스크. 같은 스레드에 두 번째 실행이 오면 호출부가 409 THREAD_BUSY."""

    def __init__(self) -> None:
        self._runs: dict[str, asyncio.Task | None] = {}

    def busy(self, thread_id: str) -> bool:
        return thread_id in self._runs

    def begin(self, thread_id: str) -> None:
        if thread_id in self._runs:
            raise ApiError(409, "THREAD_BUSY", "이 대화는 아직 답을 만드는 중이에요. 끝난 뒤 다시 보내 주세요.",
                           retryable=True)
        self._runs[thread_id] = None   # 자리만 잡음(await 없이 확인·등록하므로 같은 루프에서 경쟁 없음)

    def attach(self, thread_id: str, task: asyncio.Task) -> None:
        """태스크가 끝나면(정상·오류·취소, 시작 전 취소 포함) 자리를 푼다(done 콜백은 늘 불림)."""
        self._runs[thread_id] = task
        task.add_done_callback(lambda _t: self.end(thread_id, task))

    def end(self, thread_id: str, task: asyncio.Task | None = None) -> None:
        current = self._runs.get(thread_id, ...)
        if current is ... or (task is not None and current is not task):
            return
        self._runs.pop(thread_id, None)

    def cancel(self, thread_id: str) -> bool:
        task = self._runs.get(thread_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def cancel_all(self) -> None:
        for thread_id in list(self._runs):
            self.cancel(thread_id)

    def __len__(self) -> int:
        return len(self._runs)


class ModelSlot:
    """모델 서버를 쓰는 실행 수 상한. hold()는 자리를 얻으면 True, wait초 안에 못 얻으면 False(호출부가 MODEL_BUSY·템플릿)."""

    def __init__(self, concurrency: int = 1, *, wait: float = 20.0) -> None:
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self.wait = wait

    @asynccontextmanager
    async def hold(self):
        try:
            await asyncio.wait_for(self._sem.acquire(), self.wait)
        except TimeoutError:
            yield False
            return
        try:
            yield True
        finally:
            self._sem.release()


class RateLimiter:
    """사용자별 window초에 limit번(메시지·일정 검토). 넘으면 429."""

    def __init__(self, limit: int = 20, window: float = 600.0, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}

    def check(self, user_id: str) -> None:
        now = self.clock()
        hits = self._hits.setdefault(user_id, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            raise ApiError(429, "RATE_LIMITED", "요청이 너무 많아요. 잠시 뒤 다시 시도해 주세요.", retryable=True)
        hits.append(now)


__all__ = ["ModelSlot", "RateLimiter", "RunRegistry"]

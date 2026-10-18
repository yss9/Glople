"""영속 체크포인트(M3): SqliteSaver(langgraph-checkpoint-sqlite 3.1.1)와 대화 기록(get_state_history) 요약.

- 파일은 agent/var/checkpoints.sqlite(gitignore, design.md 3.2). CLI `chat --db PATH`가 이 파일을 열고 `--resume`·`--thread`로
  껐다 켠 뒤에도 같은 대화를 잇는다. 확인 카드가 떠 있는 채로 끄면 다시 켰을 때 그 카드에 그대로 답할 수 있다.
- SqliteSaver는 `PRAGMA journal_mode=WAL`로 연다. 그래서 연결이 열려 있는 동안 최근 쓰기는 `-wal` 파일에 있고, 마지막 연결을
  닫으면 본 파일로 합쳐진다. 토큰 검사(tests/test_token_hygiene.py)는 db_files()로 셋을 모두 읽는다.
- 저장되는 것은 state 값·재개 값·메타데이터(source·step·parents + configurable의 스칼라 값)다. context(AgentContext, 토큰)는
  configurable["__pregel_runtime"]이라 저장되지 않고, configurable에는 thread_id(와 시간 여행의 checkpoint_id)만 둔다.
- 대화 기록 요약(history)은 부모 그래프의 체크포인트만 본다. 서브그래프 판의 안쪽 체크포인트(namespace plan:<task id>)는
  `get_state(config, subgraphs=True)`로만 보이고, 시간 여행도 부모 체크포인트 단위로 한다(graph.build.fork).
"""
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph

from glople_agent.config import AGENT_DIR

DEFAULT_DB = AGENT_DIR / "var" / "checkpoints.sqlite"
SQLITE_SIDE_FILES = ("-wal", "-shm")


def connect(path: str | Path) -> sqlite3.Connection:
    """체크포인트 파일 연결. SqliteSaver가 자체 lock을 쓰므로 check_same_thread=False(문서 예와 같음)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path), check_same_thread=False)


def open_saver(path: str | Path) -> tuple[SqliteSaver, sqlite3.Connection]:
    """CLI처럼 오래 쓰는 곳: (saver, 연결). 다 쓰면 연결을 닫는다(WAL이 본 파일로 합쳐짐)."""
    conn = connect(path)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver, conn


@contextmanager
def sqlite_saver(path: str | Path) -> Iterator[SqliteSaver]:
    saver, conn = open_saver(path)
    try:
        yield saver
    finally:
        conn.close()


def db_files(path: str | Path) -> list[Path]:
    """체크포인트 파일과 WAL 곁 파일(있는 것만)."""
    path = Path(path)
    return [p for p in (path, *(Path(f"{path}{s}") for s in SQLITE_SIDE_FILES)) if p.exists()]


def latest_thread(saver: BaseCheckpointSaver) -> str | None:
    """가장 최근에 쓴 체크포인트의 thread_id(체크포인트 id는 시각 순 uuid6라 가장 큰 것이 최신). 없으면 None."""
    for tup in saver.list(None, limit=1):
        return tup.config["configurable"]["thread_id"]
    return None


def thread_exists(saver: BaseCheckpointSaver, thread_id: str) -> bool:
    return saver.get_tuple({"configurable": {"thread_id": thread_id}}) is not None


def _text(content: Any) -> str:
    return content if isinstance(content, str) else str(content or "")


def _clip(text: str | None, n: int = 30) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


@dataclass(frozen=True)
class HistoryRow:
    """get_state_history 한 줄. index 0이 가장 최근(머리)이다(/fork N의 N)."""

    index: int
    checkpoint_id: str
    step: int
    source: str                       # input | loop | fork | update
    next: tuple[str, ...]             # 이 체크포인트에서 다음에 돌 노드(없으면 턴이 끝남)
    ran: tuple[str, ...]              # 이 체크포인트를 만든 노드(부모 체크포인트의 next)
    pending: bool                     # 확인 카드를 기다리는 자리
    on_head: bool                     # 지금 머리의 조상(현재 갈래)인지. 아니면 예전 갈래
    messages: int
    last_user: str
    final_text: str | None
    config: dict = field(repr=False, compare=False)

    def describe(self) -> str:
        mark = "*" if self.on_head else " "
        what = "입력" if self.source == "input" else ",".join(self.ran) or self.source
        nxt = ",".join(self.next) if self.next else "끝"
        tail = " · 대기 카드" if self.pending else ""
        answer = f" · 답 “{_clip(self.final_text)}”" if self.final_text and not self.next else ""
        return (f"{mark}#{self.index:<3} step {self.step:>3} {self.source:<6} {what} → {nxt}"
                f" · 메시지 {self.messages} · 마지막 말 “{_clip(self.last_user, 24)}”{answer}{tail}")


def history(graph: CompiledStateGraph, thread_id: str, *, limit: int | None = None) -> list[HistoryRow]:
    """스레드의 부모 체크포인트(모든 갈래, 최신 먼저). on_head는 머리에서 parent를 따라 올라가며 표시한다."""
    snaps = list(graph.get_state_history({"configurable": {"thread_id": thread_id}}))
    by_id = {s.config["configurable"]["checkpoint_id"]: s for s in snaps}
    lineage: set[str] = set()
    cursor = snaps[0] if snaps else None
    while cursor is not None:
        cid = cursor.config["configurable"]["checkpoint_id"]
        lineage.add(cid)
        parent = (cursor.parent_config or {}).get("configurable", {}).get("checkpoint_id")
        cursor = by_id.get(parent) if parent else None
    rows: list[HistoryRow] = []
    for i, snap in enumerate(snaps[:limit] if limit else snaps):
        cid = snap.config["configurable"]["checkpoint_id"]
        parent_id = (snap.parent_config or {}).get("configurable", {}).get("checkpoint_id")
        parent = by_id.get(parent_id) if parent_id else None
        messages = snap.values.get("messages") or []
        last_user = next((_text(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), "")
        rows.append(HistoryRow(
            index=i, checkpoint_id=cid, step=int(snap.metadata.get("step", -1)) if snap.metadata else -1,
            source=str((snap.metadata or {}).get("source", "?")), next=tuple(snap.next),
            ran=tuple(parent.next) if parent is not None else (), pending=bool(snap.interrupts),
            on_head=cid in lineage, messages=len(messages), last_user=last_user,
            final_text=snap.values.get("final_text"), config=dict(snap.config)))
    return rows


def format_history(rows: Sequence[HistoryRow]) -> str:
    if not rows:
        return "  (체크포인트 없음)"
    head = "  * = 지금 갈래, #N = /fork N. step은 부모 그래프 단계(서브그래프 안 단계는 안 보임)"
    return "\n".join([head, *("  " + r.describe() for r in rows)])


__all__ = ["DEFAULT_DB", "HistoryRow", "connect", "db_files", "format_history", "history", "latest_thread",
           "open_saver", "sqlite_saver", "thread_exists"]

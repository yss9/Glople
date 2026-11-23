"""스레드 주인 표(design.md 10.2): `threads(thread_id, owner_user_id, title, created_at, updated_at, …)`, aiosqlite.

- 체크포인트(checkpoints.sqlite)와 다른 파일(threads.sqlite)에 둔다. 체크포인트에는 사용자 id가 없고(M3 토큰 검사와 같은 규칙),
  주인 판정은 이 표 하나로 한다.
- thread_id는 서버가 만든 UUID4다. 모양이 다르거나 없는 id·남의 id는 모두 None(→ 호출부가 403, 존재 여부를 숨김).
- title은 첫 메시지 앞부분(목록 표시용). 토큰·요청 id는 넣지 않는다.
"""
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

TITLE_CHARS = 40
SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_client_message_id TEXT
);
CREATE INDEX IF NOT EXISTS threads_owner ON threads(owner_user_id, updated_at);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def is_thread_id(value: str) -> bool:
    """서버가 만든 UUID4 문자열인지(소문자 하이픈 꼴 그대로)."""
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def clip_title(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 1] + "…"


@dataclass(frozen=True)
class ThreadRow:
    thread_id: str
    owner_user_id: str
    title: str
    created_at: str
    updated_at: str
    last_client_message_id: str | None

    def public(self) -> dict:
        return {"threadId": self.thread_id, "title": self.title, "createdAt": self.created_at,
                "updatedAt": self.updated_at}


class ThreadStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    @classmethod
    async def open(cls, path: str | Path) -> "ThreadStore":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(path))
        await conn.executescript(SCHEMA)
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self.conn.close()

    async def create(self, owner_user_id: str) -> ThreadRow:
        thread_id = str(uuid.uuid4())
        stamp = now_iso()
        await self.conn.execute(
            "INSERT INTO threads(thread_id, owner_user_id, title, created_at, updated_at) VALUES (?, ?, '', ?, ?)",
            (thread_id, owner_user_id, stamp, stamp))
        await self.conn.commit()
        return ThreadRow(thread_id, owner_user_id, "", stamp, stamp, None)

    async def count_for(self, owner_user_id: str) -> int:
        async with self.conn.execute("SELECT COUNT(*) FROM threads WHERE owner_user_id = ?", (owner_user_id,)) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def owned(self, thread_id: str, owner_user_id: str) -> ThreadRow | None:
        """주인이 맞으면 그 행, 아니면(남의 것·없는 것·모양이 틀린 id) None."""
        if not is_thread_id(thread_id):
            return None
        async with self.conn.execute(
                "SELECT thread_id, owner_user_id, title, created_at, updated_at, last_client_message_id "
                "FROM threads WHERE thread_id = ?", (thread_id,)) as cur:
            row = await cur.fetchone()
        if row is None or row[1] != owner_user_id:
            return None
        return ThreadRow(*row)

    async def list_for(self, owner_user_id: str, *, limit: int = 100) -> list[ThreadRow]:
        async with self.conn.execute(
                "SELECT thread_id, owner_user_id, title, created_at, updated_at, last_client_message_id "
                "FROM threads WHERE owner_user_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (owner_user_id, limit)) as cur:
            rows = await cur.fetchall()
        return [ThreadRow(*r) for r in rows]

    async def touch(self, thread_id: str, *, first_text: str | None = None,
                    client_message_id: str | None = None) -> None:
        """메시지를 받을 때: updated_at, 비어 있으면 title, 마지막 clientMessageId."""
        await self.conn.execute(
            "UPDATE threads SET updated_at = ?, "
            "title = CASE WHEN title = '' AND ? IS NOT NULL THEN ? ELSE title END, "
            "last_client_message_id = COALESCE(?, last_client_message_id) WHERE thread_id = ?",
            (now_iso(), first_text, clip_title(first_text or ""), client_message_id, thread_id))
        await self.conn.commit()

    async def delete(self, thread_id: str) -> None:
        await self.conn.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
        await self.conn.commit()


__all__ = ["ThreadRow", "ThreadStore", "clip_title", "is_thread_id", "now_iso"]

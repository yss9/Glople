"""궤적 로그(design.md 5.3 finish, 16절): 턴마다 JSON 한 줄. 옵트인·로컬(agent/var/trajectories/, gitignore).

finish 노드가 nodes.trajectory_record(state)를 sink로 넘기고, 여기서 시각을 붙여 날짜별 파일에 덧붙인다.
기록에 넣지 않는 것: 토큰, 사용자 id, 발화·인자 값·결과 글(개인정보·Type3 원문). 넣는 것: 라우터 종류·시도 수,
호출 이름·판정·근거 검사 코드, 결과 도구·상태, 답 종류, 후속 발화 처리 여부. 막힌 호출로 근거 검사 오탐을 본다(7절).
저장 전에 금지 키가 없는지 한 번 더 확인하고, 있으면 그 줄을 쓰지 않고 ValueError를 낸다.
"""
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from glople_agent.config import AGENT_DIR
from glople_agent.tools.backend import KST

DEFAULT_DIR = AGENT_DIR / "var" / "trajectories"
FORBIDDEN_KEYS = frozenset({"user_id", "bearer", "token", "authorization", "user_text", "router_input", "args",
                            "messages", "final_text", "items", "text"})


def _check(obj: Any, path: str = "") -> None:
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                raise ValueError(f"궤적 기록에 넣지 않는 키입니다: {path}{k}")
            _check(v, f"{path}{k}.")
    elif isinstance(obj, list | tuple):
        for v in obj:
            _check(v, path)


class JsonlTrajectorySink:
    """sink(record): var/trajectories/YYYYMMDD.jsonl에 한 줄 덧붙인다."""

    def __init__(self, directory: Path | None = None, *, clock: Callable[[], datetime] = lambda: datetime.now(KST)):
        self.directory = Path(directory) if directory else DEFAULT_DIR
        self.clock = clock
        self.last: dict | None = None

    def path_for(self, now: datetime) -> Path:
        return self.directory / f"{now:%Y%m%d}.jsonl"

    def __call__(self, record: Mapping[str, Any]) -> None:
        _check(record)
        now = self.clock()
        line = {"ts": now.isoformat(timespec="seconds"), **record}
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path_for(now).open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, sort_keys=False) + "\n")
        self.last = line


def format_line(record: Mapping[str, Any]) -> str:
    """CLI용 한 줄 요약: 라우터 → 판정 → 결과 → 답 종류."""
    verdicts = " ".join(f"{v['name']}:{'ok' if v['ok'] else v['reason']}"
                        + (f"[{','.join(v['issues'])}]" if v.get("issues") else "")
                        for v in record.get("verdicts") or [])
    results = " ".join(f"{r['tool']}={r['status']}" for r in record.get("results") or [])
    follow = record.get("followup") or {}
    extra = (" 이어붙임" if follow.get("joined") else "") + (" 지시어" if follow.get("demonstratives") else "")
    return (f"router={record.get('router_kind')}×{record.get('router_attempts')}{extra} | {verdicts or '-'} | "
            f"{results or '-'} | reply={record.get('reply_kind')}")


__all__ = ["DEFAULT_DIR", "FORBIDDEN_KEYS", "JsonlTrajectorySink", "format_line"]

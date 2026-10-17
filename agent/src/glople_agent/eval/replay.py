"""정답 재생(design.md 14.1 19번): OracleScript로 정답 발화를 전부 그래프에 흘려 경로·답 종류·예외를 센다.

- 라우터는 정답 assistant를 그대로 내는 '완벽한 라우터'(OracleScript, check_request=True로 요청 모양도 검사).
- 발화마다 새 thread. 확인 카드가 뜨면 approve로 재개해 END까지 간다.
- M0 검사는 스키마 수준이라 정답 호출은 하나도 막히지 않아야 한다(텍스트 정답 → direct_reply).
"""
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langgraph.checkpoint.memory import InMemorySaver

from glople_agent import contract
from glople_agent.context import AgentContext, BearerToken
from glople_agent.graph.build import build_graph, fake_deps, new_thread_id, resume, run_turn
from glople_agent.graph.nodes import router_message
from glople_agent.llm.fake import OracleCase, OracleScript, load_oracle_cases
from glople_agent.plan import InMemoryPlanStore
from glople_agent.tools.fake_backend import FakeBackend

DATASETS: dict[str, Path] = {
    "v2-test": contract.V2_TEST_PATH,
    "v2-valid": contract.V2_DATA_DIR / "valid.jsonl",
    "human": contract.HUMAN_STYLE_PATH,
}
CATEGORIES = ("tool", "tool(병렬)", "plan", "clarify", "direct", "fallback", "unfinished")
REPLAY_NOW = datetime(2026, 10, 17, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
REPLAY_TOKEN = "replay-fake-token-not-a-jwt"   # 고정 테스트 값(실제 JWT 아님)


@dataclass
class CaseResult:
    case: OracleCase
    path: list[str] = field(default_factory=list)     # 첫 invoke + 재개 invoke의 노드
    max_invoke_steps: int = 0                         # 한 번의 invoke에서 돈 노드 수 최대
    reply_kind: str | None = None
    router_kind: str | None = None
    verdicts_ok: bool = False                         # validate가 모든 호출을 통과시킴
    calls: int = 0
    executed: list[tuple[str, dict]] = field(default_factory=list)   # validate 뒤 라우터 메시지에 남은 호출
    reached_end: bool = False
    error: str | None = None

    @property
    def category(self) -> str:
        p = self.path
        if self.error or not self.reached_end:
            return "unfinished"
        if "plan_prepare" in p:
            return "plan"
        if "clarify" in p:
            return "clarify"
        if "direct_reply" in p:
            return "direct"
        if "fallback" in p:
            return "fallback"
        if "tools" in p:
            return "tool(병렬)" if self.calls > 1 else "tool"
        return "unfinished"

    @property
    def as_expected(self) -> bool:
        """정답이 호출이면 validate 통과 후 도구/일정, 텍스트면 direct_reply."""
        if self.error or not self.reached_end:
            return False
        if self.case.is_call:
            return (self.verdicts_ok and self.category in ("tool", "tool(병렬)", "plan")
                    and self.executed == self.case.expected_calls)
        return self.category == "direct"


@dataclass
class ReplayReport:
    datasets: list[str]
    results: list[CaseResult]
    missing: list[str] = field(default_factory=list)
    seconds: float = 0.0
    saved_plans: int = 0

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def errors(self) -> list[CaseResult]:
        return [r for r in self.results if r.error]

    @property
    def reached_end(self) -> int:
        return sum(r.reached_end for r in self.results)

    @property
    def unexpected(self) -> list[CaseResult]:
        return [r for r in self.results if not r.as_expected]

    def path_counts(self) -> Counter:
        return Counter(r.category for r in self.results)

    def reply_counts(self) -> Counter:
        return Counter(r.reply_kind or "-" for r in self.results)

    @property
    def max_invoke_steps(self) -> int:
        return max((r.max_invoke_steps for r in self.results), default=0)

    @property
    def max_turn_steps(self) -> int:
        return max((len(r.path) for r in self.results), default=0)


def dataset_paths(names: Iterable[str]) -> tuple[list[Path], list[str]]:
    """'v2-test,human' 같은 이름 → (있는 파일, 없는 이름)."""
    paths, missing = [], []
    for name in names:
        name = name.strip()
        if not name:
            continue
        if name not in DATASETS:
            raise ValueError(f"모르는 데이터 이름: {name!r} (가능: {', '.join(DATASETS)})")
        if DATASETS[name].is_file():
            paths.append(DATASETS[name])
        else:
            missing.append(name)   # training/data는 gitignore라 없을 수 있음
    return paths, missing


def replay_cases(cases: Sequence[OracleCase], *, check_request: bool = True) -> tuple[list[CaseResult], int]:
    """정답 케이스를 그래프에 흘린다. (케이스 결과, 저장된 일정 수)."""
    store = InMemoryPlanStore()
    oracle = OracleScript(cases=cases, check_request=check_request)
    deps = fake_deps(oracle, backend=FakeBackend(today=REPLAY_NOW.date()), store=store)
    graph = build_graph(deps, InMemorySaver())
    ctx = AgentContext(user_id="replay-user", bearer=BearerToken(REPLAY_TOKEN), request_id="replay",
                       now=REPLAY_NOW, seed=0)
    out: list[CaseResult] = []
    for case in cases:
        res = CaseResult(case)
        try:
            thread = new_thread_id()
            turn = run_turn(graph, thread, case.utterance, ctx)
            res.path, res.max_invoke_steps = list(turn.path), len(turn.path)
            if turn.pending is not None:   # 확인 카드 → 승인해서 끝까지
                after = resume(graph, thread, {"decision": "approve"}, ctx, interrupt_id=turn.pending.id)
                res.path += after.path
                res.max_invoke_steps = max(res.max_invoke_steps, len(after.path))
                turn = after
            values = turn.values
            res.reply_kind = turn.reply_kind
            res.router_kind = values.get("router_kind")
            verdicts = values.get("verdicts") or []
            res.calls = len(verdicts)
            res.verdicts_ok = bool(verdicts) and all(v["ok"] for v in verdicts)
            if (ai := router_message(values)) is not None:
                res.executed = [(tc["name"], tc["args"]) for tc in ai.tool_calls]
            res.reached_end = turn.done and turn.error is None and res.path[-1:] == ["finish"]
            if turn.error:
                res.error = turn.error
        except Exception as e:  # noqa: BLE001 - 재생은 예외를 세어 표로 보여 준다
            res.error = f"{type(e).__name__}: {str(e)[:120]}"
        out.append(res)
    return out, store.save_count


def replay(names: Iterable[str] = ("v2-test", "human"), *, check_request: bool = True) -> ReplayReport:
    names = list(names)
    paths, missing = dataset_paths(names)
    cases = load_oracle_cases(paths) if paths else []
    start = time.perf_counter()
    results, saved = replay_cases(cases, check_request=check_request)
    return ReplayReport(names, results, missing, time.perf_counter() - start, saved)


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total else "-"


def format_report(report: ReplayReport) -> str:
    """마크다운 표(README에 그대로 붙임)."""
    n = report.total
    lines = [f"데이터: {', '.join(report.datasets)} · {n}건 · {report.seconds:.1f}초"]
    if report.missing:
        lines.append(f"없는 파일(건너뜀): {', '.join(report.missing)}")
    lines += ["", "| 항목 | 값 |", "|---|---|",
              f"| 예외 | {len(report.errors)} |",
              f"| END 도달 | {report.reached_end}/{n} |",
              f"| 정답과 다른 경로 | {len(report.unexpected)} |",
              f"| 한 번의 invoke 최대 노드 수 | {report.max_invoke_steps} (recursion_limit 25) |",
              f"| 턴 전체(재개 포함) 최대 노드 수 | {report.max_turn_steps} |",
              f"| 저장된 일정 | {report.saved_plans} |"]
    paths = report.path_counts()
    lines += ["", "| 경로 | 건수 | 비율 |", "|---|---|---|"]
    lines += [f"| {c} | {paths[c]} | {_pct(paths[c], n)} |" for c in CATEGORIES if paths[c]]
    replies = report.reply_counts()
    lines += ["", "| reply_kind | 건수 |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(replies.items(), key=lambda kv: (-kv[1], kv[0]))]
    if report.errors or report.unexpected:
        lines += ["", "정답과 다르거나 예외가 난 발화(앞 10개):"]
        for r in (report.errors + [u for u in report.unexpected if not u.error])[:10]:
            lines.append(f"- {r.case.source}:{r.case.line} {r.case.utterance!r} → {r.category} {r.error or ''}".rstrip())
    return "\n".join(lines)

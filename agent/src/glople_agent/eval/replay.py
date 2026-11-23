"""정답 재생(design.md 14.1 19번): OracleScript로 정답 발화를 전부 그래프에 흘려 경로·답 종류·예외를 센다.

- 라우터는 정답 assistant를 그대로 내는 '완벽한 라우터'(OracleScript, check_request=True로 요청 모양도 검사).
- 발화마다 새 thread. 확인 카드가 뜨면 approve로 재개해 END까지 간다.
- M0 검사는 스키마 수준이라 정답 호출은 하나도 막히지 않아야 한다(텍스트 정답 → direct_reply).
- (M6) 정답 호출이어도 에이전트 정책으로 되묻는 두 경우는 따로 센다(정답과 다른 경로가 아님):
  `clarify(지역)` = 모호한 지역(광주)이라 validate가 후보를 되물음(train에 '광주' 정답 16호출 = 14발화 중 음식점·숙박
  단독 3발화는 어느 광주든 '수집 안 함' 답이라 묻지 않아 11발화, test·human 0건),
  `tool(되묻기)` = 도구는 정답대로 돌았고 결과가 동명 장소 후보뿐이라 collect → clarify(fixture에는 정답 장소 중 동명이 없어 0건).
- (M7) after_tools="loop"이면 도구 결과 뒤 요청을 LoopAnswerScript(결과 이름·제한 이유만 말하는 가짜, 다시 부르지 않음)가 받고,
  요청 모양은 check_after_tools_request로 본다. 실행 호출은 첫 라운드 호출로 정답과 비교하고, 라운드마다의 판단을 따로 센다.
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
from glople_agent.graph.after_tools import tool_rounds
from glople_agent.graph.nodes import router_message
from glople_agent.llm.fake import AfterToolsScript, LoopAnswerScript, OracleCase, OracleScript, load_oracle_cases
from glople_agent.plan import InMemoryPlanStore
from glople_agent.sanitize import clean_text
from glople_agent.tools.fake_backend import FakeBackend

DATASETS: dict[str, Path] = {
    "v2-train": contract.V2_TRAIN_PATH,
    "v2-test": contract.V2_TEST_PATH,
    "v2-valid": contract.V2_DATA_DIR / "valid.jsonl",
    "human": contract.HUMAN_STYLE_PATH,
}
CATEGORIES = ("tool", "tool(병렬)", "plan", "clarify", "clarify(지역)", "tool(되묻기)", "direct", "fallback", "unfinished")
INTENDED_CLARIFY = ("clarify(지역)", "tool(되묻기)")   # 정답 호출이지만 정책상 되묻기(M6)
REPLAY_NOW = datetime(2026, 10, 17, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
REPLAY_TOKEN = "replay-fake-token-not-a-jwt"   # 고정 테스트 값(실제 JWT 아님)


@dataclass
class CaseResult:
    case: OracleCase
    path: list[str] = field(default_factory=list)     # 첫 invoke + 재개 invoke의 노드
    max_invoke_steps: int = 0                         # 한 번의 invoke에서 돈 노드 수 최대
    reply_kind: str | None = None
    final_text: str | None = None
    router_kind: str | None = None
    verdicts_ok: bool = False                         # validate가 모든 호출을 통과시킴
    calls: int = 0
    executed: list[tuple[str, dict]] = field(default_factory=list)   # validate 뒤 라우터 메시지에 남은 호출
    reached_end: bool = False
    error: str | None = None
    region_clarify: bool = False      # (M6) 막힌 호출이 모두 clarify:region_ambiguous(모호한 지역)뿐
    after_tools: tuple[str, ...] = ()  # (M7 loop) 도구 결과 뒤 라운드마다의 판단(answer·call·guard_fail …)

    @property
    def category(self) -> str:
        p = self.path
        if self.error or not self.reached_end:
            return "unfinished"
        if "plan_prepare" in p:
            return "plan"
        if self.region_clarify:   # 병렬 중 날씨만 '어느 광주?'로 막히고 검색은 돈 경우도(답 끝에 되묻기 문장)
            return "clarify(지역)"
        if "clarify" in p and "tools" in p:
            return "tool(되묻기)"
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
            if self.category == "clarify(지역)":
                return True
            return (self.verdicts_ok and self.category in ("tool", "tool(병렬)", "plan", "tool(되묻기)")
                    and self.executed == self.case.expected_calls)
        return self.category == "direct"


@dataclass
class ReplayReport:
    datasets: list[str]
    results: list[CaseResult]
    missing: list[str] = field(default_factory=list)
    seconds: float = 0.0
    saved_plans: int = 0
    after_tools: str = "answer"   # M7: answer | loop

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


def replay_cases(cases: Sequence[OracleCase], *, check_request: bool = True,
                 plan_graph: str = "flat", after_tools: str = "answer") -> tuple[list[CaseResult], int]:
    """정답 케이스를 그래프에 흘린다. (케이스 결과, 저장된 일정 수). plan_graph(M3): "flat" | "subgraph".
    after_tools(M7): "answer"(기본) | "loop"(결과 뒤 요청은 LoopAnswerScript)."""
    store = InMemoryPlanStore()
    oracle = OracleScript(cases=cases, check_request=check_request)
    loop = after_tools == "loop"
    script = AfterToolsScript(oracle, LoopAnswerScript(), check_request=check_request) if loop else oracle
    deps = fake_deps(script, backend=FakeBackend(today=REPLAY_NOW.date()), store=store, plan_graph=plan_graph,
                     after_tools=after_tools)
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
            res.final_text = turn.final_text
            res.router_kind = values.get("router_kind")
            verdicts = values.get("verdicts") or []
            res.calls = len(verdicts)
            res.verdicts_ok = bool(verdicts) and all(v["ok"] for v in verdicts)
            blocked = [v for v in verdicts if not v["ok"]]
            res.region_clarify = bool(blocked) and all(
                "clarify:region_ambiguous" in ((v.get("grounding") or {}).get("issues") or []) for v in blocked)
            if loop:   # 두 번째 라운드가 있으면 router_message는 그 호출이라, 정답과는 첫 라운드 호출을 비교
                rounds = tool_rounds(values.get("messages") or [])
                ai = rounds[0][0] if rounds else router_message(values)
                res.after_tools = tuple(e.get("decision") or "?" for e in turn.after_tools)
            else:
                ai = router_message(values)
            if ai is not None:
                res.executed = [(tc["name"], tc["args"]) for tc in ai.tool_calls]
            res.reached_end = turn.done and turn.error is None and res.path[-1:] == ["finish"]
            if turn.error:
                res.error = turn.error
        except Exception as e:  # noqa: BLE001 - 재생은 예외를 세어 표로 보여 준다
            res.error = f"{type(e).__name__}: {str(e)[:120]}"
        out.append(res)
    return out, store.save_count


def replay(names: Iterable[str] = ("v2-test", "human"), *, check_request: bool = True,
           plan_graph: str = "flat", after_tools: str = "answer") -> ReplayReport:
    names = list(names)
    paths, missing = dataset_paths(names)
    cases = load_oracle_cases(paths) if paths else []
    start = time.perf_counter()
    results, saved = replay_cases(cases, check_request=check_request, plan_graph=plan_graph, after_tools=after_tools)
    return ReplayReport(names, results, missing, time.perf_counter() - start, saved, after_tools)


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
    if report.after_tools == "loop":   # M7: 도구 결과 뒤 라운드마다의 판단(가짜 LoopAnswerScript라 늘 1라운드 답이어야 함)
        loops = Counter(" → ".join(r.after_tools) for r in report.results if r.after_tools)
        lines += ["", "| 도구 결과 뒤(loop) 판단 | 건수 |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(loops.items(), key=lambda kv: (-kv[1], kv[0]))] or ["| - | 0 |"]
    if report.errors or report.unexpected:
        lines += ["", "정답과 다르거나 예외가 난 발화(앞 10개):"]
        for r in (report.errors + [u for u in report.unexpected if not u.error])[:10]:
            lines.append(f"- {r.case.source}:{r.case.line} {r.case.utterance!r} → {r.category} {r.error or ''}".rstrip())
    return "\n".join(lines)


# --- 모델 예측 재생(M1): 학습 평가가 남긴 모델 출력을 라우터 답으로 그래프에 흘린다 ---------------------

class PredictionScript:
    """predictions.jsonl의 출력(<tool_call> 텍스트)을 서버가 냈을 모양(tool_calls / 텍스트)으로 돌려주는 라우터."""

    def __init__(self, rows: Sequence[dict]):
        from glople_agent.eval.scoring import parse_output

        self._parse = parse_output
        self.by_user: dict[str, dict] = {}
        for row in rows:
            self.by_user.setdefault(row["user"], row)
            self.by_user.setdefault(clean_text(row["user"]), row)

    def __call__(self, messages, kwargs):
        from glople_agent.llm.fake import from_server, last_human_text, server_tool_call, text_message

        text = last_human_text(messages)
        row = self.by_user.get(text) or self.by_user.get(clean_text(text))
        if row is None:
            raise KeyError(text)
        calls, invalid = self._parse(row.get("output") or "")
        if not calls and not invalid:
            return text_message(row.get("output") or "")
        raw = [server_tool_call(c["name"], c["arguments"]) for c in calls]
        if invalid:   # 깨진 블록은 서버가 버린 것처럼(호출 없음 + finish_reason=tool_calls)
            return from_server("\n" * max(len(raw) - 1, 0) or None, raw, "tool_calls")
        return from_server("\n" * (len(raw) - 1) or None, raw, "tool_calls")


@dataclass
class PredictionReplay:
    name: str
    total: int = 0
    before_correct: int = 0
    after_correct: int = 0
    correct_changed: int = 0      # 정답이었는데 그래프를 지나며 바뀜(0이어야 함)
    wrong_fixed: int = 0
    errors: int = 0
    categories: Counter = field(default_factory=Counter)


def replay_predictions(name: str, path: Path) -> PredictionReplay:
    """예측 출력을 그래프(route → validate → …)에 흘려, validate 뒤 실제로 실행된 호출로 다시 채점한다.
    eval/grounding_eval.py(함수 단위)와 같은 숫자가 나와야 한다(test_grounding_eval)."""
    from glople_agent.eval.grounding_eval import effective_args
    from glople_agent.eval.scoring import canonical

    rows = contract.read_jsonl(path)
    script = PredictionScript(rows)
    deps = fake_deps(script, backend=FakeBackend(today=REPLAY_NOW.date()), store=InMemoryPlanStore())
    graph = build_graph(deps, InMemorySaver())
    ctx = AgentContext(user_id="replay-user", bearer=BearerToken(REPLAY_TOKEN), request_id="replay",
                       now=REPLAY_NOW, seed=0)
    rep = PredictionReplay(name)
    for row in rows:
        rep.total += 1
        correct = str(row.get("correct")) == "True"
        rep.before_correct += correct
        try:
            thread = new_thread_id()
            turn = run_turn(graph, thread, row["user"], ctx)
            path_nodes = list(turn.path)
            values = turn.values
            ai = router_message(values) if "tools" in path_nodes or "plan_prepare" in path_nodes else None
            text = values.get("router_input", "")
            executed = [{"name": tc["name"], "arguments": effective_args(tc["name"], tc["args"], text)}
                        for tc in (ai.tool_calls if ai is not None else [])]
            if turn.pending is not None:
                resume(graph, thread, {"decision": "reject"}, ctx, interrupt_id=turn.pending.id)
            expected = row.get("expected")
            if isinstance(expected, list):
                ok = canonical(executed) == canonical(expected)
            else:
                ok = not executed
            rep.after_correct += ok
            rep.correct_changed += correct and not ok
            rep.wrong_fixed += (not correct) and ok
            cat = ("plan" if "plan_prepare" in path_nodes else "clarify" if "clarify" in path_nodes
                   else "direct" if "direct_reply" in path_nodes else "fallback" if "fallback" in path_nodes
                   else "tool" if "tools" in path_nodes else "other")
            rep.categories[cat] += 1
        except Exception:  # noqa: BLE001 - 재생은 예외를 세어 표로 보여 준다
            rep.errors += 1
    return rep


def format_prediction_replays(reps: Sequence[PredictionReplay]) -> str:
    lines = ["| 예측 | 건수 | 검증 전 정답 | 그래프 지난 뒤 정답 | 고쳐서 정답 | 정답→오답 | 예외 | 경로 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in reps:
        cats = ", ".join(f"{k} {v}" for k, v in sorted(r.categories.items(), key=lambda kv: -kv[1]))
        lines.append(f"| {r.name} | {r.total} | {r.before_correct} | {r.after_correct} | {r.wrong_fixed} | "
                     f"{r.correct_changed} | {r.errors} | {cats} |")
    return "\n".join(lines)

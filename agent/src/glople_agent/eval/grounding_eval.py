"""근거 검사 측정(design.md 7절·13절 M1): 정답 호출을 막지 않는지(오탐), 모델 오답을 잡는지(탐지)를 CPU로 잰다.

- 정답 세트: v2 train·valid·test, human_style의 정답 호출을 validate와 같은 함수(nodes.judge_call)로 판정한다.
  '막힘' = 되묻기(clarify)로 바뀜, '바뀜' = 인자를 빼거나 고침. 둘 다 0이어야 한다(정답을 건드리지 않음).
- 예측 세트: training/results/*.predictions.jsonl(학습 평가가 남긴 모델 출력). 출력의 <tool_call>을 evaluate.py와 같은
  규칙(eval/scoring.py)으로 읽어 같은 판정을 한다.
  - 정답(correct=True) 중 걸린 것: 0이어야 한다(되묻기·인자 변경 모두).
  - 오답 중 걸린 것: 시범판(proto_validate.py)과 같은 정의 — 근거 검사 코드 clarify·drop·fix 중 하나라도.
    스키마 검사(모르는 키·enum)는 시범판 숫자와 견주려고 따로 센다.
  - 검증 뒤 정답: validate를 거친 뒤 실제로 실행될 호출(근거 없는 키워드·장소는 빠짐)이 정답과 같거나,
    정답이 텍스트(되묻기·바로 답변)인데 호출이 모두 막힌 경우. evaluate.py의 exact 정의를 그대로 쓴다.

python -m glople_agent.cli grounding 이 표를 찍는다.
"""
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from glople_agent import contract
from glople_agent.eval.scoring import canonical, parse_output
from glople_agent.graph.grounding import check_call
from glople_agent.graph.nodes import judge_call
from glople_agent.tools.args import ARGS_MODELS
from glople_agent.tools.keywords import resolve_keywords

RESULTS_DIR = contract.TRAINING_DIR / "results"
GOLD_SETS: dict[str, Path] = {
    "v2-train": contract.V2_TRAIN_PATH,
    "v2-valid": contract.V2_DATA_DIR / "valid.jsonl",
    "v2-test": contract.V2_TEST_PATH,
    "human": contract.HUMAN_STYLE_PATH,
}
PREDICTION_SETS: dict[str, Path] = {   # 이름: (모델)-(평가 세트)
    "v1-human": RESULTS_DIR / "finetuned_human.predictions.jsonl",
    "v2-human": RESULTS_DIR / "finetuned_v2_human.predictions.jsonl",
    "v1-v2test": RESULTS_DIR / "finetuned_v1_v2test.predictions.jsonl",
    "v2-v2test": RESULTS_DIR / "finetuned_v2_v2test.predictions.jsonl",
    # 2026-10-18 v3 학습 뒤 추가. human2 = 생성기를 보지 않고 새로 쓴 사람 말투 세트(eval_sets/human_style_v2.jsonl)
    "v3-human": RESULTS_DIR / "finetuned_v3_human.predictions.jsonl",
    "v3-v2test": RESULTS_DIR / "finetuned_v3_v2test.predictions.jsonl",
    "v1-human2": RESULTS_DIR / "finetuned_v1_human2.predictions.jsonl",
    "v2-human2": RESULTS_DIR / "finetuned_v2_human2.predictions.jsonl",
    "v3-human2": RESULTS_DIR / "finetuned_v3_human2.predictions.jsonl",
}
KNOWN_TOOLS = frozenset(ARGS_MODELS)


def _is_hard(issue: str) -> bool:
    return issue.startswith(("clarify:", "drop:", "fix:"))


# --- 정답 세트 -------------------------------------------------------------------

@dataclass
class GoldReport:
    name: str
    calls: int = 0
    blocked: int = 0                 # clarify(스키마·근거)
    changed: int = 0                 # 실행 인자가 정답과 달라짐(drop·fix)
    notes: Counter = field(default_factory=Counter)
    issues: Counter = field(default_factory=Counter)
    examples: list[tuple[str, str, dict]] = field(default_factory=list)   # (사유, 발화, 인자) 앞 몇 개


def _gold_records(path: Path) -> Iterable[tuple[str, list[dict]]]:
    for r in contract.read_jsonl(path):
        msgs = r["messages"]
        calls = [{"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"])}
                 for c in msgs[-1].get("tool_calls") or []]
        yield msgs[1]["content"], calls


def measure_gold(name: str, path: Path, *, max_examples: int = 8) -> GoldReport:
    rep = GoldReport(name)
    for text, calls in _gold_records(path):
        for c in calls:
            rep.calls += 1
            verdict, clean = judge_call(c["name"], c["arguments"], text, known=KNOWN_TOOLS)
            issues = (verdict.get("grounding") or {}).get("issues") or []
            rep.issues.update(issues)
            rep.notes.update(i for i in issues if i.startswith("note:"))
            reason = None
            if clean is None:
                rep.blocked += 1
                reason = verdict["reason"] + ":" + ",".join(i for i in issues if i.startswith("clarify:"))
            elif canonical([{"name": c["name"], "arguments": effective_args(c["name"], clean, text)}]) != canonical([c]):
                rep.changed += 1
                reason = "changed:" + ",".join(issues)
            if reason and len(rep.examples) < max_examples:
                rep.examples.append((reason, text, c["arguments"]))
    return rep


# --- 예측 세트 -------------------------------------------------------------------

def effective_args(name: str, clean: dict, text: str, recent_places: Sequence[str] = ()) -> dict:
    """validate를 지난 인자로 실제로 실행·저장될 값: 모르는 키워드는 어댑터가 빼고(서버에 안 보냄),
    근거 없는 일정 장소는 MODEL_SUGGESTED라 확인 카드에서 기본 해제된다."""
    out = dict(clean)
    if name == "recommend_by_keywords":
        out["keywords"] = resolve_keywords(out.get("keywords") or []).applied
    if name == contract.PLAN_TOOL_NAME:
        g = check_call(name, out, text, recent_places=recent_places)
        out["places"] = [p for p in out.get("places") or [] if p not in g.ungrounded_places]
    return out


@dataclass
class PredictionCase:
    task: str
    user: str
    correct: bool                       # 학습 평가 기록(evaluate.py exact)
    expects_tool: bool
    calls: list[dict]
    invalid_blocks: int
    issues: list[list[str]]             # 스키마를 통과한 호출마다 근거 검사 코드(시범판과 같은 정의)
    schema_blocked: int                 # 스키마 검사에서 막힌 호출 수(모르는 도구·enum·필수값)
    schema_fixed: int                   # 모르는 키만 빼고 통과시킨 호출 수(drop:extra_key)
    executed: list[dict]                # validate 뒤 실행될 호출(effective_args)
    clarified: int                      # clarify로 바뀐 호출 수(스키마·근거)

    @property
    def grounding_caught(self) -> bool:
        return any(_is_hard(i) for issues in self.issues for i in issues)

    @property
    def grounding_clarify(self) -> bool:
        return any(i.startswith("clarify:") for issues in self.issues for i in issues)

    @property
    def validate_caught(self) -> bool:
        """스키마 또는 근거 검사가 무엇이든 바꿈(막음·뺌·고침)."""
        return self.grounding_caught or self.schema_blocked > 0 or self.schema_fixed > 0

    def after_correct(self, expected: Any) -> bool:
        if self.expects_tool:
            return self.invalid_blocks == 0 and canonical(self.executed) == canonical(expected)
        return not self.executed   # 텍스트 정답: 호출이 하나도 실행되지 않으면 맞음(evaluate.py와 같은 판단)

    def flags(self) -> list[str]:
        out = [i for issues in self.issues for i in issues]
        if self.schema_blocked:
            out.append(f"schema×{self.schema_blocked}")
        if self.schema_fixed:
            out.append(f"drop:extra_key×{self.schema_fixed}")
        return out


@dataclass
class PredictionReport:
    name: str
    path: Path
    cases: list[PredictionCase] = field(default_factory=list)
    expected: list[Any] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.cases)

    def _sel(self, correct: bool) -> list[PredictionCase]:
        return [c for c in self.cases if c.correct is correct]

    @property
    def correct(self) -> list[PredictionCase]:
        return self._sel(True)

    @property
    def wrong(self) -> list[PredictionCase]:
        return self._sel(False)

    @property
    def correct_flagged(self) -> list[PredictionCase]:
        """정답인데 근거 검사·스키마가 무엇이든 건드린 것(0이어야 함)."""
        return [c for c in self.correct if c.validate_caught]

    @property
    def wrong_caught(self) -> list[PredictionCase]:
        return [c for c in self.wrong if c.grounding_caught]

    @property
    def wrong_clarify(self) -> list[PredictionCase]:
        return [c for c in self.wrong if c.grounding_clarify]

    @property
    def wrong_caught_any(self) -> list[PredictionCase]:
        return [c for c in self.wrong if c.validate_caught]

    def after_correct(self) -> int:
        return sum(c.after_correct(e) for c, e in zip(self.cases, self.expected))

    def fixed(self) -> int:
        """오답이었는데 validate 뒤 정답이 된 것."""
        return sum((not c.correct) and c.after_correct(e) for c, e in zip(self.cases, self.expected))

    def broken(self) -> int:
        """정답이었는데 validate 뒤 틀린 것(0이어야 함)."""
        return sum(c.correct and not c.after_correct(e) for c, e in zip(self.cases, self.expected))


def judge_prediction(row: dict) -> PredictionCase:
    text = row["user"]
    calls, invalid = parse_output(row.get("output") or "")
    per_call: list[list[str]] = []
    executed: list[dict] = []
    schema_blocked = schema_fixed = clarified = 0
    for c in calls:
        verdict, clean = judge_call(c["name"], c["arguments"], text, known=KNOWN_TOOLS)
        if verdict["reason"] in ("schema", "unknown_tool"):
            schema_blocked += 1
            clarified += bool(verdict.get("clarify"))
            continue
        issues = list((verdict.get("grounding") or {}).get("issues") or [])
        if "drop:extra_key" in issues:
            schema_fixed += 1
            issues.remove("drop:extra_key")
        per_call.append(issues)
        if clean is None:
            clarified += 1
            continue
        executed.append({"name": c["name"], "arguments": effective_args(c["name"], clean, text)})
    return PredictionCase(task=row.get("task", ""), user=text, correct=str(row.get("correct")) == "True",
                          expects_tool=isinstance(row.get("expected"), list), calls=calls, invalid_blocks=invalid,
                          issues=per_call, schema_blocked=schema_blocked, schema_fixed=schema_fixed,
                          executed=executed, clarified=clarified)


def measure_predictions(name: str, path: Path) -> PredictionReport:
    rep = PredictionReport(name, path)
    for row in contract.read_jsonl(path):
        rep.cases.append(judge_prediction(row))
        rep.expected.append(row.get("expected"))
    return rep


def run(gold: Iterable[str] = tuple(GOLD_SETS), predictions: Iterable[str] = tuple(PREDICTION_SETS)
        ) -> tuple[list[GoldReport], list[PredictionReport], list[str]]:
    """(정답 표, 예측 표, 없는 파일 이름)."""
    missing: list[str] = []
    golds, preds = [], []
    for name in gold:
        path = GOLD_SETS[name]
        if path.is_file():
            golds.append(measure_gold(name, path))
        else:
            missing.append(name)
    for name in predictions:
        path = PREDICTION_SETS[name]
        if path.is_file():
            preds.append(measure_predictions(name, path))
        else:
            missing.append(name)
    return golds, preds, missing


def format_report(golds: Sequence[GoldReport], preds: Sequence[PredictionReport], missing: Sequence[str] = (),
                  *, details: bool = False) -> str:
    lines = ["정답 호출(막힘·바뀜 0이어야 함)", "", "| 세트 | 호출 | 되묻기로 막힘 | 인자 바뀜 | 기록만(note) |",
             "|---|---|---|---|---|"]
    for g in golds:
        notes = ", ".join(f"{k} {v}" for k, v in sorted(g.notes.items())) or "0"
        lines.append(f"| {g.name} | {g.calls} | {g.blocked} | {g.changed} | {notes} |")
    total = sum(g.calls for g in golds)
    lines.append(f"| 합계 | {total} | {sum(g.blocked for g in golds)} | {sum(g.changed for g in golds)} | |")
    lines += ["", "모델 예측 재생(시범판 정의: 근거 검사 코드 clarify·drop·fix 중 하나라도 걸리면 '잡음')", "",
              "| 예측 | 건수 | 정답 | 정답 중 건드림 | 오답 | 오답 중 잡음(근거) | 그중 되묻기 | 스키마까지 | "
              "검증 전→뒤 정답 | 고쳐서 정답 | 정답→오답 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in preds:
        lines.append(f"| {p.name} | {p.n} | {len(p.correct)} | {len(p.correct_flagged)} | {len(p.wrong)} | "
                     f"{len(p.wrong_caught)} | {len(p.wrong_clarify)} | {len(p.wrong_caught_any)} | "
                     f"{len(p.correct)}→{p.after_correct()} | {p.fixed()} | {p.broken()} |")
    if missing:
        lines += ["", f"없는 파일(건너뜀): {', '.join(missing)}"]
    if details:
        for g in golds:
            for reason, text, args in g.examples:
                lines.append(f"- [정답 {g.name}] {reason} | {text[:50]} | {json.dumps(args, ensure_ascii=False)[:80]}")
        for p in preds:
            for c, e in zip(p.cases, p.expected):
                if not c.correct or c.validate_caught:
                    mark = "고침" if (not c.correct and c.after_correct(e)) else ("정답" if c.correct else "오답")
                    lines.append(f"- [{p.name}] {mark:2s} {c.task:9s} | {c.user[:38]:38s} | {c.flags()}")
    return "\n".join(lines)


__all__ = [
    "GOLD_SETS", "GoldReport", "PREDICTION_SETS", "PredictionCase", "PredictionReport", "effective_args",
    "format_report", "judge_prediction", "measure_gold", "measure_predictions", "run",
]

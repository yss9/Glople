"""결과 표(Markdown) 만들기. baseline.py 와 evaluate.py 가 같은 모양의 표를 쓰도록 한 곳에 둡니다."""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

METRICS = ("ndcg@10", "recall@20", "mrr")
METRIC_LABELS = {"ndcg@10": "nDCG@10", "recall@20": "Recall@20", "mrr": "MRR"}
TYPE_LABELS = {
    "kw": "키워드만",
    "kw+region": "키워드 + 지역",
    "kw+category": "키워드 + 분류",
    "kw+region+category": "키워드 + 지역 + 분류",
}
ORIGIN_LABELS = {"test": "1차 test (서울·인천·광주)", "test_new": "2차 test_new (여러 지역)", "valid": "valid"}
SET_LABELS = {
    "test": "test (학습에 없는 문장 틀·표현, 대표 지표)",
    "test_iid": "test_iid (학습과 같은 문장 틀, 참고)",
    "valid": "valid (학습 중 best 에폭 선택에 씀)",
}


def is_reference(label: str) -> bool:
    return label.startswith("(참고)")


def _fmt(x) -> str:
    return "-" if x is None else f"{x:.3f}"


def overall_table(methods: Sequence[Tuple[str, Dict[str, dict]]], set_name: str, bold_best: bool = True) -> str:
    """methods: [(이름, {질의 세트: evaluate_rankings 결과})]. 한 질의 세트의 전체 지표 표."""
    rows = [(label, res.get(set_name, {}).get("overall")) for label, res in methods]
    # '(참고)' 줄(정답 라벨을 쓴 천장)은 굵게 표시할 최고값 계산에서 뺍니다.
    best = {m: max((r[m] for label, r in rows if r and m in r and not is_reference(label)), default=None)
            for m in METRICS}
    n = next((r["queries"] for _, r in rows if r), 0)
    lines = [f"| 방법 ({n}개 질의) | " + " | ".join(METRIC_LABELS[m] for m in METRICS) + " |",
             "| :-- | " + " | ".join("--:" for _ in METRICS) + " |"]
    for label, r in rows:
        cells = []
        for m in METRICS:
            v = r.get(m) if r else None
            s = _fmt(v)
            if (bold_best and v is not None and best[m] is not None and abs(v - best[m]) < 1e-12
                    and not is_reference(label)):
                s = f"**{s}**"
            cells.append(s)
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def group_table(methods: Sequence[Tuple[str, Dict[str, dict]]], set_name: str, group: str,
                metric: str = "ndcg@10") -> str:
    """질의 종류별(by_type) 또는 출처 분할별(by_origin) 한 지표 표."""
    labels = TYPE_LABELS if group == "by_type" else ORIGIN_LABELS
    keys: List[str] = []
    for _, res in methods:
        for k in res.get(set_name, {}).get(group, {}):
            if k not in keys:
                keys.append(k)
    order = [k for k in labels if k in keys] + [k for k in keys if k not in labels]
    head_cells = []
    for k in order:
        n = next((res[set_name][group][k]["queries"] for _, res in methods if k in res.get(set_name, {}).get(group, {})), 0)
        head_cells.append(f"{labels.get(k, k)} ({n})")
    lines = [f"| 방법 ({METRIC_LABELS[metric]}) | " + " | ".join(head_cells) + " |",
             "| :-- | " + " | ".join("--:" for _ in order) + " |"]
    for label, res in methods:
        g = res.get(set_name, {}).get(group, {})
        lines.append(f"| {label} | " + " | ".join(_fmt(g.get(k, {}).get(metric)) for k in order) + " |")
    return "\n".join(lines)


def full_report(methods: Sequence[Tuple[str, Dict[str, dict]]], set_names: Sequence[str]) -> str:
    parts = []
    for s in set_names:
        if not any(s in res for _, res in methods):
            continue
        parts.append(f"### {SET_LABELS.get(s, s)}\n\n" + overall_table(methods, s))
        if s == "test":
            parts.append("질의 종류별 nDCG@10\n\n" + group_table(methods, s, "by_type"))
            parts.append("질의 출처 분할별 nDCG@10\n\n" + group_table(methods, s, "by_origin"))
    return "\n\n".join(parts) + "\n"

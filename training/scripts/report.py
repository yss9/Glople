"""기본 모델과 파인튜닝 모델의 평가 요약을 마크다운 표로 만든다."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = [
    ("decision_accuracy", "호출 여부 판단 정확도"),
    ("tool_name_accuracy", "도구 이름 정확도"),
    ("exact_match", "도구 + 인자 완전 일치"),
    ("json_error_rate", "JSON 오류율 (낮을수록 좋음)"),
    ("seconds_per_example", "예시당 시간(초)"),
]


def main():
    names = sys.argv[1:] or ["base", "finetuned"]
    summaries = [json.loads((ROOT / "results" / f"{n}.summary.json").read_text()) for n in names]

    lines = ["| 지표 | " + " | ".join(names) + " |", "| :-- |" + " --: |" * len(names)]
    for key, label in METRICS:
        values = [s[key] for s in summaries]
        cells = [f"{v:.2f}" if key == "seconds_per_example" else f"{v * 100:.1f}%" for v in values]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    lines += ["", "| 작업 | " + " | ".join(names) + " |", "| :-- |" + " --: |" * len(names)]
    for task in summaries[0]["by_task_accuracy"]:
        cells = [f"{s['by_task_accuracy'].get(task, 0) * 100:.1f}%" for s in summaries]
        lines.append(f"| {task} | " + " | ".join(cells) + " |")

    report = "\n".join(lines) + "\n"
    (ROOT / "results" / "report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()

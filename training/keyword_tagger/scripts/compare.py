"""학습 전 기준선 두 개(부분 문자열, 국내 규칙)와 학습한 모델의 test 결과를 한국어 마크다운 표로 출력한다.

- 학습 전 (부분 문자열): KeywordMask.fromDescription (results/baseline.json 의 test)
- 학습 전 (국내 규칙):   KeywordMask.fromKoreanDescription, 서버가 TourAPI 여행지에 지금 쓰는 규칙
                         (results/baseline.json 의 korean_rule.test, 없으면 열을 뺀다)
- 모델: 인자로 준 results/<run_name>.json (주지 않으면 기준선 두 개만)

실행: python3 -I scripts/compare.py results/roberta-base.json [results/다른실행.json ...]
      python3 -I scripts/compare.py            # 기준선만
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kt_common import OUTPUTS_DIR, RESULTS_DIR, evaluate_sets, load_keywords, read_jsonl  # noqa: E402

ROWS = [
    ("micro 정밀도", "micro", "precision"),
    ("micro 재현율", "micro", "recall"),
    ("micro F1", "micro", "f1"),
    ("macro 정밀도", "macro", "precision"),
    ("macro 재현율", "macro", "recall"),
    ("macro F1", "macro", "f1"),
    ("키워드 집합 완전 일치율", "samples", "exact_match"),
    ("항목별 Jaccard 평균", "samples", "mean_jaccard"),
    ("항목당 예측 키워드 수", "samples", "avg_pred_keywords"),
]
SUBSTRING_COL = "학습 전 (부분 문자열)"
KOREAN_COL = "학습 전 (국내 규칙)"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fmt(x):
    return f"{x:.3f}" if isinstance(x, float) else str(x)


def prediction_files(run):
    if run.get("model_dir"):
        yield Path(run["model_dir"]) / "test_predictions.jsonl"
    if run.get("name"):
        yield OUTPUTS_DIR / run["name"] / "test_predictions.jsonl"


def model_keyword_stats(run, wanted, split):
    """모델의 키워드별 test 지표. 결과 파일 per_keyword(자주 나오는 15개)에 없는 키워드는
    outputs/<run_name>/test_predictions.jsonl 로 다시 계산한다. 그 파일의 micro 지표가 결과 파일과 다르면
    (다른 실행의 예측 등) 쓰지 않는다. 계산할 수 없으면 그 키워드는 빠진다(표에는 '-')."""
    per = run.get(split, {}).get("per_keyword", {})
    stats = {k: per[k] for k in wanted if k in per}
    missing = [k for k in wanted if k not in stats]
    if not missing or split != "test":
        return stats
    for path in prediction_files(run):
        try:
            if not path.exists():
                continue
            rows = read_jsonl(path)
            m = evaluate_sets([r["gold"] for r in rows], [r["pred"] for r in rows], load_keywords(), missing)
            if m["micro"] != run[split]["micro"]:
                continue
            stats.update(m["per_keyword"])
            break
        except Exception:  # 비교표가 실패하지 않도록 계산할 수 없으면 '-' 로 둔다
            continue
    return stats


def main():
    p = argparse.ArgumentParser(description="기준선(부분 문자열, 국내 규칙) vs 모델 비교표")
    p.add_argument("runs", nargs="*", help="results/<run_name>.json (없으면 기준선만)")
    p.add_argument("--baseline", default=str(RESULTS_DIR / "baseline.json"))
    p.add_argument("--split", default="test", choices=["test"])
    args = p.parse_args()

    base = load(args.baseline)
    korean = base.get("korean_rule")
    runs = [load(r) for r in args.runs]
    cols = [(SUBSTRING_COL, base[args.split])]
    if korean and args.split in korean:
        cols.append((KOREAN_COL, korean[args.split]))
    for r in runs:
        mode = r.get("thresholds", {}).get("selected_mode", "")
        cols.append((f"{r['name']} ({mode} 임계값)", r[args.split]))

    n = base[args.split]["samples"]["items"]
    print(f"### test 분할 {n}개 비교\n")
    print("| 지표 | " + " | ".join(c[0] for c in cols) + " |")
    print("| :-- | " + " | ".join("--:" for _ in cols) + " |")
    for label, group, key in ROWS:
        print(f"| {label} | " + " | ".join(fmt(c[1][group][key]) for c in cols) + " |")
    gold_avg = base[args.split]["samples"]["avg_gold_keywords"]
    print(f"\n정답(라벨) 항목당 키워드 수 평균: {gold_avg:.3f}\n")
    if korean:
        print(f"'{KOREAN_COL}'은 서버가 TourAPI 여행지(source=TOUR_API)에 지금 쓰는 "
              "`KeywordMask.fromKoreanDescription`입니다. 한 글자 키워드(산, 섬, 성, 탑, 강)는 "
              "독립된 단어이거나 조사만 붙었을 때만 붙이고, 나머지 키워드는 부분 문자열 규칙과 같습니다.\n")

    top = base["top_keywords"]
    print("### 자주 나오는 키워드 15개의 F1\n")
    print("| 키워드 | test 정답 수 | " + " | ".join(c[0] for c in cols) + " |")
    print("| :-- | --: | " + " | ".join("--:" for _ in cols) + " |")
    for k in top:
        support = base[args.split]["per_keyword"][k]["support"]
        vals = []
        for _, m in cols:
            pk = m.get("per_keyword", {}).get(k)
            vals.append(fmt(pk["f1"]) if pk else "-")
        print(f"| {k} | {support} | " + " | ".join(vals) + " |")

    effect = base.get("single_char_keyword_effect", {}).get(args.split)
    if effect:
        single = list(effect)
        model_stats = [model_keyword_stats(r, single, args.split) for r in runs]
        print(f"\n### 한 글자 키워드 ({', '.join(single)}) 정밀도 / 재현율\n")
        print("칸마다 `정밀도 / 재현율 (예측 수)`입니다.\n")
        print("| 키워드 | test 정답 수 | " + " | ".join(c[0] for c in cols) + " |")
        print("| :-- | --: | " + " | ".join("--:" for _ in cols) + " |")
        for k in single:
            e = effect[k]
            cells = [e["substring"]] + ([e["korean_rule"]] if korean and args.split in korean else [])
            cells += [s.get(k) for s in model_stats]
            vals = [f"{fmt(c['precision'])} / {fmt(c['recall'])} ({c.get('predicted', '-')})" if c else "-"
                    for c in cells]
            print(f"| {k} | {e['support']} | " + " | ".join(vals) + " |")

    for r in runs:
        ex = r.get("excluded_keywords_zero_train_examples", [])
        print(f"\n- {r['name']}: 학습 예시가 없어 라벨 공간에서 뺀 키워드 {len(ex)}개 ({', '.join(ex) or '-'}), "
              f"best epoch {r.get('best_epoch')}, 전역 임계값 {r['thresholds']['global']}")
        other = r.get("test_other_threshold_mode")
        if other:
            print(f"  - 참고: {other['mode']} 임계값이면 micro F1 {other['micro']['f1']:.3f}, macro F1 {other['macro']['f1']:.3f}")


if __name__ == "__main__":
    main()

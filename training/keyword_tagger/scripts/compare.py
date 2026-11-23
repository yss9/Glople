"""학습 전 기준선(부분 문자열, 국내 규칙, 해외 규칙)과 학습한 모델의 평가 결과를 한국어 마크다운 표로 출력한다.

- 학습 전 (부분 문자열): KeywordMask.fromDescription (results/baseline.json 의 test / test_new / test_overseas)
- 학습 전 (국내 규칙):   KeywordMask.fromKoreanDescription, 서버가 TourAPI 여행지에 지금 쓰는 규칙
                         (results/baseline.json 의 korean_rule.<split>, 없으면 열을 뺀다)
- 학습 전 (해외 규칙):   서버가 위키데이터 여행지에 지금 쓰는 규칙(국내 규칙 ∪ 유형 코드표, 세계유산 플래그 제외).
                         results/baseline.json 의 overseas_rule.test_overseas 가 있을 때 test_overseas 표에만 넣는다
- 모델: 인자로 준 results/<run_name>.json (주지 않으면 기준선만)

평가 집합
  test           1차 배치 test 163개 (2차 배치를 넣기 전과 같은 목록이라 예전 결과와 그대로 비교된다)
  test_new       2차 배치 test (여러 지역). 2차 배치 전에 학습한 모델(예: roberta-base)은 이 결과가 없어 '-' 로 나온다
  test_overseas  3차 배치 test (해외 위키백과). 3차 배치 전에 학습한 모델(예: roberta-base-v2)은 '-' 로 나온다
기본(--split both)은 baseline.json 에 있는 평가 집합을 모두 차례로 출력한다.

실행: python3 -I scripts/compare.py results/roberta-base-v2.json [results/다른실행.json ...]
      python3 -I scripts/compare.py --split test results/roberta-base.json
      python3 -I scripts/compare.py            # 기준선만
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kt_common import EVAL_SPLITS, OUTPUTS_DIR, RESULTS_DIR, evaluate_sets, load_keywords, read_jsonl  # noqa: E402

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
OVERSEAS_COL = "학습 전 (해외 규칙)"
SPLIT_TITLES = {
    "test": "test 분할 (1차 배치)",
    "test_new": "test_new 분할 (2차 배치, 여러 지역)",
    "test_overseas": "test_overseas 분할 (3차 배치, 해외 위키백과)",
}
# 평가 집합의 결과가 없는 실행에 붙이는 설명
NO_RESULT_REASON = {
    "test_new": "2차 배치를 넣기 전에 학습한 실행",
    "test_overseas": "3차 배치를 넣기 전에 학습한 실행",
}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fmt(x):
    return f"{x:.3f}" if isinstance(x, float) else str(x)


def prediction_files(run, split):
    """train_keyword_tagger.py 가 남기는 예측 파일: outputs/<run_name>/test_predictions.jsonl, test_new_predictions.jsonl"""
    name = f"{split}_predictions.jsonl"
    if run.get("model_dir"):
        yield Path(run["model_dir"]) / name
    if run.get("name"):
        yield OUTPUTS_DIR / run["name"] / name


def model_keyword_stats(run, wanted, split):
    """모델의 키워드별 지표. 결과 파일 per_keyword 에 없는 키워드는 outputs/<run_name>/<split>_predictions.jsonl 로
    다시 계산한다. 그 파일의 micro 지표가 결과 파일과 다르면 (다른 실행의 예측 등) 쓰지 않는다.
    계산할 수 없으면 그 키워드는 빠진다(표에는 '-')."""
    if split not in run:
        return {}
    per = run[split].get("per_keyword", {})
    stats = {k: per[k] for k in wanted if k in per}
    missing = [k for k in wanted if k not in stats]
    if not missing:
        return stats
    for path in prediction_files(run, split):
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


def print_split(split, base, runs):
    korean = base.get("korean_rule")
    overseas = base.get("overseas_rule")
    cols = [(SUBSTRING_COL, base[split])]
    if korean and split in korean:
        cols.append((KOREAN_COL, korean[split]))
    if overseas and split in overseas:
        cols.append((OVERSEAS_COL, overseas[split]))
    for r in runs:
        mode = r.get("thresholds", {}).get("selected_mode", "")
        cols.append((f"{r['name']} ({mode} 임계값)", r.get(split)))

    n = base[split]["samples"]["items"]
    print(f"### {SPLIT_TITLES.get(split, split)} {n}개 비교\n")
    print("| 지표 | " + " | ".join(c[0] for c in cols) + " |")
    print("| :-- | " + " | ".join("--:" for _ in cols) + " |")
    for label, group, key in ROWS:
        print(f"| {label} | " + " | ".join(fmt(c[1][group][key]) if c[1] else "-" for c in cols) + " |")
    gold_avg = base[split]["samples"]["avg_gold_keywords"]
    print(f"\n정답(라벨) 항목당 키워드 수 평균: {gold_avg:.3f}\n")
    no_result = [r["name"] for r in runs if split not in r]
    if no_result:
        reason = NO_RESULT_REASON.get(split, "이 평가 집합을 만들기 전에 학습한 실행")
        print(f"{', '.join(no_result)}: 이 평가 집합의 결과가 없습니다 ({reason}).\n")

    # 평가 집합별 상위 15개 (test·test_new 는 국내 기준, test_overseas 는 해외 기준). 예전 baseline.json 은 top_keywords 하나
    top = base.get("top_keywords_by_split", {}).get(split) or base["top_keywords"]
    model_top = [model_keyword_stats(r, top, split) for r in runs]
    print(f"#### 자주 나오는 키워드 {len(top)}개의 F1 ({split})\n")
    print(f"| 키워드 | {split} 정답 수 | " + " | ".join(c[0] for c in cols) + " |")
    print("| :-- | --: | " + " | ".join("--:" for _ in cols) + " |")
    n_base = len(cols) - len(runs)
    for k in top:
        support = base[split]["per_keyword"][k]["support"]
        vals = []
        for _, m in cols[:n_base]:
            pk = m.get("per_keyword", {}).get(k)
            vals.append(fmt(pk["f1"]) if pk else "-")
        for s in model_top:
            vals.append(fmt(s[k]["f1"]) if k in s else "-")
        print(f"| {k} | {support} | " + " | ".join(vals) + " |")

    effect = base.get("single_char_keyword_effect", {}).get(split)
    if effect:
        single = list(effect)
        model_stats = [model_keyword_stats(r, single, split) for r in runs]
        print(f"\n#### 한 글자 키워드 ({', '.join(single)}) 정밀도 / 재현율 ({split})\n")
        print("칸마다 `정밀도 / 재현율 (예측 수)`입니다.\n")
        print(f"| 키워드 | {split} 정답 수 | " + " | ".join(c[0] for c in cols) + " |")
        print("| :-- | --: | " + " | ".join("--:" for _ in cols) + " |")
        for k in single:
            e = effect[k]
            cells = [e["substring"]] + ([e["korean_rule"]] if korean and split in korean else [])
            cells += [e.get("overseas_rule")] if overseas and split in overseas else []
            cells += [s.get(k) for s in model_stats]
            vals = [f"{fmt(c['precision'])} / {fmt(c['recall'])} ({c.get('predicted', '-')})" if c else "-"
                    for c in cells]
            print(f"| {k} | {e['support']} | " + " | ".join(vals) + " |")
    print()


def main():
    p = argparse.ArgumentParser(description="기준선(부분 문자열, 국내 규칙) vs 모델 비교표")
    p.add_argument("runs", nargs="*", help="results/<run_name>.json (없으면 기준선만)")
    p.add_argument("--baseline", default=str(RESULTS_DIR / "baseline.json"))
    p.add_argument("--split", default="both", choices=["both", *EVAL_SPLITS],
                   help="both(기본)는 baseline.json 에 있는 test, test_new, test_overseas 를 모두 출력")
    args = p.parse_args()

    base = load(args.baseline)
    runs = [load(r) for r in args.runs]
    splits = [s for s in EVAL_SPLITS if s in base] if args.split == "both" else [args.split]
    for split in splits:
        if split not in base:
            raise SystemExit(f"{args.baseline} 에 {split} 결과가 없습니다. scripts/baseline.py 를 다시 돌리세요.")
        print_split(split, base, runs)

    if base.get("korean_rule"):
        print(f"'{KOREAN_COL}'은 서버가 TourAPI 여행지(source=TOUR_API)에 지금 쓰는 "
              "`KeywordMask.fromKoreanDescription`입니다. 한 글자 키워드(산, 섬, 성, 탑, 강)는 "
              "독립된 단어이거나 조사만 붙었을 때만 붙이고, 나머지 키워드는 부분 문자열 규칙과 같습니다.")
    if base.get("overseas_rule") and any(s in base["overseas_rule"] for s in splits):
        print(f"\n'{OVERSEAS_COL}'은 서버가 위키데이터 해외 여행지(source=WIKIDATA)에 지금 쓰는 규칙입니다. "
              "설명문에서 '문화어'를 빼고 국내 규칙을 적용한 결과에, 유형 코드표(`wiki/type-keywords.csv`)가 "
              "그 여행지의 위키데이터 유형(P31)에 정해 둔 키워드를 더합니다. 세계유산 플래그로 '유네스코'를 더하는 부분은 "
              "추출본에 그 값이 없어 빠져 있습니다.")

    for r in runs:
        ex = r.get("excluded_keywords_zero_train_examples", [])
        sizes = r.get("split_sizes", {})
        print(f"\n- {r['name']}: 분할 {', '.join(f'{k} {v}' for k, v in sizes.items())}. "
              f"학습 예시가 없어 라벨 공간에서 뺀 키워드 {len(ex)}개 ({', '.join(ex) or '-'}), "
              f"best epoch {r.get('best_epoch')}, 전역 임계값 {r['thresholds']['global']}")
        for split in splits:
            other = r.get(f"{split}_other_threshold_mode")
            if other:
                print(f"  - 참고 ({split}): {other['mode']} 임계값이면 micro F1 {other['micro']['f1']:.3f}, "
                      f"macro F1 {other['macro']['f1']:.3f}")


if __name__ == "__main__":
    main()

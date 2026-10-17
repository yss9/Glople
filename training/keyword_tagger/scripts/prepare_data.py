"""라벨 검증·병합, 교차 라벨 일치도, train/valid/test 분할을 한 번에 만든다.

입력
  keywords.json, data/descriptions.jsonl, data/labels/part_*.jsonl, data/labels/cross_check.jsonl
출력
  labels/gold_labels.jsonl   {"id","keywords"} 만 (설명문 없음, 커밋용)
  data/gold.jsonl            id/title/overview/keywords (학습용, gitignore)
  labels/splits.json         seed 13, 70/15/15, id 목록
  results/agreement.json     교차 라벨 vs 기본 라벨 일치도
  results/label_stats.json   분할별 키워드 빈도

실행: python3 -I scripts/prepare_data.py
"""
from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kt_common import (  # noqa: E402
    CROSS_CHECK_PATH,
    DESCRIPTIONS_PATH,
    GOLD_LABELS_PATH,
    GOLD_PATH,
    RAW_LABELS_DIR,
    RESULTS_DIR,
    SPLIT_SEED,
    SPLITS_PATH,
    evaluate_sets,
    jaccard,
    keyword_order,
    load_keywords,
    read_jsonl,
    write_json,
    write_jsonl,
)

MIN_TRAIN_EXAMPLES = 5


def validate_and_merge(keywords):
    order = keyword_order(keywords)
    descriptions = read_jsonl(DESCRIPTIONS_PATH)
    ids = [d["id"] for d in descriptions]
    dup_desc = [i for i, c in Counter(ids).items() if c > 1]
    if dup_desc:
        raise SystemExit(f"descriptions.jsonl 에 중복 id: {dup_desc[:10]}")

    part_files = sorted(RAW_LABELS_DIR.glob("part_*.jsonl"))
    if not part_files:
        raise SystemExit(f"{RAW_LABELS_DIR} 에 part_*.jsonl 이 없습니다")
    primary = {}
    problems = []
    for path in part_files:
        for row in read_jsonl(path):
            rid = str(row.get("id"))
            kws = row.get("keywords")
            if not isinstance(kws, list):
                problems.append(f"{path.name} {rid}: keywords 가 배열이 아님")
                continue
            if rid in primary:
                problems.append(f"{path.name} {rid}: 기본 라벨 중복")
                continue
            invalid = [k for k in kws if k not in order]
            if invalid:
                problems.append(f"{path.name} {rid}: 목록에 없는 키워드 {invalid}")
            if len(kws) != len(set(kws)):
                problems.append(f"{path.name} {rid}: 같은 키워드 반복 {kws}")
            primary[rid] = kws

    missing = [i for i in ids if i not in primary]
    extra = sorted(set(primary) - set(ids))
    if missing:
        problems.append(f"라벨 없는 설명문 {len(missing)}개: {missing[:10]}")
    if extra:
        problems.append(f"설명문 없는 라벨 {len(extra)}개: {extra[:10]}")
    if problems:
        raise SystemExit("라벨 검증 실패:\n  " + "\n  ".join(problems))

    gold = []
    for d in descriptions:
        kws = sorted(set(primary[d["id"]]), key=order.__getitem__)
        gold.append({"id": d["id"], "title": d["title"], "overview": d["overview"], "keywords": kws})
    write_jsonl(GOLD_LABELS_PATH, ({"id": g["id"], "keywords": g["keywords"]} for g in gold))
    write_jsonl(GOLD_PATH, gold)

    sizes = Counter(len(g["keywords"]) for g in gold)
    summary = {
        "items": len(gold),
        "label_files": [p.name for p in part_files],
        "empty_label_items": sizes.get(0, 0),
        "keywords_per_item": {str(k): sizes[k] for k in sorted(sizes)},
        "avg_keywords_per_item": round(sum(len(g["keywords"]) for g in gold) / len(gold), 3),
        "keywords_never_used": [k for k in keywords if not any(k in g["keywords"] for g in gold)],
    }
    return gold, summary


def agreement(gold, keywords):
    primary = {g["id"]: g["keywords"] for g in gold}
    cross = read_jsonl(CROSS_CHECK_PATH)
    order = keyword_order(keywords)
    for row in cross:
        invalid = [k for k in row["keywords"] if k not in order]
        if invalid:
            raise SystemExit(f"cross_check {row['id']}: 목록에 없는 키워드 {invalid}")
    overlap = [r for r in cross if str(r["id"]) in primary]
    ref = [set(primary[str(r["id"])]) for r in overlap]
    other = [set(r["keywords"]) for r in overlap]
    metrics = evaluate_sets(ref, other, keywords)

    per_kw = {k: {"only_primary": 0, "only_cross": 0, "both": 0} for k in keywords}
    for a, b in zip(ref, other):
        for k in a & b:
            per_kw[k]["both"] += 1
        for k in a - b:
            per_kw[k]["only_primary"] += 1
        for k in b - a:
            per_kw[k]["only_cross"] += 1
    disagreements = sorted(
        (
            {"keyword": k, "disagreements": v["only_primary"] + v["only_cross"], **v}
            for k, v in per_kw.items()
            if v["only_primary"] + v["only_cross"] > 0
        ),
        key=lambda x: (-x["disagreements"], order[x["keyword"]]),
    )
    item_jaccard = sorted(
        (
            {"id": str(r["id"]), "jaccard": round(jaccard(a, b), 3),
             "only_primary": sorted(a - b, key=order.__getitem__),
             "only_cross": sorted(b - a, key=order.__getitem__)}
            for r, a, b in zip(overlap, ref, other)
        ),
        key=lambda x: (x["jaccard"], x["id"]),
    )
    return {
        "reference": "primary (data/labels/part_*.jsonl)",
        "compared": "cross_check (data/labels/cross_check.jsonl)",
        "cross_check_items": len(cross),
        "overlap_items": len(overlap),
        "micro_vs_primary": {k: metrics["micro"][k] for k in ("precision", "recall", "f1", "tp", "fp", "fn")},
        "macro_f1_vs_primary": metrics["macro"]["f1"],
        "mean_item_jaccard": metrics["samples"]["mean_jaccard"],
        "exact_set_match_rate": metrics["samples"]["exact_match"],
        "avg_keywords": {"primary": metrics["samples"]["avg_gold_keywords"],
                         "cross_check": metrics["samples"]["avg_pred_keywords"]},
        "most_disagreed_keywords": disagreements[:15],
        "lowest_jaccard_items": item_jaccard[:10],
    }


def make_splits(gold):
    ids = sorted(g["id"] for g in gold)
    rng = random.Random(SPLIT_SEED)
    rng.shuffle(ids)
    n = len(ids)
    n_train = round(n * 0.70)
    n_valid = round(n * 0.15)
    splits = {
        "seed": SPLIT_SEED,
        "method": "sorted(ids) -> random.Random(13).shuffle -> 70/15/15",
        "train": sorted(ids[:n_train]),
        "valid": sorted(ids[n_train:n_train + n_valid]),
        "test": sorted(ids[n_train + n_valid:]),
    }
    write_json(SPLITS_PATH, splits)
    return splits


def label_stats(gold, splits, keywords):
    by_id = {g["id"]: g for g in gold}
    freq = {}
    for name in ("train", "valid", "test"):
        c = Counter(k for i in splits[name] for k in by_id[i]["keywords"])
        freq[name] = c
    total = Counter(k for g in gold for k in g["keywords"])
    table = [
        {"keyword": k, "total": total[k], "train": freq["train"][k], "valid": freq["valid"][k], "test": freq["test"][k]}
        for k in sorted(keywords, key=lambda k: (-total[k], keywords.index(k)))
    ]
    low = [row for row in table if row["train"] < MIN_TRAIN_EXAMPLES]
    return {
        "split_sizes": {name: len(splits[name]) for name in ("train", "valid", "test")},
        "empty_label_items": {name: sum(1 for i in splits[name] if not by_id[i]["keywords"]) for name in ("train", "valid", "test")},
        "keywords_with_zero_train_examples": [r["keyword"] for r in table if r["train"] == 0],
        f"keywords_with_fewer_than_{MIN_TRAIN_EXAMPLES}_train_examples": [
            {"keyword": r["keyword"], "train": r["train"], "valid": r["valid"], "test": r["test"]} for r in low
        ],
        "frequency": table,
    }


def main():
    keywords = load_keywords()
    gold, summary = validate_and_merge(keywords)
    print(f"[검증] 설명문 {summary['items']}개, 모두 기본 라벨 1개씩. 빈 라벨 {summary['empty_label_items']}개, "
          f"평균 {summary['avg_keywords_per_item']}개")
    print(f"  -> {GOLD_LABELS_PATH}\n  -> {GOLD_PATH}")

    agr = agreement(gold, keywords)
    agr["gold_summary"] = summary
    write_json(RESULTS_DIR / "agreement.json", agr)
    m = agr["micro_vs_primary"]
    print(f"[일치도] 겹치는 {agr['overlap_items']}개: micro P {m['precision']:.3f} R {m['recall']:.3f} F1 {m['f1']:.3f}, "
          f"Jaccard {agr['mean_item_jaccard']:.3f}, 완전 일치 {agr['exact_set_match_rate']:.3f}")
    print("  불일치 많은 키워드:", ", ".join(f"{d['keyword']}({d['disagreements']})" for d in agr["most_disagreed_keywords"][:8]))

    splits = make_splits(gold)
    stats = label_stats(gold, splits, keywords)
    stats["cross_check_ids_by_split"] = {
        name: len(set(splits[name]) & {str(r["id"]) for r in read_jsonl(CROSS_CHECK_PATH)}) for name in ("train", "valid", "test")
    }
    write_json(RESULTS_DIR / "label_stats.json", stats)
    print(f"[분할] {stats['split_sizes']} -> {SPLITS_PATH}")
    print("  학습 예시 0개:", ", ".join(stats["keywords_with_zero_train_examples"]) or "-")
    low = stats[f"keywords_with_fewer_than_{MIN_TRAIN_EXAMPLES}_train_examples"]
    print(f"  학습 예시 {MIN_TRAIN_EXAMPLES}개 미만 ({len(low)}개):",
          ", ".join(f"{r['keyword']}({r['train']})" for r in low))


if __name__ == "__main__":
    main()

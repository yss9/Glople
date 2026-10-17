"""라벨 검증·병합, 교차 라벨 일치도, train/valid/test(/test_new) 분할을 한 번에 만든다.

배치
  1차  data/descriptions.jsonl (Neoulteo DB 덤프의 TourAPI overview) + data/labels/part_*.jsonl, data/labels/cross_check.jsonl
  2차  data/db_descriptions.jsonl (로컬 DB 에서 꺼낸 TourAPI 설명문, source=TOUR_API) + data/labels_v2/part_*.jsonl,
       data/labels_v2/cross_check.jsonl. data/chunks_v2/all_new.jsonl(라벨러에게 준 설명문)이 있으면 id·본문을 대조한다.
       data/labels_v2/ 가 없으면 1차만으로 예전과 같은 결과를 만든다.
출력
  labels/gold_labels.jsonl      {"id","keywords"} 만 (설명문 없음, 커밋용). 1차 행 뒤에 2차 행을 붙인다
  labels/batches.json           배치별 출처·라벨 파일·id 목록 (gold_labels.jsonl 의 각 id 가 몇 차인지)
  data/gold.jsonl               id/title/overview/keywords/batch (학습용, gitignore)
  labels/splits.json            배치마다 seed 13, 70/15/15. 1차 train/valid/test 는 그대로 두고
                                2차의 70%·15% 를 train·valid 뒤에 붙이며 2차의 15% 는 test_new 로 따로 둔다
  results/agreement.json        1차 교차 라벨 vs 기본 라벨 일치도
  results/agreement_batch2.json 2차 교차 라벨 vs 기본 라벨 일치도
  results/label_stats.json      분할별 키워드 빈도

실행: python3 -I scripts/prepare_data.py
"""
from __future__ import annotations

import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kt_common import (  # noqa: E402
    BATCH2_MANIFEST_PATH,
    BATCHES_PATH,
    CROSS_CHECK_PATH,
    CROSS_CHECK_V2_PATH,
    DB_DESCRIPTIONS_PATH,
    DESCRIPTIONS_PATH,
    GOLD_LABELS_PATH,
    GOLD_PATH,
    RAW_LABELS_DIR,
    RAW_LABELS_V2_DIR,
    RESULTS_DIR,
    ROOT,
    SPLIT_NAMES,
    SPLIT_SEED,
    SPLITS_PATH,
    evaluate_sets,
    jaccard,
    keyword_order,
    load_batches,
    load_keywords,
    read_jsonl,
    write_json,
    write_jsonl,
)

MIN_TRAIN_EXAMPLES = 5

# 배치별 출처. labels/batches.json 에 그대로 기록된다 (경로는 keyword_tagger 기준 상대경로).
BATCH_INFO = {
    1: {
        "text_source": "data/descriptions.jsonl",
        "text_origin": "한국관광공사 TourAPI 관광정보 overview, Neoulteo 프로젝트 DB 덤프",
        "labels": "data/labels/part_*.jsonl",
        "cross_check": "data/labels/cross_check.jsonl",
        "labeler": "AI 라벨러(Claude), 키워드 라벨링 가이드",
        "agreement": "results/agreement.json",
    },
    2: {
        "text_source": "data/db_descriptions.jsonl (source=TOUR_API, 1차에 없는 id)",
        "text_origin": "로컬 DB destination_info 에 수집된 TourAPI 설명문 (scripts/export_from_db.py)",
        "collected": "2026-10-17",
        "labels": "data/labels_v2/part_*.jsonl",
        "cross_check": "data/labels_v2/cross_check.jsonl",
        "labeler": "AI 라벨러(Claude), 1차와 같은 키워드 라벨링 가이드",
        "agreement": "results/agreement_batch2.json",
    },
}


def rel(path: Path) -> str:
    """커밋되는 파일에 로컬 절대경로(사용자 이름)가 남지 않도록 keyword_tagger 기준 상대경로로 적는다."""
    return os.path.relpath(path, ROOT)


def load_primary(labels_dir, order, problems):
    """part_*.jsonl 의 기본 라벨을 파일 순서대로 읽는다. 형식·키워드 문제는 problems 에 쌓는다."""
    part_files = sorted(labels_dir.glob("part_*.jsonl"))
    if not part_files:
        raise SystemExit(f"{labels_dir} 에 part_*.jsonl 이 없습니다")
    primary = {}
    for path in part_files:
        for row in read_jsonl(path):
            rid = str(row.get("id"))
            kws = row.get("keywords")
            where = f"{labels_dir.name}/{path.name} {rid}"
            if not isinstance(kws, list):
                problems.append(f"{where}: keywords 가 배열이 아님")
                continue
            if rid in primary:
                problems.append(f"{where}: 기본 라벨 중복")
                continue
            invalid = [k for k in kws if k not in order]
            if invalid:
                problems.append(f"{where}: 목록에 없는 키워드 {invalid}")
            if len(kws) != len(set(kws)):
                problems.append(f"{where}: 같은 키워드 반복 {kws}")
            primary[rid] = kws
    return primary, part_files


def check_coverage(name, ids, primary, problems):
    missing = [i for i in ids if i not in primary]
    extra = sorted(set(primary) - set(ids))
    if missing:
        problems.append(f"[{name}] 라벨 없는 설명문 {len(missing)}개: {missing[:10]}")
    if extra:
        problems.append(f"[{name}] 설명문 없는 라벨 {len(extra)}개: {extra[:10]}")


def batch1_descriptions(problems):
    descriptions = read_jsonl(DESCRIPTIONS_PATH)
    ids = [d["id"] for d in descriptions]
    dup = [i for i, c in Counter(ids).items() if c > 1]
    if dup:
        problems.append(f"[1차] descriptions.jsonl 에 중복 id: {dup[:10]}")
    return descriptions


def batch2_descriptions(primary, batch1_ids, problems):
    """2차 설명문: db_descriptions.jsonl 의 TourAPI 행 중 2차 라벨이 있는 것 (라벨 파일 순서).

    라벨러에게 준 data/chunks_v2/all_new.jsonl 이 있으면 id 집합과 title/overview 가 DB 와 같은지 본다.
    본문이 다르면(DB 를 다시 꺼내 설명문이 바뀐 경우) 라벨과 본문이 어긋나므로 멈춘다.
    """
    if not DB_DESCRIPTIONS_PATH.exists():
        raise SystemExit(f"{DB_DESCRIPTIONS_PATH} 가 없습니다. scripts/export_from_db.py 로 먼저 꺼내세요.")
    db = {}
    for row in read_jsonl(DB_DESCRIPTIONS_PATH):
        if row.get("source") != "TOUR_API":
            continue
        rid = str(row["id"])
        if rid in db:
            problems.append(f"[2차] db_descriptions.jsonl 에 TOUR_API 중복 id: {rid}")
        db[rid] = row

    ids = list(primary)
    not_in_db = [i for i in ids if i not in db]
    if not_in_db:
        problems.append(f"[2차] db_descriptions.jsonl(TOUR_API)에 없는 라벨 id {len(not_in_db)}개: {not_in_db[:10]}")
    overlap = [i for i in ids if i in batch1_ids]
    if overlap:
        problems.append(f"[2차] 1차와 겹치는 id {len(overlap)}개: {overlap[:10]}")
    blank = [i for i in ids if i in db and not (db[i].get("overview") or "").strip()]
    if blank:
        problems.append(f"[2차] 설명문이 빈 id {len(blank)}개: {blank[:10]}")

    manifest_check = None
    if BATCH2_MANIFEST_PATH.exists():
        manifest = read_jsonl(BATCH2_MANIFEST_PATH)
        m_ids = [str(r["id"]) for r in manifest]
        check_coverage("2차 all_new.jsonl", m_ids, primary, problems)
        changed = [str(r["id"]) for r in manifest
                   if str(r["id"]) in db and (db[str(r["id"])]["title"], db[str(r["id"])]["overview"])
                   != (r["title"], r["overview"])]
        if changed:
            problems.append(f"[2차] 라벨러에게 준 본문과 DB 본문이 다른 id {len(changed)}개: {changed[:10]}")
        manifest_check = {"path": rel(BATCH2_MANIFEST_PATH), "items": len(manifest), "text_mismatch": len(changed)}

    rows = []
    for i in ids:
        if i not in db:
            continue
        d = db[i]
        rows.append({"id": i, "title": d["title"], "overview": d["overview"],
                     "content_type_id": d.get("content_type_id"), "location": d.get("location"),
                     "copyright_type": d.get("copyright_type")})
    return rows, manifest_check


def gold_summary(gold, keywords, part_files):
    sizes = Counter(len(g["keywords"]) for g in gold)
    return {
        "items": len(gold),
        "label_files": [p.name for p in part_files],
        "empty_label_items": sizes.get(0, 0),
        "keywords_per_item": {str(k): sizes[k] for k in sorted(sizes)},
        "avg_keywords_per_item": round(sum(len(g["keywords"]) for g in gold) / len(gold), 3),
        "keywords_never_used": [k for k in keywords if not any(k in g["keywords"] for g in gold)],
    }


def validate_and_merge(keywords):
    """두 배치를 검증한 뒤 gold 로 합친다. 문제가 하나라도 있으면 아무것도 쓰지 않고 멈춘다."""
    order = keyword_order(keywords)
    problems = []

    desc1 = batch1_descriptions(problems)
    primary1, parts1 = load_primary(RAW_LABELS_DIR, order, problems)
    check_coverage("1차", [d["id"] for d in desc1], primary1, problems)

    has_batch2 = RAW_LABELS_V2_DIR.exists() and any(RAW_LABELS_V2_DIR.glob("part_*.jsonl"))
    desc2, parts2, manifest_check, primary2 = [], [], None, {}
    if has_batch2:
        primary2, parts2 = load_primary(RAW_LABELS_V2_DIR, order, problems)
        desc2, manifest_check = batch2_descriptions(primary2, {d["id"] for d in desc1}, problems)
    if problems:
        raise SystemExit("라벨 검증 실패:\n  " + "\n  ".join(problems))

    def merged(batch, descriptions, primary):
        out = []
        for d in descriptions:
            row = {"id": d["id"], "title": d["title"], "overview": d["overview"],
                   "keywords": sorted(set(primary[d["id"]]), key=order.__getitem__), "batch": batch}
            if batch > 1:
                row.update({k: d.get(k) for k in ("content_type_id", "location", "copyright_type")})
            out.append(row)
        return out

    gold1 = merged(1, desc1, primary1)
    gold2 = merged(2, desc2, primary2)
    gold = gold1 + gold2
    write_jsonl(GOLD_LABELS_PATH, ({"id": g["id"], "keywords": g["keywords"]} for g in gold))
    write_jsonl(GOLD_PATH, gold)

    batches = [{"batch": 1, **BATCH_INFO[1], "label_files": [p.name for p in parts1],
                "items": len(gold1), "ids": [g["id"] for g in gold1]}]
    if gold2:
        region = Counter((g.get("location") or "").split(" ")[0] or "(없음)" for g in gold2)
        batches.append({
            "batch": 2, **BATCH_INFO[2], "label_files": [p.name for p in parts2], "items": len(gold2),
            "manifest_check": manifest_check,
            "content_type_id": dict(Counter(str(g.get("content_type_id")) for g in gold2)),
            "copyright_type": dict(Counter(str(g.get("copyright_type")) for g in gold2)),
            "region_top10": dict(region.most_common(10)),
            "ids": [g["id"] for g in gold2],
        })
    write_json(BATCHES_PATH, {
        "description": "labels/gold_labels.jsonl 의 각 id 가 몇 차 라벨 배치인지와 배치별 출처. "
                       "이 파일이 없거나 목록에 없는 id 는 1차로 본다 (kt_common.load_batches).",
        "batches": batches,
    })
    by_batch = load_batches()
    assert all(by_batch.get(g["id"]) == g["batch"] for g in gold), "batches.json 과 gold 배치 번호가 다릅니다"

    summaries = {1: gold_summary(gold1, keywords, parts1)}
    if gold2:
        summaries[2] = gold_summary(gold2, keywords, parts2)
        summaries[2]["manifest_check"] = manifest_check
    return gold, {1: primary1, 2: primary2}, summaries


def agreement(primary, cross_path, labels_glob, keywords):
    cross = read_jsonl(cross_path)
    order = keyword_order(keywords)
    for row in cross:
        invalid = [k for k in row["keywords"] if k not in order]
        if invalid:
            raise SystemExit(f"{cross_path.name} {row['id']}: 목록에 없는 키워드 {invalid}")
    ids = [str(r["id"]) for r in cross]
    dup = [i for i, c in Counter(ids).items() if c > 1]
    if dup:
        raise SystemExit(f"{cross_path} 에 중복 id: {dup[:10]}")
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
        "reference": f"primary ({labels_glob})",
        "compared": f"cross_check ({rel(cross_path)})",
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


def split_ids(ids):
    """sorted(ids) -> random.Random(13).shuffle -> 70/15/15. 배치마다 새 Random(13) 으로 따로 나눈다."""
    ids = sorted(ids)
    rng = random.Random(SPLIT_SEED)
    rng.shuffle(ids)
    n = len(ids)
    n_train = round(n * 0.70)
    n_valid = round(n * 0.15)
    return sorted(ids[:n_train]), sorted(ids[n_train:n_train + n_valid]), sorted(ids[n_train + n_valid:])


def make_splits(gold):
    train1, valid1, test1 = split_ids(g["id"] for g in gold if g["batch"] == 1)
    if SPLITS_PATH.exists():
        # 1차 분할은 예전 결과(results/roberta-base.json 등)와 비교하는 기준이라 절대 바뀌면 안 된다.
        with open(SPLITS_PATH, encoding="utf-8") as f:
            old = json.load(f)
        for name, new in (("train", train1), ("valid", valid1), ("test", test1)):
            prev = old.get(name, [])[:len(new)]
            if old.get(name) is not None and prev != new:
                raise SystemExit(f"1차 {name} 분할이 기존 {SPLITS_PATH.name} 와 다릅니다. 1차 설명문·라벨이 바뀌었는지 확인하세요.")
    batch2 = [g["id"] for g in gold if g["batch"] == 2]
    train2, valid2, test2 = split_ids(batch2) if batch2 else ([], [], [])
    splits = {
        "seed": SPLIT_SEED,
        "method": "배치마다 따로 sorted(ids) -> random.Random(13).shuffle -> 70/15/15. "
                  "train/valid 는 1차 목록(그대로) 뒤에 2차 목록을 붙이고, test 는 1차 그대로, 2차의 15% 는 test_new",
        "sizes_by_batch": {"1": {"train": len(train1), "valid": len(valid1), "test": len(test1)}},
        "train": train1 + train2,
        "valid": valid1 + valid2,
        "test": test1,
    }
    if batch2:
        splits["sizes_by_batch"]["2"] = {"train": len(train2), "valid": len(valid2), "test_new": len(test2)}
        splits["test_new"] = test2
    write_json(SPLITS_PATH, splits)
    return {name: splits[name] for name in SPLIT_NAMES if name in splits}


def label_stats(gold, splits, keywords):
    by_id = {g["id"]: g for g in gold}
    names = list(splits)
    freq = {name: Counter(k for i in splits[name] for k in by_id[i]["keywords"]) for name in names}
    total = Counter(k for g in gold for k in g["keywords"])
    batch_freq = {b: Counter(k for g in gold if g["batch"] == b for k in g["keywords"])
                  for b in sorted({g["batch"] for g in gold})}
    table = [
        {"keyword": k, "total": total[k], **{name: freq[name][k] for name in names},
         **{f"batch{b}": c[k] for b, c in batch_freq.items()}}
        for k in sorted(keywords, key=lambda k: (-total[k], keywords.index(k)))
    ]
    low = [row for row in table if row["train"] < MIN_TRAIN_EXAMPLES]
    return {
        "split_sizes": {name: len(splits[name]) for name in names},
        "split_sizes_by_batch": {
            name: dict(sorted(Counter(str(by_id[i]["batch"]) for i in splits[name]).items())) for name in names
        },
        "empty_label_items": {name: sum(1 for i in splits[name] if not by_id[i]["keywords"]) for name in names},
        "keywords_with_zero_train_examples": [r["keyword"] for r in table if r["train"] == 0],
        f"keywords_with_fewer_than_{MIN_TRAIN_EXAMPLES}_train_examples": [
            {"keyword": r["keyword"], **{name: r[name] for name in names}} for r in low
        ],
        "frequency": table,
    }


def print_agreement(name, agr):
    m = agr["micro_vs_primary"]
    print(f"[일치도 {name}] 겹치는 {agr['overlap_items']}개: micro P {m['precision']:.3f} R {m['recall']:.3f} "
          f"F1 {m['f1']:.3f}, macro F1 {agr['macro_f1_vs_primary']:.3f}, Jaccard {agr['mean_item_jaccard']:.3f}, "
          f"완전 일치 {agr['exact_set_match_rate']:.3f}")
    print("  불일치 많은 키워드:", ", ".join(f"{d['keyword']}({d['disagreements']})" for d in agr["most_disagreed_keywords"][:8]))


def main():
    keywords = load_keywords()
    gold, primary, summaries = validate_and_merge(keywords)
    for b, s in summaries.items():
        print(f"[검증 {b}차] 설명문 {s['items']}개, 모두 기본 라벨 1개씩. 빈 라벨 {s['empty_label_items']}개, "
              f"평균 {s['avg_keywords_per_item']}개")
    print(f"  -> {GOLD_LABELS_PATH} ({len(gold)}개)\n  -> {BATCHES_PATH}\n  -> {GOLD_PATH}")

    agr = agreement(primary[1], CROSS_CHECK_PATH, "data/labels/part_*.jsonl", keywords)
    agr["gold_summary"] = summaries[1]
    write_json(RESULTS_DIR / "agreement.json", agr)
    print_agreement("1차", agr)
    cross_ids = {str(r["id"]) for r in read_jsonl(CROSS_CHECK_PATH)}
    if 2 in summaries:
        agr2 = agreement(primary[2], CROSS_CHECK_V2_PATH, "data/labels_v2/part_*.jsonl", keywords)
        agr2["gold_summary"] = summaries[2]
        write_json(RESULTS_DIR / "agreement_batch2.json", agr2)
        print_agreement("2차", agr2)
        cross_ids |= {str(r["id"]) for r in read_jsonl(CROSS_CHECK_V2_PATH)}

    splits = make_splits(gold)
    stats = label_stats(gold, splits, keywords)
    stats["cross_check_ids_by_split"] = {name: len(set(ids) & cross_ids) for name, ids in splits.items()}
    write_json(RESULTS_DIR / "label_stats.json", stats)
    print(f"[분할] {stats['split_sizes']} (배치별 {stats['split_sizes_by_batch']}) -> {SPLITS_PATH}")
    print("  학습 예시 0개:", ", ".join(stats["keywords_with_zero_train_examples"]) or "-")
    low = stats[f"keywords_with_fewer_than_{MIN_TRAIN_EXAMPLES}_train_examples"]
    print(f"  학습 예시 {MIN_TRAIN_EXAMPLES}개 미만 ({len(low)}개):",
          ", ".join(f"{r['keyword']}({r['train']})" for r in low))


if __name__ == "__main__":
    main()

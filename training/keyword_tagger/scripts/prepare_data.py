"""라벨 검증·병합, 교차 라벨 일치도, train/valid/test(/test_new/test_overseas) 분할을 한 번에 만든다.

배치
  1차  data/descriptions.jsonl (Neoulteo DB 덤프의 TourAPI overview) + data/labels/part_*.jsonl, data/labels/cross_check.jsonl
  2차  data/db_descriptions.jsonl (로컬 DB 에서 꺼낸 TourAPI 설명문, source=TOUR_API) + data/labels_v2/part_*.jsonl,
       data/labels_v2/cross_check.jsonl. data/chunks_v2/all_new.jsonl(라벨러에게 준 설명문)이 있으면 id·본문을 대조한다.
       data/labels_v2/ 가 없으면 1차만으로 예전과 같은 결과를 만든다.
  3차  같은 data/db_descriptions.jsonl 의 해외 설명문(source=WIKIDATA, 한국어 위키백과 도입부, CC BY-SA 4.0)
       + data/labels_v3/part_*.jsonl, data/labels_v3/cross_check.jsonl. data/chunks_v3/all_new.jsonl 이 있으면
       id·본문(+국가·위치·유형)을 대조한다. data/labels_v3/ 가 없으면 1·2차만으로 예전과 같은 결과를 만든다.
  id 는 배치·출처를 통틀어 문자열로 유일해야 한다(국내는 숫자 contentid, 해외는 QID). 겹치면 멈춘다.
출력
  labels/gold_labels.jsonl      {"id","keywords"} 만 (설명문 없음, 커밋용). 1차 행 뒤에 2차, 3차 행을 차례로 붙인다
  labels/batches.json           배치별 출처(source)·라벨 파일·id 목록 (gold_labels.jsonl 의 각 id 가 몇 차인지)
  data/gold.jsonl               id/title/overview/keywords/batch/source (+3차는 category 등) (학습용, gitignore)
  labels/splits.json            배치마다 seed 13, 70/15/15. 1차 train/valid/test, 2차 train/valid/test_new 는 그대로 두고
                                3차의 70%·15% 를 train·valid 뒤에 붙이며 3차의 15% 는 test_overseas 로 따로 둔다
  results/agreement.json        1차 교차 라벨 vs 기본 라벨 일치도
  results/agreement_batch2.json 2차 교차 라벨 vs 기본 라벨 일치도
  results/agreement_batch3.json 3차 교차 라벨 vs 기본 라벨 일치도
  results/label_stats.json      분할별 키워드 빈도

실행: python3 -I scripts/prepare_data.py
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kt_common import (  # noqa: E402
    BATCH2_MANIFEST_PATH,
    BATCH3_MANIFEST_PATH,
    BATCHES_PATH,
    CROSS_CHECK_PATH,
    CROSS_CHECK_V2_PATH,
    CROSS_CHECK_V3_PATH,
    DB_DESCRIPTIONS_PATH,
    DESCRIPTIONS_PATH,
    GOLD_LABELS_PATH,
    GOLD_PATH,
    RAW_LABELS_DIR,
    RAW_LABELS_V2_DIR,
    RAW_LABELS_V3_DIR,
    RESULTS_DIR,
    ROOT,
    SOURCE_TOUR_API,
    SOURCE_WIKIDATA,
    SPLIT_NAMES,
    SPLIT_SEED,
    SPLITS_PATH,
    WIKI_QID_PATTERN,
    WIKI_TYPE_KEYWORDS_PATH,
    category_qids,
    evaluate_sets,
    jaccard,
    keyword_order,
    load_batches,
    load_keywords,
    load_wiki_type_table,
    read_jsonl,
    write_json,
    write_jsonl,
)

MIN_TRAIN_EXAMPLES = 5
# 출처별 id 형식. 국내 contentid 는 숫자, 해외는 위키데이터 QID 라서 문자열로도 겹칠 수 없다 (그래도 겹침은 따로 검사한다).
ID_PATTERNS = {SOURCE_TOUR_API: re.compile(r"[0-9]+"), SOURCE_WIKIDATA: WIKI_QID_PATTERN}

# 배치별 출처. labels/batches.json 에 그대로 기록된다 (경로는 keyword_tagger 기준 상대경로).
BATCH_INFO = {
    1: {
        "source": SOURCE_TOUR_API,
        "text_source": "data/descriptions.jsonl",
        "text_origin": "한국관광공사 TourAPI 관광정보 overview, Neoulteo 프로젝트 DB 덤프",
        "labels": "data/labels/part_*.jsonl",
        "cross_check": "data/labels/cross_check.jsonl",
        "labeler": "AI 라벨러(Claude), 키워드 라벨링 가이드",
        "agreement": "results/agreement.json",
    },
    2: {
        "source": SOURCE_TOUR_API,
        "text_source": "data/db_descriptions.jsonl (source=TOUR_API, 1차에 없는 id)",
        "text_origin": "로컬 DB destination_info 에 수집된 TourAPI 설명문 (scripts/export_from_db.py)",
        "collected": "2026-10-17",
        "labels": "data/labels_v2/part_*.jsonl",
        "cross_check": "data/labels_v2/cross_check.jsonl",
        "labeler": "AI 라벨러(Claude), 1차와 같은 키워드 라벨링 가이드",
        "agreement": "results/agreement_batch2.json",
    },
    3: {
        "source": SOURCE_WIKIDATA,
        "region": "해외",
        "text_source": "data/db_descriptions.jsonl (source=WIKIDATA, 1·2차에 없는 id)",
        "text_origin": "로컬 DB destination_info 에 수집된 위키데이터 해외 여행지의 한국어 위키백과 도입부 "
                       "(scripts/export_from_db.py, 2차와 같은 추출본)",
        "text_license": "CC BY-SA 4.0 (한국어 위키백과 본문), CC0 (Wikidata 항목·유형)",
        "exported": "2026-10-17",
        "labeled": "2026-10-18",
        "labels": "data/labels_v3/part_*.jsonl",
        "cross_check": "data/labels_v3/cross_check.jsonl (라벨 파일 순서로 10번째마다 1개, 80개)",
        "labeler": "AI 라벨러(Claude), 1·2차와 같은 키워드 라벨링 가이드",
        "agreement": "results/agreement_batch3.json",
    },
}
# DB 추출본에서 설명문과 함께 gold.jsonl 에 옮겨 둘 필드 (배치별)
EXTRA_FIELDS = {
    2: ("content_type_id", "location", "copyright_type"),
    3: ("country", "country_code", "location", "category", "license"),
}
# 라벨러에게 준 설명문(all_new.jsonl)과 DB 추출본을 대조할 때, 본문 외에 같아야 하는 필드 (다르면 경고만)
MANIFEST_META_FIELDS = {2: (), 3: ("country", "country_code", "location", "category")}


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


def load_db_by_source(problems):
    """data/db_descriptions.jsonl -> {source: {id: row}}. 같은 출처 안의 중복 id 는 problems 에 쌓는다."""
    if not DB_DESCRIPTIONS_PATH.exists():
        raise SystemExit(f"{DB_DESCRIPTIONS_PATH} 가 없습니다. scripts/export_from_db.py 로 먼저 꺼내세요.")
    db = {}
    for row in read_jsonl(DB_DESCRIPTIONS_PATH):
        source = row.get("source")
        rid = str(row["id"])
        rows = db.setdefault(source, {})
        if rid in rows:
            problems.append(f"db_descriptions.jsonl 에 {source} 중복 id: {rid}")
        rows[rid] = row
    return db


def db_batch_descriptions(batch, source, primary, db, earlier_ids, manifest_path, problems):
    """2차·3차 설명문: db_descriptions.jsonl 에서 source 가 같은 행 중 그 배치 라벨이 있는 것 (라벨 파일 순서).

    - id 가 그 출처의 DB 추출본에 있는지, 출처의 id 형식(국내 숫자, 해외 QID)인지, 앞 배치와 겹치지 않는지 본다.
    - 다른 출처의 DB id 와 문자열로 같은 id 가 있으면 멈춘다 (gold·분할은 id 문자열로만 찾으므로 (source, id) 로 나눠야 함).
    - 라벨러에게 준 manifest_path(all_new.jsonl)가 있으면 id 집합과 title/overview 가 DB 와 같은지 본다.
      본문이 다르면(DB 를 다시 꺼내 설명문이 바뀐 경우) 라벨과 본문이 어긋나므로 멈춘다.
    """
    tag = f"[{batch}차]"
    rows_db = db.get(source, {})
    ids = list(primary)
    not_in_db = [i for i in ids if i not in rows_db]
    if not_in_db:
        problems.append(f"{tag} db_descriptions.jsonl({source})에 없는 라벨 id {len(not_in_db)}개: {not_in_db[:10]}")
    bad_format = [i for i in ids if not ID_PATTERNS[source].fullmatch(i)]
    if bad_format:
        problems.append(f"{tag} {source} id 형식이 아닌 id {len(bad_format)}개: {bad_format[:10]}")
    overlap = [i for i in ids if i in earlier_ids]
    if overlap:
        names = "·".join(str(b) for b in range(1, batch))
        problems.append(f"{tag} {names}차와 겹치는 id {len(overlap)}개: {overlap[:10]}")
    other_ids = {i for s, rows in db.items() if s != source for i in rows}
    collide = [i for i in ids if i in other_ids]
    if collide:
        problems.append(f"{tag} 다른 출처의 DB id 와 문자열이 같은 id {len(collide)}개: {collide[:10]} "
                        "(id 를 (source, id) 로 나눠야 합니다)")
    blank = [i for i in ids if i in rows_db and not (rows_db[i].get("overview") or "").strip()]
    if blank:
        problems.append(f"{tag} 설명문이 빈 id {len(blank)}개: {blank[:10]}")

    manifest_check = None
    if manifest_path.exists():
        manifest = read_jsonl(manifest_path)
        m_ids = [str(r["id"]) for r in manifest]
        check_coverage(f"{batch}차 {manifest_path.name}", m_ids, primary, problems)
        changed = [i for i, r in zip(m_ids, manifest)
                   if i in rows_db and (rows_db[i]["title"], rows_db[i]["overview"]) != (r["title"], r["overview"])]
        if changed:
            problems.append(f"{tag} 라벨러에게 준 본문과 DB 본문이 다른 id {len(changed)}개: {changed[:10]}")
        manifest_check = {"path": rel(manifest_path), "items": len(manifest), "text_mismatch": len(changed)}
        meta_fields = MANIFEST_META_FIELDS.get(batch, ())
        if meta_fields:
            meta_changed = [i for i, r in zip(m_ids, manifest)
                            if i in rows_db and any(rows_db[i].get(f) != r.get(f) for f in meta_fields)]
            if meta_changed:
                print(f"[경고] {tag} 라벨러에게 준 {'/'.join(meta_fields)} 와 DB 값이 다른 id {len(meta_changed)}개: "
                      f"{meta_changed[:10]} (본문은 같으므로 계속합니다)")
            manifest_check["compared_fields"] = ["title", "overview", *meta_fields]
            manifest_check["meta_mismatch"] = len(meta_changed)

    id_check = {
        "id_format": ID_PATTERNS[source].pattern,
        "bad_format": len(bad_format),
        "overlap_with_earlier_batches": len(overlap),
        "same_string_as_other_source_db_ids": len(collide),
        "other_source_db_ids_checked": len(other_ids),
    }
    rows = []
    for i in ids:
        if i not in rows_db:
            continue
        d = rows_db[i]
        rows.append({"id": i, "title": d["title"], "overview": d["overview"], "source": source,
                     **{k: d.get(k) for k in EXTRA_FIELDS[batch]}})
    return rows, manifest_check, id_check


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


def has_labels(labels_dir):
    return labels_dir.exists() and any(labels_dir.glob("part_*.jsonl"))


def type_counts(gold3, keywords):
    """3차 항목의 위키데이터 유형(category QID) 빈도 상위 10개. 서버 유형 코드표가 있으면 한국어 유형명을 붙인다."""
    labels = {}
    if WIKI_TYPE_KEYWORDS_PATH.exists():
        labels = {q: t["ko_label"] or t["en_label"] for q, t in load_wiki_type_table(keywords).items()}
    counts = Counter(q for g in gold3 for q in category_qids(g.get("category")))
    return {(f"{q} ({labels[q]})" if q in labels else q): n for q, n in counts.most_common(10)}


def validate_and_merge(keywords):
    """배치(1차, 있으면 2차·3차)를 검증한 뒤 gold 로 합친다. 문제가 하나라도 있으면 아무것도 쓰지 않고 멈춘다."""
    order = keyword_order(keywords)
    problems = []

    desc1 = batch1_descriptions(problems)
    primary1, parts1 = load_primary(RAW_LABELS_DIR, order, problems)
    check_coverage("1차", [d["id"] for d in desc1], primary1, problems)

    later = {2: (RAW_LABELS_V2_DIR, SOURCE_TOUR_API, BATCH2_MANIFEST_PATH),
             3: (RAW_LABELS_V3_DIR, SOURCE_WIKIDATA, BATCH3_MANIFEST_PATH)}
    primary = {1: primary1}
    parts = {1: parts1}
    descs = {1: desc1}
    manifest_checks, id_checks = {}, {}
    earlier_ids = {d["id"] for d in desc1}
    db = None
    for batch, (labels_dir, source, manifest_path) in later.items():
        if not has_labels(labels_dir):
            continue
        if db is None:
            db = load_db_by_source(problems)
        primary[batch], parts[batch] = load_primary(labels_dir, order, problems)
        descs[batch], manifest_checks[batch], id_checks[batch] = db_batch_descriptions(
            batch, source, primary[batch], db, earlier_ids, manifest_path, problems)
        earlier_ids |= set(primary[batch])
    if problems:
        raise SystemExit("라벨 검증 실패:\n  " + "\n  ".join(problems))

    def merged(batch, descriptions, prim):
        out = []
        for d in descriptions:
            row = {"id": d["id"], "title": d["title"], "overview": d["overview"],
                   "keywords": sorted(set(prim[d["id"]]), key=order.__getitem__), "batch": batch,
                   "source": BATCH_INFO[batch]["source"]}
            if batch > 1:
                row.update({k: d.get(k) for k in EXTRA_FIELDS[batch]})
            out.append(row)
        return out

    golds = {b: merged(b, descs[b], primary[b]) for b in sorted(descs)}
    gold = [g for b in sorted(golds) for g in golds[b]]
    ids = [g["id"] for g in gold]
    assert len(ids) == len(set(ids)), "배치 사이에 겹치는 id 가 있습니다"
    write_jsonl(GOLD_LABELS_PATH, ({"id": g["id"], "keywords": g["keywords"]} for g in gold))
    write_jsonl(GOLD_PATH, gold)

    batches = [{"batch": 1, **BATCH_INFO[1], "label_files": [p.name for p in parts1],
                "items": len(golds[1]), "ids": [g["id"] for g in golds[1]]}]
    if golds.get(2):
        gold2 = golds[2]
        region = Counter((g.get("location") or "").split(" ")[0] or "(없음)" for g in gold2)
        batches.append({
            "batch": 2, **BATCH_INFO[2], "label_files": [p.name for p in parts[2]], "items": len(gold2),
            "manifest_check": manifest_checks[2],
            "id_check": id_checks[2],
            "content_type_id": dict(Counter(str(g.get("content_type_id")) for g in gold2)),
            "copyright_type": dict(Counter(str(g.get("copyright_type")) for g in gold2)),
            "region_top10": dict(region.most_common(10)),
            "ids": [g["id"] for g in gold2],
        })
    if golds.get(3):
        gold3 = golds[3]
        countries = Counter(g.get("country") or "(없음)" for g in gold3)
        batches.append({
            "batch": 3, **BATCH_INFO[3], "label_files": [p.name for p in parts[3]], "items": len(gold3),
            "manifest_check": manifest_checks[3],
            "id_check": id_checks[3],
            "license": dict(Counter(str(g.get("license")) for g in gold3)),
            "countries": len(countries),
            "country_top10": dict(countries.most_common(10)),
            "category_missing": sum(1 for g in gold3 if not category_qids(g.get("category"))),
            "category_top10": type_counts(gold3, keywords),
            "ids": [g["id"] for g in gold3],
        })
    write_json(BATCHES_PATH, {
        "description": "labels/gold_labels.jsonl 의 각 id 가 몇 차 라벨 배치인지와 배치별 출처. "
                       "이 파일이 없거나 목록에 없는 id 는 1차로 본다 (kt_common.load_batches).",
        "id_namespace": "id 는 배치·출처를 통틀어 문자열로 유일하다. source=TOUR_API 는 숫자 contentid, "
                        "source=WIKIDATA 는 위키데이터 QID('Q' + 숫자)라 서로 겹칠 수 없고, prepare_data.py 가 "
                        "배치 사이와 DB 추출본의 다른 출처 id 와 겹치지 않는지 확인한다(겹치면 멈춤). "
                        "그래서 gold·분할은 id 문자열 하나로 찾는다.",
        "batches": batches,
    })
    by_batch = load_batches()
    assert all(by_batch.get(g["id"]) == g["batch"] for g in gold), "batches.json 과 gold 배치 번호가 다릅니다"

    summaries = {}
    for b in sorted(golds):
        if not golds[b]:
            continue
        summaries[b] = gold_summary(golds[b], keywords, parts[b])
        if b in manifest_checks:
            summaries[b]["manifest_check"] = manifest_checks[b]
    return gold, primary, summaries


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


# 배치별 시험 분할 이름. 배치마다 15% 를 따로 두고 서로 섞지 않는다.
BATCH_TEST_SPLIT = {1: "test", 2: "test_new", 3: "test_overseas"}


def check_existing_splits(new_splits, batch_of):
    """기존 splits.json 의 배치별 목록이 새로 만든 목록과 같은지 본다. 다르면 멈춘다.

    1차 train/valid/test, 2차 train/valid/test_new 는 예전 결과(results/roberta-base.json, roberta-base-v2.json)와
    비교하는 기준이라 절대 바뀌면 안 된다. 분할마다 배치별로 (순서까지) 비교하고, 기존 파일에 없던 배치는 건너뛴다.
    """
    if not SPLITS_PATH.exists():
        return
    with open(SPLITS_PATH, encoding="utf-8") as f:
        old = json.load(f)
    for name, new in new_splits.items():
        prev = old.get(name)
        if prev is None:
            continue
        for b in sorted({batch_of[i] for i in prev if i in batch_of}):
            before = [i for i in prev if batch_of.get(i) == b]
            after = [i for i in new if batch_of[i] == b]
            if before != after:
                raise SystemExit(f"{b}차 {name} 분할이 기존 {SPLITS_PATH.name} 와 다릅니다. "
                                 f"{b}차 설명문·라벨이 바뀌었는지 확인하세요.")


def make_splits(gold):
    batch_of = {g["id"]: g["batch"] for g in gold}
    batches = sorted(set(batch_of.values()))
    parts = {b: split_ids(g["id"] for g in gold if g["batch"] == b) for b in batches}
    lists = {
        "train": [i for b in batches for i in parts[b][0]],
        "valid": [i for b in batches for i in parts[b][1]],
        **{BATCH_TEST_SPLIT[b]: parts[b][2] for b in batches},
    }
    check_existing_splits(lists, batch_of)
    splits = {
        "seed": SPLIT_SEED,
        "method": "배치마다 따로 sorted(ids) -> random.Random(13).shuffle -> 70/15/15. "
                  "train/valid 는 1차 목록(그대로) 뒤에 2차 목록을 붙이고, test 는 1차 그대로, 2차의 15% 는 test_new. "
                  "3차(해외)는 70%·15% 를 train·valid 의 2차 목록 뒤에 붙이고, 15% 는 test_overseas",
        "sizes_by_batch": {
            str(b): {"train": len(parts[b][0]), "valid": len(parts[b][1]), BATCH_TEST_SPLIT[b]: len(parts[b][2])}
            for b in batches
        },
        **{name: lists[name] for name in SPLIT_NAMES if name in lists},
    }
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
    for b, cross_path, labels_glob in ((2, CROSS_CHECK_V2_PATH, "data/labels_v2/part_*.jsonl"),
                                       (3, CROSS_CHECK_V3_PATH, "data/labels_v3/part_*.jsonl")):
        if b not in summaries:
            continue
        agr_b = agreement(primary[b], cross_path, labels_glob, keywords)
        agr_b["gold_summary"] = summaries[b]
        write_json(RESULTS_DIR / f"agreement_batch{b}.json", agr_b)
        print_agreement(f"{b}차", agr_b)
        cross_ids |= {str(r["id"]) for r in read_jsonl(cross_path)}

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

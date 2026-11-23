"""DB 추출본(data/db_descriptions.jsonl)의 모든 설명문에 태거 키워드를 붙여, 서버로 가져갈 파일과 요약을 만든다.

- 입력: scripts/export_from_db.py 가 만든 jsonl (id, source, title, overview, category ...). 이 스크립트는 DB에 붙지 않는다.
- 모델: outputs/<run_name>/ (학습 때 저장한 tagger_config.json 의 임계값을 그대로 씀). 기본은 CPU.
- 출력(data/ 아래라 커밋되지 않음):
  - data/predicted_keywords_<run>.jsonl: 한 줄에 {"source", "id", "keywords", "scores", "model", "info_crc32"}.
    서버 POST /api/admin/keyword-masks/import 가 이 파일을 그대로 받는다(docs/keyword-tagger-serving.md).
    scores 는 고른 키워드의 확률(소수 셋째 자리), info_crc32 는 예측에 쓴 설명문(overview = DB info)의 UTF-8 CRC32.
    서버는 이 값이 지금 설명문의 CRC32 와 다르면 그 행을 가져오지 않는다(설명문이 바뀐 뒤의 낡은 예측).
  - data/predicted_keywords_<run>.summary.json: 지금 서버 규칙과 비교한 요약(바뀌는 행 수, 키워드별 추가·제거, 예시).
    예시는 id·여행지 이름·키워드만 담고 설명문 원문은 담지 않는다.
- 설명문 원문은 어느 출력에도 쓰지 않는다.

지금 서버 규칙(비교 기준)은 DestinationInfo.computeKeywordMask 의 파이썬 포트다.
  TOUR_API: fromKoreanDescription(info) ∪ 분류 코드표(tour/category-keywords.csv)
  WIKIDATA: fromKoreanDescription(info - '문화어') ∪ 유형 코드표(wiki/type-keywords.csv) (∪ 세계유산이면 '유네스코')
세계유산 플래그는 추출본에 없어 WIKIDATA 의 '유네스코' 규칙은 재현하지 않는다(요약의 rule_note).

실행 (training/keyword_tagger 에서):
  .venv/bin/python -I scripts/predict_all.py --model-dir outputs/roberta-base-v3            # CPU, 전체
  .venv/bin/python -I scripts/predict_all.py --model-dir outputs/roberta-base-v3 --device mps
  .venv/bin/python -I scripts/predict_all.py --model-dir outputs/roberta-base-v2 --limit 40 --num-threads 2   # 시험
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import zlib
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kt_common import (  # noqa: E402
    DATA_DIR, DB_DESCRIPTIONS_PATH, ROOT, SOURCE_TOUR_API, SOURCE_WIKIDATA, domestic_rule_predict, load_keywords,
    load_tour_category_table, load_wiki_type_table, overseas_rule_predict, read_jsonl, write_json, write_jsonl,
)

SOURCES = (SOURCE_TOUR_API, SOURCE_WIKIDATA)
TOP_KEYWORDS = 15


def info_crc32(text: str) -> int:
    """java.util.zip.CRC32 에 String.getBytes(UTF_8) 를 넣은 값과 같다 (DestinationInfo.infoCrc32)."""
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


def load_rows(path: Path, source: str) -> tuple[List[dict], Counter]:
    """추출본에서 예측할 행만 고른다. 설명문이 비었거나 같은 (source, id) 가 다시 나오면 건너뛰고 사유별로 센다."""
    skipped: Counter = Counter()
    seen = set()
    rows = []
    for row in read_jsonl(path):
        src = row.get("source")
        if src not in SOURCES:
            skipped["unknown_source"] += 1
            continue
        if source != "all" and src != source:
            continue
        overview = row.get("overview")
        if overview is None or not overview.strip():
            skipped["blank_overview"] += 1
            continue
        key = (src, str(row["id"]))
        if key in seen:
            skipped["duplicate_id"] += 1
            continue
        seen.add(key)
        rows.append({**row, "id": str(row["id"]), "title": row.get("title") or ""})
    return rows, skipped


def interleave_limit(rows: Sequence[dict], limit: int) -> List[dict]:
    """출처별로 번갈아 앞에서부터 limit 개 (추출본은 출처 순으로 정렬돼 있어 앞에서 자르면 한 출처만 남는다)."""
    if limit <= 0 or limit >= len(rows):
        return list(rows)
    by_source: Dict[str, List[dict]] = {}
    for row in rows:
        by_source.setdefault(row["source"], []).append(row)
    queues = [iter(by_source[s]) for s in SOURCES if s in by_source]
    out: List[dict] = []
    while len(out) < limit and queues:
        for q in list(queues):
            item = next(q, None)
            if item is None:
                queues.remove(q)
                continue
            out.append(item)
            if len(out) == limit:
                break
    return out


def rule_keywords(row: dict, keywords: Sequence[str], tour_table, wiki_table) -> List[str]:
    if row["source"] == SOURCE_TOUR_API:
        return domestic_rule_predict(row["overview"], row.get("category"), keywords, tour_table)
    return overseas_rule_predict(row["overview"], row.get("category"), keywords, wiki_table)


def summarize(rows: Sequence[dict], predicted: Sequence[List[str]], rules: Sequence[List[str]],
              keywords: Sequence[str], examples: int) -> dict:
    """출처별·전체로 지금 규칙(rule)과 태거(tagger), 기본 추천 마스크(union = rule ∪ tagger)를 비교한다."""
    order = {k: i for i, k in enumerate(keywords)}

    def block(indices: Iterable[int]) -> dict:
        indices = list(indices)
        n = len(indices)
        added, dropped = Counter(), Counter()
        union_changed = tagger_changed = empty_tagger = empty_rule = 0
        rule_total = tagger_total = union_total = 0
        for i in indices:
            r, t = set(rules[i]), set(predicted[i])
            added.update(t - r)
            dropped.update(r - t)
            union_changed += bool(t - r)
            tagger_changed += t != r
            empty_tagger += not t
            empty_rule += not r
            rule_total += len(r)
            tagger_total += len(t)
            union_total += len(r | t)

        def top(counter: Counter) -> List[dict]:
            return [{"keyword": k, "rows": c} for k, c in sorted(counter.items(), key=lambda kv: (-kv[1], order[kv[0]]))[:TOP_KEYWORDS]]

        return {
            "rows": n,
            # union 모드(기본): 태거가 규칙에 없는 키워드를 하나 이상 더하는 행 = 추천 마스크가 바뀌는 행
            "rows_changed_union": union_changed,
            # tagger 모드: 태거 키워드 집합이 규칙과 다른 행
            "rows_changed_tagger_only": tagger_changed,
            "rows_empty_tagger": empty_tagger,
            "rows_empty_rule": empty_rule,
            "avg_keywords": {
                "rule": round(rule_total / n, 3) if n else 0.0,
                "tagger": round(tagger_total / n, 3) if n else 0.0,
                "union": round(union_total / n, 3) if n else 0.0,
            },
            "added_by_tagger": top(added),
            "rule_only_keywords": top(dropped),
        }

    by_source = {s: [i for i, row in enumerate(rows) if row["source"] == s] for s in SOURCES}
    out = {"all": block(range(len(rows)))}
    out.update({s: block(idx) for s, idx in by_source.items() if idx})

    sample = {}
    for s, idx in by_source.items():
        if not idx:
            continue
        # 태거가 규칙에 없는 키워드를 가장 많이 더한 행, 규칙에만 있는 키워드가 가장 많은 행 (같으면 추출본 순서)
        def entry(i: int) -> dict:
            r, t = set(rules[i]), set(predicted[i])
            return {"id": rows[i]["id"], "title": rows[i]["title"],
                    "rule": rules[i], "tagger": predicted[i],
                    "added_by_tagger": [k for k in keywords if k in t - r],
                    "rule_only": [k for k in keywords if k in r - t]}

        most_added = sorted((i for i in idx if set(predicted[i]) - set(rules[i])),
                            key=lambda i: -len(set(predicted[i]) - set(rules[i])))[:examples]
        most_dropped = sorted((i for i in idx if set(rules[i]) - set(predicted[i])),
                              key=lambda i: -len(set(rules[i]) - set(predicted[i])))[:examples]
        sample[s] = {"most_added": [entry(i) for i in most_added], "most_rule_only": [entry(i) for i in most_dropped]}
    out["examples"] = sample
    return out


def main():
    p = argparse.ArgumentParser(description="DB 추출본 전체에 키워드 태거 예측 붙이기 (서버 가져오기용)")
    p.add_argument("--model-dir", required=True, help="예: outputs/roberta-base-v3 (training/keyword_tagger 기준 상대 경로도 됨)")
    p.add_argument("--input", default=str(DB_DESCRIPTIONS_PATH))
    p.add_argument("--output", help="기본: data/predicted_keywords_<run>.jsonl")
    p.add_argument("--summary", help="기본: 출력 파일 이름의 .jsonl 을 .summary.json 으로 바꾼 것")
    p.add_argument("--model-name", help="서버 tagger_model 에 남길 이름 (기본: 모델 폴더 이름)")
    p.add_argument("--source", choices=["all", *SOURCES], default="all")
    p.add_argument("--limit", type=int, default=0, help="시험용: 출처별로 번갈아 앞에서부터 N개만 (0=전체)")
    p.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--num-threads", type=int, default=4, help="CPU 스레드 수 (0이면 torch 기본값)")
    p.add_argument("--no-scores", action="store_true", help="출력에 scores 를 넣지 않음")
    p.add_argument("--examples", type=int, default=8, help="요약에 남길 출처별 예시 수")
    p.add_argument("--log-every", type=int, default=50, help="진행 줄 간격 (배치 수)")
    args = p.parse_args()
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    model_dir = Path(args.model_dir)
    if not model_dir.is_absolute() and not model_dir.exists():
        model_dir = ROOT / model_dir
    model_dir = model_dir.resolve()
    if not (model_dir / "tagger_config.json").exists():
        sys.exit(f"{model_dir}/tagger_config.json 이 없습니다. 학습이 끝난 모델 폴더(outputs/<run_name>)를 주세요.")
    run = args.model_name or model_dir.name
    output = Path(args.output) if args.output else DATA_DIR / f"predicted_keywords_{run}.jsonl"
    summary_path = Path(args.summary) if args.summary else output.with_name(output.name.removesuffix(".jsonl") + ".summary.json")

    keywords = load_keywords()
    tour_table = load_tour_category_table(keywords)
    wiki_table = load_wiki_type_table(keywords)
    rows, skipped = load_rows(Path(args.input), args.source)
    rows = interleave_limit(rows, args.limit)
    if not rows:
        sys.exit("예측할 행이 없습니다.")

    # torch 는 여기서 불러온다 (--help 와 입력 검사는 torch 없이 된다)
    import torch
    from predict import load_tagger, predict_probs, selected_keywords

    if args.num_threads > 0:
        torch.set_num_threads(args.num_threads)
    tokenizer, model, cfg, labels, thresholds = load_tagger(model_dir, args.device)
    unknown = [k for k in labels if k not in set(keywords)]
    if unknown:
        sys.exit(f"모델 라벨 공간에 keywords.json(서버 KeywordMask) 에 없는 키워드가 있습니다: {unknown}")

    # 길이가 비슷한 행끼리 배치를 묶어 패딩을 줄인다(CPU 시간 절약). 결과는 원래 순서로 되돌린다.
    order = sorted(range(len(rows)), key=lambda i: len(rows[i]["title"]) + len(rows[i]["overview"]))
    started = time.time()
    every = max(1, args.log_every) * args.batch_size

    def progress(done: int, total: int) -> None:
        if done % every == 0 or done == total:
            elapsed = time.time() - started
            eta = elapsed / done * (total - done)
            print(f"  {done}/{total} ({done * 100 // total}%) 경과 {elapsed / 60:.1f}분, 남은 예상 {eta / 60:.1f}분", flush=True)

    print(f"[설정] model={model_dir.name} run={run} device={args.device} threads={torch.get_num_threads()} "
          f"rows={len(rows)} threshold_mode={cfg.get('threshold_mode')} global_threshold={cfg.get('global_threshold')}", flush=True)
    sorted_probs = predict_probs(tokenizer, model, cfg, [rows[i] for i in order], args.device, args.batch_size, progress)
    probs = [None] * len(rows)
    for pos, i in enumerate(order):
        probs[i] = sorted_probs[pos]
    seconds = round(time.time() - started, 1)

    predicted = [selected_keywords(pr, labels, thresholds) for pr in probs]
    label_index = {k: j for j, k in enumerate(labels)}
    out = []
    for row, pr, kws in zip(rows, probs, predicted):
        item = {"source": row["source"], "id": row["id"], "keywords": kws}
        if not args.no_scores:
            item["scores"] = {k: round(float(pr[label_index[k]]), 3) for k in kws}
        item["model"] = run
        item["info_crc32"] = info_crc32(row["overview"])
        out.append(item)
    write_jsonl(output, out)

    rules = [rule_keywords(row, keywords, tour_table, wiki_table) for row in rows]
    summary = {
        "model": run,
        "model_dir": str(model_dir.relative_to(ROOT)) if model_dir.is_relative_to(ROOT) else model_dir.name,
        "threshold_mode": cfg.get("threshold_mode"),
        "global_threshold": cfg.get("global_threshold"),
        "excluded_keywords": cfg.get("excluded_keywords", []),
        "input": Path(args.input).name,
        "source_filter": args.source,
        "limit": args.limit,
        "device": args.device,
        "seconds": seconds,
        "skipped": dict(skipped),
        "rule": "TOUR_API: fromKoreanDescription(info) ∪ tour/category-keywords.csv, "
                "WIKIDATA: fromKoreanDescription(info - '문화어') ∪ wiki/type-keywords.csv",
        "rule_note": "세계유산 플래그(world_heritage)가 추출본에 없어 WIKIDATA 의 '유네스코' 규칙은 빼고 비교함",
        "compare": summarize(rows, predicted, rules, keywords, args.examples),
    }
    write_json(summary_path, summary)

    comp = summary["compare"]
    print(f"{len(out)}개 -> {output} ({seconds}초)")
    print(f"요약 -> {summary_path}")
    for key in ("all", *SOURCES):
        if key in comp:
            c = comp[key]
            print(f"  {key:9} 행 {c['rows']}: union 으로 바뀌는 행 {c['rows_changed_union']}, "
                  f"tagger 만 쓰면 다른 행 {c['rows_changed_tagger_only']}, 평균 키워드 규칙 {c['avg_keywords']['rule']} / "
                  f"태거 {c['avg_keywords']['tagger']} / union {c['avg_keywords']['union']}")
    if skipped:
        print(f"  건너뜀: {dict(skipped)}")


if __name__ == "__main__":
    main()

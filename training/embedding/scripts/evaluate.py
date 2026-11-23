"""학습한 임베딩 모델을 기준선과 같은 질의·정답·지표로 평가하고 비교표를 씁니다.

- 모델 폴더(outputs/<run>/best)에 남긴 설정(접두어, 최대 길이, 설명문 정책)을 그대로 씁니다.
- results/baseline.json 의 기준선 숫자와 한 표로 묶어 results/<run>.compare.md 를 씁니다.
- --compare-base(기본 켬)면 학습 전 모델로도 같은 test 질의를 다시 돌려, 질의별로 가장 좋아진·나빠진 예를 붙입니다
  (질의는 합성 문장이고 결과에는 여행지 이름만 넣습니다. 설명문은 넣지 않음).

결과: results/<run>.json, results/<run>.compare.md
사용: .venv/bin/python -I scripts/evaluate.py --model-dir outputs/e5-base-ft/best [--device mps]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import emb_common as ec  # noqa: E402
import report  # noqa: E402
import retrieval  # noqa: E402

SETS = ("test", "test_iid", "valid")
BASELINE_ROWS = ("keyword_chips", "keyword_literal", "bm25_all", "embedding_base_all", "embedding_base_none",
                 "keyword_chips_gold_mask")


def main() -> None:
    ec.setup_line_buffering()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-dir", required=True, help="학습한 모델 폴더 (또는 Hugging Face 모델 이름)")
    parser.add_argument("--run-name", default=None, help="결과 파일 이름 (기본: 모델 폴더의 상위 폴더 이름)")
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"])
    parser.add_argument("--num-threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--description-policy", choices=ec.DESCRIPTION_POLICIES, default=None,
                        help="기본: 학습 때 정책 (모델 폴더 메타), 없으면 all")
    parser.add_argument("--max-query-len", type=int, default=None)
    parser.add_argument("--max-passage-len", type=int, default=None)
    parser.add_argument("--no-compare-base", action="store_true", help="학습 전 모델과 질의별 비교를 생략")
    parser.add_argument("--examples", type=int, default=6, help="좋아진·나빠진 예시 개수")
    args = parser.parse_args()

    import torch
    torch.set_num_threads(args.num_threads)
    device = ec.resolve_device(args.device)
    model_dir = Path(args.model_dir)
    settings = ec.model_settings(args.model_dir)
    meta = settings["meta"] or {}
    run_name = args.run_name or (model_dir.parent.name if model_dir.is_dir() and model_dir.name == "best"
                                 else model_dir.name.replace("/", "_"))
    policy = args.description_policy or meta.get("description_policy", "all")
    q_len = args.max_query_len or meta.get("max_query_len", 64)
    p_len = args.max_passage_len or meta.get("max_passage_len", 256)

    dests = ec.load_destinations()
    sets = {s: ec.load_query_set(s) for s in SETS}
    corpora = {s: ec.corpus_ids(dests, s) for s in SETS}

    def run_model(m, prefixes):
        out, per_query, rankings_test = {}, None, None
        for s in SETS:
            rankings = retrieval.embedding_rankings(
                m, sets[s], corpora[s], dests, policy=policy, query_prefix=prefixes[0], passage_prefix=prefixes[1],
                max_query_len=q_len, max_passage_len=p_len, batch_size=args.batch_size)
            res = ec.evaluate_rankings(sets[s], rankings, dests, corpora[s], return_per_query=True)
            if s == "test":
                per_query, rankings_test = res["per_query"], rankings
            res.pop("per_query")
            out[s] = res
        return out, per_query, rankings_test

    t0 = time.time()
    model = ec.load_model(settings, device)
    metrics, per_query, rankings = run_model(model, (settings["query_prefix"], settings["passage_prefix"]))
    o = metrics["test"]["overall"]
    print(f"{run_name}: test nDCG@10 {o['ndcg@10']:.3f}  R@20 {o['recall@20']:.3f}  MRR {o['mrr']:.3f} "
          f"({device}, {time.time() - t0:.1f}s)", flush=True)
    del model
    ec.empty_device_cache(device)

    # 학습 전 모델과 질의별 비교 -------------------------------------------------------
    examples = {}
    if not args.no_compare_base and meta.get("base_model"):
        base_settings = ec.model_settings(meta["base_model"], meta.get("base_revision"))
        base = ec.load_model(base_settings, device)
        _, base_pq, base_rankings = run_model(base, (base_settings["query_prefix"], base_settings["passage_prefix"]))
        del base
        ec.empty_device_cache(device)
        queries = sets["test"]
        diffs = sorted(range(len(queries)), key=lambda i: per_query[i]["ndcg@10"] - base_pq[i]["ndcg@10"])

        def example(i):
            q = queries[i]
            return {
                "query": q["text"], "keywords": q["keywords"], "type": q["type"],
                "region": q["region"]["surface"] if q["region"] else None,
                "ndcg@10_base": round(base_pq[i]["ndcg@10"], 3), "ndcg@10_finetuned": round(per_query[i]["ndcg@10"], 3),
                "top3_base": [dests[d]["title"] for d in base_rankings[i][:3]],
                "top3_finetuned": [dests[d]["title"] for d in rankings[i][:3]],
            }
        examples = {"improved": [example(i) for i in reversed(diffs[-args.examples:])],
                    "worsened": [example(i) for i in diffs[:args.examples]],
                    "counts": {"better": sum(per_query[i]["ndcg@10"] > base_pq[i]["ndcg@10"] + 1e-9 for i in diffs),
                               "worse": sum(per_query[i]["ndcg@10"] < base_pq[i]["ndcg@10"] - 1e-9 for i in diffs),
                               "same": sum(abs(per_query[i]["ndcg@10"] - base_pq[i]["ndcg@10"]) <= 1e-9 for i in diffs)}}

    out = {
        "note": "합성 질의 + AI silver 라벨 기준. 설명문 원문 없음.",
        "run_name": run_name, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "device": device,
        "model_dir": str(model_dir), "train_meta": meta, "description_policy": policy,
        "max_query_len": q_len, "max_passage_len": p_len, "versions": ec.package_versions(),
        "data_sha256": {p.name: ec.sha256_file(p) for p in [ec.DESTINATIONS_PATH, *ec.QUERY_SET_PATHS.values()]},
        "sets": metrics, "examples": examples,
    }
    ec.write_json(ec.RESULTS_DIR / f"{run_name}.json", out)

    # 비교표 ------------------------------------------------------------------
    rows = []
    baseline_path = ec.RESULTS_DIR / "baseline.json"
    if baseline_path.exists():
        base = ec.read_json(baseline_path)
        if base.get("data_sha256") != out["data_sha256"]:
            print("[경고] baseline.json 을 만든 데이터와 지금 데이터가 다릅니다. baseline.py 를 다시 돌리세요.", flush=True)
        for key in BASELINE_ROWS:
            if key in base["methods"]:
                rows.append((base["methods"][key]["label"], base["methods"][key]["sets"]))
    rows.insert(len(rows) - 1 if rows else 0, (f"**{run_name} (파인튜닝, 설명문 {policy})**", metrics))

    md = [f"# {run_name} 평가 (합성 질의, AI silver 라벨 기준)", "",
          f"- 모델: `{model_dir}` (기본 모델 {meta.get('base_model', '-')}, best 에폭 {meta.get('epoch', '-')}, "
          f"valid nDCG@10 {meta.get('valid', {}).get('ndcg@10', '-')})",
          f"- 질의 최대 {q_len} 토큰, 여행지 글 최대 {p_len} 토큰, 설명문 정책 `{policy}`, 평가 장치 {device}",
          "- 맨 아래 '(참고)' 줄은 정답 라벨을 마스크로 쓴 키워드 검색이라 관련도 정의와 거의 같습니다. 천장으로만 보세요.", "",
          report.full_report(rows, SETS)]
    if examples:
        c = examples["counts"]
        md.append(f"### 학습 전 모델과 질의별 비교 (test {sum(c.values())}개: 좋아짐 {c['better']}, "
                  f"나빠짐 {c['worse']}, 같음 {c['same']})\n")
        for title, key in (("가장 좋아진 질의", "improved"), ("가장 나빠진 질의", "worsened")):
            md.append(f"{title}\n\n| 질의 (합성) | 정답 키워드 | nDCG@10 전 → 후 | 학습 전 상위 3 | 학습 후 상위 3 |\n"
                      "| :-- | :-- | --: | :-- | :-- |")
            for e in examples[key]:
                md.append(f"| {e['query']} | {', '.join(e['keywords'])} | {e['ndcg@10_base']:.2f} → "
                          f"{e['ndcg@10_finetuned']:.2f} | {' / '.join(e['top3_base'])} | {' / '.join(e['top3_finetuned'])} |")
            md.append("")
    (ec.RESULTS_DIR / f"{run_name}.compare.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"저장: results/{run_name}.json, results/{run_name}.compare.md", flush=True)


if __name__ == "__main__":
    main()

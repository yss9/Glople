"""학습 전 기준선: 같은 질의·같은 정답으로 지금 서버의 키워드 검색, BM25, 학습 전 임베딩 모델을 잽니다.

검색기
- (a) 키워드 마스크 검색 = 지금 서버 /api/search. 여행지 마스크는 서버가 저장하는 값(국내 규칙(설명문) ∪ 분류 코드표 키워드),
      겹치는 키워드 수 내림차순, 같으면 DB id 순, 최대 21개. 질의 쪽 키워드는 두 가지로 넣어 봅니다.
      - 칩: 사용자가 94개 키워드 화면에서 질의 키워드를 정확히 고른 경우 (지금 화면과 같은 사용법, 키워드 쪽에 유리)
      - 글자 추출: 문장에 키워드 글자가 그대로 있을 때만 (자연어를 그대로 넣었을 때)
      - (참고) 칩 + 정답 라벨 마스크: 태거가 완벽하다고 칠 때. 관련도 정의와 거의 같아서 '키워드 방식의 천장'으로만 봅니다.
- (b) BM25 글자 2-gram: 형태소 분석 없이 여행지 글(이름 | 지역 | 분류 | 설명문)에서 찾기
- (c) 학습 전 임베딩 (기본 multilingual-e5-base): 설명문을 넣은 것과 이름·지역·분류만 넣은 것

결과: results/baseline.json, results/baseline.md (설명문 원문 없음)
사용: nice -n 10 .venv/bin/python -I scripts/baseline.py --device cpu [--skip-embedding]
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


def main() -> None:
    ec.setup_line_buffering()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=ec.DEFAULT_MODEL)
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"])
    parser.add_argument("--num-threads", type=int, default=4, help="CPU 스레드 수")
    parser.add_argument("--max-query-len", type=int, default=64)
    parser.add_argument("--max-passage-len", type=int, default=256)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--skip-embedding", action="store_true", help="모델 없이 키워드·BM25 만")
    parser.add_argument("--render-only", action="store_true", help="다시 재지 않고 results/baseline.json 으로 표만 다시 씀")
    args = parser.parse_args()
    if args.render_only:
        write_markdown(ec.read_json(ec.RESULTS_DIR / "baseline.json")["methods"])
        print("저장: results/baseline.md", flush=True)
        return

    dests = ec.load_destinations()
    keywords = ec.load_keywords()
    sets = {s: ec.load_query_set(s) for s in SETS}
    corpora = {s: ec.corpus_ids(dests, s) for s in SETS}

    def run(name, label, rank_fn):
        t0 = time.time()
        res = {}
        for s in SETS:
            rankings = rank_fn(sets[s], corpora[s])
            res[s] = ec.evaluate_rankings(sets[s], rankings, dests, corpora[s])
        o = res["test"]["overall"]
        print(f"{label:52} test nDCG@10 {o['ndcg@10']:.3f}  R@20 {o['recall@20']:.3f}  MRR {o['mrr']:.3f}  "
              f"({time.time() - t0:.1f}s)", flush=True)
        methods[name] = {"label": label, "sets": res}

    methods = {}
    run("keyword_chips", "키워드 검색: 질의 키워드를 칩으로 고름 (서버 마스크)",
        lambda qs, corpus: [retrieval.keyword_mask_rank(q["keywords"], corpus, dests) for q in qs])
    run("keyword_literal", "키워드 검색: 문장에서 키워드 글자 추출 (서버 마스크)",
        lambda qs, corpus: [retrieval.keyword_mask_rank(retrieval.literal_query_keywords(q["text"], keywords),
                                                        corpus, dests) for q in qs])
    run("keyword_chips_gold_mask", "(참고) 키워드 칩 + 정답 라벨 마스크",
        lambda qs, corpus: [retrieval.keyword_mask_rank(q["keywords"], corpus, dests, "keywords") for q in qs])
    run("bm25_all", "BM25 글자 2-gram (설명문 포함)",
        lambda qs, corpus: retrieval.bm25_rankings(qs, corpus, dests, "all"))
    run("bm25_none", "BM25 글자 2-gram (이름·지역·분류만)",
        lambda qs, corpus: retrieval.bm25_rankings(qs, corpus, dests, "none"))

    extra = {}
    if not args.skip_embedding:
        import torch
        torch.set_num_threads(args.num_threads)
        device = ec.resolve_device(args.device)
        settings = ec.model_settings(args.model)
        t0 = time.time()
        model = ec.load_model(settings, device)
        print(f"모델 불러옴: {args.model}@{(settings['revision'] or '')[:12]} ({device}, {time.time() - t0:.1f}s)", flush=True)
        short = args.model.split("/")[-1]
        for policy, desc in (("all", "설명문 포함"), ("none", "이름·지역·분류만")):
            run(f"embedding_base_{policy}", f"{short} 학습 전 ({desc})",
                lambda qs, corpus, p=policy: retrieval.embedding_rankings(
                    model, qs, corpus, dests, policy=p, query_prefix=settings["query_prefix"],
                    passage_prefix=settings["passage_prefix"], max_query_len=args.max_query_len,
                    max_passage_len=args.max_passage_len, batch_size=args.batch_size))
        all_ids = [d for d in dests]
        extra["passage_tokens_all_policy"] = retrieval.passage_token_stats(
            model, [ec.passage_text(dests[i], "all") for i in all_ids], settings["passage_prefix"], args.max_passage_len)
        extra["query_tokens_train"] = retrieval.passage_token_stats(
            model, [p["text"] for p in ec.read_jsonl(ec.TRAIN_PAIRS_PATH)], settings["query_prefix"], args.max_query_len)
        extra["model"] = {"name": args.model, "revision": settings["revision"], "device": device,
                          "max_query_len": args.max_query_len, "max_passage_len": args.max_passage_len}
        print(f"여행지 글 토큰 수: {extra['passage_tokens_all_policy']}", flush=True)

    out = {
        "note": "정답은 합성 질의의 키워드와 keyword_tagger AI silver 라벨로 계산. 설명문 원문 없음.",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "versions": ec.package_versions(),
        "data_sha256": {p.name: ec.sha256_file(p) for p in [ec.DESTINATIONS_PATH, *ec.QUERY_SET_PATHS.values()]},
        **extra,
        "methods": methods,
    }
    ec.write_json(ec.RESULTS_DIR / "baseline.json", out)

    write_markdown(methods)
    print("저장: results/baseline.json, results/baseline.md", flush=True)


def write_markdown(methods: dict) -> None:
    # '(참고)' 줄은 맨 아래로
    ordered = sorted(methods.values(), key=lambda m: report.is_reference(m["label"]))
    table_methods = [(m["label"], m["sets"]) for m in ordered]
    md = ["# 임베딩 검색 기준선 (학습 전)", "",
          "합성 질의 기준입니다. 정답(관련도)은 질의 키워드와 keyword_tagger의 AI silver 라벨로 계산했습니다. "
          "검색 대상은 질의 세트마다 test+test_new 여행지 290개, valid 여행지 290개입니다. 지표 정의는 README를 보세요. "
          "'(참고)' 줄은 정답 라벨을 마스크로 쓴 키워드 검색이라 관련도 정의와 거의 같습니다. 천장으로만 보고, 굵은 글씨(최고값) 계산에서도 뺐습니다.", "",
          report.full_report(table_methods, SETS)]
    (ec.RESULTS_DIR / "baseline.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()

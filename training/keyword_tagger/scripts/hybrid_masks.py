"""키워드마다 규칙·태거·합집합 중 더 나은 쪽을 골라 서버에 가져갈 예측 파일을 만든다.

태거만 쓰면(서버 mask-mode=tagger) 정밀도는 가장 높지만, 태거가 거의 예측하지 않는 드문 키워드(예: 모스크)는
결과가 0개가 된다(2026-10-18 실제 서버에서 확인). 그래서 키워드별로 출처를 고른다.
- 고르는 기준은 valid 분할의 키워드별 F1이다(test·test_new·test_overseas는 고를 때 보지 않고 평가에만 쓴다).
- valid 정답이 MIN_SUPPORT개 미만이거나 태거가 전체 추출본에서 그 키워드를 한 번도 예측하지 않으면 규칙을 쓴다
  (빈 결과를 막는 쪽을 택함).
- 결과 파일은 predict_all.py와 같은 모양이라 같은 가져오기 API(POST /api/admin/keyword-masks/import)에 넣으면 된다.
  서버는 mask-mode=tagger(기본)로 이 '태거 마스크'를 쓴다.
사용: .venv/bin/python -I scripts/hybrid_masks.py --run roberta-base-v3
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import predict_all as pa
from kt_common import load_tour_category_table, load_wiki_type_table

ROOT = Path(__file__).resolve().parent.parent
MIN_SUPPORT = 3
TEST_SPLITS = ("test", "test_new", "test_overseas")


def f1(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return (2 * p * r / (p + r) if p + r else 0.0), p, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="roberta-base-v3")
    args = ap.parse_args()
    keywords = json.loads((ROOT / "keywords.json").read_text(encoding="utf-8"))
    tour, wiki = load_tour_category_table(keywords), load_wiki_type_table(keywords)
    rows = {(r["source"], r["id"]): r for r in map(json.loads, (ROOT / "data" / "db_descriptions.jsonl").open(encoding="utf-8"))}
    pred_path = ROOT / "data" / f"predicted_keywords_{args.run}.jsonl"
    preds = [json.loads(line) for line in pred_path.open(encoding="utf-8")]
    tagger = {(p["source"], p["id"]): set(p["keywords"]) for p in preds}
    rule = {key: set(pa.rule_keywords(row, keywords, tour, wiki)) for key, row in rows.items()}
    gold = {r["id"]: set(r["keywords"]) for r in map(json.loads, (ROOT / "labels" / "gold_labels.jsonl").open(encoding="utf-8"))}
    splits = json.loads((ROOT / "labels" / "splits.json").read_text(encoding="utf-8"))
    key_of = {i: k for k in rows for i in [k[1]]}

    ever_tagged = Counter(k for kws in tagger.values() for k in kws)
    # valid 분할에서 키워드별 (규칙, 태거, 합집합)의 tp·fp·fn
    stats = defaultdict(lambda: {m: [0, 0, 0] for m in ("rule", "tagger", "union")})
    support = Counter()
    for i in splits["valid"]:
        key = key_of.get(i)
        if key is None:
            continue
        g, t, r = gold[i], tagger.get(key, set()), rule[key]
        for k in g:
            support[k] += 1
        for k in g | t | r:
            for mode, p in (("rule", r), ("tagger", t), ("union", r | t)):
                s = stats[k][mode]
                s[0] += k in p and k in g
                s[1] += k in p and k not in g
                s[2] += k not in p and k in g
    choice = {}
    for k in keywords:
        if not ever_tagged[k] or support[k] < MIN_SUPPORT:
            choice[k] = "rule"
            continue
        scores = {m: f1(*stats[k][m])[0] for m in ("tagger", "rule", "union")}
        choice[k] = max(scores, key=lambda m: (scores[m], m == "tagger"))   # 같으면 태거

    def hybrid(key):
        t, r = tagger.get(key, set()), rule[key]
        return sorted(k for k in t | r if (choice[k] == "tagger" and k in t) or (choice[k] == "rule" and k in r)
                      or choice[k] == "union")

    print("키워드별 출처:", dict(Counter(choice.values())))
    print("  규칙을 쓰는 키워드:", [k for k in keywords if choice[k] == "rule"])
    print("  합집합을 쓰는 키워드:", [k for k in keywords if choice[k] == "union"])
    for split in TEST_SPLITS:
        totals = {m: [0, 0, 0] for m in ("rule", "tagger", "union", "hybrid")}
        n = 0
        for i in splits[split]:
            key = key_of.get(i)
            if key is None:
                continue
            n += 1
            g = gold[i]
            t, r = tagger.get(key, set()), rule[key]
            for mode, p in (("rule", r), ("tagger", t), ("union", r | t), ("hybrid", set(hybrid(key)))):
                totals[mode][0] += len(p & g)
                totals[mode][1] += len(p - g)
                totals[mode][2] += len(g - p)
        cells = []
        for mode, (tp, fp, fn) in totals.items():
            f, p, r = f1(tp, fp, fn)
            cells.append(f"{mode} F1 {f:.3f} (P {p:.3f} R {r:.3f})")
        print(f"{split} {n}개: " + " | ".join(cells))

    out = ROOT / "data" / f"predicted_keywords_{args.run}-hybrid.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for p in preds:
            key = (p["source"], p["id"])
            item = dict(p, keywords=hybrid(key), model=f"{args.run}-rule-hybrid")
            item.pop("scores", None)
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    (ROOT / "results" / f"{args.run}.hybrid_choice.json").write_text(
        json.dumps({"min_support": MIN_SUPPORT, "chosen_on": "valid", "choice": choice}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print("->", out.relative_to(ROOT), "/ results/" + f"{args.run}.hybrid_choice.json")


if __name__ == "__main__":
    main()

"""학습 전 기준선: KeywordMask 의 두 규칙을 그대로 평가한다.

규칙 (src/main/java/proj/travien/service/KeywordMask.java)
  substring    fromDescription: description 이 비어 있으면 빈 마스크, 아니면 KEYWORDS 중 description.contains(keyword) 인 것.
  korean_rule  fromKoreanDescription (DestinationInfo.source 가 TOUR_API 일 때 서버가 쓰는 규칙):
               두 글자 이상 키워드는 substring 과 같고, 한 글자 키워드(산, 섬, 성, 탑, 강)는 앞이 한글 음절이 아니고
               뒤가 비었거나 정해진 조사(은, 이, 에서, 으로 …)일 때만 인정.
  여기서는 description 자리에 TourAPI overview 를 넣는다.

평가 집합
  test      1차 배치의 test 분할 163개 (2차 배치를 넣기 전과 같은 목록이라 숫자도 그대로)
  test_new  2차 배치의 test 분할 (splits.json 에 있을 때만)
  all       전체 gold (1차 + 2차). all_by_batch 에 배치별 전체 항목 지표도 남긴다 (all_by_batch["1"] 은 예전 all 과 같음)

출력
  results/baseline.json          두 규칙의 test·test_new·전체 항목 지표, 한 글자 키워드별 전후 비교,
                                 substring 규칙의 오탐/누락 예시 10개 (1차 배치에서 고름, 설명문 발췌 없음, 커밋용)
  data/baseline_examples.jsonl   같은 예시 10개 + 설명문 앞뒤 발췌 (gitignore)
실행: python3 -I scripts/baseline.py
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kt_common import (  # noqa: E402
    DATA_DIR,
    RESULTS_DIR,
    EVAL_SPLITS,
    contains_standalone_word,
    evaluate_sets,
    korean_rule_predict,
    load_gold,
    load_keywords,
    load_splits,
    report_keywords,
    row_batch,
    substring_predict,
    top_frequent_keywords,
    write_json,
    write_jsonl,
)

SNIPPET_RADIUS = 14
N_FP_EXAMPLES = 5
N_FN_EXAMPLES = 5
# 설명문 원문 발췌는 커밋하지 않는 data/ 쪽에만 남긴다
TEXT_FIELDS = ("context", "overview_head")


def snippet(text: str, start: int, length: int) -> str:
    lo = max(0, start - SNIPPET_RADIUS)
    hi = min(len(text), start + length + SNIPPET_RADIUS)
    s = text[lo:start] + "[" + text[start:start + length] + "]" + text[start + length:hi]
    return ("…" if lo > 0 else "") + s.replace("\n", " ") + ("…" if hi < len(text) else "")


def containing_word(text: str, start: int, length: int) -> str:
    """매칭 위치를 감싼 어절(공백 기준)."""
    lo = start
    while lo > 0 and not text[lo - 1].isspace():
        lo -= 1
    hi = start + length
    while hi < len(text) and not text[hi].isspace():
        hi += 1
    return text[lo:hi].strip(".,·'\"()[]<>“”‘’")


def pick_examples(rows, preds, keywords):
    """오탐(FP) 5개, 누락(FN) 5개를 키워드가 겹치지 않게 결정적으로 고른다.

    오탐은 FP 가 많은 키워드 순으로, 그 키워드가 가장 자주 걸린 어절(예: '산' -> '산책로')을 포함한 항목을 고른다.
    누락은 FN 이 많은 키워드 순으로, 설명문에 그 글자가 없는 항목을 고른다. 한 항목은 한 번만 쓴다.
    """
    fp_docs = defaultdict(list)
    fn_docs = defaultdict(list)
    for row, pred in zip(rows, preds):
        gold = set(row["keywords"])
        for k in pred:
            if k not in gold:
                fp_docs[k].append(row)
        for k in gold - set(pred):
            fn_docs[k].append(row)

    def matched_words(kw):
        words = Counter()
        for row in fp_docs[kw]:
            pos = row["overview"].find(kw)
            words[containing_word(row["overview"], pos, len(kw))] += 1
        return words

    examples = []
    used = set()
    used_ids = set()
    # '성' 이 '성당' 에 걸리는 경우는 README 에 적힌 대표 사례라서 있으면 먼저 넣는다.
    preferred = [("성", "성당")]
    for kw, confuser in preferred:
        for row in fp_docs.get(kw, []):
            pos = row["overview"].find(confuser)
            if pos >= 0:
                examples.append(fp_example(row, kw, pos, fp_count=len(fp_docs[kw]),
                                           common_words=matched_words(kw).most_common(5)))
                used.add(kw)
                used_ids.add(row["id"])
                break

    for kw in sorted(fp_docs, key=lambda k: (-len(fp_docs[k]), keywords.index(k))):
        if sum(e["type"] == "false_positive" for e in examples) >= N_FP_EXAMPLES:
            break
        if kw in used:
            continue
        words = matched_words(kw)
        candidates = {}
        for row in fp_docs[kw]:
            if row["id"] in used_ids:
                continue
            pos = row["overview"].find(kw)
            candidates.setdefault(containing_word(row["overview"], pos, len(kw)), (row, pos))
        if not candidates:
            continue
        top_word = max(candidates, key=lambda w: (words[w], -len(w)))
        row, pos = candidates[top_word]
        examples.append(fp_example(row, kw, pos, fp_count=len(fp_docs[kw]), common_words=words.most_common(5)))
        used.add(kw)
        used_ids.add(row["id"])

    for kw in sorted(fn_docs, key=lambda k: (-len(fn_docs[k]), keywords.index(k))):
        if sum(e["type"] == "false_negative" for e in examples) >= N_FN_EXAMPLES:
            break
        rows_left = [r for r in fn_docs[kw] if r["id"] not in used_ids]
        if not rows_left:
            continue
        row = rows_left[0]
        used_ids.add(row["id"])
        examples.append({
            "type": "false_negative",
            "id": row["id"],
            "title": row["title"],
            "keyword": kw,
            "gold_keywords": row["keywords"],
            "why": f"라벨은 '{kw}' 인데 설명문에 '{kw}' 글자가 없어 부분 문자열 규칙이 놓침",
            "overview_head": row["overview"][:2 * SNIPPET_RADIUS + 10].replace("\n", " ") + "…",
            "fn_count": len(fn_docs[kw]),
        })
    return examples


def fp_example(row, kw, pos, fp_count=None, common_words=None):
    word = containing_word(row["overview"], pos, len(kw))
    ex = {
        "type": "false_positive",
        "id": row["id"],
        "title": row["title"],
        "keyword": kw,
        "gold_keywords": row["keywords"],
        "matched_in_word": word,
        "why": f"'{word}' 안의 '{kw}' 글자에 걸렸지만 라벨에는 '{kw}' 가 없음",
        "context": snippet(row["overview"], pos, len(kw)),
    }
    if fp_count is not None:
        ex["fp_count"] = fp_count
    if common_words:
        ex["most_common_matched_words"] = [{"word": w, "count": c} for w, c in common_words]
    return ex


def run(rows, keywords, top_keywords, predict=substring_predict):
    preds = [predict(r["overview"], keywords) for r in rows]
    metrics = evaluate_sets([r["keywords"] for r in rows], preds, keywords, top_keywords)
    return metrics, preds


def keyword_counts(rows, preds, kw):
    tp = fp = fn = 0
    for row, pred in zip(rows, preds):
        g, p = kw in row["keywords"], kw in pred
        tp += g and p
        fp += p and not g
        fn += g and not p
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "predicted": tp + fp,
            "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4)}


def words_with(rows, kw, standalone_only=False):
    """kw 글자가 들어 있는 어절(공백 기준) 빈도. 항목마다 한 번만 센다.

    standalone_only 이면 korean_rule 이 인정하는 어절(예: '산', '산에')만 센다.
    """
    words = Counter()
    for row in rows:
        text = row["overview"]
        found = set()
        pos = text.find(kw)
        while pos >= 0:
            word = containing_word(text, pos, len(kw))
            if not standalone_only or contains_standalone_word(word, kw):
                found.add(word)
            pos = text.find(kw, pos + 1)
        words.update(found)
    return [{"word": w, "count": c} for w, c in sorted(words.items(), key=lambda x: (-x[1], x[0]))[:5]]


def single_char_effect(rows, sub_preds, kor_preds, single_chars):
    """한 글자 키워드별 substring -> korean_rule 전후 비교.

    korean_rule 로 남은 오탐 항목과 새로 생긴 누락 항목에서 그 글자를 품은 어절을 함께 남긴다.
    """
    out = {}
    for kw in single_chars:
        before = keyword_counts(rows, sub_preds, kw)
        after = keyword_counts(rows, kor_preds, kw)
        remaining_fp = [r for r, p in zip(rows, kor_preds) if kw in p and kw not in r["keywords"]]
        new_fn = [r for r, a, b in zip(rows, sub_preds, kor_preds)
                  if kw in r["keywords"] and kw in a and kw not in b]
        out[kw] = {
            "support": before["tp"] + before["fn"],
            "substring": before,
            "korean_rule": after,
            "korean_rule_remaining_fp_words": words_with(remaining_fp, kw, standalone_only=True),
            "korean_rule_new_fn_words": words_with(new_fn, kw),
        }
    return out


def main():
    keywords = load_keywords()
    gold = load_gold()
    splits = load_splits()
    by_id = {g["id"]: g for g in gold}
    top = top_frequent_keywords(gold, keywords)
    batch_ids = sorted({row_batch(g) for g in gold})
    gold_batch = {b: [g for g in gold if row_batch(g) == b] for b in batch_ids}
    top_batch1 = top_frequent_keywords(gold_batch[1], keywords)
    # per_keyword 는 전체 상위 15개 + 1차 상위 15개 중 빠진 것까지 남긴다 (예전 test 숫자를 그대로 비교할 수 있게)
    reported = report_keywords(gold, keywords)

    single_chars = [k for k in keywords if len(k) == 1]
    eval_splits = [name for name in EVAL_SPLITS if name in splits]
    split_rows = {name: [by_id[i] for i in splits[name]] for name in eval_splits}

    def evaluate_all(predict):
        """평가 집합(test, test_new), 전체, 배치별 전체에 같은 규칙을 적용한다. (지표, 예측) 을 돌려준다."""
        out = {name: run(rows, keywords, reported, predict) for name, rows in split_rows.items()}
        out["all"] = run(gold, keywords, reported, predict)
        out["all_by_batch"] = {b: run(gold_batch[b], keywords, reported, predict) for b in batch_ids}
        return out

    sub = evaluate_all(substring_predict)
    kor = evaluate_all(korean_rule_predict)

    def metrics_only(res):
        out = {name: res[name][0] for name in eval_splits + ["all"]}
        out["all_by_batch"] = {str(b): res["all_by_batch"][b][0] for b in batch_ids}
        return out

    # 참고용: 모델 입력과 같은 "title + overview" 에 같은 규칙을 적용했을 때
    def title_variant(rows):
        m = evaluate_sets([r["keywords"] for r in rows],
                          [substring_predict(r["title"] + " " + r["overview"], keywords) for r in rows], keywords)
        return {k: m[k] for k in ("micro", "macro", "samples")}

    result = {
        "name": "baseline_substring",
        "description": "KeywordMask.fromDescription 규칙 (overview 에 키워드 글자가 있으면 표시)",
        "rule": "keyword in overview (Java String.contains 와 동일), 빈 설명문은 빈 집합",
        "macro_definition": "평가 집합 gold 에 한 번 이상 나온 키워드만 평균",
        "eval_sets": {
            "test": "1차 배치 test 분할 (2차 배치를 넣기 전과 같은 목록)",
            "test_new": "2차 배치 test 분할 (여러 지역, 1차와 따로 평가)",
            "all": "전체 gold (1차 + 2차)",
            "all_by_batch": "배치별 전체 항목 (\"1\" 은 2차 배치를 넣기 전의 all 과 같음)",
        },
        "items": {**{name: len(rows) for name, rows in split_rows.items()}, "all": len(gold),
                  **{f"batch{b}": len(gold_batch[b]) for b in batch_ids}},
        "top_keywords": top,
        "top_keywords_batch1": top_batch1,
        "per_keyword_keywords": reported,
        **metrics_only(sub),
        "reference_title_plus_overview_test": title_variant(split_rows["test"]),
    }
    if "test_new" in split_rows:
        result["reference_title_plus_overview_test_new"] = title_variant(split_rows["test_new"])
    # 오탐/누락 예시는 README 표와 맞도록 1차 배치에서만 고른다 (2차 배치를 넣기 전과 같은 예시)
    examples = pick_examples(gold_batch[1], sub["all_by_batch"][1][1], keywords)
    result["examples_scope"] = "1차 배치 전체 항목"
    result["examples"] = [{k: v for k, v in e.items() if k not in TEXT_FIELDS} for e in examples]
    # 위쪽 키(name ~ examples)는 부분 문자열 기준선 그대로 두고, 국내용 규칙은 따로 붙인다.
    result["korean_rule"] = {
        "name": "baseline_korean_rule",
        "description": "KeywordMask.fromKoreanDescription 규칙 (TourAPI 설명문용, 한 글자 키워드는 독립된 단어일 때만)",
        "rule": "len(keyword) == 1 이면 앞이 한글 음절(U+AC00~U+D7A3)이 아니고 뒤에 이어지는 한글 음절이 없거나 "
                "조사(kt_common.KOREAN_PARTICLES)일 때만, 나머지는 keyword in overview. 빈 설명문은 빈 집합",
        "single_char_keywords": single_chars,
        **metrics_only(kor),
    }

    def effect(rows, sub_res, kor_res):
        return single_char_effect(rows, sub_res[1], kor_res[1], single_chars)

    result["single_char_keyword_effect"] = {
        "description": "한 글자 키워드별 substring(fromDescription) -> korean_rule(fromKoreanDescription) 전후 비교",
        **{name: effect(split_rows[name], sub[name], kor[name]) for name in eval_splits},
        "all": effect(gold, sub["all"], kor["all"]),
        "all_by_batch": {str(b): effect(gold_batch[b], sub["all_by_batch"][b], kor["all_by_batch"][b])
                         for b in batch_ids},
    }
    write_json(RESULTS_DIR / "baseline.json", result)
    write_jsonl(DATA_DIR / "baseline_examples.jsonl", examples)

    names = eval_splits + ["all"]
    for rule, res in (("substring", result), ("korean_rule", result["korean_rule"])):
        for name, m in [(n, res[n]) for n in names] + [(f"batch{b}", m) for b, m in res["all_by_batch"].items()]:
            print(f"[{rule}/{name}] items {m['samples']['items']}  micro P {m['micro']['precision']:.3f} "
                  f"R {m['micro']['recall']:.3f} F1 {m['micro']['f1']:.3f} | macro F1 {m['macro']['f1']:.3f} "
                  f"| exact {m['samples']['exact_match']:.3f}")
    print("한 글자 키워드 (substring -> korean_rule):")
    for name in names:
        for kw, e in result["single_char_keyword_effect"][name].items():
            a, b = e["substring"], e["korean_rule"]
            print(f"  [{name}] {kw} 정답 {e['support']:>3}  P {a['precision']:.3f} -> {b['precision']:.3f}  "
                  f"R {a['recall']:.3f} -> {b['recall']:.3f}  예측 {a['predicted']} -> {b['predicted']}")
    print("예시:")
    for e in examples:
        if e["type"] == "false_positive":
            print(f"  FP {e['keyword']:<6} {e['title']} :: {e['context']}")
        else:
            print(f"  FN {e['keyword']:<6} {e['title']} :: {e['overview_head']}")
    print(f"-> {RESULTS_DIR / 'baseline.json'}\n-> {DATA_DIR / 'baseline_examples.jsonl'}")


if __name__ == "__main__":
    main()

"""keyword_tagger 스크립트가 함께 쓰는 경로, 입출력, 평가 지표.

표준 라이브러리만 사용하므로 시스템 python3(3.9 이상)으로도 돌아간다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LABELS_DIR = ROOT / "labels"
RESULTS_DIR = ROOT / "results"
OUTPUTS_DIR = ROOT / "outputs"

KEYWORDS_PATH = ROOT / "keywords.json"
DESCRIPTIONS_PATH = DATA_DIR / "descriptions.jsonl"
RAW_LABELS_DIR = DATA_DIR / "labels"
CROSS_CHECK_PATH = RAW_LABELS_DIR / "cross_check.jsonl"
GOLD_LABELS_PATH = LABELS_DIR / "gold_labels.jsonl"
GOLD_PATH = DATA_DIR / "gold.jsonl"
SPLITS_PATH = LABELS_DIR / "splits.json"

SPLIT_SEED = 13
TOP_K_KEYWORDS = 15


def read_jsonl(path: Path) -> List[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no}: JSON 오류: {e}") from e
    return rows


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_keywords() -> List[str]:
    with open(KEYWORDS_PATH, encoding="utf-8") as f:
        keywords = json.load(f)
    if len(keywords) != len(set(keywords)):
        raise ValueError("keywords.json에 중복 키워드가 있습니다")
    return keywords


def load_gold() -> List[dict]:
    if not GOLD_PATH.exists():
        raise SystemExit(f"{GOLD_PATH} 가 없습니다. 먼저 scripts/prepare_data.py 를 실행하세요.")
    return read_jsonl(GOLD_PATH)


def load_splits() -> Dict[str, List[str]]:
    with open(SPLITS_PATH, encoding="utf-8") as f:
        splits = json.load(f)
    return {name: splits[name] for name in ("train", "valid", "test")}


def keyword_order(keywords: Sequence[str]) -> Dict[str, int]:
    return {k: i for i, k in enumerate(keywords)}


def substring_predict(text: str, keywords: Sequence[str]) -> List[str]:
    """KeywordMask.fromDescription 과 같은 규칙: 설명문에 키워드 글자가 들어 있으면 표시.

    Java: description == null || description.isBlank() 이면 빈 마스크,
    아니면 KEYWORDS.stream().filter(description::contains).
    """
    if text is None or not text.strip():
        return []
    return [k for k in keywords if k in text]


# KeywordMask.PARTICLES 와 같은 집합. 한 글자 키워드(산, 섬, 성, 탑, 강) 뒤에 이 조사만 붙으면 같은 단어로 본다.
KOREAN_PARTICLES = frozenset({
    "은", "이", "을", "과", "의", "에", "에서", "에는", "에도", "으로", "으로는", "까지", "부터", "처럼",
    "이다", "이며", "이고", "이자", "이나", "이라는", "이었다",
})
# KeywordMask.isHangulSyllable 과 같은 범위 (한글 완성형 음절 '가'~'힣')
HANGUL_SYLLABLE_FIRST = "가"
HANGUL_SYLLABLE_LAST = "힣"


def is_hangul_syllable(c: str) -> bool:
    return HANGUL_SYLLABLE_FIRST <= c <= HANGUL_SYLLABLE_LAST


def contains_standalone_word(text: str, keyword: str) -> bool:
    """KeywordMask.containsStandaloneWord 를 그대로 옮긴 것.

    keyword 가 나오는 자리마다, 바로 앞 글자가 한글 음절이 아니고(단어 시작)
    뒤에 이어지는 한글 음절 덩어리가 비어 있거나 KOREAN_PARTICLES 중 하나이면 참.
    """
    index = text.find(keyword)
    while index >= 0:
        end = index + len(keyword)
        word_end = end
        while word_end < len(text) and is_hangul_syllable(text[word_end]):
            word_end += 1

        starts_word = index == 0 or not is_hangul_syllable(text[index - 1])
        suffix = text[end:word_end]
        if starts_word and (not suffix or suffix in KOREAN_PARTICLES):
            return True
        index = text.find(keyword, index + 1)
    return False


def korean_rule_predict(text: str, keywords: Sequence[str]) -> List[str]:
    """KeywordMask.fromKoreanDescription 과 같은 규칙 (DestinationInfo 의 source 가 TOUR_API 일 때 쓰임).

    Java: description == null || description.isBlank() 이면 빈 마스크, 아니면
    keyword.length() == 1 ? containsStandaloneWord(description, keyword) : description.contains(keyword).
    한 글자 키워드는 모두 BMP 한글 음절이라 Java length()(UTF-16 단위)와 파이썬 len() 이 같다.
    공백만 있는 설명문은 어느 쪽 공백 정의로 보든 키워드가 없으므로 결과가 같다.
    """
    if text is None or not text.strip():
        return []
    return [k for k in keywords if (contains_standalone_word(text, k) if len(k) == 1 else k in text)]


def keyword_mask(selected: Iterable[str], keywords: Sequence[str]):
    """KeywordMask.fromKeywords 와 같은 (low, high) 비트 마스크. 순서 i < 63 은 low, 나머지는 high 의 i - 63 번 비트."""
    order = keyword_order(keywords)
    low = high = 0
    for k in selected:
        i = order.get(k.strip()) if k is not None else None
        if i is None:
            continue
        if i < 63:
            low |= 1 << i
        else:
            high |= 1 << (i - 63)
    return low, high


def _prf(tp: int, fp: int, fn: int) -> Dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def evaluate_sets(
    gold: Sequence[Iterable[str]],
    pred: Sequence[Iterable[str]],
    keywords: Sequence[str],
    top_keywords: Sequence[str] = (),
) -> dict:
    """키워드 집합 단위 평가. 모델과 기준선이 같은 함수로 계산되도록 한다.

    - micro: 전체 (항목, 키워드) 쌍 기준
    - macro: 평가 집합 gold 에 한 번이라도 나온 키워드만 평균 (support > 0)
    - per_keyword: top_keywords 각각의 P/R/F1/support
    """
    assert len(gold) == len(pred)
    gold_sets = [set(g) for g in gold]
    pred_sets = [set(p) for p in pred]
    counts = {k: {"tp": 0, "fp": 0, "fn": 0} for k in keywords}
    for g, p in zip(gold_sets, pred_sets):
        for k in p:
            if k not in counts:
                raise ValueError(f"알 수 없는 키워드: {k}")
            counts[k]["tp" if k in g else "fp"] += 1
        for k in g - p:
            counts[k]["fn"] += 1

    tp = sum(c["tp"] for c in counts.values())
    fp = sum(c["fp"] for c in counts.values())
    fn = sum(c["fn"] for c in counts.values())
    micro = _prf(tp, fp, fn)
    micro.update({"tp": tp, "fp": fp, "fn": fn})

    per_label = {}
    for k, c in counts.items():
        support = c["tp"] + c["fn"]
        stats = _prf(c["tp"], c["fp"], c["fn"])
        stats.update({"support": support, "predicted": c["tp"] + c["fp"]})
        per_label[k] = stats
    supported = [k for k in keywords if per_label[k]["support"] > 0]
    macro = {
        m: round(sum(per_label[k][m] for k in supported) / len(supported), 4) if supported else 0.0
        for m in ("precision", "recall", "f1")
    }
    macro["labels"] = len(supported)

    n = len(gold_sets)
    samples = {
        "items": n,
        "exact_match": round(sum(g == p for g, p in zip(gold_sets, pred_sets)) / n, 4) if n else 0.0,
        "mean_jaccard": round(sum(jaccard(g, p) for g, p in zip(gold_sets, pred_sets)) / n, 4) if n else 0.0,
        "avg_gold_keywords": round(sum(len(g) for g in gold_sets) / n, 3) if n else 0.0,
        "avg_pred_keywords": round(sum(len(p) for p in pred_sets) / n, 3) if n else 0.0,
    }
    return {
        "micro": micro,
        "macro": macro,
        "samples": samples,
        "per_keyword": {k: per_label[k] for k in top_keywords},
        "worst_false_positive_keywords": sorted(
            ({"keyword": k, "fp": counts[k]["fp"], "tp": counts[k]["tp"]} for k in keywords if counts[k]["fp"]),
            key=lambda x: (-x["fp"], x["keyword"]),
        )[:15],
    }


def top_frequent_keywords(rows: Sequence[dict], keywords: Sequence[str], k: int = TOP_K_KEYWORDS) -> List[str]:
    order = keyword_order(keywords)
    freq = {kw: 0 for kw in keywords}
    for row in rows:
        for kw in row["keywords"]:
            freq[kw] += 1
    return sorted(keywords, key=lambda kw: (-freq[kw], order[kw]))[:k]

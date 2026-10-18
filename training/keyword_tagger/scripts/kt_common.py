"""keyword_tagger 스크립트가 함께 쓰는 경로, 입출력, 평가 지표.

표준 라이브러리만 사용하므로 시스템 python3(3.9 이상)으로도 돌아간다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

ROOT = Path(__file__).resolve().parent.parent
GLOPLE_ROOT = ROOT.parents[1]
DATA_DIR = ROOT / "data"
LABELS_DIR = ROOT / "labels"
RESULTS_DIR = ROOT / "results"
OUTPUTS_DIR = ROOT / "outputs"

KEYWORDS_PATH = ROOT / "keywords.json"
# 1차 배치: Neoulteo DB 덤프의 TourAPI overview (서울·인천·광주)
DESCRIPTIONS_PATH = DATA_DIR / "descriptions.jsonl"
RAW_LABELS_DIR = DATA_DIR / "labels"
CROSS_CHECK_PATH = RAW_LABELS_DIR / "cross_check.jsonl"
# 2차 배치: 로컬 DB(destination_info) 에서 꺼낸 TourAPI 설명문 (scripts/export_from_db.py)
DB_DESCRIPTIONS_PATH = DATA_DIR / "db_descriptions.jsonl"
BATCH2_MANIFEST_PATH = DATA_DIR / "chunks_v2" / "all_new.jsonl"  # 라벨러에게 준 설명문 (있으면 대조용)
RAW_LABELS_V2_DIR = DATA_DIR / "labels_v2"
CROSS_CHECK_V2_PATH = RAW_LABELS_V2_DIR / "cross_check.jsonl"
# 3차 배치: 같은 db_descriptions.jsonl 의 해외 설명문(source=WIKIDATA, 한국어 위키백과 도입부, CC BY-SA 4.0)
BATCH3_MANIFEST_PATH = DATA_DIR / "chunks_v3" / "all_new.jsonl"  # 라벨러에게 준 설명문 (있으면 대조용)
RAW_LABELS_V3_DIR = DATA_DIR / "labels_v3"
CROSS_CHECK_V3_PATH = RAW_LABELS_V3_DIR / "cross_check.jsonl"
GOLD_LABELS_PATH = LABELS_DIR / "gold_labels.jsonl"
BATCHES_PATH = LABELS_DIR / "batches.json"
GOLD_PATH = DATA_DIR / "gold.jsonl"
SPLITS_PATH = LABELS_DIR / "splits.json"
# 서버의 위키데이터 유형 코드표 (WikiTypeKeywords). 해외 규칙 기준선에서 읽기만 한다.
WIKI_TYPE_KEYWORDS_PATH = GLOPLE_ROOT / "src" / "main" / "resources" / "wiki" / "type-keywords.csv"
# 서버의 TourAPI 분류 코드표 (TourCategories). predict_all.py 가 지금 서버 규칙과 비교할 때 읽기만 한다.
TOUR_CATEGORY_KEYWORDS_PATH = GLOPLE_ROOT / "src" / "main" / "resources" / "tour" / "category-keywords.csv"

# destination_info.source. 1·2차는 국내(TourAPI), 3차는 해외(위키데이터). source 가 없는 예전 gold.jsonl 행은 모두 국내다.
SOURCE_TOUR_API = "TOUR_API"
SOURCE_WIKIDATA = "WIKIDATA"

SPLIT_SEED = 13
TOP_K_KEYWORDS = 15
# splits.json 에 있을 수 있는 분할. test 는 1차 배치 그대로(이전 결과와 비교용), test_new 는 2차 배치의 15%,
# test_overseas 는 3차(해외) 배치의 15%.
SPLIT_NAMES = ("train", "valid", "test", "test_new", "test_overseas")
EVAL_SPLITS = ("test", "test_new", "test_overseas")


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
    """train/valid/test 와, 있으면 test_new·test_overseas. 예전 splits.json(test_new 나 test_overseas 없음)도 그대로 읽는다."""
    with open(SPLITS_PATH, encoding="utf-8") as f:
        splits = json.load(f)
    return {name: splits[name] for name in SPLIT_NAMES if name in splits}


def load_batches() -> Dict[str, int]:
    """id -> 라벨 배치 번호 (labels/batches.json). 파일이 없거나 목록에 없는 id 는 1차(1)로 본다.

    id 는 출처를 통틀어 문자열로 유일하다(TourAPI 는 숫자 contentid, 위키데이터는 'Q' 로 시작하는 QID).
    prepare_data.py 가 배치 사이·출처 사이에 겹치는 id 가 없는지 확인하므로 (source, id) 로 나누지 않아도 된다.
    """
    if not BATCHES_PATH.exists():
        return {}
    with open(BATCHES_PATH, encoding="utf-8") as f:
        info = json.load(f)
    return {str(i): int(b["batch"]) for b in info.get("batches", []) for i in b.get("ids", [])}


def row_batch(row: dict) -> int:
    """data/gold.jsonl 행의 배치 번호. batch 필드가 없는 예전 gold.jsonl 은 모두 1차."""
    return int(row.get("batch", 1))


def row_source(row: dict) -> str:
    """data/gold.jsonl 행의 출처(TOUR_API 또는 WIKIDATA). source 필드가 없는 예전 gold.jsonl(1·2차만) 은 모두 국내."""
    return row.get("source") or SOURCE_TOUR_API


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


WIKI_TYPE_HEADER = "qid,ko_label,en_label,keywords"
WIKI_QID_PATTERN = re.compile(r"Q[1-9][0-9]*")


def split_csv_line(line: str, where: str) -> List[str]:
    """TourCategories.splitCsvLine 을 그대로 옮긴 것: 큰따옴표로 감싼 칸(안의 쉼표, "" 이스케이프)을 지원하는 한 줄 CSV 분리."""
    fields, field = [], []
    quoted = after_quote = False
    i = 0
    while i < len(line):
        c = line[i]
        if quoted:
            if c == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    field.append('"')
                    i += 1
                else:
                    quoted, after_quote = False, True
            else:
                field.append(c)
        elif c == ",":
            fields.append("".join(field))
            field = []
            after_quote = False
        elif after_quote:
            raise ValueError(f"{where}: 닫는 큰따옴표 뒤에 쉼표가 아닌 글자가 있습니다")
        elif c == '"':
            if field:
                raise ValueError(f"{where}: 큰따옴표는 칸의 처음에만 올 수 있습니다")
            quoted = True
        else:
            field.append(c)
        i += 1
    if quoted:
        raise ValueError(f"{where}: 큰따옴표가 닫히지 않았습니다")
    fields.append("".join(field))
    return fields


def load_wiki_type_table(keywords: Sequence[str], path: Path = WIKI_TYPE_KEYWORDS_PATH) -> Dict[str, dict]:
    """서버 WikiTypeKeywords 가 읽는 wiki/type-keywords.csv -> {qid: {"ko_label", "en_label", "keywords"}} (파일 순서).

    WikiTypeKeywords.parse 와 같게 검사한다: '#' 주석·빈 줄은 건너뛰고, 첫 줄은 헤더, 열 4개, QID 형식, 영어 유형명,
    키워드는 keywords.json 안의 값만 '|' 로 (빈 칸이면 키워드 없음), 중복 QID·중복 키워드는 오류.
    """
    supported = set(keywords)
    table: Dict[str, dict] = {}
    header_seen = False
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for line_no, line in enumerate(lines, 1):
        if line_no == 1 and line.startswith("﻿"):
            line = line[1:]
        if not line.strip() or line.strip().startswith("#"):
            continue
        where = f"{path.name} {line_no}번째 줄"
        if not header_seen:
            if line.strip() != WIKI_TYPE_HEADER:
                raise ValueError(f"{where}: 주석이 아닌 첫 줄은 '{WIKI_TYPE_HEADER}'여야 합니다")
            header_seen = True
            continue
        fields = split_csv_line(line, where)
        if len(fields) != 4:
            raise ValueError(f"{where}: 열이 4개여야 하는데 {len(fields)}개입니다")
        qid = fields[0].strip()
        if not WIKI_QID_PATTERN.fullmatch(qid):
            raise ValueError(f"{where}: 위키데이터 QID 형식이 아닙니다: '{qid}'")
        if not fields[2].strip():
            raise ValueError(f"{where}: 영어 유형명(en_label)이 비어 있습니다")
        kws: List[str] = []
        if fields[3].strip():
            for raw in fields[3].split("|"):
                kw = raw.strip()
                if not kw:
                    raise ValueError(f"{where}: 빈 키워드가 있습니다")
                if kw not in supported:
                    raise ValueError(f"{where}: 지원하지 않는 키워드입니다: '{kw}'")
                if kw in kws:
                    raise ValueError(f"{where}: 키워드가 중복되었습니다: '{kw}'")
                kws.append(kw)
        if qid in table:
            raise ValueError(f"{where}: QID가 중복되었습니다: {qid}")
        table[qid] = {"ko_label": fields[1].strip(), "en_label": fields[2].strip(), "keywords": kws}
    if not header_seen or not table:
        raise ValueError(f"{path}: 헤더나 유형이 없습니다")
    return table


def category_qids(category: Optional[str]) -> List[str]:
    """destination_info.category('|' 로 이은 QID) -> QID 목록. WikiTypeKeywords.split 과 같다 (빈 값은 빈 목록)."""
    if category is None or not category.strip():
        return []
    return [q.strip() for q in category.split("|") if q.strip()]


# DestinationInfo.MISLEADING_WORDS와 같아야 한다: 키워드가 다른 낱말 속에 들어 있어 잘못 켜지는 낱말
MISLEADING_WORDS = ("문화어", "모스크바")


def strip_misleading_words(text: str) -> str:
    for word in MISLEADING_WORDS:
        text = text.replace(word, " ")
    return text


def overseas_rule_predict(text: str, category: Optional[str], keywords: Sequence[str],
                          type_table: Dict[str, dict]) -> List[str]:
    """DestinationInfo.computeKeywordMask 의 WIKIDATA 분기에서 세계유산 플래그만 뺀 규칙.

    Java: KeywordMask.fromKoreanDescription(info.replace("문화어", " ")) ∪ WikiTypeKeywords.keywordMask(category)
          (∪ worldHeritage 이면 '유네스코'). worldHeritage(P1435 = Q9259)는 data/db_descriptions.jsonl 에 없어서
    재현하지 않는다. category 에 'Q9259'(유네스코 세계유산 유형)가 있으면 코드표로 '유네스코' 가 붙는 것은 같다.
    코드표에 없는 QID 는 건너뛴다.
    """
    selected = set(korean_rule_predict(None if text is None else strip_misleading_words(text), keywords))
    for qid in category_qids(category):
        if qid in type_table:
            selected.update(type_table[qid]["keywords"])
    return [k for k in keywords if k in selected]


TOUR_CATEGORY_HEADER = "code,depth1,depth2,depth3,keywords"
TOUR_CATEGORY_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9]+")


def load_tour_category_table(keywords: Sequence[str], path: Path = TOUR_CATEGORY_KEYWORDS_PATH) -> Dict[str, List[str]]:
    """서버 TourCategories 가 읽는 tour/category-keywords.csv -> {분류 코드: 키워드 목록} (파일 순서).

    TourCategories.parse 와 같게 검사한다: 첫 줄은 헤더(BOM 허용), 빈 줄은 건너뛰고(주석 없음), 열 5개, 코드 형식,
    분류명 3개는 비어 있으면 안 되고, 키워드는 keywords.json 안의 값만 '|' 로 (빈 칸이면 키워드 없음), 중복 코드·중복 키워드는 오류.
    """
    supported = set(keywords)
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines or lines[0].lstrip("﻿").strip() != TOUR_CATEGORY_HEADER:
        raise ValueError(f"{path.name} 첫 줄은 '{TOUR_CATEGORY_HEADER}'여야 합니다")
    table: Dict[str, List[str]] = {}
    for line_no, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        where = f"{path.name} {line_no}번째 줄"
        fields = split_csv_line(line, where)
        if len(fields) != 5:
            raise ValueError(f"{where}: 열이 5개여야 하는데 {len(fields)}개입니다")
        code = fields[0].strip()
        if not TOUR_CATEGORY_CODE_PATTERN.fullmatch(code):
            raise ValueError(f"{where}: 분류 코드 형식이 아닙니다: '{code}'")
        if any(not fields[i].strip() for i in (1, 2, 3)):
            raise ValueError(f"{where}: 분류명이 비어 있습니다")
        kws: List[str] = []
        if fields[4].strip():
            for raw in fields[4].split("|"):
                kw = raw.strip()
                if not kw:
                    raise ValueError(f"{where}: 빈 키워드가 있습니다")
                if kw not in supported:
                    raise ValueError(f"{where}: 지원하지 않는 키워드입니다: '{kw}'")
                if kw in kws:
                    raise ValueError(f"{where}: 키워드가 중복되었습니다: '{kw}'")
                kws.append(kw)
        if code in table:
            raise ValueError(f"{where}: 코드가 중복되었습니다: {code}")
        table[code] = kws
    return table


def domestic_rule_predict(text: str, category: Optional[str], keywords: Sequence[str],
                          category_table: Dict[str, List[str]]) -> List[str]:
    """DestinationInfo.computeKeywordMask 의 TOUR_API 분기: fromKoreanDescription(info) ∪ TourCategories.keywordMask(category).

    baseline.py 의 '국내 규칙'(korean_rule_predict)은 예전처럼 설명문만 보지만, 지금 서버는 분류 코드표 키워드도 더한다.
    코드표에 없는 코드나 빈 코드는 건너뛴다 (TourCategories.find 는 앞뒤 공백을 지운 코드로 찾음).
    """
    selected = set(korean_rule_predict(text, keywords))
    if category is not None and category.strip() in category_table:
        selected.update(category_table[category.strip()])
    return [k for k in keywords if k in selected]


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


def top_keywords_by_split(gold: Sequence[dict], keywords: Sequence[str], k: int = TOP_K_KEYWORDS) -> Dict[str, List[str]]:
    """평가 집합별 '자주 나오는 키워드' 표에 쓸 상위 k 개.

    test·test_new 는 국내(1·2차) gold 기준이라 3차 배치를 넣기 전의 top_keywords 와 같고,
    test_overseas 는 해외(3차) gold 기준이다. 해외 행이 없으면 test_overseas 는 빠진다.
    """
    domestic = [g for g in gold if row_source(g) == SOURCE_TOUR_API]
    overseas = [g for g in gold if row_source(g) == SOURCE_WIKIDATA]
    top_domestic = top_frequent_keywords(domestic, keywords, k)
    out = {"test": top_domestic, "test_new": top_domestic}
    if overseas:
        out["test_overseas"] = top_frequent_keywords(overseas, keywords, k)
    return out


def report_keywords(gold: Sequence[dict], keywords: Sequence[str], k: int = TOP_K_KEYWORDS) -> List[str]:
    """결과 파일 per_keyword 에 남길 키워드: 국내(1·2차) 상위 k 개 + 1차 상위 k 개 중 빠진 것
    + 해외(3차) 상위 k 개 중 빠진 것 + 전체 상위 k 개 중 빠진 것.

    2차 배치를 넣으면 상위 15개가 바뀌므로(도시·사원·기념물 -> 바다·해안·야외 활동), 1차 기준 15개도 같이 남겨
    예전 결과(test 분할)의 키워드별 숫자와 그대로 비교할 수 있게 한다. 3차(해외)를 넣어도 앞부분(국내 + 1차)은
    3차를 넣기 전 목록과 같고, 해외·전체 상위 키워드는 뒤에 붙는다.
    """
    groups = [
        [g for g in gold if row_source(g) == SOURCE_TOUR_API],
        [g for g in gold if row_batch(g) == 1],
        [g for g in gold if row_source(g) == SOURCE_WIKIDATA],
        list(gold),
    ]
    out: List[str] = []
    for rows in groups:
        if not rows:
            continue
        out += [kw for kw in top_frequent_keywords(rows, keywords, k) if kw not in out]
    return out

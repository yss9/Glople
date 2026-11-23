"""embedding 스크립트가 함께 쓰는 경로, 입출력, 지역 정규화, 여행지 글(passage) 만들기, 검색 지표, 모델 설정.

- 이 파일 자체는 표준 라이브러리만 씁니다. torch·sentence-transformers 가 필요한 함수(load_model, encode)는
  함수 안에서 import 하므로 prepare_data.py 는 시스템 python3 로도 돌아갑니다.
- 키워드 규칙(서버 KeywordMask 의 파이썬 포트)은 keyword_tagger/scripts/kt_common.py 를 그대로 가져다 씁니다.
  Java 와 비트 단위로 같다는 것을 그쪽에서 확인했으므로 여기서 다시 옮겨 쓰지 않습니다.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# 경로
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent          # training/embedding
TRAINING_DIR = ROOT.parent                              # training
GLOPLE_DIR = TRAINING_DIR.parent                        # 저장소 루트
TAGGER_DIR = TRAINING_DIR / "keyword_tagger"

DATA_DIR = ROOT / "data"            # gitignore: 설명문 원문이 들어감
OUTPUTS_DIR = ROOT / "outputs"      # gitignore: 학습한 모델, 로그, 색인
RESULTS_DIR = ROOT / "results"      # 커밋: 지표와 합성 질의 예시만 (설명문 없음)

# 읽기만 하는 입력 (다른 폴더의 파일은 절대 고치지 않음)
KEYWORDS_PATH = TAGGER_DIR / "keywords.json"
GOLD_LABELS_PATH = TAGGER_DIR / "labels" / "gold_labels.jsonl"
SPLITS_PATH = TAGGER_DIR / "labels" / "splits.json"
DB_EXPORT_PATH = TAGGER_DIR / "data" / "db_descriptions.jsonl"   # 로컬 DB 추출본 (gitignore)
DUMP_PATH = TAGGER_DIR / "data" / "descriptions.jsonl"           # 1차 배치 덤프 (gitignore)
CATEGORY_CSV_PATH = GLOPLE_DIR / "src" / "main" / "resources" / "tour" / "category-keywords.csv"

# prepare_data.py 가 만드는 파일
DESTINATIONS_PATH = DATA_DIR / "destinations.jsonl"
TRAIN_PAIRS_PATH = DATA_DIR / "train_pairs.jsonl"
QUERY_SET_PATHS = {
    "valid": DATA_DIR / "queries_valid.jsonl",        # 학습 중 best 에폭 고르기 (valid 여행지, 학습과 같은 문장 틀)
    "test": DATA_DIR / "queries_test.jsonl",          # 대표 평가 (test+test_new 여행지, 학습에 없는 문장 틀·바꿔 말하기)
    "test_iid": DATA_DIR / "queries_test_iid.jsonl",  # 참고 평가 (test+test_new 여행지, 학습과 같은 문장 틀)
}
# 질의 세트마다 검색 대상(corpus)이 되는 태거 분할. test 쪽은 1차 test 와 2차 test_new 를 합친다.
CORPUS_SPLITS = {"valid": ("valid",), "test": ("test", "test_new"), "test_iid": ("test", "test_new")}

SEED = 13

# ---------------------------------------------------------------------------
# keyword_tagger 의 검증된 규칙 포트 가져오기
# ---------------------------------------------------------------------------
sys.path.insert(0, str(TAGGER_DIR / "scripts"))
# 다른 폴더(keyword_tagger/scripts/__pycache__)에 바이트코드를 쓰지 않도록 이 가져오기만 캐시를 끕니다.
_dont_write = sys.dont_write_bytecode
sys.dont_write_bytecode = True
import kt_common  # noqa: E402  (keyword_tagger/scripts/kt_common.py, 표준 라이브러리만 씀)
sys.dont_write_bytecode = _dont_write
_KT_REQUIRED = ("read_jsonl", "write_jsonl", "write_json", "load_keywords", "korean_rule_predict")
_missing = [name for name in _KT_REQUIRED if not hasattr(kt_common, name)]
if _missing:
    raise ImportError(f"keyword_tagger/scripts/kt_common.py 에 {_missing} 가 없습니다 (태거 쪽 코드가 바뀜)")

korean_rule_predict = kt_common.korean_rule_predict


# ---------------------------------------------------------------------------
# 입출력
# ---------------------------------------------------------------------------
def read_jsonl(path: Path) -> List[dict]:
    return kt_common.read_jsonl(path)


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    kt_common.write_jsonl(path, rows)


def write_json(path: Path, obj) -> None:
    kt_common.write_json(path, obj)


def read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_keywords() -> List[str]:
    """keyword_tagger/keywords.json 의 94개 (서버 KeywordMask 와 같은 순서)."""
    return kt_common.load_keywords()


def load_category_table() -> Dict[str, dict]:
    """tour/category-keywords.csv: 분류 코드 -> {depth1, depth2, depth3, keywords[]} (서버 TourCategories 와 같은 표)."""
    table = {}
    with open(CATEGORY_CSV_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            kws = [k.strip() for k in (row.get("keywords") or "").split("|") if k.strip()]
            table[row["code"].strip()] = {
                "depth1": row["depth1"].strip(), "depth2": row["depth2"].strip(), "depth3": row["depth3"].strip(),
                "keywords": kws,
            }
    return table


def require_prepared() -> None:
    missing = [p for p in [DESTINATIONS_PATH, TRAIN_PAIRS_PATH, *QUERY_SET_PATHS.values()] if not p.exists()]
    if missing:
        raise SystemExit(f"{missing[0]} 가 없습니다. 먼저 scripts/prepare_data.py 를 실행하세요.")


def load_destinations() -> Dict[str, dict]:
    require_prepared()
    return {d["id"]: d for d in read_jsonl(DESTINATIONS_PATH)}


def load_query_set(name: str) -> List[dict]:
    require_prepared()
    return read_jsonl(QUERY_SET_PATHS[name])


def corpus_ids(destinations: Dict[str, dict], query_set: str) -> List[str]:
    """질의 세트의 검색 대상 여행지 id 목록. 순서는 destinations.jsonl 순서(= 서버 동점 순서와 같은 db_order)."""
    splits = set(CORPUS_SPLITS[query_set])
    rows = [d for d in destinations.values() if d["split"] in splits]
    rows.sort(key=lambda d: d["db_order"])
    return [d["id"] for d in rows]


# ---------------------------------------------------------------------------
# 지역 정규화
# ---------------------------------------------------------------------------
# 시도 이름(행정 이름, 줄임말, 예전 이름) -> 정규 키. 2026년 로컬 DB 에는 광주광역시·전라남도가
# '전남광주통합특별시' 하나로 들어 있어서, 예전 덤프의 '광주광역시' 주소도 같은 키로 모읍니다.
SIDO_CANON = {
    "서울특별시": "서울", "서울": "서울",
    "부산광역시": "부산", "부산": "부산",
    "대구광역시": "대구", "대구": "대구",
    "인천광역시": "인천", "인천": "인천",
    "광주광역시": "전남광주", "전라남도": "전남광주", "전남": "전남광주", "전남광주통합특별시": "전남광주",
    "대전광역시": "대전", "대전": "대전",
    "울산광역시": "울산", "울산": "울산",
    "세종특별자치시": "세종", "세종": "세종",
    "경기도": "경기", "경기": "경기",
    "강원특별자치도": "강원", "강원도": "강원", "강원": "강원",
    "충청북도": "충북", "충북": "충북",
    "충청남도": "충남", "충남": "충남",
    "전북특별자치도": "전북", "전라북도": "전북", "전북": "전북",
    "경상북도": "경북", "경북": "경북",
    "경상남도": "경남", "경남": "경남",
    "제주특별자치도": "제주", "제주도": "제주", "제주": "제주",
}


def region_of_address(address: Optional[str]) -> Optional[str]:
    """서버 TourDestinationSyncService.regionOf 와 같은 생각: 주소 앞 두 낱말(세종은 한 낱말)."""
    if not address or not address.strip():
        return None
    parts = address.split()
    if parts[0].startswith("세종"):
        return parts[0]
    return " ".join(parts[:2])


def parse_region(location: Optional[str]) -> dict:
    """'경상북도 경주시' -> {'sido': '경북', 'sigungu': '경주시'}. 모르면 None."""
    if not location or not location.strip():
        return {"sido": None, "sigungu": None}
    parts = location.split()
    sido = SIDO_CANON.get(parts[0])
    sigungu = parts[1] if len(parts) > 1 else None
    return {"sido": sido, "sigungu": sigungu}


# ---------------------------------------------------------------------------
# 여행지 글 (임베딩·BM25 가 읽는 passage)
# ---------------------------------------------------------------------------
DESCRIPTION_POLICIES = ("all", "type1", "none")
_HTML_TAG = re.compile(r"<\s*/?\s*(br|p|b|strong|span|div|a)(\s[^>]*)?/?\s*>", re.IGNORECASE)


def clean_description(text: Optional[str]) -> str:
    if not text:
        return ""
    text = _HTML_TAG.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def uses_description(dest: dict, policy: str) -> bool:
    """설명문을 학습·색인 입력에 넣을지. 출력(화면·결과 파일)에는 어느 정책이든 절대 내보내지 않습니다.

    - all  : 모든 설명문 사용 (기본값, README '저작권' 절의 소유자 결정 참고)
    - type1: 공공누리 제1유형(출처표시)만 사용, 제3유형(변경금지)·표시 없음은 분류 요약만
    - none : 설명문을 쓰지 않고 이름·지역·분류만 (서버 summary 와 같은 정보)
    """
    if policy == "all":
        return bool(dest.get("description"))
    if policy == "type1":
        return bool(dest.get("description")) and (dest.get("copyright_type") or "").strip().lower() == "type1"
    if policy == "none":
        return False
    raise ValueError(f"알 수 없는 설명문 정책: {policy}")


def passage_text(dest: dict, policy: str) -> str:
    """'이름 | 지역 | 분류 경로 | 설명문' 한 줄. 설명문이 빠지면 서버 summary(지역·관광 타입·분류)와 같은 정보만 남습니다."""
    parts = [dest["title"]]
    if dest.get("location"):
        parts.append(dest["location"])
    if dest.get("category_path"):
        parts.append(dest["category_path"])
    elif dest.get("content_type_name"):
        parts.append(dest["content_type_name"])
    if uses_description(dest, policy):
        parts.append(dest["description"])
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# 관련도 (정답)
# ---------------------------------------------------------------------------
def constraint_ok(query: dict, dest: dict) -> bool:
    """질의의 지역·분류 조건을 여행지가 만족하는지. 조건이 없으면 참."""
    region = query.get("region")
    if region:
        if region["level"] == "sido" and dest.get("sido") != region["value"]:
            return False
        if region["level"] == "sigungu" and (dest.get("sido"), dest.get("sigungu")) != (region["sido"], region["value"]):
            return False
    category = query.get("category")
    if category:
        code = dest.get("category") or ""
        if not any(code.startswith(prefix) for prefix in category["code_prefixes"]):
            return False
    return True


def graded_relevance(query: dict, dest: dict) -> float:
    """관련도 = (질의 키워드 중 여행지 정답 키워드에 있는 비율), 지역·분류 조건이 틀리면 0.

    1.0 이면 '완전 관련'(Recall·MRR 의 정답), 0 과 1 사이면 nDCG 에서 부분 점수만 받습니다.
    정답 키워드는 keyword_tagger 의 AI silver 라벨이므로 사람이 검수한 정답이 아닙니다.
    """
    if not constraint_ok(query, dest):
        return 0.0
    kq = query["keywords"]
    gold = set(dest["keywords"])
    return sum(1 for k in kq if k in gold) / len(kq)


def is_fully_relevant(query: dict, dest: dict) -> bool:
    return graded_relevance(query, dest) >= 1.0


# ---------------------------------------------------------------------------
# 검색 지표
# ---------------------------------------------------------------------------
NDCG_K = 10
RECALL_K = 20


def ndcg_at_k(ranked_gains: Sequence[float], all_gains: Sequence[float], k: int = NDCG_K) -> float:
    """nDCG@k (선형 gain). ranked_gains: 검색 결과 순서대로의 관련도, all_gains: 검색 대상 전체의 관련도."""
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(ranked_gains[:k]))
    ideal = sorted(all_gains, reverse=True)[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(ranked_full: Sequence[bool], n_relevant: int, k: int = RECALL_K) -> float:
    """완전 관련 여행지 중 상위 k 안에 든 비율. 분모는 min(정답 수, k) (정답이 k 개보다 많아도 1.0 까지 가능)."""
    if n_relevant == 0:
        return 0.0
    return sum(ranked_full[:k]) / min(n_relevant, k)


def reciprocal_rank(ranked_full: Sequence[bool]) -> float:
    for i, ok in enumerate(ranked_full):
        if ok:
            return 1.0 / (i + 1)
    return 0.0


def evaluate_rankings(queries: Sequence[dict], rankings: Sequence[Sequence[str]], destinations: Dict[str, dict],
                      corpus: Sequence[str], groups: Sequence[str] = ("by_origin", "by_type"),
                      return_per_query: bool = False) -> dict:
    """질의마다 검색 결과(id 순서 목록)를 받아 평균 지표와 묶음별 지표를 계산합니다.

    rankings[i] 는 corpus 의 일부(키워드 검색처럼 21개만 돌려주는 경우)여도 됩니다. 빠진 여행지는 순위 밖으로 봅니다.
    return_per_query=True 면 질의별 점수(per_query)도 돌려줍니다 (예시 분석용).
    """
    assert len(queries) == len(rankings)
    corpus_set = set(corpus)
    per_query = []
    for q, ranked in zip(queries, rankings):
        if any(r not in corpus_set for r in ranked):
            raise ValueError(f"검색 대상 밖의 여행지가 결과에 있습니다 (질의 {q['qid']})")
        all_gains = [graded_relevance(q, destinations[d]) for d in corpus]
        n_rel = sum(1 for g in all_gains if g >= 1.0)
        gains = [graded_relevance(q, destinations[d]) for d in ranked]
        full = [g >= 1.0 for g in gains]
        per_query.append({
            "qid": q["qid"],
            "ndcg@10": ndcg_at_k(gains, all_gains),
            "recall@20": recall_at_k(full, n_rel),
            "mrr": reciprocal_rank(full),
            "n_relevant": n_rel,
            "origin_split": q["origin_split"],
            "type": q["type"],
        })
    result = {"overall": _mean_metrics(per_query)}
    if "by_origin" in groups:
        result["by_origin"] = {g: _mean_metrics([p for p in per_query if p["origin_split"] == g])
                               for g in sorted({p["origin_split"] for p in per_query})}
    if "by_type" in groups:
        result["by_type"] = {g: _mean_metrics([p for p in per_query if p["type"] == g])
                             for g in sorted({p["type"] for p in per_query})}
    if return_per_query:
        result["per_query"] = per_query
    return result


def _mean_metrics(rows: Sequence[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"queries": 0}
    return {
        "queries": n,
        "ndcg@10": round(sum(r["ndcg@10"] for r in rows) / n, 4),
        "recall@20": round(sum(r["recall@20"] for r in rows) / n, 4),
        "mrr": round(sum(r["mrr"] for r in rows) / n, 4),
        "avg_relevant": round(sum(r["n_relevant"] for r in rows) / n, 2),
    }


def rank_by_scores(scores: Sequence[float], corpus: Sequence[str]) -> List[str]:
    """점수 내림차순, 같으면 corpus 순서(db_order). 내장 정렬은 안정 정렬이라 동점 순서가 그대로 유지됩니다."""
    order = sorted(range(len(corpus)), key=lambda i: -scores[i])
    return [corpus[i] for i in order]


# ---------------------------------------------------------------------------
# 모델 설정 (CPU/MPS 공통)
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "intfloat/multilingual-e5-base"
# Hugging Face 저장소가 갱신돼도 같은 가중치를 쓰도록 revision(커밋 해시)을 고정합니다. (2026-10-18 확인)
MODEL_PRESETS = {
    "intfloat/multilingual-e5-base": {
        "revision": "d128750597153bb5987e10b1c3493a34e5a4502a", "license": "MIT",
        "query_prefix": "query: ", "passage_prefix": "passage: ",
    },
    "intfloat/multilingual-e5-small": {
        "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3", "license": "MIT",
        "query_prefix": "query: ", "passage_prefix": "passage: ",
    },
    # 스모크 테스트용 작은 모델 (keyword_tagger 때 받아 둔 캐시). 문장 임베딩용으로 학습된 모델은 아님.
    "klue/roberta-small": {
        "revision": "b6b4c36d827e0293ae2fcf04d527072f10a23064", "license": "CC-BY-SA-4.0",
        "query_prefix": "", "passage_prefix": "",
    },
}
# download_model.py 가 받는 파일 (onnx·openvino·pytorch_model.bin 은 받지 않음)
MODEL_FILE_PATTERNS = [
    "config.json", "model.safetensors", "modules.json", "sentence_bert_config.json", "1_Pooling/*",
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "sentencepiece.bpe.model",
    "vocab.txt", "README.md",
]
TRAIN_META_NAME = "glople_embedding_meta.json"  # 학습한 모델 폴더에 같이 저장하는 설정 (prefix, 길이, 정책 등)


def model_settings(model_name_or_dir: str, revision: Optional[str] = None) -> dict:
    """모델 이름(또는 학습해 저장한 폴더)에 맞는 revision, 질의/글 접두어.

    e5 계열은 학습 때 'query: ', 'passage: ' 접두어를 붙였으므로 쓸 때도 반드시 붙여야 합니다.
    학습한 폴더에는 TRAIN_META_NAME 파일에 원래 모델의 접두어를 남겨 둡니다.
    """
    path = Path(model_name_or_dir)
    meta_path = path / TRAIN_META_NAME
    if path.is_dir() and meta_path.exists():
        meta = read_json(meta_path)
        return {"name": str(path), "revision": None, "query_prefix": meta["query_prefix"],
                "passage_prefix": meta["passage_prefix"], "base_model": meta["base_model"],
                "base_revision": meta.get("base_revision"), "meta": meta}
    preset = MODEL_PRESETS.get(model_name_or_dir, {})
    if not preset and not path.is_dir():
        print(f"[경고] {model_name_or_dir} 는 MODEL_PRESETS 에 없어 revision 을 고정하지 않고 접두어도 붙이지 않습니다.",
              flush=True)
    return {"name": model_name_or_dir, "revision": revision or preset.get("revision"),
            "query_prefix": preset.get("query_prefix", ""), "passage_prefix": preset.get("passage_prefix", ""),
            "base_model": model_name_or_dir, "base_revision": revision or preset.get("revision"), "meta": None}


def resolve_device(name: str) -> str:
    """기본은 cpu. GPU(mps) 는 --device mps 로 직접 고를 때만 씁니다 (다른 학습이 GPU 를 쓰는 중일 수 있음)."""
    import torch
    if name == "cpu":
        return "cpu"
    if name == "mps":
        if not torch.backends.mps.is_available():
            raise SystemExit("MPS 를 쓸 수 없습니다 (Apple Silicon + MPS 빌드 torch 필요). --device cpu 로 돌리세요.")
        return "mps"
    if name == "cuda":
        if not torch.cuda.is_available():
            raise SystemExit("CUDA 를 쓸 수 없습니다.")
        return "cuda"
    raise SystemExit(f"알 수 없는 device: {name}")


def load_model(settings: dict, device: str):
    from sentence_transformers import SentenceTransformer
    kwargs = {"device": device}
    if settings.get("revision"):
        kwargs["revision"] = settings["revision"]
    return SentenceTransformer(settings["name"], **kwargs)


def encode_texts(model, texts: Sequence[str], prefix: str, max_length: int, batch_size: int = 32):
    """정규화한 임베딩(numpy float32, 행마다 길이 1). 내적 = 코사인 유사도."""
    import numpy as np
    model.max_seq_length = max_length
    emb = model.encode([prefix + t for t in texts], batch_size=batch_size, normalize_embeddings=True,
                       convert_to_numpy=True, show_progress_bar=False)
    return np.asarray(emb, dtype=np.float32)


def empty_device_cache(device: str) -> None:
    import torch
    if device == "mps":
        torch.mps.empty_cache()
    elif device == "cuda":
        torch.cuda.empty_cache()


def device_memory_str(device: str) -> str:
    import torch
    if device == "mps":
        return (f"mps 할당 {torch.mps.current_allocated_memory() / 2**30:.2f}GB / "
                f"드라이버 {torch.mps.driver_allocated_memory() / 2**30:.2f}GB")
    if device == "cuda":
        return f"cuda 할당 {torch.cuda.memory_allocated() / 2**30:.2f}GB"
    return "cpu"


# ---------------------------------------------------------------------------
# 로그 (버퍼링 때문에 로그가 사라지지 않도록)
# ---------------------------------------------------------------------------
def setup_line_buffering() -> None:
    """stdout/stderr 를 줄 단위로 내보냅니다. tee·파일로 넘겨도 진행 줄이 바로 남습니다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except AttributeError:
            pass
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


class Logger:
    """화면과 파일에 같은 줄을 바로 씁니다 (파일도 줄 단위 버퍼링)."""

    def __init__(self, path: Optional[Path] = None):
        self.file = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.file = open(path, "a", encoding="utf-8", buffering=1)

    def __call__(self, msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line, flush=True)
        if self.file:
            self.file.write(line + "\n")
            self.file.flush()

    def close(self) -> None:
        if self.file:
            self.file.close()


def format_seconds(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}"


def package_versions() -> dict:
    out = {"python": sys.version.split()[0]}
    for name in ("torch", "transformers", "sentence_transformers", "numpy"):
        try:
            module = __import__(name)
            out[name] = getattr(module, "__version__", "?")
        except ImportError:
            out[name] = None
    return out

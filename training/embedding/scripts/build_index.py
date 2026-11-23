"""여행지 전체를 임베딩해 서빙용 색인을 만듭니다: numpy 행렬(.npy) + id 사이드카(ids.json).

- embeddings.npy : float32 [여행지 수, 차원], 행마다 길이 1로 정규화 (질의 벡터와 내적 = 코사인 유사도)
- ids.json       : 행 순서와 같은 id 목록 + 이름·지역·분류(사실 필드만) + 모델·접두어·길이·설명문 정책
  설명문 원문은 넣지 않습니다. 서빙 쪽은 질의를 같은 모델·같은 접두어로 임베딩한 뒤 embeddings @ q 로 상위 k 를 고르면 됩니다.

검색 대상(--corpus)
- labeled (기본): 학습·평가에 쓴 국내 1,934곳 (data/destinations.jsonl). run_embedding.sh 가 만드는 색인
- db            : 로컬 DB 추출본(keyword_tagger/data/db_descriptions.jsonl)의 여행지 전체 = 설명문이 있는 국내 TOUR_API +
                  해외 WIKIDATA 행. 서비스(scripts/serve_search.py)가 쓰는 색인. 학습·평가 분할과 data/ 는 건드리지 않습니다.
                  여행지 글 모양은 학습 때와 같은 '이름 | 지역 | 분류 | 설명문'이고, 해외 분류는 서버 유형 코드표
                  (src/main/resources/wiki/type-keywords.csv)의 한국어 유형명입니다.

지금 로컬 DB 는 MySQL 8.4 라 VECTOR 타입이 없어, 벡터는 DB 밖 파일(사이드카)로 둡니다. 저장 방식(FAISS·DB 업그레이드 등)은 소유자 결정.

사용
  .venv/bin/python -I scripts/build_index.py --model-dir outputs/e5-base-ft/best [--out outputs/index/e5-base-ft]
  .venv/bin/python -I scripts/build_index.py --model-dir outputs/e5-base-ft/best --corpus db   # -> outputs/index/e5-base-ft-db
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import emb_common as ec  # noqa: E402

WIKI_TYPES_PATH = ec.GLOPLE_DIR / "src" / "main" / "resources" / "wiki" / "type-keywords.csv"
# prepare_data.CONTENT_TYPE_NAMES 와 같은 표 (TourAPI 관광 타입). 추출본에는 12·14만 있음
CONTENT_TYPE_NAMES = {"12": "관광지", "14": "문화시설", "15": "축제공연행사", "28": "레포츠"}
DOMESTIC_COUNTRY = "대한민국"


# ---------------------------------------------------------------------------
# 서비스 검색 대상 (--corpus db)
# ---------------------------------------------------------------------------
def load_wiki_type_labels(path: Path = WIKI_TYPES_PATH) -> List[Tuple[str, str]]:
    """서버 WikiTypeKeywords 와 같은 표: (qid, 한국어 유형명) 목록. 행 순서 = 대표 유형 우선순위. '#' 줄은 주석."""
    with open(path, encoding="utf-8") as f:
        lines = [line for line in f if line.strip() and not line.startswith("#")]
    return [(row["qid"].strip(), (row.get("ko_label") or "").strip()) for row in csv.DictReader(lines)]


def wiki_primary_label(category: Optional[str], type_labels: List[Tuple[str, str]]) -> Optional[str]:
    """category('Q11303|Q2319498')에 든 유형 중 한국어 유형명이 있고 코드표에서 가장 앞에 있는 것 (서버 primaryType 과 같은 규칙)."""
    qids = {q.strip() for q in (category or "").split("|") if q.strip()}
    for qid, label in type_labels:
        if qid in qids and label:
            return label
    return None


def scope_of_row(source: Optional[str], country: Optional[str]) -> str:
    """서버 /api/search 의 scope 와 같은 기준: TOUR_API 이거나 나라가 대한민국이면 국내, 나머지는 해외."""
    return "domestic" if source == "TOUR_API" or (country or "").strip() == DOMESTIC_COUNTRY else "overseas"


def service_destinations(export_rows: List[dict], category_table: Dict[str, dict],
                         type_labels: List[Tuple[str, str]], splits: Dict[str, str]) -> List[dict]:
    """DB 추출본 행 -> 색인할 여행지. passage_text 가 읽는 칸(title·location·category_path·description)과 화면용 칸.

    - 국내: 분류 경로 = 서버 코드표(tour/category-keywords.csv) 'depth1 > depth2 > depth3' (학습 때와 같음), 화면 분류 = depth3
    - 해외: 분류 = 위키데이터 대표 유형의 한국어 이름(없으면 관광 타입 자리에 '여행지')
    - split: 임베딩 학습·평가 분할(data/destinations.jsonl, 국내 라벨 1,934곳). train 이면 학습에 쓴 여행지, 없으면 None
    """
    out = []
    seen = set()
    for order, r in enumerate(export_rows):
        source = r.get("source")
        key = (source, r["id"])
        if key in seen:
            raise SystemExit(f"추출본에 같은 여행지가 두 번 있습니다: {key}")
        seen.add(key)
        scope = scope_of_row(source, r.get("country"))
        if source == "TOUR_API":
            cat = category_table.get((r.get("category") or "").strip())
            category_path = f"{cat['depth1']} > {cat['depth2']} > {cat['depth3']}" if cat else None
            label = cat["depth3"] if cat else None
            type_name = CONTENT_TYPE_NAMES.get((r.get("content_type_id") or "").strip(), "여행지")
        else:
            label = wiki_primary_label(r.get("category"), type_labels)
            category_path = label
            type_name = "여행지"
        out.append({
            "source": source,
            "id": r["id"],
            "title": (r.get("title") or "").strip(),
            "location": (r.get("location") or "").strip() or None,
            "country": (r.get("country") or "").strip() or None,
            "scope": scope,
            "category": label or type_name,
            "category_path": category_path,
            "content_type_name": type_name,
            "copyright_type": r.get("copyright_type"),
            "description": ec.clean_description(r.get("overview")) or None,
            "split": splits.get(r["id"]) if source == "TOUR_API" else None,
            "db_order": order,
        })
    return out


def embedding_split_of() -> Dict[str, str]:
    """국내 여행지 id -> 임베딩 분할. 태거 splits.json 에는 해외 3차도 train 에 있지만 임베딩 학습에는 국내만 썼으므로 이 표를 씁니다."""
    if not ec.DESTINATIONS_PATH.exists():
        return {}
    return {d["id"]: d["split"] for d in ec.read_jsonl(ec.DESTINATIONS_PATH)}


# ---------------------------------------------------------------------------
# 임베딩 (진행률을 줄 단위로)
# ---------------------------------------------------------------------------
def encode_with_progress(model, texts: List[str], prefix: str, max_length: int, batch_size: int, chunk: int):
    """긴 글부터 chunk 개씩 임베딩하며 진행 줄을 찍고, 원래 순서로 되돌립니다.

    sentence-transformers 도 한 번 부를 때 글 길이로 정렬하므로, 미리 전체를 길이순으로 정렬해 두면
    chunk 로 나눠도 배치 안 패딩이 거의 늘지 않습니다.
    """
    import numpy as np
    order = sorted(range(len(texts)), key=lambda i: -len(texts[i]))
    parts = []
    t0 = time.time()
    for start in range(0, len(order), chunk):
        idx = order[start:start + chunk]
        parts.append(ec.encode_texts(model, [texts[i] for i in idx], prefix, max_length, batch_size))
        done = min(start + chunk, len(order))
        elapsed = time.time() - t0
        eta = elapsed / done * (len(order) - done)
        print(f"임베딩 {done}/{len(order)} ({done / len(order):.0%}) 경과 {ec.format_seconds(elapsed)} "
              f"남은 시간 약 {ec.format_seconds(eta)}", flush=True)
    stacked = np.concatenate(parts, axis=0)
    out = np.zeros_like(stacked)
    out[np.asarray(order)] = stacked
    return out


def main() -> None:
    ec.setup_line_buffering()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-dir", required=True, help="학습한 모델 폴더 또는 Hugging Face 모델 이름(학습 전 비교용)")
    parser.add_argument("--corpus", choices=["labeled", "db"], default="labeled",
                        help="labeled: 학습·평가용 국내 1,934곳 (기본), db: DB 추출본 전체 (서비스용)")
    parser.add_argument("--out", default=None, help="기본: outputs/index/<이름> (--corpus db 면 outputs/index/<이름>-db)")
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"])
    parser.add_argument("--num-threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--chunk", type=int, default=512, help="진행 줄을 찍는 단위 (여행지 수)")
    parser.add_argument("--description-policy", choices=ec.DESCRIPTION_POLICIES, default=None)
    args = parser.parse_args()

    import numpy as np
    import torch
    torch.set_num_threads(args.num_threads)
    device = ec.resolve_device(args.device)
    model_dir = Path(args.model_dir)
    settings = ec.model_settings(args.model_dir)
    meta = settings["meta"] or {}
    name = model_dir.parent.name if model_dir.is_dir() and model_dir.name == "best" else model_dir.name.replace("/", "_")
    if args.corpus == "db":
        name += "-db"
    out_dir = Path(args.out) if args.out else ec.OUTPUTS_DIR / "index" / name
    policy = args.description_policy or meta.get("description_policy", "all")
    q_len, p_len = meta.get("max_query_len", 64), meta.get("max_passage_len", 256)

    extra = {}
    if args.corpus == "labeled":
        dests = ec.load_destinations()
        ids = sorted(dests, key=lambda i: dests[i]["db_order"])
        rows = [dests[i] for i in ids]
        items = [{"id": d["id"], "title": d["title"], "location": d["location"],
                  "category_path": d["category_path"], "split": d["split"]} for d in rows]
    else:
        if not ec.DB_EXPORT_PATH.exists():
            raise SystemExit(f"{ec.DB_EXPORT_PATH} 가 없습니다. keyword_tagger/scripts/export_from_db.py 로 만든 로컬 추출본이 필요합니다.")
        rows = service_destinations(ec.read_jsonl(ec.DB_EXPORT_PATH), ec.load_category_table(), load_wiki_type_labels(),
                                    embedding_split_of())
        items = [{k: d[k] for k in ("source", "id", "title", "location", "country", "scope", "category",
                                    "category_path", "split")} for d in rows]
        by_scope = Counter(d["scope"] for d in rows)
        extra = {
            "corpus": "db",
            "corpus_file": str(ec.DB_EXPORT_PATH.relative_to(ec.GLOPLE_DIR)),
            "corpus_sha256": ec.sha256_file(ec.DB_EXPORT_PATH),
            "by_source": dict(Counter(d["source"] for d in rows)),
            "by_scope": dict(by_scope),
            "with_description": sum(1 for d in rows if ec.uses_description(d, policy)),
            "in_embedding_splits": dict(Counter(d["split"] for d in rows if d["split"])),
        }
        print(f"검색 대상: {len(rows)}곳 (국내 {by_scope['domestic']}, 해외 {by_scope['overseas']}), 설명문 정책 {policy}",
              flush=True)

    t0 = time.time()
    model = ec.load_model(settings, device)
    texts = [ec.passage_text(d, policy) for d in rows]
    emb = encode_with_progress(model, texts, settings["passage_prefix"], p_len, args.batch_size, args.chunk)
    assert emb.shape[0] == len(items) and np.allclose(np.linalg.norm(emb, axis=1), 1.0, atol=1e-3)

    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / "embeddings.npy", emb)
    sidecar = {
        "note": "embeddings.npy 의 행 순서와 items 순서가 같습니다. 설명문 원문 없음.",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": str(model_dir), "base_model": settings["base_model"], "base_revision": settings.get("base_revision"),
        "query_prefix": settings["query_prefix"], "passage_prefix": settings["passage_prefix"],
        "max_query_len": q_len, "max_passage_len": p_len, "description_policy": policy,
        "dim": int(emb.shape[1]), "count": len(items), "dtype": "float32", "normalized": True,
        **extra,
        "items": items,
    }
    ec.write_json(out_dir / "ids.json", sidecar)
    print(f"색인 저장: {out_dir} ({len(items)}개 x {emb.shape[1]}차원, {emb.nbytes / 2**20:.1f}MB, {device}, "
          f"{time.time() - t0:.1f}s)", flush=True)


if __name__ == "__main__":
    main()

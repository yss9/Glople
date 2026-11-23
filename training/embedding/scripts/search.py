"""색인으로 자연어 취향 검색을 해 보는 작은 CLI. 설명문은 출력하지 않습니다 (이름·지역·분류·정답 키워드만).

사용
  .venv/bin/python -I scripts/search.py "조용한 바닷가 마을"
  .venv/bin/python -I scripts/search.py "경주에서 역사 유적 둘러볼 곳" --index outputs/index/e5-base-ft --top-k 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import emb_common as ec  # noqa: E402


def main() -> None:
    ec.setup_line_buffering()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", nargs="*", default=["조용한 바닷가 마을"])
    parser.add_argument("--index", default=None, help="build_index.py 가 만든 폴더 (기본: outputs/index 안에서 가장 최근 것)")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"])
    args = parser.parse_args()

    import numpy as np

    if args.index:
        index_dir = Path(args.index)
    else:
        found = sorted((ec.OUTPUTS_DIR / "index").glob("*/ids.json"), key=lambda p: p.stat().st_mtime)
        if not found:
            raise SystemExit("색인이 없습니다. 먼저 scripts/build_index.py 를 실행하세요.")
        index_dir = found[-1].parent
    side = ec.read_json(index_dir / "ids.json")
    emb = np.load(index_dir / "embeddings.npy")
    keywords = {}
    if ec.DESTINATIONS_PATH.exists():
        keywords = {d["id"]: d["keywords"] for d in ec.read_jsonl(ec.DESTINATIONS_PATH)}

    settings = ec.model_settings(side["model"], side.get("base_revision"))
    model = ec.load_model(settings, ec.resolve_device(args.device))
    query = " ".join(args.query)
    qv = ec.encode_texts(model, [query], side["query_prefix"], side["max_query_len"])[0]
    scores = emb @ qv
    top = np.argsort(-scores, kind="stable")[:args.top_k]

    print(f"질의: {query}  (색인 {index_dir.name}, 여행지 {side['count']}개, 모델 {side['model']})")
    for rank, j in enumerate(top, 1):
        item = side["items"][j]
        kw = ", ".join(keywords.get(item["id"], [])) if item.get("source", "TOUR_API") == "TOUR_API" else ""
        print(f"{rank:2d}. {scores[j]:.3f}  {item['title']}  [{item['location'] or '지역 모름'}]  "
              f"{item['category_path'] or ''}  (정답 키워드: {kw or '-'}, {item.get('split') or item.get('source') or '-'})")


if __name__ == "__main__":
    main()

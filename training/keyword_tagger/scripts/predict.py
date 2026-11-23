"""학습한 모델(outputs/<run_name>/)로 설명문 jsonl 에 키워드를 붙인다.

입력 jsonl 의 각 줄에는 id, title, overview 가 있어야 한다. 출력은 {"id","keywords"} jsonl.
임계값은 학습 때 저장한 tagger_config.json 을 그대로 쓴다.
DB 추출본 전체에 붙여 서버로 가져갈 파일을 만들 때는 scripts/predict_all.py 를 쓴다 (이 파일의 load_tagger, predict_probs 를 같이 씀).

실행: .venv/bin/python scripts/predict.py --model-dir outputs/roberta-base \
        --input data/descriptions.jsonl --output data/predicted_keywords.jsonl --device cpu
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kt_common import read_jsonl, write_jsonl  # noqa: E402


def load_tagger(model_dir: Path, device: str):
    """outputs/<run_name>/ 의 토크나이저, 모델(eval 모드), tagger_config.json, 라벨 공간, 임계값 텐서(라벨 공간 순서)."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    with open(model_dir / "tagger_config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    labels = cfg["label_space"]
    thresholds = torch.tensor([cfg["thresholds"][k] for k in labels])

    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir).to(device).eval()
    return tokenizer, model, cfg, labels, thresholds


def predict_probs(tokenizer, model, cfg: dict, rows: Sequence[dict], device: str, batch_size: int,
                  on_batch: Optional[Callable[[int, int], None]] = None) -> List:
    """rows 의 (title, overview) 마다 라벨 공간 확률(cpu float 텐서). 입력 순서 그대로 돌려준다.

    on_batch(끝낸 행 수, 전체 행 수)는 배치마다 부른다 (진행 표시용).
    """
    import torch

    out = []
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            chunk = rows[start:start + batch_size]
            enc = tokenizer([r["title"] for r in chunk], [r["overview"] for r in chunk], truncation=True,
                            max_length=cfg.get("max_length", 512), padding=True, return_tensors="pt")
            # klue/roberta 는 type_vocab_size=1 이라 BertTokenizer 가 만드는 token_type_ids(두 번째 문장=1)를 넣으면 안 된다.
            # 학습 때와 같이 input_ids, attention_mask 만 넘긴다.
            inputs = {k: enc[k].to(device) for k in ("input_ids", "attention_mask")}
            out.extend(torch.sigmoid(model(**inputs).logits.float()).cpu())
            if on_batch is not None:
                on_batch(min(start + batch_size, len(rows)), len(rows))
    return out


def selected_keywords(probs, labels: Sequence[str], thresholds) -> List[str]:
    """확률이 그 키워드의 임계값 이상인 키워드 (라벨 공간 순서 = keywords.json 순서)."""
    import torch

    return [labels[j] for j in torch.nonzero(probs >= thresholds).flatten().tolist()]


def main():
    p = argparse.ArgumentParser(description="키워드 태거 추론")
    p.add_argument("--model-dir", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--num-threads", type=int, default=4)
    args = p.parse_args()
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    import torch

    if args.num_threads > 0:
        torch.set_num_threads(args.num_threads)
    tokenizer, model, cfg, labels, thresholds = load_tagger(Path(args.model_dir), args.device)
    rows = read_jsonl(Path(args.input))

    probs = predict_probs(tokenizer, model, cfg, rows, args.device, args.batch_size)
    out = [{"id": r["id"], "keywords": selected_keywords(pr, labels, thresholds)} for r, pr in zip(rows, probs)]
    write_jsonl(Path(args.output), out)
    print(f"{len(out)}개 -> {args.output}")


if __name__ == "__main__":
    main()

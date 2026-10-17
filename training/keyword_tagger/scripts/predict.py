"""학습한 모델(outputs/<run_name>/)로 설명문 jsonl 에 키워드를 붙인다.

입력 jsonl 의 각 줄에는 id, title, overview 가 있어야 한다. 출력은 {"id","keywords"} jsonl.
임계값은 학습 때 저장한 tagger_config.json 을 그대로 쓴다.

실행: .venv/bin/python scripts/predict.py --model-dir outputs/roberta-base \
        --input data/descriptions.jsonl --output data/predicted_keywords.jsonl --device cpu
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kt_common import read_jsonl, write_jsonl  # noqa: E402


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
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    if args.num_threads > 0:
        torch.set_num_threads(args.num_threads)
    model_dir = Path(args.model_dir)
    with open(model_dir / "tagger_config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    labels = cfg["label_space"]
    thresholds = torch.tensor([cfg["thresholds"][k] for k in labels])

    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir).to(args.device).eval()
    rows = read_jsonl(Path(args.input))

    out = []
    with torch.no_grad():
        for start in range(0, len(rows), args.batch_size):
            chunk = rows[start:start + args.batch_size]
            enc = tokenizer([r["title"] for r in chunk], [r["overview"] for r in chunk], truncation=True,
                            max_length=cfg.get("max_length", 512), padding=True, return_tensors="pt")
            # klue/roberta 는 type_vocab_size=1 이라 BertTokenizer 가 만드는 token_type_ids(두 번째 문장=1)를 넣으면 안 된다.
            # 학습 때와 같이 input_ids, attention_mask 만 넘긴다.
            inputs = {k: enc[k].to(args.device) for k in ("input_ids", "attention_mask")}
            probs = torch.sigmoid(model(**inputs).logits.float()).cpu()
            for r, pr in zip(chunk, probs):
                out.append({"id": r["id"], "keywords": [labels[j] for j in torch.nonzero(pr >= thresholds).flatten().tolist()]})
    write_jsonl(Path(args.output), out)
    print(f"{len(out)}개 -> {args.output}")


if __name__ == "__main__":
    main()

"""여행지 설명문 -> 키워드 다중 라벨 분류기 학습 (Hugging Face transformers + torch).

- 입력: title 과 overview 를 문장 쌍으로 넣고 512 토큰으로 자른다.
- 출력: 키워드마다 시그모이드 확률 (problem_type=multi_label_classification, BCE 손실).
- 학습 예시가 0개인 키워드는 라벨 공간에서 빼고 결과에 기록한다 (평가 때는 항상 '없음'으로 예측).
- 임계값은 valid(1차 + 2차 배치)에서 고른다: 전역 1개(기본) + 키워드별(valid 양성이 충분할 때만, 아니면 전역으로 대체).
- 고른 임계값으로 test(1차 배치, 예전과 같은 163개)와 test_new(2차 배치, splits.json 에 있을 때)를 따로 평가한다.
  지표 함수는 기준선(scripts/baseline.py)과 같다.

결과: results/<run_name>.json (test 키는 예전 형식 그대로, test_new 키를 더함), 모델: outputs/<run_name>/ (gitignore)

예) 스모크 테스트 (CPU)
  .venv/bin/python scripts/train_keyword_tagger.py --device cpu --model klue/roberta-small --max-steps 5 --run-name smoke
예) 본 학습 (Apple GPU, 2차 배치 포함)
  .venv/bin/python scripts/train_keyword_tagger.py --device mps --run-name roberta-base-v2
"""
from __future__ import annotations

import argparse
import copy
import math
import os
import platform
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kt_common import (  # noqa: E402
    EVAL_SPLITS,
    OUTPUTS_DIR,
    RESULTS_DIR,
    ROOT,
    evaluate_sets,
    load_gold,
    load_keywords,
    load_splits,
    report_keywords,
    row_batch,
    top_frequent_keywords,
    write_json,
    write_jsonl,
)

THRESHOLD_GRID = [round(0.05 + 0.01 * i, 2) for i in range(91)]  # 0.05 ~ 0.95
# 기본 에폭 수. 1차 배치만 쓴 roberta-base(10에폭, 480 step)는 마지막 에폭까지 valid micro F1 이 올랐고
# (0.614 -> 0.622 -> 0.637), 마지막 train loss 0.114 는 키워드 빈도만 맞히는 예측의 BCE(약 0.146)보다 조금 낮을
# 뿐이라 덜 학습된 상태였다. 2차 배치를 넣으면 train 이 758 -> 1,354개라 10에폭도 850 step 이지만, 에폭마다 valid
# micro F1 이 가장 좋은 가중치를 남기므로 15에폭(약 1,275 step)으로 늘려도 지나친 학습의 위험은 작다.
# 예전 설정과 똑같이 돌리려면 --epochs 10.
DEFAULT_EPOCHS = 15


def parse_args():
    p = argparse.ArgumentParser(description="Glople 키워드 다중 라벨 분류기 학습")
    p.add_argument("--model", default="klue/roberta-base")
    p.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS,
                   help=f"기본 {DEFAULT_EPOCHS} (1차 배치만 쓰던 예전 기본값은 10)")
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--eval-batch-size", type=int, default=32)
    p.add_argument("--lr", type=float, default=5e-5)
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument("--warmup-ratio", type=float, default=0.1)
    p.add_argument("--max-length", type=int, default=512)
    p.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto",
                   help="auto 는 mps > cuda > cpu 순으로 고른다")
    p.add_argument("--num-threads", type=int, default=4,
                   help="torch CPU 스레드 수 (다른 작업과 CPU 를 나눠 쓸 때 낮게)")
    p.add_argument("--max-steps", type=int, default=0, help="0 이면 제한 없음. 스모크 테스트용")
    p.add_argument("--patience", type=int, default=0, help="valid micro F1 이 n 에폭 동안 안 오르면 중단 (0=끔)")
    p.add_argument("--min-valid-pos", type=int, default=3,
                   help="키워드별 임계값을 쓰려면 valid 양성이 이 수 이상이어야 함 (아니면 전역 임계값)")
    p.add_argument("--per-label-thresholds", action="store_true",
                   help="test 대표 결과로 키워드별 임계값을 사용 (기본은 전역 임계값, 둘 다 기록함)")
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--run-name", default=None)
    p.add_argument("--no-save-model", action="store_true", help="outputs/<run_name>/ 에 모델을 저장하지 않음")
    return p.parse_args()


def pick_device(torch, name):
    if name == "cpu":
        return torch.device("cpu")
    if name == "mps":
        if not torch.backends.mps.is_available():
            raise SystemExit("MPS 를 쓸 수 없습니다")
        return torch.device("mps")
    if name == "cuda":
        if not torch.cuda.is_available():
            raise SystemExit("CUDA 를 쓸 수 없습니다")
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def micro_f1_from_counts(tp, fp, fn):
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def tune_global_threshold(probs, labels):
    """valid micro F1 을 최대화하는 전역 임계값 하나."""
    best_t, best_f = 0.5, -1.0
    for t in THRESHOLD_GRID:
        pred = probs >= t
        tp = int((pred & labels).sum())
        fp = int((pred & ~labels).sum())
        fn = int((~pred & labels).sum())
        f = micro_f1_from_counts(tp, fp, fn)
        if f > best_f + 1e-12:
            best_t, best_f = t, f
    return best_t, best_f


def tune_per_label_thresholds(probs, labels, global_t, min_pos):
    """키워드별 F1 최대 임계값. valid 양성이 min_pos 미만이거나 F1 이 0 이면 전역 값으로 대체."""
    thresholds, source = [], []
    for j in range(probs.shape[1]):
        n_pos = int(labels[:, j].sum())
        if n_pos < min_pos:
            thresholds.append(global_t)
            source.append("fallback_few_valid_positives")
            continue
        best_t, best_f = global_t, -1.0
        for t in THRESHOLD_GRID:
            pred = probs[:, j] >= t
            tp = int((pred & labels[:, j]).sum())
            fp = int((pred & ~labels[:, j]).sum())
            fn = int((~pred & labels[:, j]).sum())
            f = micro_f1_from_counts(tp, fp, fn)
            if f > best_f + 1e-12:
                best_t, best_f = t, f
        if best_f <= 0:
            thresholds.append(global_t)
            source.append("fallback_zero_f1")
        else:
            thresholds.append(best_t)
            source.append("tuned")
    return thresholds, source


def to_keyword_sets(probs, thresholds, label_space):
    import numpy as np

    thr = np.asarray(thresholds, dtype=probs.dtype)
    out = []
    for row in probs >= thr:
        out.append([label_space[j] for j in np.nonzero(row)[0]])
    return out


def main():
    args = parse_args()
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    import numpy as np
    import torch
    import transformers
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    if args.num_threads > 0:
        torch.set_num_threads(args.num_threads)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    device = pick_device(torch, args.device)
    run_name = args.run_name or f"{args.model.split('/')[-1]}_e{args.epochs}"
    print(f"[설정] model={args.model} device={device} threads={torch.get_num_threads()} run={run_name}")

    keywords = load_keywords()
    gold = load_gold()
    splits = load_splits()
    by_id = {g["id"]: g for g in gold}
    rows = {name: [by_id[i] for i in ids] for name, ids in splits.items()}
    eval_splits = [name for name in EVAL_SPLITS if name in rows]
    top_keywords = top_frequent_keywords(gold, keywords)
    # per_keyword 는 상위 15개 + 1차 배치 상위 15개 중 빠진 것까지 (기준선 baseline.json 과 같은 목록)
    reported_keywords = report_keywords(gold, keywords)
    print("[분할] " + ", ".join(f"{name} {len(r)}" for name, r in rows.items()))

    train_freq = Counter(k for r in rows["train"] for k in r["keywords"])
    label_space = [k for k in keywords if train_freq[k] > 0]
    excluded = [k for k in keywords if train_freq[k] == 0]
    label_index = {k: j for j, k in enumerate(label_space)}
    print(f"[라벨] 학습에 쓰는 키워드 {len(label_space)}개, 학습 예시 0개라 제외 {len(excluded)}개: {', '.join(excluded)}")

    tokenizer = AutoTokenizer.from_pretrained(args.model)

    def encode(split_rows):
        enc = tokenizer(
            [r["title"] for r in split_rows],
            [r["overview"] for r in split_rows],
            truncation=True,
            max_length=args.max_length,
        )
        y = np.zeros((len(split_rows), len(label_space)), dtype=np.float32)
        for i, r in enumerate(split_rows):
            for k in r["keywords"]:
                if k in label_index:
                    y[i, label_index[k]] = 1.0
        return enc["input_ids"], y

    data = {name: encode(rows[name]) for name in rows}
    truncated = sum(1 for ids in data["train"][0] if len(ids) >= args.max_length)
    print(f"[토큰] train 평균 길이 {np.mean([len(x) for x in data['train'][0]]):.0f}, {args.max_length} 토큰에서 잘린 항목 {truncated}개")

    model = AutoModelForSequenceClassification.from_pretrained(
        args.model,
        num_labels=len(label_space),
        problem_type="multi_label_classification",
        id2label={j: k for j, k in enumerate(label_space)},
        label2id=label_index,
    )
    model.to(device)

    def batches(ids_list, y, batch_size, order):
        for start in range(0, len(order), batch_size):
            idx = order[start:start + batch_size]
            padded = tokenizer.pad({"input_ids": [ids_list[i] for i in idx]}, return_tensors="pt")
            batch = {k: v.to(device) for k, v in padded.items()}
            batch["labels"] = torch.from_numpy(y[idx]).to(device)
            yield batch

    @torch.no_grad()
    def predict(name):
        model.eval()
        ids_list, y = data[name]
        # 길이순으로 묶어 패딩을 줄이고 원래 순서로 되돌린다
        order = sorted(range(len(ids_list)), key=lambda i: len(ids_list[i]))
        probs = np.zeros_like(y)
        for start in range(0, len(order), args.eval_batch_size):
            idx = order[start:start + args.eval_batch_size]
            padded = tokenizer.pad({"input_ids": [ids_list[i] for i in idx]}, return_tensors="pt")
            logits = model(**{k: v.to(device) for k, v in padded.items()}).logits
            probs[idx] = torch.sigmoid(logits.float()).cpu().numpy()
        return probs, y > 0.5

    no_decay = ("bias", "LayerNorm.weight", "layer_norm.weight", "LayerNorm.bias")
    params = [
        {"params": [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
         "weight_decay": args.weight_decay},
        {"params": [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)],
         "weight_decay": 0.0},
    ]
    optimizer = torch.optim.AdamW(params, lr=args.lr)
    steps_per_epoch = math.ceil(len(rows["train"]) / args.batch_size)
    total_steps = steps_per_epoch * args.epochs
    if args.max_steps > 0:
        total_steps = min(total_steps, args.max_steps)
    warmup = int(total_steps * args.warmup_ratio)

    def lr_lambda(step):
        if step < warmup:
            return (step + 1) / max(1, warmup)
        return max(0.0, (total_steps - step) / max(1, total_steps - warmup))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
    rng = np.random.default_rng(args.seed)

    history = []
    best = {"valid_micro_f1": -1.0, "epoch": None, "state": None}
    global_step = 0
    stale = 0
    t0 = time.time()
    for epoch in range(1, args.epochs + 1):
        model.train()
        order = rng.permutation(len(rows["train"])).tolist()
        losses = []
        for batch in batches(*data["train"], args.batch_size, order):
            loss = model(**batch).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            losses.append(float(loss.detach().cpu()))
            global_step += 1
            if args.max_steps and global_step >= args.max_steps:
                break

        valid_probs, valid_labels = predict("valid")
        t, f = tune_global_threshold(valid_probs, valid_labels)
        history.append({"epoch": epoch, "steps": global_step, "train_loss": round(float(np.mean(losses)), 5),
                        "valid_micro_f1_at_best_global_threshold": round(f, 4), "threshold": t,
                        "elapsed_sec": round(time.time() - t0, 1)})
        print(f"[에폭 {epoch}] step {global_step} loss {np.mean(losses):.4f} valid micro F1 {f:.4f} (t={t})")
        if f > best["valid_micro_f1"]:
            best = {"valid_micro_f1": f, "epoch": epoch,
                    "state": {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}}
            stale = 0
        else:
            stale += 1
        if args.max_steps and global_step >= args.max_steps:
            print(f"[중단] --max-steps {args.max_steps} 도달")
            break
        if args.patience and stale >= args.patience:
            print(f"[중단] valid micro F1 이 {args.patience} 에폭 동안 오르지 않음")
            break

    model.load_state_dict(best["state"])
    del best["state"]

    valid_probs, valid_labels = predict("valid")
    global_t, global_valid_f1 = tune_global_threshold(valid_probs, valid_labels)
    per_label_t, per_label_src = tune_per_label_thresholds(valid_probs, valid_labels, global_t, args.min_valid_pos)
    probs = {"valid": valid_probs, **{name: predict(name)[0] for name in eval_splits}}

    def evaluate(name, thresholds):
        return evaluate_sets([r["keywords"] for r in rows[name]],
                             to_keyword_sets(probs[name], thresholds, label_space), keywords, reported_keywords)

    global_thr = [global_t] * len(label_space)
    results_by_mode = {
        mode: {name: evaluate(name, thr) for name in ["valid"] + eval_splits}
        for mode, thr in (("global", global_thr), ("per_label", per_label_t))
    }
    selected = "per_label" if args.per_label_thresholds else "global"
    other = "global" if selected == "per_label" else "per_label"
    selected_thr = per_label_t if selected == "per_label" else global_thr

    out_dir = OUTPUTS_DIR / run_name
    if not args.no_save_model:
        out_dir.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(out_dir)
        tokenizer.save_pretrained(out_dir)
        write_json(out_dir / "tagger_config.json", {
            "label_space": label_space,
            "excluded_keywords": excluded,
            "threshold_mode": selected,
            "thresholds": dict(zip(label_space, selected_thr)),
            "global_threshold": global_t,
            "max_length": args.max_length,
            "input": "tokenizer(title, overview, truncation=True) 의 input_ids + attention_mask (token_type_ids 는 넣지 않음)",
        })
        # compare.py 가 키워드별 지표를 다시 계산할 때 읽는다: test_predictions.jsonl, test_new_predictions.jsonl
        for name in eval_splits:
            pred_sets = to_keyword_sets(probs[name], selected_thr, label_space)
            write_jsonl(out_dir / f"{name}_predictions.jsonl", (
                {"id": r["id"], "title": r["title"], "gold": r["keywords"], "pred": p,
                 "missed": [k for k in r["keywords"] if k not in p], "extra": [k for k in p if k not in r["keywords"]]}
                for r, p in zip(rows[name], pred_sets)
            ))

    result = {
        "name": run_name,
        "description": f"{args.model} 다중 라벨 분류 (title+overview, BCE)",
        "config": {**vars(args), "run_name": run_name, "device_used": str(device),
                   "torch": torch.__version__, "transformers": transformers.__version__,
                   "python": platform.python_version(), "num_threads": torch.get_num_threads()},
        "split_sizes": {k: len(v) for k, v in rows.items()},
        "split_sizes_by_batch": {k: dict(sorted(Counter(str(row_batch(r)) for r in v).items())) for k, v in rows.items()},
        "label_space_size": len(label_space),
        "excluded_keywords_zero_train_examples": excluded,
        "best_epoch": best["epoch"],
        "steps": global_step,
        "train_seconds": round(time.time() - t0, 1),
        "history": history,
        "thresholds": {
            "selected_mode": selected,
            "global": global_t,
            "global_valid_micro_f1": round(global_valid_f1, 4),
            "per_label": dict(zip(label_space, per_label_t)),
            "per_label_source": dict(Counter(per_label_src)),
            "min_valid_pos": args.min_valid_pos,
        },
        "top_keywords": top_keywords,
        "eval_splits": eval_splits,
        "valid": results_by_mode[selected]["valid"],
        # test 는 1차 배치 163개 (예전 결과 파일과 같은 키), test_new 는 2차 배치 (있을 때만)
        **{name: results_by_mode[selected][name] for name in eval_splits},
        **{f"{name}_other_threshold_mode": {
            "mode": other, **{k: results_by_mode[other][name][k] for k in ("micro", "macro", "samples")}}
           for name in eval_splits},
        # 커밋되는 결과 파일에 로컬 절대경로(사용자 이름)가 남지 않도록 keyword_tagger 기준 상대경로로 적는다.
        "model_dir": os.path.relpath(out_dir, ROOT) if not args.no_save_model else None,
    }
    result_path = RESULTS_DIR / f"{run_name}.json"
    write_json(result_path, result)

    for name in eval_splits:
        m = result[name]
        print(f"[{name}/{selected}] items {m['samples']['items']} micro P {m['micro']['precision']:.3f} "
              f"R {m['micro']['recall']:.3f} F1 {m['micro']['f1']:.3f} | macro F1 {m['macro']['f1']:.3f} "
              f"| exact {m['samples']['exact_match']:.3f}")
    print(f"-> {result_path}")
    if not args.no_save_model:
        print(f"-> {out_dir}")


if __name__ == "__main__":
    main()

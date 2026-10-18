"""임베딩 모델 파인튜닝: (합성 선호 질의, 정답 여행지, 어려운 음성) 쌍 + MultipleNegativesRankingLoss.

학습이 하는 일 (한 step)
1. 배치 B개 쌍을 꺼냅니다. 질의 q_i, 정답 여행지 글 p_i, 어려운 음성 글 n_i.
2. 같은 모델로 셋을 모두 임베딩합니다 (질의는 'query: ', 글은 'passage: ' 접두어, e5 규칙).
3. 질의 q_i 마다 후보 2B개(p_1..p_B, n_1..n_B)와 코사인 유사도를 구하고 scale(=1/온도, 기본 20)을 곱해
   '정답이 p_i 인 2B지선다' 교차 엔트로피를 계산합니다. 다른 질의의 정답 p_j 가 공짜 음성(in-batch negative)이 됩니다.
   이것이 sentence-transformers 의 MultipleNegativesRankingLoss 입니다 (InfoNCE 와 같은 꼴).
4. 역전파 -> AdamW -> 학습률 스케줄(선형 warmup 후 선형 감소).

거짓 음성 가리기: p_j 가 사실 q_i 에도 완전 관련이면(예: '바다' 질의 두 개가 한 배치에) 맞는 답을 틀렸다고 가르치게 됩니다.
그래서 배치마다 [질의 x 후보] 마스크를 만들어, 질의 q_i 에 완전 관련인 남의 후보 칸은 유사도를 -inf 로 바꿔 softmax 에서 뺍니다
(MaskedCosSim 을 MNRL 의 similarity_fct 로 넘김. 마스크가 비면 원래 MNRL 과 값이 같음을 확인함). --no-false-negative-mask 로 끔.
처음에는 부딪히는 쌍을 다음 배치로 미루는 방식을 썼는데, '역사' 같은 흔한 키워드 질의가 많아 배치 32에서 배치 대부분이
절반도 안 차서(485개 중 346개) 버렸습니다. 지금 배치에서는 같은 여행지가 두 번 나오지 않게만 합니다.

MPS 메모리 안전장치 (keyword_tagger 첫 실행이 MPS 캐시 23GB + 스왑으로 멈췄던 일의 교훈)
- 패딩 길이 고정: 질의는 항상 --max-query-len, 글은 --pad-multiple 의 배수(기본 64 -> 64/128/192/256 네 가지)로만 맞춤.
  길이가 매번 다르면 MPS 가 모양마다 커널·버퍼를 새로 잡아 캐시가 계속 커집니다. --padding max 는 글도 한 가지 길이.
- 평가가 끝날 때마다, 그리고 --empty-cache-every step 마다 torch.mps.empty_cache().
- MPS 에서는 gradient checkpointing 을 기본으로 켭니다 (활성값 메모리를 줄이는 대신 약 30% 느림).
- 메모리 부족 오류가 나면 이유를 남기고 종료 코드 3 으로 끝냅니다 (run_embedding.sh 가 배치를 줄여 다시 시도).

로그: 줄 단위 버퍼링 + print(flush=True), outputs/<run>/train.log 에도 같은 줄. --log-every step 마다 손실·학습률·속도·ETA·메모리.

사용
  # CPU 스모크 테스트 (작은 모델, 5 step)
  .venv/bin/python -I scripts/train.py --device cpu --model klue/roberta-small --max-steps 5 \\
      --batch-size 8 --limit-eval 40 --run-name smoke
  # 본 학습 (GPU 가 비었을 때)
  .venv/bin/python -I scripts/train.py --device mps --run-name e5-base-ft
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import emb_common as ec  # noqa: E402
import retrieval  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default=ec.DEFAULT_MODEL)
    p.add_argument("--revision", default=None, help="기본값은 emb_common.MODEL_PRESETS 의 고정 revision")
    p.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"], help="기본 cpu. GPU 는 직접 mps 로 고름")
    p.add_argument("--run-name", default="e5-base-ft")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--warmup-ratio", type=float, default=0.1)
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument("--grad-clip", type=float, default=1.0)
    p.add_argument("--scale", type=float, default=20.0, help="MNRL 의 scale (= 1/온도)")
    p.add_argument("--max-query-len", type=int, default=64)
    p.add_argument("--max-passage-len", type=int, default=256)
    p.add_argument("--padding", choices=["bucket", "max"], default="bucket",
                   help="bucket: 글을 --pad-multiple 배수로, max: 글도 항상 --max-passage-len")
    p.add_argument("--pad-multiple", type=int, default=64)
    p.add_argument("--no-hard-negatives", action="store_true", help="어려운 음성을 빼고 in-batch 음성만")
    p.add_argument("--no-false-negative-mask", action="store_true", help="거짓 음성 가리기를 끔 (비교 실험용)")
    p.add_argument("--grad-checkpointing", choices=["auto", "on", "off"], default="auto", help="auto: mps 에서만 켬")
    p.add_argument("--freeze-embeddings", action="store_true",
                   help="단어 임베딩 행렬을 학습하지 않음 (e5-base 는 278M 중 약 192M 이 25만 토큰 임베딩이라 메모리가 크게 줄어듦)")
    p.add_argument("--description-policy", choices=ec.DESCRIPTION_POLICIES, default="all")
    p.add_argument("--log-every", type=int, default=10)
    p.add_argument("--empty-cache-every", type=int, default=50, help="MPS 캐시 비우기 간격(step), 0=평가 때만")
    p.add_argument("--eval-batch-size", type=int, default=64)
    p.add_argument("--skip-initial-eval", action="store_true", help="학습 전 valid 평가 생략")
    p.add_argument("--max-steps", type=int, default=0, help="스모크 테스트용: 이 step 수에서 멈춤")
    p.add_argument("--limit-train", type=int, default=0, help="스모크 테스트용: 학습 쌍 앞 N개만")
    p.add_argument("--limit-eval", type=int, default=0, help="스모크 테스트용: valid 질의 앞 N개만")
    p.add_argument("--num-threads", type=int, default=4, help="CPU 스레드 수")
    p.add_argument("--seed", type=int, default=ec.SEED)
    return p.parse_args()


# ---------------------------------------------------------------------------
# 배치 만들기와 거짓 음성 마스크
# ---------------------------------------------------------------------------
def build_batches(pairs, batch_size, rng, use_negatives):
    """쌍을 섞은 뒤 앞에서부터 채웁니다. 같은 여행지(정답·음성)가 한 배치에 두 번 나오는 쌍만 다음 배치로 미룹니다."""
    pending = list(pairs)
    rng.shuffle(pending)
    batches, deferred = [], set()

    def ids_of(p):
        return (p["positive_id"], p["negative_id"]) if use_negatives else (p["positive_id"],)

    def compatible(p, batch, used):
        return not any(i in used for i in ids_of(p))

    while pending:
        batch, rest, used = [], [], set()
        for idx, p in enumerate(pending):
            if len(batch) >= batch_size:
                rest.extend(pending[idx:])
                break
            if compatible(p, batch, used):
                batch.append(p)
                used.update(ids_of(p))
            else:
                rest.append(p)
                deferred.add(p["qid"])
        batches.append(batch)
        pending = rest
    return batches, len(deferred)


def false_negative_mask(batch, dests, use_negatives):
    """[B, 후보 수] bool. 후보 순서는 MNRL 과 같게 (정답 p_1..p_B, 음성 n_1..n_B). 자기 정답 칸은 절대 가리지 않습니다."""
    cand = [b["positive_id"] for b in batch] + ([b["negative_id"] for b in batch] if use_negatives else [])
    return [[j != i and ec.is_fully_relevant(q, dests[c]) for j, c in enumerate(cand)] for i, q in enumerate(batch)]


class MaskedCosSim:
    """MNRL 이 부르는 유사도 함수. 이번 배치 마스크 칸의 코사인 유사도를 -inf 로 바꿉니다 (softmax 확률 0, 기울기 0)."""

    def __init__(self):
        self.mask = None

    def __call__(self, a, b):
        from sentence_transformers.util import cos_sim
        sim = cos_sim(a, b)
        if self.mask is not None:
            if self.mask.shape != sim.shape:
                raise RuntimeError(f"마스크 모양 {tuple(self.mask.shape)} != 유사도 {tuple(sim.shape)}")
            sim = sim.masked_fill(self.mask, float("-inf"))
        return sim


# ---------------------------------------------------------------------------
def main() -> None:
    ec.setup_line_buffering()
    args = parse_args()
    out_dir = ec.OUTPUTS_DIR / args.run_name
    best_dir = out_dir / "best"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = ec.Logger(out_dir / "train.log")

    import numpy as np
    import torch
    from sentence_transformers.sentence_transformer.losses import MultipleNegativesRankingLoss
    from transformers import get_linear_schedule_with_warmup

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(args.num_threads)
    device = ec.resolve_device(args.device)

    log(f"=== train {args.run_name} 시작 ===")
    log(f"버전 {ec.package_versions()}")
    log(f"설정 {vars(args)}")

    # 데이터 ------------------------------------------------------------------
    dests = ec.load_destinations()
    pairs = ec.read_jsonl(ec.TRAIN_PAIRS_PATH)
    if args.limit_train:
        pairs = pairs[:args.limit_train]
    valid_queries = ec.load_query_set("valid")
    if args.limit_eval:
        valid_queries = valid_queries[:args.limit_eval]
    valid_corpus = ec.corpus_ids(dests, "valid")
    eval_ids = {d["id"] for d in dests.values() if d["split"] != "train"}
    leaked = {p["positive_id"] for p in pairs} | {p["negative_id"] for p in pairs}
    assert not (leaked & eval_ids), "학습 쌍에 valid/test 여행지가 있습니다. prepare_data.py 를 다시 돌리세요."
    passage = {i: ec.passage_text(d, args.description_policy) for i, d in dests.items()}
    log(f"데이터: 학습 쌍 {len(pairs)}, valid 질의 {len(valid_queries)} / 검색 대상 {len(valid_corpus)}, "
        f"설명문 정책 {args.description_policy}")

    # 모델 --------------------------------------------------------------------
    settings = ec.model_settings(args.model, args.revision)
    t0 = time.time()
    model = ec.load_model(settings, device)
    tokenizer = model.tokenizer
    qp, pp = settings["query_prefix"], settings["passage_prefix"]
    use_gc = args.grad_checkpointing == "on" or (args.grad_checkpointing == "auto" and device == "mps")
    if use_gc:
        hf_model = model[0].auto_model
        hf_model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    if args.freeze_embeddings:
        for prm in model[0].auto_model.get_input_embeddings().parameters():
            prm.requires_grad = False
    n_params = sum(p.numel() for p in model.parameters())
    n_trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log(f"모델 {args.model}@{(settings['revision'] or '-')[:12]} ({n_params / 1e6:.0f}M 파라미터, 학습 {n_trainable / 1e6:.0f}M, {device}, "
        f"접두어 {qp!r}/{pp!r}, gradient checkpointing {'켬' if use_gc else '끔'}, {time.time() - t0:.1f}s)")
    use_mask = not args.no_false_negative_mask
    masked_sim = MaskedCosSim()
    loss_fn = MultipleNegativesRankingLoss(model, scale=args.scale, similarity_fct=masked_sim)

    def tokenize(texts, max_len, fixed):
        kwargs = {"padding": "max_length"} if fixed else {"padding": True, "pad_to_multiple_of": args.pad_multiple}
        feats = tokenizer(texts, truncation=True, max_length=max_len, return_tensors="pt", **kwargs)
        return {k: v.to(device) for k, v in feats.items()}

    # 배치 계획 (스케줄러가 전체 step 수를 알아야 하므로 모든 에폭 배치를 먼저 만듦) -------------------
    rng = random.Random(args.seed)
    use_neg = not args.no_hard_negatives
    epoch_batches = []
    for epoch in range(args.epochs):
        batches, deferred = build_batches(pairs, args.batch_size, rng, use_neg)
        epoch_batches.append(batches)
        small = sum(len(b) < args.batch_size // 2 for b in batches)
        log(f"에폭 {epoch + 1} 배치 {len(batches)}개 (배치 크기 절반 미만 {small}개, "
            f"같은 여행지가 겹쳐 뒤 배치로 미룬 쌍 {deferred}개)")
    total_steps = sum(len(b) for b in epoch_batches)
    if args.max_steps:
        total_steps = min(total_steps, args.max_steps)
    # 에폭이 끝나는 step (ETA 에 남은 평가 횟수를 넣으려고)
    epoch_ends, acc = [], 0
    for b in epoch_batches:
        acc += len(b)
        epoch_ends.append(min(acc, total_steps))
        if acc >= total_steps:
            break

    no_decay = ("bias", "LayerNorm.weight", "layer_norm", "LayerNorm.bias")
    trainable = [(n, p) for n, p in model.named_parameters() if p.requires_grad]
    groups = [
        {"params": [p for n, p in trainable if not any(k in n for k in no_decay)], "weight_decay": args.weight_decay},
        {"params": [p for n, p in trainable if any(k in n for k in no_decay)], "weight_decay": 0.0},
    ]
    optimizer = torch.optim.AdamW(groups, lr=args.lr)
    scheduler = get_linear_schedule_with_warmup(optimizer, int(total_steps * args.warmup_ratio), total_steps)

    # 평가 함수 ----------------------------------------------------------------
    def evaluate_valid():
        model.eval()
        t = time.time()
        rankings = retrieval.embedding_rankings(
            model, valid_queries, valid_corpus, dests, policy=args.description_policy, query_prefix=qp,
            passage_prefix=pp, max_query_len=args.max_query_len, max_passage_len=args.max_passage_len,
            batch_size=args.eval_batch_size)
        res = ec.evaluate_rankings(valid_queries, rankings, dests, valid_corpus)
        ec.empty_device_cache(device)
        model.train()
        return res, time.time() - t

    history = []
    best = {"ndcg@10": -1.0, "epoch": None}
    eval_seconds = 0.0
    if not args.skip_initial_eval:
        res, eval_seconds = evaluate_valid()
        o = res["overall"]
        log(f"[학습 전] valid nDCG@10 {o['ndcg@10']:.4f}  R@20 {o['recall@20']:.4f}  MRR {o['mrr']:.4f} "
            f"({eval_seconds:.1f}s, {ec.device_memory_str(device)})")
        history.append({"epoch": 0, "step": 0, "valid": res})

    def save_best(epoch, step, res):
        model.save(str(best_dir))
        meta = {
            "base_model": args.model, "base_revision": settings["revision"], "query_prefix": qp, "passage_prefix": pp,
            "max_query_len": args.max_query_len, "max_passage_len": args.max_passage_len,
            "description_policy": args.description_policy, "epoch": epoch, "step": step, "valid": res["overall"],
            "train_pairs": len(pairs), "data_sha256": ec.sha256_file(ec.TRAIN_PAIRS_PATH),
            "versions": ec.package_versions(), "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        ec.write_json(best_dir / ec.TRAIN_META_NAME, meta)

    # 학습 ---------------------------------------------------------------------
    model.train()
    step, window_loss, window_n, window_masked, window_cells = 0, 0.0, 0, 0, 0
    train_start = time.time()
    stop = False
    log(f"학습 시작: 총 {total_steps} step (에폭 {args.epochs}, 배치 {args.batch_size}, lr {args.lr}, "
        f"warmup {int(total_steps * args.warmup_ratio)} step)")
    try:
        for epoch in range(1, args.epochs + 1):
            for batch in epoch_batches[epoch - 1]:
                q_feat = tokenize([qp + b["text"] for b in batch], args.max_query_len, fixed=True)
                docs = [pp + passage[b["positive_id"]] for b in batch]
                if use_neg:
                    docs += [pp + passage[b["negative_id"]] for b in batch]
                d_feat = tokenize(docs, args.max_passage_len, fixed=args.padding == "max")

                q_emb = model(q_feat)["sentence_embedding"]
                d_emb = model(d_feat)["sentence_embedding"]
                columns = [q_emb, d_emb[:len(batch)]] + ([d_emb[len(batch):]] if use_neg else [])
                if use_mask:
                    mask = torch.tensor(false_negative_mask(batch, dests, use_neg), dtype=torch.bool, device=device)
                    window_masked += int(mask.sum())
                    window_cells += mask.numel()
                    masked_sim.mask = mask
                loss = loss_fn.compute_loss_from_embeddings(columns, labels=None)
                masked_sim.mask = None

                loss.backward()
                torch.nn.utils.clip_grad_norm_([p for _, p in trainable], args.grad_clip)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)

                step += 1
                window_loss += loss.item()
                window_n += 1
                if step == 1 or step % args.log_every == 0 or step == total_steps:
                    elapsed = time.time() - train_start
                    per_step = elapsed / step
                    evals_left = sum(1 for e in epoch_ends if e >= step)
                    eta = per_step * (total_steps - step) + eval_seconds * evals_left
                    masked_pct = 100.0 * window_masked / window_cells if window_cells else 0.0
                    log(f"step {step}/{total_steps} (에폭 {epoch}) loss {window_loss / window_n:.4f} "
                        f"lr {scheduler.get_last_lr()[0]:.2e} {per_step:.2f}s/step 경과 {ec.format_seconds(elapsed)} "
                        f"ETA {ec.format_seconds(eta)} | 가린 칸 {masked_pct:.1f}% | 글 길이 {d_feat['input_ids'].shape[1]} "
                        f"| {ec.device_memory_str(device)}")
                    window_loss, window_n, window_masked, window_cells = 0.0, 0, 0, 0
                if device == "mps" and args.empty_cache_every and step % args.empty_cache_every == 0:
                    ec.empty_device_cache(device)
                if args.max_steps and step >= args.max_steps:
                    stop = True
                    break

            res, eval_seconds = evaluate_valid()
            o = res["overall"]
            improved = o["ndcg@10"] > best["ndcg@10"]
            log(f"[에폭 {epoch}] valid nDCG@10 {o['ndcg@10']:.4f}  R@20 {o['recall@20']:.4f}  MRR {o['mrr']:.4f} "
                f"({eval_seconds:.1f}s){' -> best 저장' if improved else ''} | {ec.device_memory_str(device)}")
            history.append({"epoch": epoch, "step": step, "valid": res})
            if improved:
                best = {"ndcg@10": o["ndcg@10"], "epoch": epoch, "step": step}
                save_best(epoch, step, res)
            if stop:
                log(f"--max-steps {args.max_steps} 에 닿아 멈춤")
                break
    except RuntimeError as e:
        msg = str(e)
        if "out of memory" in msg.lower() or "MPS backend out of memory" in msg:
            log(f"메모리 부족으로 멈춤 (step {step}): {msg[:300]}")
            log("--batch-size 를 줄이거나(예: 16) --max-passage-len 을 줄여 다시 돌리세요.")
            sys.exit(3)
        raise

    total_time = time.time() - train_start
    summary = {
        "run_name": args.run_name, "args": vars(args), "device": device, "versions": ec.package_versions(),
        "model": {"name": args.model, "revision": settings["revision"], "params_m": round(n_params / 1e6, 1),
                  "trainable_m": round(n_trainable / 1e6, 1)},
        "steps": step, "train_seconds": round(total_time, 1), "best": best,
        "history": [{"epoch": h["epoch"], "step": h["step"], "valid": h["valid"]["overall"]} for h in history],
        "best_dir": str(best_dir.relative_to(ec.ROOT)),
    }
    ec.write_json(out_dir / "train_summary.json", summary)
    ec.write_json(ec.RESULTS_DIR / f"{args.run_name}.train.json", summary)
    log(f"=== 끝: {step} step, {ec.format_seconds(total_time)}, best 에폭 {best['epoch']} "
        f"(valid nDCG@10 {best['ndcg@10']:.4f}) -> {best_dir.relative_to(ec.ROOT)} ===")
    log.close()


if __name__ == "__main__":
    main()

#!/bin/bash
# v2 데이터로 LoRA를 학습하고, 기본·v1·v2 모델을 v2 test와 사람 말투 평가 세트로 비교한다.
# 실행: bash training/run_v2.sh   (맥이 잠들지 않도록 caffeinate로 감싸서 돌린다)
# 실행 중에는 이 파일을 고치지 않는다. bash가 실행 중에도 파일을 이어서 읽는다.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs results

PY=.venv/bin/python
V1=adapters/qwen3-4b-tool-calling
V2=adapters/qwen3-4b-tool-calling-v2
V2_TEST=data/tool_calling_v2/test.jsonl
HUMAN=eval_sets/human_style.jsonl

step() { echo; echo "===== [$(date '+%H:%M:%S')] $*"; }

step "v2 LoRA 학습 (로그: logs/train_v2.log)"
.venv/bin/mlx_lm.lora --config configs/qwen3-4b-lora-v2.yaml 2>&1 | tee logs/train_v2.log

# 평가는 하나가 실패해도 나머지를 계속 돌린다.
step "v2 모델 - v2 test"
$PY scripts/evaluate.py --data "$V2_TEST" --name finetuned_v2_v2test --adapter "$V2" || echo "실패"
step "v2 모델 - 사람 말투 평가 세트"
$PY scripts/evaluate.py --data "$HUMAN" --name finetuned_v2_human --adapter "$V2" || echo "실패"
step "v1 모델 - v2 test"
$PY scripts/evaluate.py --data "$V2_TEST" --name finetuned_v1_v2test --adapter "$V1" || echo "실패"
step "기본 모델 - v2 test"
$PY scripts/evaluate.py --data "$V2_TEST" --name base_v2test || echo "실패"

# report.py는 항상 results/report.md에 쓰므로 비교표마다 이름을 바꿔 두고, 밤샘 v1 비교표를 되돌려 둔다.
step "비교표 생성"
$PY scripts/report.py base_v2test finetuned_v1_v2test finetuned_v2_v2test && cp results/report.md results/report_v2_test.md || echo "v2 test 비교표 실패"
$PY scripts/report.py base_human finetuned_human finetuned_v2_human && cp results/report.md results/report_human_v2.md || echo "사람 말투 비교표 실패"
cp results/report_test.md results/report.md

step "완료"

#!/bin/bash
# v3 데이터로 LoRA를 학습하고, v3 모델을 v2 test(같은 시험지)와 사람 말투 평가 세트로 기본·v1·v2 모델과 비교한다.
# 새 사람 말투 세트(eval_sets/human_style_v2.jsonl)가 있으면 네 모델을 모두 그 세트로도 평가한다.
# 실행: caffeinate -ims bash training/run_v3.sh   (맥이 잠들지 않도록)
# 실행 중에는 이 파일을 고치지 않는다. bash가 실행 중에도 파일을 이어서 읽는다.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs results

PY=.venv/bin/python
V1=adapters/qwen3-4b-tool-calling
V2=adapters/qwen3-4b-tool-calling-v2
V3=adapters/qwen3-4b-tool-calling-v3
V2_TEST=data/tool_calling_v2/test.jsonl
HUMAN=eval_sets/human_style.jsonl
HUMAN2=eval_sets/human_style_v2.jsonl

step() { echo; echo "===== [$(date '+%H:%M:%S')] $*"; }

step "v3 LoRA 학습 (로그: logs/train_v3.log)"
.venv/bin/mlx_lm.lora --config configs/qwen3-4b-lora-v3.yaml 2>&1 | tee logs/train_v3.log

# 평가는 하나가 실패해도 나머지를 계속 돌린다. v3 test 대신 v2 test로 재야 v1·v2와 같은 시험지로 비교된다.
step "v3 모델 - v2 test"
$PY scripts/evaluate.py --data "$V2_TEST" --name finetuned_v3_v2test --adapter "$V3" || echo "실패"
step "v3 모델 - 사람 말투 평가 세트"
$PY scripts/evaluate.py --data "$HUMAN" --name finetuned_v3_human --adapter "$V3" || echo "실패"

if [ -f "$HUMAN2" ]; then
  step "새 사람 말투 세트 - 기본·v1·v2·v3"
  $PY scripts/evaluate.py --data "$HUMAN2" --name base_human2 || echo "실패"
  $PY scripts/evaluate.py --data "$HUMAN2" --name finetuned_v1_human2 --adapter "$V1" || echo "실패"
  $PY scripts/evaluate.py --data "$HUMAN2" --name finetuned_v2_human2 --adapter "$V2" || echo "실패"
  $PY scripts/evaluate.py --data "$HUMAN2" --name finetuned_v3_human2 --adapter "$V3" || echo "실패"
else
  step "새 사람 말투 세트 없음 - 건너뜀"
fi

# report.py는 항상 results/report.md에 쓰므로 비교표마다 이름을 바꿔 두고, 밤샘 v1 비교표를 되돌려 둔다.
step "비교표 생성"
$PY scripts/report.py base_v2test finetuned_v1_v2test finetuned_v2_v2test finetuned_v3_v2test && cp results/report.md results/report_v3_test.md || echo "v2 test 비교표 실패"
$PY scripts/report.py base_human finetuned_human finetuned_v2_human finetuned_v3_human && cp results/report.md results/report_human_v3.md || echo "사람 말투 비교표 실패"
if [ -f "$HUMAN2" ]; then
  $PY scripts/report.py base_human2 finetuned_v1_human2 finetuned_v2_human2 finetuned_v3_human2 && cp results/report.md results/report_human2.md || echo "새 사람 말투 비교표 실패"
fi
cp results/report_test.md results/report.md

step "완료"

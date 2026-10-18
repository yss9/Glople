#!/bin/bash
# v4(M7) 다턴 데이터로 LoRA를 학습하고 두 가지를 잰다.
#   1. 첫 판단: v4 모델을 v2 test(같은 시험지)·사람 말투 세트 두 개로 평가해 기본·v1·v2·v3와 비교 (evaluate.py)
#   2. 도구 결과 뒤 행동: agent/scripts/measure_after_tools.py와 같은 문제·판정으로 v3·v4를 방식 A(일반 루프)·B(답변 전용)로 비교
#      (evaluate_after_tools.py: 문제 만들기·채점은 agent/.venv, 생성은 training/.venv의 mlx)
# 실행: caffeinate -ims bash training/run_v4.sh 2>&1 | tee training/logs/v4_pipeline.log   (맥이 잠들지 않도록)
# GPU를 쓰는 다른 일(서버·다른 학습)이 없을 때 돌린다. 실행 중에는 이 파일을 고치지 않는다(bash가 실행 중에도 파일을 이어서 읽음).
# REBUILD_V4=1이면 학습 전에 데이터를 다시 만든다(에이전트 도구 코드가 바뀌었다는 경고가 나왔을 때).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs results
export PYTHONUNBUFFERED=1   # 진행 로그를 줄 단위로 바로 남긴다

PY=.venv/bin/python
APY=../agent/.venv/bin/python
V1=adapters/qwen3-4b-tool-calling
V2=adapters/qwen3-4b-tool-calling-v2
V3=adapters/qwen3-4b-tool-calling-v3
V4=adapters/qwen3-4b-tool-calling-v4
V2_TEST=data/tool_calling_v2/test.jsonl
HUMAN=eval_sets/human_style.jsonl
HUMAN2=eval_sets/human_style_v2.jsonl

step() { echo; echo "===== [$(date '+%H:%M:%S')] $*"; }

if [ "${REBUILD_V4:-0}" = "1" ]; then
  step "v4 데이터 다시 만들기 (agent/.venv, CPU)"
  $APY scripts/build_multiturn_dataset.py
fi

# 데이터가 형식·길이·누설 검사를 통과하지 못하면 학습하지 않는다(set -e). 에이전트 코드가 바뀌었다는 경고는 멈추지 않는다.
step "v4 데이터 확인 (CPU: 형식·토큰 길이·누설·에이전트 코드 변경)"
$PY scripts/check_multiturn_dataset.py --json data/tool_calling_v4/token_stats.json

step "v4 LoRA 학습 (로그: logs/train_v4.log)"
.venv/bin/mlx_lm.lora --config configs/qwen3-4b-lora-v4.yaml 2>&1 | tee logs/train_v4.log

# 1. 첫 판단 -------------------------------------------------------------------------------------
# 평가는 하나가 실패해도 나머지를 계속 돌린다. v1~v3 결과는 이미 results/에 있다.
step "첫 판단: v4 - v2 test"
$PY scripts/evaluate.py --data "$V2_TEST" --name finetuned_v4_v2test --adapter "$V4" || echo "실패"
step "첫 판단: v4 - 사람 말투 평가 세트"
$PY scripts/evaluate.py --data "$HUMAN" --name finetuned_v4_human --adapter "$V4" || echo "실패"
step "첫 판단: v4 - 새 사람 말투 세트"
$PY scripts/evaluate.py --data "$HUMAN2" --name finetuned_v4_human2 --adapter "$V4" || echo "실패"

# report.py는 항상 results/report.md에 쓰므로 비교표마다 이름을 바꿔 두고, 밤샘 v1 비교표를 되돌려 둔다.
step "첫 판단 비교표"
$PY scripts/report.py base_v2test finetuned_v1_v2test finetuned_v2_v2test finetuned_v3_v2test finetuned_v4_v2test \
  && cp results/report.md results/report_v4_test.md || echo "v2 test 비교표 실패"
$PY scripts/report.py base_human finetuned_human finetuned_v2_human finetuned_v3_human finetuned_v4_human \
  && cp results/report.md results/report_human_v4.md || echo "사람 말투 비교표 실패"
$PY scripts/report.py base_human2 finetuned_v1_human2 finetuned_v2_human2 finetuned_v3_human2 finetuned_v4_human2 \
  && cp results/report.md results/report_human2_v4.md || echo "새 사람 말투 비교표 실패"
cp results/report_test.md results/report.md

# 2. 도구 결과 뒤 ---------------------------------------------------------------------------------
step "도구 결과 뒤: 문제 만들기 (agent/.venv, CPU)"
$APY scripts/evaluate_after_tools.py cases || echo "실패"
step "도구 결과 뒤: v3 방식 A (protocol + human2 + chain)"
$PY scripts/evaluate_after_tools.py run --name after_v3_A --adapter "$V3" --mode A || echo "실패"
step "도구 결과 뒤: v4 방식 A (protocol + human2 + chain)"
$PY scripts/evaluate_after_tools.py run --name after_v4_A --adapter "$V4" --mode A || echo "실패"
step "도구 결과 뒤: v3 방식 B (답변 전용, protocol)"
$PY scripts/evaluate_after_tools.py run --name after_v3_B --adapter "$V3" --mode B --sets protocol || echo "실패"
step "도구 결과 뒤: v4 방식 B (답변 전용, protocol)"
$PY scripts/evaluate_after_tools.py run --name after_v4_B --adapter "$V4" --mode B --sets protocol || echo "실패"
step "도구 결과 뒤: 가드 채점·비교표 (agent/.venv)"
$APY scripts/evaluate_after_tools.py score --names after_v3_A,after_v4_A,after_v3_B,after_v4_B \
  --out results/report_after_tools_v4.md || echo "실패"

step "완료"

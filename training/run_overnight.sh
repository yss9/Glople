#!/bin/bash
# 데이터 생성 → LoRA 학습 → 파인튜닝/기본 모델 평가 → 비교표 생성을 한 번에 실행한다.
# 실행: bash training/run_overnight.sh   (맥이 잠들지 않도록 caffeinate로 감싸서 돈다)
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs results

PY=.venv/bin/python
ADAPTER=adapters/qwen3-4b-tool-calling

step() { echo; echo "===== [$(date '+%H:%M:%S')] $*"; }

step "모델 폴더 준비"
$PY scripts/prepare_model.py

step "학습 데이터 생성"
$PY scripts/build_tool_dataset.py

step "LoRA 학습 시작 (로그: logs/train.log)"
.venv/bin/mlx_lm.lora --config configs/qwen3-4b-lora.yaml 2>&1 | tee logs/train.log

# 평가는 하나가 실패해도 나머지를 계속 돌린다.
step "파인튜닝 모델 평가"
$PY scripts/evaluate.py --name finetuned --adapter "$ADAPTER" || echo "finetuned 평가 실패"

step "기본 모델 평가"
$PY scripts/evaluate.py --name base || echo "base 평가 실패"

step "비교표 생성 (results/report.md)"
$PY scripts/report.py base finetuned || echo "비교표 생성 실패"

step "완료"

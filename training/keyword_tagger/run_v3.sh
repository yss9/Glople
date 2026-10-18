#!/bin/bash
# 키워드 태거 v3(국내 1·2차 + 해외 3차) 학습과 비교표. 진행은 화면과 outputs/auto_run_v3.log에 실시간으로 나온다.
# 실행: bash training/keyword_tagger/run_v3.sh
set -uo pipefail
cd "$(dirname "$0")"
mkdir -p outputs
caffeinate -ims .venv/bin/python -I scripts/train_keyword_tagger.py --device mps --run-name roberta-base-v3 2>&1 | tee outputs/auto_run_v3.log
status=${PIPESTATUS[0]}
echo "[$(date '+%H:%M:%S')] 학습 종료 코드=$status"
if [ "$status" -eq 0 ]; then
  python3 -I scripts/compare.py results/roberta-base-v3.json results/roberta-base-v2.json results/roberta-base.json > results/roberta-base-v3.compare.md
  echo "[$(date '+%H:%M:%S')] 비교표: results/roberta-base-v3.compare.md"
fi

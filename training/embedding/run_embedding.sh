#!/usr/bin/env bash
# 임베딩 파인튜닝 한 번에 돌리기 (GPU = Apple MPS)
#   데이터·모델·기준선 확인 -> MPS 학습 -> 평가·비교표 -> 색인 -> 검색 예시
#
# 실행 (GPU 를 쓰는 다른 학습이 끝난 뒤):
#   caffeinate -i bash training/embedding/run_embedding.sh
# 설정 바꾸기 (환경 변수):
#   RUN_NAME=e5-base-ft-e5 EPOCHS=5 bash training/embedding/run_embedding.sh
#   BATCH_SIZE=16 ...                       메모리가 부족할 때
#   DESCRIPTION_POLICY=none ...             설명문 없이(이름·지역·분류만) 학습
#   EXTRA_TRAIN_ARGS="--no-hard-negatives"  train.py 에 그대로 넘길 옵션
#   FORCE=1 ...                             다른 GPU 학습이 돌고 있어도 실행
#
# 로그: outputs/<RUN_NAME>/pipeline.log (모든 단계), outputs/<RUN_NAME>/train.log (학습만)
# 결과: results/<RUN_NAME>.compare.md, results/<RUN_NAME>.json, results/<RUN_NAME>.train.json
# 실행 중에는 이 파일을 고치지 마세요 (bash 는 실행 중에도 파일을 이어서 읽습니다).
set -uo pipefail
cd "$(dirname "$0")"

RUN_NAME=${RUN_NAME:-e5-base-ft}
MODEL=${MODEL:-intfloat/multilingual-e5-base}
DEVICE=${DEVICE:-mps}
EPOCHS=${EPOCHS:-3}
BATCH_SIZE=${BATCH_SIZE:-32}
LR=${LR:-2e-5}
POLICY=${DESCRIPTION_POLICY:-all}
EXTRA_TRAIN_ARGS=${EXTRA_TRAIN_ARGS:-}
PY=.venv/bin/python

export PYTHONUNBUFFERED=1          # 파이썬 출력이 버퍼에 묶여 로그가 사라지지 않도록
export TOKENIZERS_PARALLELISM=false
# MPS 메모리 상한(권장 최대치에 대한 비율). 넘으면 스왑으로 몇 시간씩 느려지는 대신 바로 메모리 부족 오류가 나고,
# 아래에서 배치를 절반으로 줄여 한 번 더 시도합니다.
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=${PYTORCH_MPS_HIGH_WATERMARK_RATIO:-0.7}
# 하한(LOW)은 상한(HIGH) 이하여야 한다. PyTorch 기본 하한(통합 메모리에서 1.4)을 그대로 두면
# 상한만 0.7로 낮췄을 때 'invalid low watermark ratio 1.4'로 학습이 시작도 못 한다(2026-10-18 첫 실행).
export PYTORCH_MPS_LOW_WATERMARK_RATIO=${PYTORCH_MPS_LOW_WATERMARK_RATIO:-0.5}

mkdir -p "outputs/$RUN_NAME" results
LOG="outputs/$RUN_NAME/pipeline.log"
say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
run() { say "\$ $*"; "$@" 2>&1 | tee -a "$LOG"; return "${PIPESTATUS[0]}"; }
fail() { say "실패: $*"; exit 1; }

say "=== run_embedding.sh 시작 (RUN_NAME=$RUN_NAME MODEL=$MODEL DEVICE=$DEVICE EPOCHS=$EPOCHS BATCH_SIZE=$BATCH_SIZE LR=$LR POLICY=$POLICY) ==="
[ -x "$PY" ] || fail "가상환경이 없습니다. README 의 '설치' 절대로 training/embedding/.venv 를 만드세요."

# 0. GPU 를 쓰는 다른 학습이 돌고 있으면 멈춤 (메모리를 나눠 쓰면 둘 다 스왑으로 느려짐)
if [ "$DEVICE" = mps ] && [ "${FORCE:-0}" != 1 ]; then
  others=$(pgrep -fl 'mlx_lm|train_keyword_tagger.py|embedding/scripts/train.py' || true)
  if [ -n "$others" ]; then
    say "GPU 를 쓰는 다른 작업이 있습니다. 끝난 뒤 다시 실행하거나 FORCE=1 을 주세요:"
    say "$others"
    exit 2
  fi
fi

# 1. 데이터 (없을 때만 만듦. 시드 고정이라 다시 만들어도 같은 파일)
if [ ! -f data/train_pairs.jsonl ]; then
  run "$PY" -I scripts/prepare_data.py || fail "prepare_data.py"
fi

# 2. 기본 모델 (캐시에 없을 때만 내려받음, 그 뒤로는 오프라인)
if ! HF_HUB_OFFLINE=1 "$PY" -I scripts/download_model.py --model "$MODEL" >/dev/null 2>&1; then
  run "$PY" -I scripts/download_model.py --model "$MODEL" || fail "모델 내려받기"
fi
export HF_HUB_OFFLINE=1

# 3. 학습 전 기준선 (없을 때만, CPU 약 1분)
if [ ! -f results/baseline.json ]; then
  run "$PY" -u -I scripts/baseline.py --device cpu || fail "baseline.py"
fi

# 4. 학습 (메모리 부족이면 배치를 절반으로 줄여 한 번 더)
train() {
  # shellcheck disable=SC2086
  run "$PY" -u -I scripts/train.py --device "$DEVICE" --model "$MODEL" --run-name "$RUN_NAME" \
      --epochs "$EPOCHS" --batch-size "$1" --lr "$LR" --description-policy "$POLICY" $EXTRA_TRAIN_ARGS
}
SECONDS=0
train "$BATCH_SIZE"
status=$?
if [ "$status" -eq 3 ] && [ "$BATCH_SIZE" -gt 8 ]; then
  half=$((BATCH_SIZE / 2))
  say "메모리 부족 -> 배치 $half 로 다시 학습"
  train "$half"
  status=$?
fi
[ "$status" -eq 0 ] || fail "train.py (종료 코드 $status)"
[ -d "outputs/$RUN_NAME/best" ] || fail "outputs/$RUN_NAME/best 가 없습니다"
say "학습 끝 ($((SECONDS / 60))분)"

# 5. 평가와 비교표
run "$PY" -u -I scripts/evaluate.py --model-dir "outputs/$RUN_NAME/best" --device "$DEVICE" || fail "evaluate.py"

# 6. 서빙용 색인 (여행지 전체)
run "$PY" -u -I scripts/build_index.py --model-dir "outputs/$RUN_NAME/best" --device "$DEVICE" \
    --out "outputs/index/$RUN_NAME" || fail "build_index.py"

# 7. 검색 예시
: > "outputs/$RUN_NAME/search_demo.txt"
for q in "조용한 바닷가 마을" "경주에서 역사 유적 둘러볼 곳" "아이랑 가기 좋은 동물 체험" "단풍 보러 산에 가고 싶어"; do
  "$PY" -I scripts/search.py "$q" --index "outputs/index/$RUN_NAME" --device "$DEVICE" 2>/dev/null \
    | tee -a "outputs/$RUN_NAME/search_demo.txt" "$LOG"
done

say "=== 끝: results/$RUN_NAME.compare.md, outputs/$RUN_NAME/train.log, outputs/index/$RUN_NAME/ ==="

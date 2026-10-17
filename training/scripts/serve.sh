#!/bin/bash
# 파인튜닝한 도구 호출 모델을 OpenAI 호환 API(/v1/chat/completions)로 띄운다. Apple Silicon(MLX) 전용.
# 사용: bash scripts/serve.sh [어댑터 경로] [포트]
#   bash scripts/serve.sh                                   # v1 어댑터, 8090 포트
#   bash scripts/serve.sh adapters/qwen3-4b-tool-calling-v2 # v2 어댑터
# 응답의 choices[0].message.tool_calls에 도구 호출이 담긴다. 확인: .venv/bin/python scripts/check_server.py
set -euo pipefail
cd "$(dirname "$0")/.."

ADAPTER=${1:-adapters/qwen3-4b-tool-calling}
PORT=${2:-8090}

exec .venv/bin/mlx_lm.server \
  --model models/qwen3-4b-instruct-2507-4bit \
  --adapter-path "$ADAPTER" \
  --host 127.0.0.1 --port "$PORT" \
  --temp 0.0 --max-tokens 256

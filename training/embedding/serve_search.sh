#!/usr/bin/env bash
# '문장으로 여행지 찾기' 검색 서버 (scripts/serve_search.py, 127.0.0.1:8096, CPU, 읽기 전용)
#
#   bash training/embedding/serve_search.sh          앞에서 실행 (Ctrl+C 로 끔)
#   bash training/embedding/serve_search.sh start    뒤에서 실행 (nohup). 로그 outputs/logs/serve_search.log, PID outputs/serve_search.pid
#   bash training/embedding/serve_search.sh stop     뒤에서 띄운 서버 끄기
#   bash training/embedding/serve_search.sh status   떠 있는지, 색인 개수
#
# 설정 (환경 변수): INDEX=outputs/index/e5-base-ft-db  PORT=8096  NUM_THREADS=2
# 색인이 없으면 먼저: HF_HUB_OFFLINE=1 .venv/bin/python -I scripts/build_index.py --model-dir outputs/e5-base-ft/best --corpus db
set -uo pipefail
cd "$(dirname "$0")"

INDEX=${INDEX:-outputs/index/e5-base-ft-db}
PORT=${PORT:-8096}
NUM_THREADS=${NUM_THREADS:-2}
PY=.venv/bin/python
LOG=outputs/logs/serve_search.log
PID_FILE=outputs/serve_search.pid
HEALTH="http://127.0.0.1:$PORT/semantic-api/v1/health"

export HF_HUB_OFFLINE=1            # 모델은 로컬 폴더(outputs/e5-base-ft/best)에서만 읽음
export TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1

[ -x "$PY" ] || { echo "가상환경이 없습니다. README 의 '설치' 절대로 training/embedding/.venv 를 만드세요."; exit 1; }
[ -f "$INDEX/ids.json" ] || { echo "색인이 없습니다: $INDEX (위 build_index.py --corpus db 를 먼저 실행)"; exit 1; }

running_pid() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid=$(cat "$PID_FILE")
  if kill -0 "$pid" 2>/dev/null && ps -p "$pid" -o command= | grep -q 'scripts/serve_search.py'; then
    echo "$pid"
    return 0
  fi
  return 1
}

port_busy() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; }

case "${1:-run}" in
  run)
    exec "$PY" -u -I scripts/serve_search.py --index "$INDEX" --port "$PORT" --num-threads "$NUM_THREADS"
    ;;
  start)
    if pid=$(running_pid); then echo "이미 떠 있습니다 (PID $pid)"; exit 0; fi
    if port_busy; then echo "포트 $PORT 를 다른 프로그램이 쓰고 있습니다."; exit 1; fi
    mkdir -p "$(dirname "$LOG")"
    nohup "$PY" -u -I scripts/serve_search.py --index "$INDEX" --port "$PORT" --num-threads "$NUM_THREADS" \
      >>"$LOG" 2>&1 </dev/null &
    echo $! >"$PID_FILE"
    # 모델을 읽는 데 10~20초 걸림. 준비될 때까지 기다림 (최대 120초)
    for _ in $(seq 1 120); do
      if curl -sf -m 2 "$HEALTH" >/dev/null 2>&1; then
        echo "떴습니다: PID $(cat "$PID_FILE"), 로그 $LOG"
        curl -s -m 2 "$HEALTH"; echo
        exit 0
      fi
      if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "서버가 바로 꺼졌습니다. 로그를 보세요: $LOG"; tail -20 "$LOG"; rm -f "$PID_FILE"; exit 1
      fi
      sleep 1
    done
    echo "120초 안에 준비되지 않았습니다. 로그를 보세요: $LOG"; exit 1
    ;;
  stop)
    if pid=$(running_pid); then kill "$pid" && rm -f "$PID_FILE" && echo "껐습니다 (PID $pid)"; else echo "떠 있지 않습니다"; fi
    ;;
  status)
    if pid=$(running_pid); then echo "떠 있음 (PID $pid)"; curl -s -m 2 "$HEALTH"; echo; else echo "떠 있지 않음"; exit 1; fi
    ;;
  *)
    echo "사용: $0 [run|start|stop|status]"; exit 2
    ;;
esac

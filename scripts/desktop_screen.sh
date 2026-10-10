#!/bin/bash
# 한 모델을 PERFORMANCE 26 의 자로 잰다 — 2026-09-26 claude. 사용자가 "qwen 125b 가 로컬에서 가장 최적인 것 같다" 고 해서
# 결선에 오른 qwen3.8:27b 와 같은 항목으로 잰다. FA 는 지금 앱과 같은 조건(ADR-064: Ollama 기본값)이다 — 26 의 다른
# 후보는 FA off 로 쟀으므로 속도는 그대로 비교하지 않는다. 품질 항목은 같은 질문·같은 채점이다.
#
#   bash scripts/desktop_screen.sh MODEL screen   # 배치·속도, replay n=3, fresh n=2, 학습 시점   (약 30분)
#   bash scripts/desktop_screen.sh MODEL finals   # 어려운 파이썬 6, RTL 6, 정체성 n=2, 웹 7×2     (약 40~60분)
#
# 안전장치(26-8 사고, 사용자: "fan 돌아가는 소리 들어보면 터질거 같이 들려서 불안한데"):
#   - GPU 가 TEMP_LIMIT(83°C) 이상이면 멈춘다.
#   - ollama-server.log 에 새 'CUDA error' 가 찍히면 멈추고 Ollama 를 끈다 — GPU 를 잃은 Ollama 가 큰 모델을 RAM 에만
#     다시 올리다 PC 가 멈춘 것이 26-8 이다. 끈 뒤에는 ALEX 를 다시 켜야 한다.
# 모든 단계는 ALEX 가 5분 쉬어야 시작하고, 사용자가 대화하면 그 단계를 멈추고 다시 한다(stage 스크립트와 같다).
set -uo pipefail
MODEL=${1:?model}
PHASE=${2:?screen or finals}
J=/mnt/c/ALEX/jarvis
PY=/mnt/c/Users/<user>/AppData/Local/Programs/Python/Python311/python.exe
LOG=$J/logs/claude-desktop-screen-20260926.txt
CTX=16384
TEMP_LIMIT=${TEMP_LIMIT:-83}
FROZEN='C:\Users\<user>\AppData\Local\Temp\alex-replay-source'
TAG=${MODEL//:/_}
DONE=$J/logs/desktop-screen-done/$TAG
STOP=$DONE/SAFETY_STOP
CHILD_PID_FILE=$DONE/.child
export ALEX_QUIET_SECONDS=300
export WSLENV="ALEX_QUIET_SECONDS${WSLENV:+:$WSLENV}"
ISOLATION="JARVIS_DESKTOP_NUM_CTX=$CTX JARVIS_WEB_TOOL=0 JARVIS_AUTO_LESSONS=0 JARVIS_LESSON_CONTEXT_LIMIT=0"
ISOLATION_WSLENV="JARVIS_DESKTOP_NUM_CTX:JARVIS_WEB_TOOL:JARVIS_AUTO_LESSONS:JARVIS_LESSON_CONTEXT_LIMIT"
cd "$J" || exit 1
mkdir -p "$DONE"
rm -f "$STOP"

echo "=== screen $PHASE $MODEL start $(date '+%F %T') ctx=$CTX temp_limit=${TEMP_LIMIT}C" >> "$LOG"
grep 'msg="server config"' logs/ollama-server.log | tail -1 | grep -o 'OLLAMA_FLASH_ATTENTION:[a-z]*' | sed 's/^/    ollama env (unset = --flash-attn auto): /' >> "$LOG"
if ! env JARVIS_WEB_TOOL=0 JARVIS_AUTO_LESSONS=0 JARVIS_LESSON_CONTEXT_LIMIT=0 \
  WSLENV=JARVIS_WEB_TOOL:JARVIS_AUTO_LESSONS:JARVIS_LESSON_CONTEXT_LIMIT \
  "$PY" -X utf8 scripts/check_benchmark_settings.py >> "$LOG" 2>&1; then
  echo "    ABORT: benchmark isolation flags did not take effect" >> "$LOG"
  exit 1
fi

kill_child() {
  local pid; pid=$(cat "$CHILD_PID_FILE" 2>/dev/null || true)
  if [ -n "$pid" ]; then kill -TERM "$pid" 2>/dev/null || true; fi
}

# 안전 감시: 5초마다 GPU 온도와 Ollama 로그의 CUDA 오류를 본다.
MAIN=$$
(
  base=$(grep -c 'CUDA error' logs/ollama-server.log)
  while kill -0 "$MAIN" 2>/dev/null; do
    t=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -dc '0-9')
    if [ -n "$t" ] && [ "$t" -ge "$TEMP_LIMIT" ]; then
      echo "    SAFETY STOP $(date '+%T'): GPU ${t}C >= ${TEMP_LIMIT}C" >> "$LOG"; touch "$STOP"; kill_child; exit 0
    fi
    now=$(grep -c 'CUDA error' logs/ollama-server.log)
    if [ "$now" -gt "$base" ]; then
      echo "    SAFETY STOP $(date '+%T'): new CUDA error in ollama-server.log - stopping Ollama so it cannot reload the model into RAM only (26-8)" >> "$LOG"
      touch "$STOP"; kill_child
      /mnt/c/Windows/System32/taskkill.exe /IM ollama.exe /F >> "$LOG" 2>&1
      /mnt/c/Windows/System32/taskkill.exe /IM llama-server.exe /F >> "$LOG" 2>&1
      exit 0
    fi
    sleep 5
  done
) &
WATCHER=$!
trap 'kill "$WATCHER" 2>/dev/null; kill_child' EXIT

unload() {
  "$PY" -X utf8 -c "
import json, urllib.request
for m in json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/ps')).get('models', []):
    r = urllib.request.Request('http://127.0.0.1:11434/api/generate', data=json.dumps({'model': m['name'], 'keep_alive': 0}).encode(), headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(r).read(); print('unloaded', m['name'])
"
  sleep 3
}
routes() { "$PY" -X utf8 logs/probe/alex_idle.py count | tr -d '\r'; }

run_step() {
  local label=$1; shift
  local attempt before after rc marker child
  marker=$DONE/$(printf '%s' "$label" | sed 's/[^A-Za-z0-9]/_/g')
  if [ -f "$marker" ]; then echo "    already clean: $label" >> "$LOG"; return 0; fi
  for attempt in 1 2; do
    [ -f "$STOP" ] && { echo "    stopped before: $label" >> "$LOG"; exit 2; }
    "$PY" -X utf8 logs/probe/alex_idle.py wait >> "$LOG" 2>&1
    unload >> "$LOG" 2>&1
    before=$(routes)
    echo "--- $MODEL :: $label (attempt $attempt) $(date '+%T') gpu=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits | head -1)C" >> "$LOG"
    "$@" >> "$LOG" 2>&1 &
    child=$!; echo "$child" > "$CHILD_PID_FILE"
    rc=0
    while kill -0 "$child" 2>/dev/null; do
      sleep 2
      if [ "$(routes)" != "$before" ]; then
        echo "    USER CHAT: stopping this attempt so ALEX can answer" >> "$LOG"
        kill -TERM "$child" 2>/dev/null || true
        break
      fi
    done
    wait "$child" || rc=$?
    rm -f "$CHILD_PID_FILE"
    [ -f "$STOP" ] && { echo "    SAFETY STOP during: $label" >> "$LOG"; exit 2; }
    after=$(routes)
    if [ "$after" != "$before" ]; then continue; fi
    if [ "$rc" -ne 0 ]; then
      echo "    FAILED: exit=$rc $(date '+%T') - skipped" >> "$LOG"
      return 0
    fi
    touch "$marker"
    echo "    clean $(date '+%T') gpu=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits | head -1)C" >> "$LOG"
    return 0
  done
  echo "    FAILED: both attempts overlapped user chat - skipped" >> "$LOG"
}

isolated() { env JARVIS_DESKTOP_NUM_CTX=$CTX JARVIS_WEB_TOOL=0 JARVIS_AUTO_LESSONS=0 JARVIS_LESSON_CONTEXT_LIMIT=0 WSLENV=$ISOLATION_WSLENV "$@"; }

case $PHASE in
  screen)
    run_step "offload ctx=$CTX" "$PY" -X utf8 scripts/measure_offload.py --model "$MODEL" --num-ctx "$CTX" \
      --turns 5 --think --timeout 900 --out "logs/offload-desktop-fadefault-$TAG-$CTX.json"
    run_step "replay n=3" isolated env JARVIS_DATA_DIR="$FROZEN" WSLENV=$ISOLATION_WSLENV:JARVIS_DATA_DIR \
      "$PY" -X utf8 logs/probe/capture_done.py --sidecar "logs/replay-desktop-replay-$TAG-done.json" \
      scripts/replay_session.py replay --model "$MODEL" -n 3 --out "logs/replay-desktop-replay-$TAG.json"
    run_step "fresh n=2" isolated "$PY" -X utf8 logs/probe/capture_done.py --sidecar "logs/replay-desktop-fresh-$TAG-done.json" \
      scripts/replay_session.py fresh --model "$MODEL" -n 2 --out "logs/replay-desktop-fresh-$TAG.json"
    run_step "knowledge cutoff" isolated "$PY" -X utf8 logs/probe/knowledge_cutoff.py --model "$MODEL" \
      --out "logs/cutoff-desktop-$TAG.json" ;;
  finals)
    run_step "python hard" "$PY" -X utf8 scripts/grade_python.py --execute --only-hard -n 1 --think \
      --model "$MODEL" --num-ctx "$CTX" --num-predict 6144 --timeout 1800 --out "logs/python-grade-desktop-$TAG.json"
    run_step "rtl" cmd.exe /c "set PATH=C:\\oss-cad-suite\\shim;C:\\oss-cad-suite\\bin;C:\\oss-cad-suite\\lib;%PATH% && C:\\Users\\<user>\\AppData\\Local\\Programs\\Python\\Python311\\python.exe -X utf8 scripts\\grade_rtl.py --execute -n 1 --think --model $MODEL --num-ctx $CTX --num-predict 6144 --timeout 1800 --out logs\\rtl-grade-desktop-$TAG.json"
    run_step "identity n=2" "$PY" -X utf8 scripts/grade_identity.py --run -n 2 --model "$MODEL" \
      --num-ctx "$CTX" --timeout 1800 --out "logs/identity-desktop-$TAG.json"
    run_step "web x2" isolated "$PY" -X utf8 logs/probe/web_live_desktop.py --passes 2 --model "$MODEL" \
      --out "logs/web-live-desktop-$TAG.json" ;;
  *) echo "phase must be screen or finals" >&2; exit 1 ;;
esac
unload >> "$LOG" 2>&1
echo "=== screen $PHASE $MODEL end $(date '+%F %T')" >> "$LOG"

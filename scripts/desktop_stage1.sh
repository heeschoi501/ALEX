#!/bin/bash
# Stage 1 (screening, PERFORMANCE 26) v2: every step waits until ALEX is idle and is re-run once
# if the user chatted during it (the user switched ALEX to 120b and chatted during phase 0).
set -euo pipefail
J=/mnt/c/ALEX/jarvis
PY=/mnt/c/Users/<user>/AppData/Local/Programs/Python/Python311/python.exe
LOG=$J/logs/claude-desktop-measure-20260925.txt
CTX=32768   # provisional, via env only - jarvis.local.json is not touched by the measurement
FROZEN='C:\Users\<user>\AppData\Local\Temp\alex-replay-source'
export ALEX_QUIET_SECONDS=300  # the user is actively trying models in ALEX
DONE=$J/logs/desktop-stage1-done
cd "$J" || exit 1
mkdir -p "$DONE"
until grep -q "ALL DONE 2" logs/claude-pull-candidates-20260925.txt 2>/dev/null; do sleep 10; done

ACTIVE_CHILD=''
cleanup() {
  if [ -n "$ACTIVE_CHILD" ]; then kill -TERM "$ACTIVE_CHILD" 2>/dev/null || true; fi
}
trap cleanup EXIT

unload() {
  "$PY" -X utf8 -c "
import json, urllib.request
ps = json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/ps'))
for m in ps.get('models', []):
    r = urllib.request.Request('http://127.0.0.1:11434/api/generate', data=json.dumps({'model': m['name'], 'keep_alive': 0}).encode(), headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(r).read(); print('unloaded', m['name'])
"
  sleep 3
}
routes() { "$PY" -X utf8 logs/probe/alex_idle.py count | tr -d '\r'; }

# run_step LABEL CMD... : wait for idle, unload, run; re-run once if user requests appeared meanwhile.
run_step() {
  local label=$1; shift
  local attempt before after rc key marker contaminated
  key=$(printf '%s' "$label" | sed 's/[^A-Za-z0-9]/_/g')
  marker=$DONE/$key
  if [ -f "$marker" ]; then echo "    already clean: $label" >> "$LOG"; return 0; fi
  for attempt in 1 2; do
    "$PY" -X utf8 logs/probe/alex_idle.py wait >> "$LOG" 2>&1
    unload >> "$LOG" 2>&1
    before=$(routes)
    echo "--- $label (attempt $attempt) $(date '+%T')" >> "$LOG"
    rc=0
    contaminated=0
    "$@" >> "$LOG" 2>&1 &
    ACTIVE_CHILD=$!
    while kill -0 "$ACTIVE_CHILD" 2>/dev/null; do
      sleep 2
      after=$(routes)
      if [ "$after" != "$before" ]; then
        echo "    USER CHAT: stopping this attempt so ALEX can answer" >> "$LOG"
        kill -TERM "$ACTIVE_CHILD" 2>/dev/null || true
        wait "$ACTIVE_CHILD" 2>/dev/null || true
        ACTIVE_CHILD=''
        contaminated=1
        break
      fi
    done
    if [ "$contaminated" -eq 1 ]; then continue; fi
    wait "$ACTIVE_CHILD" || rc=$?
    ACTIVE_CHILD=''
    after=$(routes)
    if [ "$rc" -ne 0 ]; then
      echo "    FAILED: exit=$rc $(date '+%T')" >> "$LOG"
      return "$rc"
    fi
    if [ "$after" = "$before" ]; then
      touch "$marker"
      echo "    clean $(date '+%T')" >> "$LOG"
      return 0
    fi
    echo "    CONTAMINATED: $((after - before)) user chat request(s) during this step" >> "$LOG"
  done
  echo "    FAILED: both attempts overlapped user chat" >> "$LOG"
  return 1
}

echo "=== stage1 start $(date '+%F %T') ctx=$CTX" >> "$LOG"
for model in qwen3.8:27b gemma4:31b muse-glimmer:30b qwen3.5:122b; do
  tag=${model//:/_}
  for ctx in 16384 32768; do
    run_step "offload $model ctx=$ctx" "$PY" -X utf8 scripts/measure_offload.py --model "$model" --num-ctx "$ctx" \
      --turns 5 --think --timeout 900 --out "logs/offload-desktop-$tag-$ctx.json"
    echo "nvidia-smi used: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)" >> "$LOG"
  done
done

for model in gpt-oss:20b gpt-oss:120b qwen3.8:27b gemma4:31b muse-glimmer:30b qwen3.5:122b; do
  tag=${model//:/_}
  run_step "$model ctx=$CTX :: replay n=3" env JARVIS_DESKTOP_NUM_CTX=$CTX JARVIS_DATA_DIR="$FROZEN" \
    WSLENV=JARVIS_DESKTOP_NUM_CTX:JARVIS_DATA_DIR "$PY" -X utf8 logs/probe/capture_done.py \
    --sidecar "logs/replay-desktop-replay-$tag-done.json" scripts/replay_session.py replay --model "$model" -n 3 \
    --out "logs/replay-desktop-replay-$tag.json"
  run_step "$model ctx=$CTX :: fresh n=2" env JARVIS_DESKTOP_NUM_CTX=$CTX WSLENV=JARVIS_DESKTOP_NUM_CTX \
    "$PY" -X utf8 logs/probe/capture_done.py --sidecar "logs/replay-desktop-fresh-$tag-done.json" \
    scripts/replay_session.py fresh --model "$model" -n 2 --out "logs/replay-desktop-fresh-$tag.json"
  run_step "$model ctx=$CTX :: knowledge cutoff" env JARVIS_DESKTOP_NUM_CTX=$CTX WSLENV=JARVIS_DESKTOP_NUM_CTX \
    "$PY" -X utf8 logs/probe/knowledge_cutoff.py --model "$model" --out "logs/cutoff-desktop-$tag.json"
done
unload >> "$LOG" 2>&1
echo "=== stage1 end $(date '+%F %T')" >> "$LOG"

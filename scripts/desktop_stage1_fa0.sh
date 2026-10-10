#!/bin/bash
# Stage 1 again, with flash attention OFF (ADR-054) and the app's ctx 16384 (PERFORMANCE 26-1).
# desktop_stage1.sh ran with FA on; the app no longer runs that way. Its files stay as evidence and
# this run writes new names (offload-desktop-fa0-*) so the two conditions never mix.
# Order: FA check -> lesson A/B (ADR-055) -> placement/speed for all six -> quality for all six.
# A failed step is logged and skipped - one crash must not stop a run that takes hours.
# Every step waits until ALEX has been idle ALEX_QUIET_SECONDS and is redone once if the user chats.
set -uo pipefail
J=/mnt/c/ALEX/jarvis
PY=/mnt/c/Users/<user>/AppData/Local/Programs/Python/Python311/python.exe
LOG=$J/logs/claude-desktop-measure-20260925.txt
CTX=16384
FROZEN='C:\Users\<user>\AppData\Local\Temp\alex-replay-source'
LESSONS='C:\Users\<user>\AppData\Local\Temp\alex-replay-lessons'
# ADR-056 이후 앱은 모델에게 web_search 도구를 준다. 이 측정의 기준(PERFORMANCE 26)과 이미 끝난 20b A/B 는
# 도구가 없던 조건이고, 학습 시점 측정은 모델 자체 지식을 재므로 검색하면 안 된다 — 측정 단계는 JARVIS_WEB_TOOL=0.
export ALEX_QUIET_SECONDS=300
# WSL 이 Windows 프로그램에 넘기는 변수는 WSLENV 에 적힌 것뿐이다. 없으면 alex_idle.py 는 기본 60초로 돈다
# (2026-09-25 에 실제로 그랬다: 'idle after waiting 80s').
export WSLENV="ALEX_QUIET_SECONDS${WSLENV:+:$WSLENV}"
DONE=$J/logs/desktop-stage1-done-fa0
cd "$J" || exit 1
mkdir -p "$DONE"

# The server config line Ollama printed at its last start is the only proof of the FA setting it runs with.
fa=$(grep 'msg="server config"' logs/ollama-server.log | tail -1 | grep -o 'OLLAMA_FLASH_ATTENTION:[a-z]*')
echo "=== stage1-fa0 start $(date '+%F %T') ctx=$CTX $fa" >> "$LOG"
if [ "$fa" != "OLLAMA_FLASH_ATTENTION:false" ]; then
  echo "    ABORT: Ollama is not running with FA off ($fa). Restart ALEX (앱 종료 -> 실행) first." >> "$LOG"
  exit 1
fi
if ! env JARVIS_WEB_TOOL=0 JARVIS_AUTO_LESSONS=0 JARVIS_LESSON_CONTEXT_LIMIT=0 \
  WSLENV=JARVIS_WEB_TOOL:JARVIS_AUTO_LESSONS:JARVIS_LESSON_CONTEXT_LIMIT \
  "$PY" -X utf8 scripts/check_benchmark_settings.py >> "$LOG" 2>&1; then
  echo "    ABORT: benchmark isolation flags did not take effect" >> "$LOG"
  exit 1
fi
crash_base=$(grep -c '0xc0000409' logs/ollama-server.log)

ACTIVE_CHILD=''
cleanup() { if [ -n "$ACTIVE_CHILD" ]; then kill -TERM "$ACTIVE_CHILD" 2>/dev/null || true; fi; }
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
      echo "    FAILED: exit=$rc $(date '+%T') - skipped, run continues" >> "$LOG"
      return 0
    fi
    if [ "$after" = "$before" ]; then
      touch "$marker"
      echo "    clean $(date '+%T')" >> "$LOG"
      return 0
    fi
    echo "    CONTAMINATED: $((after - before)) user chat request(s) during this step" >> "$LOG"
  done
  echo "    FAILED: both attempts overlapped user chat - skipped" >> "$LOG"
  return 0
}

replay() {  # replay MODEL DATA_DIR OUT_TAG — 교훈은 교훈 효과를 재는 [replay-lessons] 에서만 켠다(ADR-057)
  local model=$1 data=$2 tag=$3 lessons=0
  [ "$tag" = "replay-lessons" ] && lessons=3
  run_step "$model ctx=$CTX :: replay n=3 [$tag]" env JARVIS_DESKTOP_NUM_CTX=$CTX JARVIS_DATA_DIR="$data" \
    JARVIS_WEB_TOOL=0 JARVIS_AUTO_LESSONS=0 JARVIS_LESSON_CONTEXT_LIMIT=$lessons \
    WSLENV=JARVIS_DESKTOP_NUM_CTX:JARVIS_DATA_DIR:JARVIS_WEB_TOOL:JARVIS_AUTO_LESSONS:JARVIS_LESSON_CONTEXT_LIMIT \
    "$PY" -X utf8 logs/probe/capture_done.py \
    --sidecar "logs/replay-desktop-$tag-${model//:/_}-done.json" scripts/replay_session.py replay --model "$model" -n 3 \
    --out "logs/replay-desktop-$tag-${model//:/_}.json"
}

# 1. Lesson A/B (ADR-055): same frozen conversation, same question (#143). The only difference is that
#    the copy has the train-split corrections approved - in the copy only; the user's DB is untouched.
"$PY" -X utf8 logs/probe/approve_in_copy.py "$FROZEN" "$LESSONS" a07390fb f8310271 41c7b471 ea613d5e 2a2f5ade >> "$LOG" 2>&1
for model in gpt-oss:20b gpt-oss:120b; do
  replay "$model" "$FROZEN" replay
  replay "$model" "$LESSONS" replay-lessons
done

# 2. Placement and speed with FA off. 20b/120b also at 8192 so criterion 2 (PERFORMANCE 26) compares like with like.
for spec in gpt-oss:20b:8192 gpt-oss:20b:16384 gpt-oss:20b:32768 gpt-oss:120b:8192 gpt-oss:120b:16384 gpt-oss:120b:32768 \
            qwen3.8:27b:16384 qwen3.8:27b:32768 gemma4:31b:16384 gemma4:31b:32768 \
            muse-glimmer:30b:16384 muse-glimmer:30b:32768 qwen3.5:122b:16384 qwen3.5:122b:32768; do
  model=${spec%:*}; ctx=${spec##*:}
  run_step "offload-fa0 $model ctx=$ctx" "$PY" -X utf8 scripts/measure_offload.py --model "$model" --num-ctx "$ctx" \
    --turns 5 --think --timeout 900 --out "logs/offload-desktop-fa0-${model//:/_}-$ctx.json"
  echo "nvidia-smi used: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)" >> "$LOG"
done

# 3. Quality screening for all six (20b/120b replay already done above).
for model in gpt-oss:20b gpt-oss:120b qwen3.8:27b gemma4:31b muse-glimmer:30b qwen3.5:122b; do
  tag=${model//:/_}
  case $model in gpt-oss:*) ;; *) replay "$model" "$FROZEN" replay ;; esac
  run_step "$model ctx=$CTX :: fresh n=2" env JARVIS_DESKTOP_NUM_CTX=$CTX JARVIS_WEB_TOOL=0 JARVIS_AUTO_LESSONS=0 JARVIS_LESSON_CONTEXT_LIMIT=0 WSLENV=JARVIS_DESKTOP_NUM_CTX:JARVIS_WEB_TOOL:JARVIS_AUTO_LESSONS:JARVIS_LESSON_CONTEXT_LIMIT \
    "$PY" -X utf8 logs/probe/capture_done.py --sidecar "logs/replay-desktop-fresh-$tag-done.json" \
    scripts/replay_session.py fresh --model "$model" -n 2 --out "logs/replay-desktop-fresh-$tag.json"
  run_step "$model ctx=$CTX :: knowledge cutoff" env JARVIS_DESKTOP_NUM_CTX=$CTX JARVIS_WEB_TOOL=0 JARVIS_AUTO_LESSONS=0 JARVIS_LESSON_CONTEXT_LIMIT=0 WSLENV=JARVIS_DESKTOP_NUM_CTX:JARVIS_WEB_TOOL:JARVIS_AUTO_LESSONS:JARVIS_LESSON_CONTEXT_LIMIT \
    "$PY" -X utf8 logs/probe/knowledge_cutoff.py --model "$model" --out "logs/cutoff-desktop-$tag.json"
done
unload >> "$LOG" 2>&1

# ADR-054 re-check 1: any FA-kernel crash while FA is off refutes the hypothesis.
crashes=$(( $(grep -c '0xc0000409' logs/ollama-server.log) - crash_base ))
echo "    llama-server 0xc0000409 during this run: $crashes (ADR-054: must be 0 with FA off)" >> "$LOG"
echo "=== stage1-fa0 end $(date '+%F %T')" >> "$LOG"

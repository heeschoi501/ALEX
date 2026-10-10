#!/bin/bash
# Stage 2 (finals, PERFORMANCE 26): 20b baseline gets every item + B (calculator n=10);
# each finalist passed as an argument gets python-hard, RTL, identity n=2, web x2.
# Usage: stage2.sh [finalist ...]
set -euo pipefail
J=/mnt/c/ALEX/jarvis
PY=/mnt/c/Users/<user>/AppData/Local/Programs/Python/Python311/python.exe
LOG=$J/logs/claude-desktop-measure-20260925.txt
CTX=16384   # the app runs 16384 since PERFORMANCE 26-1; stage 2 grades under the same condition
# ADR-056 이후 앱은 모델에게 web_search 도구를 준다. 이 측정의 기준(PERFORMANCE 26)과 이미 끝난 20b A/B 는
# 도구가 없던 조건이고, 학습 시점 측정은 모델 자체 지식을 재므로 검색하면 안 된다 — 측정 단계는 JARVIS_WEB_TOOL=0.
export ALEX_QUIET_SECONDS=300
# WSL 이 Windows 프로그램에 넘기는 변수는 WSLENV 에 적힌 것뿐이다. 없으면 alex_idle.py 는 기본 60초로 돈다
# (2026-09-25 에 실제로 그랬다: 'idle after waiting 80s').
export WSLENV="ALEX_QUIET_SECONDS${WSLENV:+:$WSLENV}"
DONE=$J/logs/desktop-stage2-done-fa0
cd "$J" || exit 1
mkdir -p "$DONE"

# 품질 결과도 stage1 과 같은 FA off 서버에서만 비교한다.
# grep 이어야 한다 — 데스크톱 WSL 에는 rg 가 없어 빈 값이 되고 곧바로 ABORT 한다(2026-09-26 claude, stage1 과 같은 명령).
fa=$(grep 'msg="server config"' logs/ollama-server.log | tail -1 | grep -o 'OLLAMA_FLASH_ATTENTION:[a-z]*' || true)
if [ "$fa" != "OLLAMA_FLASH_ATTENTION:false" ]; then
  echo "    ABORT stage2: Ollama is not running with FA off ($fa)" >> "$LOG"
  exit 1
fi
if ! env JARVIS_WEB_TOOL=0 JARVIS_AUTO_LESSONS=0 JARVIS_LESSON_CONTEXT_LIMIT=0 \
  WSLENV=JARVIS_WEB_TOOL:JARVIS_AUTO_LESSONS:JARVIS_LESSON_CONTEXT_LIMIT \
  "$PY" -X utf8 scripts/check_benchmark_settings.py >> "$LOG" 2>&1; then
  echo "    ABORT stage2: benchmark isolation flags did not take effect" >> "$LOG"
  exit 1
fi
# 2026-09-26 데스크톱: Smart App Control 이 서명 없는 C:\oss-cad-suite\bin\vvp.exe 를 막아 RTL 이 첫 시뮬레이션에서
# WinError 4551 로 죽었고 set -e 로 결선 전체가 멈췄다. 한 번 띄워 보고 막혀 있으면 RTL 만 건너뛴다 — 완료 표시를
# 남기지 않으므로 풀린 뒤 이 스크립트를 다시 돌리면 RTL 만 잰다.
# pipefail 아래에서는 vvp 의 종료 코드가 판정을 바꾸지 않게 출력만 본다. RTL 단계와 같은 PATH 로 띄운다 — lib 가
# 없으면 풀린 뒤에도 DLL 을 못 찾아(0xC0000135) 막힌 것처럼 보인다(Smart App Control 을 끈 뒤 실측).
vvp_version=$(cmd.exe /c "set PATH=C:\\oss-cad-suite\\shim;C:\\oss-cad-suite\\bin;C:\\oss-cad-suite\\lib;%PATH% && C:\\oss-cad-suite\\bin\\vvp.exe -V" 2>&1 || true)
if grep -q 'Icarus Verilog' <<<"$vvp_version"; then
  RTL_OK=1
else
  RTL_OK=0
  echo "    RTL blocked: vvp.exe did not start (Smart App Control?) - RTL steps will be skipped" >> "$LOG"
fi

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
finals() {
  local model=$1 tag=${1//:/_}
  run_step "$model :: python hard" "$PY" -X utf8 scripts/grade_python.py --execute --only-hard -n 1 --think \
    --model "$model" --num-ctx "$CTX" --num-predict 6144 --timeout 1800 --out "logs/python-grade-desktop-$tag.json"
  if [ "$RTL_OK" = 1 ]; then
    run_step "$model :: rtl" cmd.exe /c "set PATH=C:\\oss-cad-suite\\shim;C:\\oss-cad-suite\\bin;C:\\oss-cad-suite\\lib;%PATH% && C:\\Users\\<user>\\AppData\\Local\\Programs\\Python\\Python311\\python.exe -X utf8 scripts\\grade_rtl.py --execute -n 1 --think --model $model --num-ctx $CTX --num-predict 6144 --timeout 1800 --out logs\\rtl-grade-desktop-$tag.json"
  else
    echo "    RTL SKIPPED for $model (vvp.exe blocked) - no done marker; rerun stage2 after allowing it" >> "$LOG"
  fi
  run_step "$model :: identity n=2" "$PY" -X utf8 scripts/grade_identity.py --run -n 2 --model "$model" \
    --num-ctx "$CTX" --timeout 1800 --out "logs/identity-desktop-$tag.json"
  run_step "$model :: web x2" env JARVIS_DESKTOP_NUM_CTX=$CTX JARVIS_WEB_TOOL=0 JARVIS_AUTO_LESSONS=0 JARVIS_LESSON_CONTEXT_LIMIT=0 WSLENV=JARVIS_DESKTOP_NUM_CTX:JARVIS_WEB_TOOL:JARVIS_AUTO_LESSONS:JARVIS_LESSON_CONTEXT_LIMIT \
    "$PY" -X utf8 logs/probe/web_live_desktop.py --passes 2 --model "$model" --out "logs/web-live-desktop-$tag.json"
}

echo "=== stage2 start $(date '+%F %T') finalists: $*" >> "$LOG"
finals gpt-oss:20b
run_step "gpt-oss:20b :: B calculator n=10" "$PY" -X utf8 scripts/grade_codegen.py --execute -n 10 --think \
  --model gpt-oss:20b --num-ctx "$CTX" --num-predict 7000 --timeout 900 --out logs/codegen-gptoss20b.json
for model in "$@"; do finals "$model"; done
unload >> "$LOG" 2>&1
echo "=== stage2 end $(date '+%F %T')" >> "$LOG"

#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "Python 3 not found. Install Python 3.10+ first." >&2
  exit 127
fi
export JARVIS_USE_OLLAMA=1
export JARVIS_FILE_SEARCH_ROOT="${JARVIS_FILE_SEARCH_ROOT:-$PWD}"
"$PYTHON" scripts/check_environment.py || echo "[WARN] Environment check reported a problem. Web Alpha will still start for diagnostics."
printf '\nOpen http://127.0.0.1:8765 in your browser.\n'
"$PYTHON" -m jarvis.web --port 8765

#!/usr/bin/env bash
# Mac/Linux: ./start.sh        (thêm --mock để dùng AI giả lập, không cần model)
cd "$(dirname "$0")" || exit 1
PY=python3
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"
exec "$PY" start.py "$@"

#!/usr/bin/env bash
# =====================================================================
# 9Router OpenCode Dual-Gate Patch Launcher for Linux & macOS
# =====================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check for python3
if command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PY_BIN="python"
else
    echo "Error: Python 3 is required to run this patcher." >&2
    exit 1
fi

exec "$PY_BIN" "$SCRIPT_DIR/patch_9router.py" "$@"

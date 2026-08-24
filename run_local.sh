#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

set -a
source "$SCRIPT_DIR/.env"
set +a

if command -v xvfb-run >/dev/null 2>&1; then
    xvfb-run -a "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/check_stock.py"
else
    "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/check_stock.py"
fi

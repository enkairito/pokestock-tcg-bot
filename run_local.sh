#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

set -a
source "$SCRIPT_DIR/.env"
set +a

"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/check_stock.py"

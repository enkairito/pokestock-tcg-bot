"""Cuenta cuántas veces ha pasado cada producto de no_disponible a
disponible/invitación en los últimos 7 días.

No existe un histórico dedicado de precios/estado — pero state.json se
commitea a git cada vez que cambia (ver check_stock.yml), así que cada
commit es efectivamente una foto fechada. Este script explota eso en vez
de mantener un fichero de histórico aparte: camina los commits recientes
que tocaron state.json, en orden cronológico, y cuenta las transiciones
no_disponible -> (compra_directa|invitacion) por producto.
"""
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_PATH = "state.json"
OUTPUT_FILE = Path(__file__).parent / "restock_stats.json"
WINDOW_DAYS = 7
# Margen extra al pedir el log: para saber si el primer commit dentro de
# la ventana de 7 días fue o no un restock hace falta conocer el estado
# justo antes — sin esto, el primer commit visto siempre parecería "sin
# cambio" al no tener con qué compararlo.
FETCH_WINDOW_DAYS = WINDOW_DAYS + 3


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def commit_log():
    since = (datetime.now(timezone.utc) - timedelta(days=FETCH_WINDOW_DAYS)).strftime("%Y-%m-%d")
    out = git("log", "--since", since, "--format=%H %aI", "--reverse", "--", STATE_PATH)
    commits = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, ts = line.split(" ", 1)
        commits.append((sha, datetime.fromisoformat(ts)))
    return commits


def state_at(sha):
    try:
        content = git("show", f"{sha}:{STATE_PATH}")
    except subprocess.CalledProcessError:
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def main():
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    counts = defaultdict(int)
    prev_status = {}

    for sha, ts in commit_log():
        snapshot = state_at(sha)
        for key, info in snapshot.items():
            status = info.get("status")
            was = prev_status.get(key)
            if ts >= cutoff and status in ("compra_directa", "invitacion") and was == "no_disponible":
                counts[key] += 1
            prev_status[key] = status

    OUTPUT_FILE.write_text(json.dumps(dict(counts), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ {len(counts)} productos con restocks detectados en los últimos {WINDOW_DAYS} días.")


if __name__ == "__main__":
    main()

"""Comprueba ejecuciones y datos publicados sin enviar mensajes ni modificar estado."""
import json
import os
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen

SOURCES = {
    "stock": ("Pokémon", "products.json", 1),
    "onepiece": ("One Piece", "onepiece.json", 1),
    "magic": ("Magic", "magic.json", 6),
    "lorcana": ("Lorcana", "lorcana.json", 6),
    "yugioh": ("Yu-Gi-Oh!", "yugioh.json", 6),
    "accessories": ("Accesorios", "accesorios.json", 24),
}


def evaluate(runs, snapshot, hours, now):
    problems = []
    completed = [run for run in runs if run.get("status") == "completed"]
    if len(completed) >= 2 and all(run.get("conclusion") != "success" for run in completed[:2]):
        problems.append("Dos o más ejecuciones consecutivas sin éxito")
    successful = next((run for run in completed if run.get("conclusion") == "success"), None)
    limit = (hours * 2 + 0.5) * 3600
    def check_date(value, label):
        try:
            age = (now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds()
            if age < -300 or age > limit:
                problems.append(f"{label}: fuera del margen de actualización")
        except (ValueError, TypeError, AttributeError):
            problems.append(f"{label}: fecha ausente o inválida")
    check_date(successful.get("updated_at") if successful else None, "Última ejecución correcta")
    check_date(snapshot.get("updated_at"), "Datos publicados")
    if not isinstance(snapshot.get("products"), list):
        problems.append("Listado de productos inválido")
    return problems


def get_json(url, token=None):
    headers = {"Accept": "application/json", "User-Agent": "wts-health-monitor"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    now = datetime.now(timezone.utc)
    lines = ["# Salud del stock", "", "Juego | Resultado", "--- | ---"]
    failed = False
    for key, (label, filename, hours) in SOURCES.items():
        try:
            data = get_json(f"https://api.github.com/repos/{repository}/actions/workflows/check_{key}.yml/runs?branch=main&per_page=30", os.environ["GH_TOKEN"])
            # La fecha de accesorios debe pertenecer a su scraper diario,
            # no a la última aportación horaria de One Piece.
            snapshot = get_json(f"https://wheresthatstock.com/{filename}?health={int(now.timestamp())}")
            if key == "accessories":
                snapshot = {**snapshot, "updated_at": snapshot.get("source_updates", {}).get("accessories")}
            problems = evaluate(data["workflow_runs"], snapshot, hours, now)
        except Exception as error:
            problems = [f"No se pudo comprobar la fuente ({type(error).__name__})"]
        if problems:
            failed = True
            print(f"::error title=Stock {label}::{' / '.join(problems)}")
        lines.append(f"{label} | {'; '.join(problems) if problems else 'Correcto'}")
    report = "\n".join(lines) + "\n"
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(report)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())

"""Reglas compartidas sin navegador, credenciales ni llamadas de red."""
import json
import re
from datetime import datetime, timezone

ALERT_STATUSES = ("compra_directa", "invitacion", "preventa")
PRICE_NUMBER_RE = re.compile(r"(\d+(?:\.\d{3})*),(\d{2})")


def price_to_float(value):
    match = PRICE_NUMBER_RE.search(value or "")
    return float(f"{match[1].replace('.', '')}.{match[2]}") if match else None


def alert_changes(current, previous):
    status = current["status"]
    status_changed = status in ALERT_STATUSES and status != previous.get("status")
    current_stock, previous_stock = current.get("stock"), previous.get("stock")
    stock_decreased = (status in ALERT_STATUSES and current_stock is not None
                       and previous_stock is not None and int(current_stock) < int(previous_stock))
    current_price, previous_price = price_to_float(current.get("price")), price_to_float(previous.get("price"))
    price_decreased = (status == "compra_directa" and current_price is not None
                       and previous_price is not None and current_price < previous_price)
    return status_changed, stock_decreased, price_decreased


def snapshot_entry(info, game=None):
    row = {key: info.get(key) for key in (
        "asin", "store_label", "flag", "name", "image", "price", "original_price",
        "status", "stock", "link", "first_seen",
    )}
    row["marketplace"] = info["marketplace_code"]
    row["categories"] = info.get("categories", [])
    if game:
        row["game"] = game
    return row


def write_snapshot(path, products, game=None):
    snapshot = {"updated_at": datetime.now(timezone.utc).isoformat(),
                "products": [snapshot_entry(info, game) for info in products.values()]}
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

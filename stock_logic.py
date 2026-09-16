"""Reglas compartidas sin navegador, credenciales ni llamadas de red."""
import json
import re
from datetime import datetime, timezone

ALERT_STATUSES = ("compra_directa", "invitacion", "preventa")
PRICE_NUMBER_RE = re.compile(r"(\d+(?:\.\d{3})*),(\d{2})")
PRICE_INCREASE_CONFIRMATIONS = 2
MIN_PRICE_DROP_PERCENT = 0.01
MIN_PRICE_DROP_EUR = 2.0


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
    price_drop_amount = ((previous_price - current_price)
                         if current_price is not None and previous_price is not None
                         else None)
    price_drop_percent = ((price_drop_amount / previous_price)
                          if price_drop_amount is not None and previous_price > 0
                          else None)
    # Evita avisos por oscilaciones de céntimos: basta superar el 1 % del
    # precio anterior o alcanzar una diferencia absoluta de 2 €.
    price_decreased = (status == "compra_directa" and price_drop_amount is not None
                       and price_drop_amount > 0
                       and (price_drop_percent > MIN_PRICE_DROP_PERCENT
                            or price_drop_amount >= MIN_PRICE_DROP_EUR))
    return status_changed, stock_decreased, price_decreased


def confirmed_price_fields(current_price, previous, confirmations=PRICE_INCREASE_CONFIRMATIONS):
    """Devuelve los campos de precio que deben persistirse en el estado.

    Las bajadas se confirman al instante, pero una subida debe repetirse en
    varias comprobaciones consecutivas. Así, un precio alto que Amazon
    muestre durante una sola ejecución no se convierte en la referencia y
    su vuelta al precio real no genera una falsa alerta de bajada.
    """
    previous_price = previous.get("price")
    current_value = price_to_float(current_price)
    previous_value = price_to_float(previous_price)

    # Una lectura sin precio no debe borrar una referencia válida.
    if current_value is None:
        return {"price": previous_price if previous_value is not None else current_price}

    # El primer precio, una bajada real o la vuelta al precio confirmado se
    # aceptan inmediatamente y descartan cualquier subida pendiente.
    if previous_value is None or current_value <= previous_value:
        return {"price": current_price}

    pending_price = previous.get("pending_price")
    pending_value = price_to_float(pending_price)
    pending_count = previous.get("pending_price_count", 0)
    if pending_value == current_value:
        pending_count += 1
    else:
        pending_price = current_price
        pending_count = 1

    if pending_count >= confirmations:
        return {"price": current_price}

    return {
        "price": previous_price,
        "pending_price": pending_price,
        "pending_price_count": pending_count,
    }


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

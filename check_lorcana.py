import asyncio
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from patchright.async_api import async_playwright

from check_stock import (
    ALERT_STATUSES,
    DRY_RUN,
    STATUS_COPY,
    _strip_accents,
    check_single_product,
    discover_products,
    is_excluded_by_name,
    merge_product_record,
    new_context,
    price_to_float,
    watermark_product_image,
)

TELEGRAM_LORCANA_BOT_TOKEN = os.environ["TELEGRAM_LORCANA_BOT_TOKEN"]
TELEGRAM_LORCANA_CHAT_ID = os.environ["TELEGRAM_LORCANA_CHAT_ID"]
LORCANA_WEBSITE_URL = "https://wheresthatstock.com/lorcana"

# A diferencia de Magic, la tienda de marca de Lorcana en Amazon ES SÍ tiene
# una página de productos directa (búsqueda "lorcana tcg" dentro de la
# tienda de Ravensburger, el editor) — confirmado 2026-09-07: 48 elementos
# [data-asin], 25 nombres comprobados a mano, todos productos reales de
# Lorcana (sobres, mazos de inicio, Illumineer's Trove, cajas/álbumes).
LORCANA_MARKETPLACE = {
    "code": "ES",
    "domain": "amazon.es",
    "flag": "🇪🇸",
    "store_label": "Amazon ES",
    "tag": "enkairito-21",
    "cookies_file": Path(__file__).parent / "amazon_cookies.json",
    "invitation_marker": "invitaci",
    "interstitial_marker": "haz clic en el botón de abajo",
    "continue_button_pattern": "text=/seguir comprando/i",
    "stock_count_re": re.compile(r"queda\(?n?\)?\s+(\d+)\s+en stock", re.IGNORECASE),
    "pages": [
        ("Novedades", "https://www.amazon.es/stores/page/342835D1-D062-4B14-BC90-DB7A97BC8053/search?terms=lorcana%20tcg"),
    ],
    "allow_individual_fallback": True,
    "exclude_out_of_stock": True,
}

STATE_FILE = Path(__file__).parent / "state_lorcana.json"
SNAPSHOT_FILE = Path(__file__).parent / "lorcana_snapshot.json"


def categorize_lorcana(name):
    """Reglas (por orden de prioridad), a partir de los 25 nombres reales
    comprobados a mano el 2026-09-07:
    1. Mazo Inicial: "starter deck"/"mazo de inicio"/"deck" junto con
       "jugador(es)" (ej. "Deck para 2 jugadores").
    2. Caja de Sobres: "display" (booster box).
    3. Sobre: "booster", o "sobre" si además menciona Lorcana.
    4. Otros: menciona Lorcana pero no encaja arriba (cubre Illumineer's
       Trove, sets de regalo, álbumes/cajas de almacenamiento — de
       momento sin categoría propia, revisar si conviene una vez se vea
       el reparto real en producción).
    Si no menciona Lorcana en absoluto, se descarta (return None)."""
    text = _strip_accents((name or "").lower())
    mentions_lorcana = "lorcana" in text

    if "starter deck" in text or "mazo de inicio" in text or ("deck" in text and "jugador" in text):
        return "Mazo Inicial"
    if "display" in text:
        return "Caja de Sobres"
    if "booster" in text or ("sobre" in text and mentions_lorcana):
        return "Sobre"
    if mentions_lorcana:
        return "Otros"
    return None


def load_state():
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _telegram_post(method, data, files=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_LORCANA_BOT_TOKEN}/{method}"
    resp = requests.post(url, data=data, files=files, timeout=15)
    resp.raise_for_status()


def send_telegram_message(text):
    _telegram_post(
        "sendMessage",
        {"chat_id": TELEGRAM_LORCANA_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "false"},
    )


def send_telegram_photo(photo_url, caption):
    _telegram_post(
        "sendPhoto",
        {"chat_id": TELEGRAM_LORCANA_CHAT_ID, "photo": photo_url, "caption": caption, "parse_mode": "HTML"},
    )


def send_telegram_photo_bytes(image_bytes, caption):
    _telegram_post(
        "sendPhoto",
        {"chat_id": TELEGRAM_LORCANA_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("product.jpg", image_bytes, "image/jpeg")},
    )


def save_snapshot(products):
    snapshot = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "products": [
            {
                "asin": info["asin"],
                "marketplace": info["marketplace_code"],
                "store_label": info["store_label"],
                "flag": info["flag"],
                "name": info["name"],
                "image": info.get("image"),
                "price": info.get("price"),
                "original_price": info.get("original_price"),
                "status": info["status"],
                "stock": info.get("stock"),
                "link": info["link"],
                "first_seen": info.get("first_seen"),
                "categories": info["categories"],
                "game": "Lorcana",
            }
            for info in products.values()
        ],
    }
    SNAPSHOT_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    state = load_state()
    lorcana_products = {}
    marketplace = LORCANA_MARKETPLACE

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await new_context(browser)
        page = await context.new_page()

        marketplace_products = {}
        fallback_asins = set()

        for label, url in marketplace["pages"]:
            print(f"🔍 Descubriendo productos de Disney Lorcana ({label})...")
            try:
                page_products, page_fallback = await discover_products(page, label, url, marketplace)
                print(f"📦 [{label}] {len(page_products)} productos encontrados")
                for asin, info in page_products.items():
                    marketplace_products[asin] = merge_product_record(marketplace_products.get(asin), info)
                fallback_asins.update(page_fallback)
            except Exception as e:
                print(f"❌ No se pudo cargar la página de Disney Lorcana ({label}): {e!r}")

        fallback_asins -= marketplace_products.keys()
        if fallback_asins:
            print(f"🔎 Comprobando individualmente {len(fallback_asins)} productos sin datos en la tarjeta...")
            for i, asin in enumerate(fallback_asins):
                if i > 0:
                    await asyncio.sleep(2)
                result = await check_single_product(page, asin, marketplace)
                if result:
                    marketplace_products[asin] = result

        out_of_stock = {a for a, i in marketplace_products.items() if i["status"] == "no_disponible"}
        if out_of_stock:
            print(f"⏭️ Omitiendo {len(out_of_stock)} productos agotados de la web.")
            for asin in out_of_stock:
                key = f"{marketplace['code']}:{asin}"
                prev_first_seen = state.get(key, {}).get("first_seen")
                state[key] = {
                    "name": marketplace_products[asin]["name"],
                    "status": "no_disponible",
                    "stock": None,
                    "first_seen": prev_first_seen or datetime.now(timezone.utc).isoformat(),
                }
                del marketplace_products[asin]

        for asin, info in marketplace_products.items():
            if is_excluded_by_name(info["name"]):
                continue
            info["asin"] = asin
            info["marketplace_code"] = marketplace["code"]
            info["store_label"] = marketplace["store_label"]
            info["flag"] = marketplace["flag"]
            info["link"] = f"https://www.{marketplace['domain']}/dp/{asin}?tag={marketplace['tag']}"

            category = categorize_lorcana(info["name"])
            if category is None:
                print(f"⏭️ Sin categoría reconocida, se descarta: {info['name'][:70]}")
                continue
            info["categories"] = [category]
            lorcana_products[f"{marketplace['code']}:{asin}"] = info

        await browser.close()

    if not lorcana_products:
        print("❌ No se encontró ningún producto de Disney Lorcana.")
        sys.exit(1)

    print(f"📦 Total combinado: {len(lorcana_products)} productos de Disney Lorcana")

    for key, info in lorcana_products.items():
        status = info["status"]
        name = info["name"]
        prev = state.get(key, {})
        prev_status = prev.get("status")
        prev_stock = prev.get("stock")
        prev_price = prev.get("price")
        first_seen = prev.get("first_seen") or datetime.now(timezone.utc).isoformat()
        info["first_seen"] = first_seen

        status_changed = status in ALERT_STATUSES and status != prev_status
        stock_decreased = (
            status in ALERT_STATUSES
            and info.get("stock") is not None
            and prev_stock is not None
            and int(info["stock"]) < int(prev_stock)
        )
        current_price_num = price_to_float(info.get("price"))
        prev_price_num = price_to_float(prev_price)
        price_decreased = (
            status == "compra_directa"
            and current_price_num is not None
            and prev_price_num is not None
            and current_price_num < prev_price_num
        )

        send_failed = False

        if status_changed or stock_decreased or price_decreased:
            safe_name = html.escape(name)
            link = html.escape(info["link"])
            hashtag, cta_emoji, cta_label = STATUS_COPY[status]

            price_change_line = "💸 <b>¡Bajada de precio!</b>" if price_decreased else ""

            if price_decreased:
                price_line = f"💰 <s>{html.escape(prev_price)}</s> <b>{html.escape(info['price'])}</b>"
            elif info["price"] and info["original_price"] and info["original_price"] != info["price"]:
                price_line = f"💰 <s>{html.escape(info['original_price'])}</s> <b>{html.escape(info['price'])}</b>"
            elif info["price"]:
                price_line = f"💰 <b>{html.escape(info['price'])}</b>"
            else:
                price_line = ""

            stock_line = f"📊 <b>SÓLO QUEDA(N) {html.escape(str(info['stock']))} EN STOCK</b>" if info.get("stock") else ""
            cta = f'{cta_emoji} <b><a href="{link}">{cta_label}</a></b>'
            store_line = f"<b>{info['store_label']} {info['flag']} {hashtag}</b>"
            website_line = f'🌐 <a href="{LORCANA_WEBSITE_URL}">Ver todo el stock de Disney Lorcana</a>'

            message = "\n\n".join(
                part for part in [f"<b>{safe_name}</b>", store_line, price_change_line, price_line, stock_line, cta] if part
            )
            message += f"\n\n{website_line}"

            if DRY_RUN:
                print(f"🧪 [DRY_RUN] Se habría enviado ({status}): {name}")
            else:
                try:
                    if info.get("image"):
                        try:
                            image_resp = requests.get(info["image"], timeout=15)
                            image_resp.raise_for_status()
                            watermarked = watermark_product_image(image_resp.content, info["marketplace_code"])
                            send_telegram_photo_bytes(watermarked, message)
                        except Exception as watermark_error:
                            print(f"⚠️ No se pudo generar la marca de agua para {name}: {watermark_error!r}")
                            send_telegram_photo(info["image"], message)
                    else:
                        send_telegram_message(message)
                    print(f"✅ Alerta enviada ({status}): {name}")
                except Exception as e:
                    print(f"❌ Error enviando Telegram para {name}: {e!r}")
                    send_failed = True
                await asyncio.sleep(5)

        if send_failed:
            print(f"⚠️ No se actualiza el estado de '{name}' — se reintentará el aviso en la próxima ejecución.")
        else:
            state[key] = {
                "name": name,
                "status": status,
                "stock": info.get("stock"),
                "price": info.get("price"),
                "first_seen": first_seen,
            }

    save_state(state)
    save_snapshot(lorcana_products)
    print("✅ Comprobación de Disney Lorcana completada.")


if __name__ == "__main__":
    asyncio.run(main())

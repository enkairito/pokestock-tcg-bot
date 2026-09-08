from stock_logic import alert_changes, write_snapshot
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
    DRY_RUN,
    STATUS_COPY,
    _strip_accents,
    check_single_product,
    discover_products,
    is_excluded_by_name,
    merge_product_record,
    new_context,
    watermark_product_image,
)

# Bot y canal propios (pendiente de crear, 2026-09-06) — igual que One Piece,
# separado del canal principal de Pokémon a propósito: la gente que solo
# quiere avisos de Pokémon no debería recibir ruido de Magic.
TELEGRAM_MAGIC_BOT_TOKEN = os.environ["TELEGRAM_MAGIC_BOT_TOKEN"]
TELEGRAM_MAGIC_CHAT_ID = os.environ["TELEGRAM_MAGIC_CHAT_ID"]
MAGIC_WEBSITE_URL = "https://wheresthatstock.com/magic"

# Solo la página de "Novedades" de la tienda de marca de Magic en Amazon ES,
# sin filtrar por expansión concreta — pedido explícitamente 2026-09-06:
# A diferencia de Pokémon ES, la tienda de Magic NO tiene una pestaña plana
# "Novedades" con productos directamente — comprobado 2026-09-06:
# https://www.amazon.es/stores/page/CB702A7F-.../ ("Expansiones") y su
# pestaña "Productos" son ambas páginas-índice (banners/categorías que
# enlazan a otras páginas, 0 elementos [data-asin]), no listados de
# producto. La única estructura real son las páginas de cada expansión
# individual, que sí tienen productos — así que "las 4 últimas expansiones"
# se traduce en listar aquí, a mano, las 4 páginas de expansión más
# recientes tal como las ordena la propia Amazon en su nav (más reciente
# primero). A diferencia de Pokémon, esto SÍ requiere tocar código cuando
# salga una expansión nueva (quitar la más antigua de las 4, añadir la
# nueva) — no hay forma de evitarlo dada la estructura de esta tienda.
MAGIC_MARKETPLACE = {
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
        ("Star Trek", "https://www.amazon.es/stores/page/31C78E94-28D4-45D7-987F-22BB35165EB1"),
        ("The Hobbit", "https://www.amazon.es/stores/page/A132385E-4369-40F2-AC92-7CB2AD98C1B0"),
        ("Reality Fracture", "https://www.amazon.es/stores/page/40942C9F-B68F-4F4D-A9A1-91B6C22A45A4"),
        ("Secretos de Strixhaven", "https://www.amazon.es/stores/page/A970672B-E966-4459-B439-25F99A6D7660"),
    ],
    "allow_individual_fallback": True,
    "exclude_out_of_stock": True,
}

STATE_FILE = Path(__file__).parent / "state_magic.json"
SNAPSHOT_FILE = Path(__file__).parent / "magic_snapshot.json"


def categorize_magic(name):
    """Reglas (por orden de prioridad):
    1. Booster de Coleccionista: "collector booster"/equivalente en español.
    2. Mazo Commander: menciona "commander" junto con "deck"/"mazo".
    3. Bundle: "bundle".
    4. Sobre: "play booster"/"draft booster"/"booster de duelo", o "sobre"
       si además menciona Magic (para no colar sobres de otro juego).
    5. Otros: menciona Magic/MTG pero no encaja arriba.
    Si no menciona Magic/MTG en absoluto, se descarta (return None) — mismo
    criterio que is_relevant_by_name en check_stock.py para Pokémon."""
    text = _strip_accents((name or "").lower())
    mentions_magic = "magic" in text or "mtg" in text or "the gathering" in text

    if "collector booster" in text or "booster de coleccionista" in text:
        return "Booster de Coleccionista"
    if "commander" in text and ("deck" in text or "mazo" in text):
        return "Mazo Commander"
    if "bundle" in text:
        return "Bundle"
    if "play booster" in text or "draft booster" in text or "booster de duelo" in text or ("sobre" in text and mentions_magic):
        return "Sobre"
    if mentions_magic:
        return "Otros"
    return None


def load_state():
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _telegram_post(method, data, files=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_MAGIC_BOT_TOKEN}/{method}"
    resp = requests.post(url, data=data, files=files, timeout=15)
    resp.raise_for_status()


def send_telegram_message(text):
    _telegram_post(
        "sendMessage",
        {"chat_id": TELEGRAM_MAGIC_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "false"},
    )


def send_telegram_photo(photo_url, caption):
    _telegram_post(
        "sendPhoto",
        {"chat_id": TELEGRAM_MAGIC_CHAT_ID, "photo": photo_url, "caption": caption, "parse_mode": "HTML"},
    )


def send_telegram_photo_bytes(image_bytes, caption):
    _telegram_post(
        "sendPhoto",
        {"chat_id": TELEGRAM_MAGIC_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("product.jpg", image_bytes, "image/jpeg")},
    )


def save_snapshot(products):
    write_snapshot(SNAPSHOT_FILE, products, game='Magic')


async def main():
    state = load_state()
    magic_products = {}
    marketplace = MAGIC_MARKETPLACE

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await new_context(browser)
        page = await context.new_page()

        marketplace_products = {}
        fallback_asins = set()

        for label, url in marketplace["pages"]:
            print(f"🔍 Descubriendo productos de Magic: The Gathering ({label})...")
            try:
                page_products, page_fallback = await discover_products(page, label, url, marketplace)
                print(f"📦 [{label}] {len(page_products)} productos encontrados")
                for asin, info in page_products.items():
                    marketplace_products[asin] = merge_product_record(marketplace_products.get(asin), info)
                fallback_asins.update(page_fallback)
            except Exception as e:
                print(f"❌ No se pudo cargar la página de Magic: The Gathering ({label}): {e!r}")
                raise

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

            category = categorize_magic(info["name"])
            if category is None:
                print(f"⏭️ Sin categoría reconocida, se descarta: {info['name'][:70]}")
                continue
            info["categories"] = [category]
            magic_products[f"{marketplace['code']}:{asin}"] = info

        await browser.close()

    print(f"📦 Total combinado: {len(magic_products)} productos de Magic: The Gathering")

    for key, info in magic_products.items():
        status = info["status"]
        name = info["name"]
        prev = state.get(key, {})
        prev_price = prev.get("price")
        first_seen = prev.get("first_seen") or datetime.now(timezone.utc).isoformat()
        info["first_seen"] = first_seen

        status_changed, stock_decreased, price_decreased = alert_changes(info, prev)

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
            website_line = f'🌐 <a href="{MAGIC_WEBSITE_URL}">Ver todo el stock de Magic: The Gathering</a>'

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

    if DRY_RUN:
        print("[DRY_RUN] Estado, snapshots e historial conservados sin cambios.")
        return

    save_state(state)
    save_snapshot(magic_products)
    print("✅ Comprobación de Magic: The Gathering completada.")


if __name__ == "__main__":
    asyncio.run(main())

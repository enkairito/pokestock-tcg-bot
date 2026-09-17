from stock_logic import alert_changes, confirmed_price_fields, write_snapshot
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
    discover_bestsellers_products,
    is_excluded_by_name,
    merge_product_record,
    new_context,
    watermark_product_image,
)
from gaming_product_filter import is_non_gaming_product, is_non_gaming_title

# Opcionales a propósito: hasta que el canal de Telegram de Gaming esté
# creado, el scraper debe poder seguir publicando stock en la web sin
# fallar — solo se saltan los avisos, no la comprobación en sí.
TELEGRAM_GAMING_BOT_TOKEN = os.environ.get("TELEGRAM_GAMING_BOT_TOKEN")
TELEGRAM_GAMING_CHAT_ID = os.environ.get("TELEGRAM_GAMING_CHAT_ID")
XBOX_WEBSITE_URL = "https://wheresthatstock.com/xbox"

# Mismo patrón que check_nintendo.py: la tienda de marca de Xbox en Amazon
# ES es editorial, sin rejilla de productos aprovechable. Se usa en su
# lugar el departamento "Videojuegos" de Amazon filtrado por nodo de
# categoría (Series X|S / One) + búsqueda de texto. Confirmado 2026-09-11:
# el nodo de Series X devuelve 80 productos reales (juegos y accesorios).
XBOX_MARKETPLACE = {
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
        # Más vendidos de Amazon, por plataforma y categoría — mismo patrón
        # [data-asin] que las búsquedas de arriba, confirmado el 2026-09-17.
        ("Más vendidos Xbox Series X — Consolas", "https://www.amazon.es/gp/bestsellers/videogames/20938019031"),
        ("Más vendidos Xbox Series X — Juegos", "https://www.amazon.es/gp/bestsellers/videogames/20938020031"),
        ("Más vendidos Xbox Series X — Accesorios", "https://www.amazon.es/gp/bestsellers/videogames/20938005031"),
        ("Más vendidos Xbox One — Consolas", "https://www.amazon.es/gp/bestsellers/videogames/2785653031"),
        ("Más vendidos Xbox One — Juegos", "https://www.amazon.es/gp/bestsellers/videogames/2785654031"),
        ("Más vendidos Xbox One — Accesorios", "https://www.amazon.es/gp/bestsellers/videogames/2785652031"),
    ],
    "allow_individual_fallback": True,
    "exclude_out_of_stock": True,
}

STATE_FILE = Path(__file__).parent / "state_xbox.json"
SNAPSHOT_FILE = Path(__file__).parent / "xbox_snapshot.json"
ACCESORIOS_SNAPSHOT_FILE = Path(__file__).parent / "accesorios_xbox_snapshot.json"


ACCESSORY_NAME_KEYWORDS = [
    "funda", "estuche", "sleeve", "case", "protector de pantalla", "mando",
    "controller", "controlador", "gamepad", "joystick", "volante",
    "auriculares", "tarjeta de memoria", "microsd", "cargador", "grip",
    "soporte", "base de carga", "ventilador", "cable", "adaptador",
]

def is_accessory(name):
    text = _strip_accents((name or "").lower())
    return any(keyword in text for keyword in ACCESSORY_NAME_KEYWORDS)


def categorize_xbox(name):
    """Reglas (por orden de prioridad). Desde que las páginas de origen son
    "Más vendidos" filtradas por plataforma/categoría (2026-09-17), la
    relevancia de plataforma ya la garantiza la página de origen — exigir
    "Xbox" en el nombre descartaba juegos reales que no mencionan la
    consola en absoluto. Solo se descarta el merchandising ajeno; el resto
    que no sea consola es videojuego.
    1. Se descarta si es merchandising ajeno (libros, peluches...).
    2. Consola: menciona "consola" explícitamente.
    3. Videojuego: cualquier otra cosa.
    Los mandos/auriculares ya se separan antes con is_accessory."""
    text = _strip_accents((name or "").lower())

    if is_non_gaming_title(name):
        return None
    if "consola" in text:
        return "Consola"
    return "Videojuego"


def load_state():
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _telegram_post(method, data, files=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_GAMING_BOT_TOKEN}/{method}"
    resp = requests.post(url, data=data, files=files, timeout=15)
    resp.raise_for_status()


def send_telegram_message(text):
    _telegram_post(
        "sendMessage",
        {"chat_id": TELEGRAM_GAMING_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "false"},
    )


def send_telegram_photo(photo_url, caption):
    _telegram_post(
        "sendPhoto",
        {"chat_id": TELEGRAM_GAMING_CHAT_ID, "photo": photo_url, "caption": caption, "parse_mode": "HTML"},
    )


def send_telegram_photo_bytes(image_bytes, caption):
    _telegram_post(
        "sendPhoto",
        {"chat_id": TELEGRAM_GAMING_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("product.jpg", image_bytes, "image/jpeg")},
    )


def save_snapshot(products):
    write_snapshot(SNAPSHOT_FILE, products, game='Xbox')


async def main():
    state = load_state()
    xbox_products = {}
    accessory_products = {}
    marketplace = XBOX_MARKETPLACE

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await new_context(browser)
        page = await context.new_page()

        marketplace_products = {}
        fallback_asins = set()

        for label, url in marketplace["pages"]:
            print(f"🔍 Descubriendo productos de Xbox ({label})...")
            try:
                page_products, page_fallback = await discover_bestsellers_products(page, label, url, marketplace)
                print(f"📦 [{label}] {len(page_products)} productos encontrados")
                for asin, info in page_products.items():
                    marketplace_products[asin] = merge_product_record(marketplace_products.get(asin), info)
                fallback_asins.update(page_fallback)
            except Exception as e:
                print(f"⚠️ No se pudo cargar la página de Xbox ({label}), se omite esta vez: {e!r}")

        fallback_asins -= marketplace_products.keys()
        if fallback_asins:
            print(f"🔎 Comprobando individualmente {len(fallback_asins)} productos sin datos en la tarjeta...")
            for i, asin in enumerate(fallback_asins):
                if i > 0:
                    await asyncio.sleep(2)
                result = await check_single_product(page, asin, marketplace)
                if result:
                    marketplace_products[asin] = result

        rejected = {
            asin for asin, info in marketplace_products.items()
            if is_non_gaming_product(asin, info.get("name"))
        }
        for asin in rejected:
            print(f"⏭️ Producto ajeno a Gaming, se descarta: {marketplace_products[asin]['name'][:70]}")
            state.pop(f"{marketplace['code']}:{asin}", None)
            del marketplace_products[asin]

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
            info["asin"] = asin
            info["marketplace_code"] = marketplace["code"]
            info["store_label"] = marketplace["store_label"]
            info["flag"] = marketplace["flag"]
            info["link"] = f"https://www.{marketplace['domain']}/dp/{asin}?tag={marketplace['tag']}"
            key = f"{marketplace['code']}:{asin}"

            if is_accessory(info["name"]):
                info["categories"] = ["Xbox"]
                accessory_products[key] = info
                continue
            if is_excluded_by_name(info["name"]):
                continue

            category = categorize_xbox(info["name"])
            if category is None:
                print(f"⏭️ Sin categoría reconocida, se descarta: {info['name'][:70]}")
                continue
            info["categories"] = [category]
            xbox_products[key] = info

        await browser.close()

    all_products = {**xbox_products, **accessory_products}
    print(f"📦 Total combinado: {len(xbox_products)} productos Xbox + {len(accessory_products)} accesorios")

    for key, info in all_products.items():
        status = info["status"]
        name = info["name"]
        prev = state.get(key, {})
        prev_price = prev.get("price")
        first_seen = prev.get("first_seen") or datetime.now(timezone.utc).isoformat()
        info["first_seen"] = first_seen

        # Los accesorios no generan avisos — igual que check_nintendo.py,
        # solo alimentan la web.
        is_main_product = key in xbox_products

        status_changed, stock_decreased, price_decreased = alert_changes(info, prev) if is_main_product else (False, False, False)

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
            website_line = f'🌐 <a href="{XBOX_WEBSITE_URL}">Ver todo el stock de Xbox</a>'

            message = "\n\n".join(
                part for part in [f"<b>{safe_name}</b>", store_line, price_change_line, price_line, stock_line, cta] if part
            )
            message += f"\n\n{website_line}"

            if DRY_RUN:
                print(f"🧪 [DRY_RUN] Se habría enviado ({status}): {name}")
            elif not TELEGRAM_GAMING_BOT_TOKEN:
                print(f"⏭️ Sin bot de Telegram de Gaming configurado, no se avisa ({status}): {name}")
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
                "first_seen": first_seen,
                **confirmed_price_fields(info.get("price"), prev),
            }

    if DRY_RUN:
        print("[DRY_RUN] Estado, snapshots e historial conservados sin cambios.")
        return

    save_state(state)
    save_snapshot(xbox_products)
    write_snapshot(ACCESORIOS_SNAPSHOT_FILE, accessory_products)
    print("✅ Comprobación de Xbox completada.")


if __name__ == "__main__":
    asyncio.run(main())

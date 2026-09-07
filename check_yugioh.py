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

TELEGRAM_YUGIOH_BOT_TOKEN = os.environ["TELEGRAM_YUGIOH_BOT_TOKEN"]
TELEGRAM_YUGIOH_CHAT_ID = os.environ["TELEGRAM_YUGIOH_CHAT_ID"]
YUGIOH_WEBSITE_URL = "https://wheresthatstock.com/yugioh"

# A diferencia de Magic, la tienda de marca de Yu-Gi-Oh! en Amazon ES SÍ
# tiene páginas de productos directas — confirmado 2026-09-07: "Novedades"
# (título real de la página: "Amazon.es: Yu Gi Oh: NOVEDADES", 45
# elementos [data-asin]) y "Próximos Lanzamientos" (12 elementos, 5 de 6
# productos comprobados ya con el marcador de preventa detectado
# correctamente).
YUGIOH_MARKETPLACE = {
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
        ("Novedades", "https://www.amazon.es/stores/page/F7C5D60B-6ACA-42BB-A404-EADD122D66DF"),
        ("Próximos lanzamientos", "https://www.amazon.es/stores/page/6127A2CB-8F6F-4E5C-A2B7-36DA0222A691"),
    ],
    "allow_individual_fallback": True,
    "exclude_out_of_stock": True,
}

STATE_FILE = Path(__file__).parent / "state_yugioh.json"
SNAPSHOT_FILE = Path(__file__).parent / "yugioh_snapshot.json"


def categorize_yugioh(name):
    """Reglas (por orden de prioridad), a partir de los 31 nombres reales
    comprobados a mano el 2026-09-07 (Novedades + Próximos Lanzamientos):
    1. Caja de Sobres: "display" (booster box, ej. "Display (24)").
    2. Lata: "lata"/"mega-pack"/"mega pack" (ej. "Lata Mega-Pack 2025").
    3. Mazo de Estructura: "deck"/"baraja".
    4. Sobre: "booster"/"pack booster", o "sobre" si además menciona
       Yu-Gi-Oh.
    5. Otros: menciona Yu-Gi-Oh pero no encaja arriba.
    Si no menciona Yu-Gi-Oh en absoluto, se descarta (return None). Las
    fundas ("Card Sleeves") ya las filtra is_excluded_by_name (importado
    de check_stock, mismo criterio que Pokémon/Magic) antes de llegar
    aquí, así que no hace falta una regla aparte para ellas."""
    text = _strip_accents((name or "").lower())
    mentions_yugioh = "yu-gi-oh" in text or "yu gi oh" in text or "yugioh" in text

    if "display" in text:
        return "Caja de Sobres"
    if "lata" in text or "mega-pack" in text or "mega pack" in text:
        return "Lata"
    if "deck" in text or "baraja" in text:
        return "Mazo de Estructura"
    if "booster" in text or ("sobre" in text and mentions_yugioh):
        return "Sobre"
    if mentions_yugioh:
        return "Otros"
    return None


def load_state():
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _telegram_post(method, data, files=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_YUGIOH_BOT_TOKEN}/{method}"
    resp = requests.post(url, data=data, files=files, timeout=15)
    resp.raise_for_status()


def send_telegram_message(text):
    _telegram_post(
        "sendMessage",
        {"chat_id": TELEGRAM_YUGIOH_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "false"},
    )


def send_telegram_photo(photo_url, caption):
    _telegram_post(
        "sendPhoto",
        {"chat_id": TELEGRAM_YUGIOH_CHAT_ID, "photo": photo_url, "caption": caption, "parse_mode": "HTML"},
    )


def send_telegram_photo_bytes(image_bytes, caption):
    _telegram_post(
        "sendPhoto",
        {"chat_id": TELEGRAM_YUGIOH_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
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
                "game": "Yu-Gi-Oh!",
            }
            for info in products.values()
        ],
    }
    SNAPSHOT_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    state = load_state()
    yugioh_products = {}
    marketplace = YUGIOH_MARKETPLACE

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await new_context(browser)
        page = await context.new_page()

        marketplace_products = {}
        fallback_asins = set()

        for label, url in marketplace["pages"]:
            print(f"🔍 Descubriendo productos de Yu-Gi-Oh! ({label})...")
            try:
                page_products, page_fallback = await discover_products(page, label, url, marketplace)
                print(f"📦 [{label}] {len(page_products)} productos encontrados")
                for asin, info in page_products.items():
                    marketplace_products[asin] = merge_product_record(marketplace_products.get(asin), info)
                fallback_asins.update(page_fallback)
            except Exception as e:
                print(f"❌ No se pudo cargar la página de Yu-Gi-Oh! ({label}): {e!r}")

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

            category = categorize_yugioh(info["name"])
            if category is None:
                print(f"⏭️ Sin categoría reconocida, se descarta: {info['name'][:70]}")
                continue
            info["categories"] = [category]
            yugioh_products[f"{marketplace['code']}:{asin}"] = info

        await browser.close()

    if not yugioh_products:
        print("❌ No se encontró ningún producto de Yu-Gi-Oh!.")
        sys.exit(1)

    print(f"📦 Total combinado: {len(yugioh_products)} productos de Yu-Gi-Oh!")

    for key, info in yugioh_products.items():
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
            website_line = f'🌐 <a href="{YUGIOH_WEBSITE_URL}">Ver todo el stock de Yu-Gi-Oh!</a>'

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
    save_snapshot(yugioh_products)
    print("✅ Comprobación de Yu-Gi-Oh! completada.")


if __name__ == "__main__":
    asyncio.run(main())

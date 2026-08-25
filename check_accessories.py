import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from patchright.async_api import async_playwright

from check_stock import (
    check_single_product,
    discover_products,
    merge_product_record,
    new_context,
)

# Reutiliza las mismas cookies de Amazon ES que check_stock.py — no hace
# falta un fichero de cookies aparte, es la misma cuenta/dominio.
ACCESORIOS_MARKETPLACE = {
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
        ("Accesorios 1", "https://www.amazon.es/stores/page/8178E021-AF05-4445-9E8D-5248469050B1"),
        ("Accesorios 2", "https://www.amazon.es/stores/page/5C170227-7E40-4AA0-945E-19A1041691E8"),
        ("Accesorios 3", "https://www.amazon.es/stores/page/7A6B3783-30F2-471D-ADAF-8B2184512298"),
        ("Accesorios 4", "https://www.amazon.es/stores/page/0C7A147F-CC38-42D7-A60F-5BFBF8B92063"),
    ],
    "allow_individual_fallback": True,
    "exclude_out_of_stock": True,
}

STATE_FILE = Path(__file__).parent / "state_accesorios.json"
SNAPSHOT_FILE = Path(__file__).parent / "accesorios_snapshot.json"


def load_state():
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


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
            }
            for info in products.values()
        ],
    }
    SNAPSHOT_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    state = load_state()
    products = {}
    marketplace = ACCESORIOS_MARKETPLACE

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await new_context(browser)
        page = await context.new_page()

        marketplace_products = {}
        fallback_asins = set()

        for label, url in marketplace["pages"]:
            print(f"🔍 Descubriendo accesorios en la tienda ({label})...")
            try:
                page_products, page_fallback = await discover_products(page, label, url, marketplace)
                print(f"📦 [{label}] {len(page_products)} productos encontrados")
                for asin, info in page_products.items():
                    marketplace_products[asin] = merge_product_record(marketplace_products.get(asin), info)
                fallback_asins.update(page_fallback)
            except Exception as e:
                print(f"❌ No se pudo cargar la página de accesorios ({label}): {e!r}")

        fallback_asins -= marketplace_products.keys()
        if fallback_asins:
            print(f"🔎 Comprobando individualmente {len(fallback_asins)} accesorios sin datos en la tarjeta...")
            for i, asin in enumerate(fallback_asins):
                if i > 0:
                    await asyncio.sleep(2)
                result = await check_single_product(page, asin, marketplace)
                if result:
                    marketplace_products[asin] = result

        out_of_stock = {a for a, i in marketplace_products.items() if i["status"] == "no_disponible"}
        if out_of_stock:
            print(f"⏭️ Omitiendo {len(out_of_stock)} accesorios agotados de la web.")
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
            products[f"{marketplace['code']}:{asin}"] = info

        await browser.close()

    if not products:
        print("❌ No se encontró ningún accesorio.")
        sys.exit(1)

    print(f"📦 Total combinado: {len(products)} accesorios únicos")

    for key, info in products.items():
        prev = state.get(key, {})
        first_seen = prev.get("first_seen") or datetime.now(timezone.utc).isoformat()
        info["first_seen"] = first_seen
        state[key] = {
            "name": info["name"],
            "status": info["status"],
            "stock": info.get("stock"),
            "price": info.get("price"),
            "first_seen": first_seen,
        }

    save_state(state)
    save_snapshot(products)
    print("✅ Comprobación de accesorios completada.")


if __name__ == "__main__":
    asyncio.run(main())

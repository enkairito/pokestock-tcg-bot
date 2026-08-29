import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from patchright.async_api import async_playwright

from check_stock import (
    _strip_accents,
    check_single_product,
    discover_products,
    merge_product_record,
    new_context,
)

# Tienda oficial de Bandai en Amazon ES, filtrada a "one piece tcg" — mismo
# patrón que ACCESORIOS_MARKETPLACE en check_accessories.py, pero para el
# juego de cartas de One Piece en vez de accesorios de Pokémon.
ONEPIECE_MARKETPLACE = {
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
        ("One Piece TCG", "https://www.amazon.es/stores/page/E55E08C6-0FB8-446A-BF31-730BB33310B7/search?terms=one%20piece%20tcg"),
    ],
    "allow_individual_fallback": True,
    "exclude_out_of_stock": True,
}

# Visto manualmente en la tienda: listado con nombre roto/ambiguo que no es
# fiable (revisar de nuevo si Amazon actualiza la ficha).
BLACKLIST_ASINS = {"B0GY9V8DKV"}

STATE_FILE = Path(__file__).parent / "state_onepiece.json"
SNAPSHOT_FILE = Path(__file__).parent / "onepiece_snapshot.json"
ACCESORIOS_SNAPSHOT_FILE = Path(__file__).parent / "accesorios_onepiece_snapshot.json"


def is_accessory(name):
    text = _strip_accents((name or "").lower())
    return "funda" in text or "estuche" in text


def categorize_onepiece(name):
    """Reglas acordadas (por orden de prioridad):
    1. Sobre: "booster pack"/"paquete de refuerzo", salvo que también diga
       "(caja)" — eso es una caja de varios sobres, no un sobre suelto.
    2. Booster Box: "booster box"/"caja de refuerzo"/"(caja)", y además
       menciona "one piece"/"una pieza"/"op-" en algún punto.
    3. Double Pack: "dp-" o "double pack".
    4. Starter Deck: "st-" seguido de número.
    5. Otros: menciona "one piece" o "una pieza" pero no encaja arriba.
    Si no encaja en nada, se descarta (return None) — no se publica."""
    text = _strip_accents((name or "").lower())
    has_caja = "(caja)" in text
    mentions_op = "one piece" in text or "una pieza" in text or "op-" in text

    if ("booster pack" in text or "paquete de refuerzo" in text) and not has_caja:
        return "Sobre"
    if ("booster box" in text or "caja de refuerzo" in text or has_caja) and mentions_op:
        return "Booster Box"
    if "dp-" in text or "double pack" in text:
        return "Double Pack"
    if re.search(r"st-\d", text):
        return "Starter Deck"
    if "one piece" in text or "una pieza" in text:
        return "Otros"
    return None


def load_state():
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _snapshot_entry(info, game=None):
    entry = {
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
    }
    if game:
        entry["game"] = game
    return entry


def save_snapshot(path, products, game=None):
    snapshot = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "products": [_snapshot_entry(info, game=game) for info in products.values()],
    }
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    state = load_state()
    onepiece_products = {}
    accessory_products = {}
    marketplace = ONEPIECE_MARKETPLACE

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await new_context(browser)
        page = await context.new_page()

        marketplace_products = {}
        fallback_asins = set()

        for label, url in marketplace["pages"]:
            print(f"🔍 Descubriendo productos de One Piece TCG ({label})...")
            try:
                page_products, page_fallback = await discover_products(page, label, url, marketplace)
                print(f"📦 [{label}] {len(page_products)} productos encontrados")
                for asin, info in page_products.items():
                    marketplace_products[asin] = merge_product_record(marketplace_products.get(asin), info)
                fallback_asins.update(page_fallback)
            except Exception as e:
                print(f"❌ No se pudo cargar la página de One Piece TCG ({label}): {e!r}")

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
            if asin in BLACKLIST_ASINS:
                continue
            info["asin"] = asin
            info["marketplace_code"] = marketplace["code"]
            info["store_label"] = marketplace["store_label"]
            info["flag"] = marketplace["flag"]
            info["link"] = f"https://www.{marketplace['domain']}/dp/{asin}?tag={marketplace['tag']}"
            key = f"{marketplace['code']}:{asin}"

            if is_accessory(info["name"]):
                info["categories"] = ["One Piece"]
                accessory_products[key] = info
                continue

            category = categorize_onepiece(info["name"])
            if category is None:
                print(f"⏭️ Sin categoría reconocida, se descarta: {info['name'][:70]}")
                continue
            info["categories"] = [category]
            onepiece_products[key] = info

        await browser.close()

    all_products = {**onepiece_products, **accessory_products}
    if not all_products:
        print("❌ No se encontró ningún producto de One Piece TCG.")
        sys.exit(1)

    print(f"📦 Total combinado: {len(onepiece_products)} productos TCG + {len(accessory_products)} accesorios")

    for key, info in all_products.items():
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
    save_snapshot(SNAPSHOT_FILE, onepiece_products, game="One Piece")
    save_snapshot(ACCESORIOS_SNAPSHOT_FILE, accessory_products)
    print("✅ Comprobación de One Piece TCG completada.")


if __name__ == "__main__":
    asyncio.run(main())

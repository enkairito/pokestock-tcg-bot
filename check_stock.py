import asyncio
import json
import os
import random
import re
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from patchright.async_api import async_playwright
from PIL import Image

MARKETPLACES = [
    {
        "code": "ES",
        "domain": "amazon.es",
        "flag": "🇪🇸",
        "store_label": "Amazon ES",
        "tag": "enkairito-21",
        "cookies_file": Path(__file__).parent / "amazon_cookies.json",
        "locale": "es-ES",
        "accept_language": "es-ES,es;q=0.9",
        "invitation_marker": "invitaci",
        "invitation_button_pattern": "text=/solicitar invitaci[oó]n/i",
        "interstitial_marker": "haz clic en el botón de abajo",
        "continue_button_pattern": "text=/seguir comprando/i",
        "stock_count_re": re.compile(r"queda\(?n?\)?\s+(\d+)\s+en stock", re.IGNORECASE),
        "pages": [
            ("Todos los productos", "https://www.amazon.es/stores/page/4CC86B6A-CAD9-4B47-A949-86C99C87A382"),
            ("Disponible de nuevo", "https://www.amazon.es/stores/page/41180886-559D-47A1-9CEB-5BF332812A91"),
            ("Novedades", "https://www.amazon.es/stores/page/70E78EA6-79CB-4678-9249-717F2A13EB77"),
        ],
        "allow_individual_fallback": True,
        # Solicitado explícitamente: no interesa mantener productos agotados
        # en el estado/web para ES tampoco (mismo criterio que UK/US).
        "exclude_out_of_stock": True,
    },
    {
        "code": "UK",
        "domain": "amazon.co.uk",
        "flag": "🇬🇧",
        "store_label": "Amazon UK",
        "tag": "wtsuk-21",
        "cookies_file": Path(__file__).parent / "amazon_cookies_uk.json",
        "locale": "en-GB",
        "accept_language": "en-GB,en;q=0.9",
        # NOTA: patrones en inglés sin verificar contra una página real de
        # invitación/bajo stock de Amazon.co.uk todavía — revisar con datos
        # reales la primera vez que aparezca un producto en ese estado.
        "invitation_marker": "invit",
        "invitation_button_pattern": "text=/request.*invit/i",
        "interstitial_marker": "click the button below",
        "continue_button_pattern": "text=/continue shopping/i",
        "stock_count_re": re.compile(r"only\s+(\d+)\s+left in stock", re.IGNORECASE),
        "pages": [
            ("All products", "https://www.amazon.co.uk/stores/page/0C27883C-C67C-4CB1-B1DC-2F5ACDBEC0C6"),
        ],
        # No visitar fichas de producto individuales en este marketplace —
        # solo la página de tienda indicada arriba. Solicitado explícitamente
        # tras ver redirecciones inesperadas (a Barclays) al comprobar
        # productos individuales de Amazon.co.uk.
        "allow_individual_fallback": False,
        # Solicitado explícitamente: no incluir productos agotados de este
        # marketplace ni en el snapshot de la web ni en el estado/avisos.
        "exclude_out_of_stock": True,
    },
    {
        "code": "US",
        "domain": "amazon.com",
        "flag": "🇺🇸",
        "store_label": "Amazon USA",
        "tag": "wtsus-20",
        "cookies_file": Path(__file__).parent / "amazon_cookies_us.json",
        "locale": "en-US",
        "accept_language": "en-US,en;q=0.9",
        # NOTA: patrones en inglés sin verificar contra una página real de
        # invitación/bajo stock de Amazon.com todavía — revisar con datos
        # reales la primera vez que aparezca un producto en ese estado.
        "invitation_marker": "invit",
        "invitation_button_pattern": "text=/request.*invit/i",
        "interstitial_marker": "click the button below",
        "continue_button_pattern": "text=/continue shopping/i",
        "stock_count_re": re.compile(r"only\s+(\d+)\s+left in stock", re.IGNORECASE),
        "pages": [
            ("TCG search", "https://www.amazon.com/stores/page/DC6D208A-8D81-4AFA-90C5-616473E94ECA/search?terms=tcg"),
            ("ETB search", "https://www.amazon.com/stores/page/DC6D208A-8D81-4AFA-90C5-616473E94ECA/search?terms=etb"),
        ],
        # Mismo criterio de precaución que UK: no visitar fichas de producto
        # individuales ni incluir agotados en la web/estado.
        "allow_individual_fallback": False,
        "exclude_out_of_stock": True,
    },
]

WEBSITE_URL = "https://enkairito.github.io/wheresthatstock/"
STATE_FILE = Path(__file__).parent / "state.json"
SNAPSHOT_FILE = Path(__file__).parent / "products_snapshot.json"
DEBUG_DIR = Path(__file__).parent / "debug"

CAPTCHA_MARKERS = [
    "introduzca los caracteres",
    "enter the characters you see below",
    "api-services-support@amazon.com",
    "robot check",
]

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
DRY_RUN = os.environ.get("DRY_RUN") == "1"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

ASIN_VALID_RE = re.compile(r"^[A-Z0-9]{10}$")
ASIN_HREF_RE = re.compile(r"/dp/([A-Z0-9]{10})")


EXCLUDED_NAME_KEYWORDS = ["funda"]


def is_excluded_by_name(name):
    """Filtra accesorios (ej. fundas de cartas) que aparecen en los
    resultados de búsqueda de la tienda pero no son el producto en sí."""
    name_lower = (name or "").lower()
    return any(keyword in name_lower for keyword in EXCLUDED_NAME_KEYWORDS)


def clean_price(value):
    """Amazon a veces renderiza el precio tachado como el texto literal
    'null' cuando el producto no tiene precio de referencia (visto en
    Amazon.co.uk). Lo tratamos como si no hubiera precio."""
    if not value:
        return None
    value = value.strip()
    if not value or value.lower() == "null":
        return None
    return value

SAMESITE_MAP = {
    "strict": "Strict",
    "lax": "Lax",
    "no_restriction": "None",
    "none": "None",
}


def load_state():
    if not STATE_FILE.exists():
        return {}
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    migrated = {}
    for key, info in state.items():
        if "status" not in info and "available" in info:
            info["status"] = "compra_directa" if info["available"] else "no_disponible"
        # Claves antiguas eran solo el ASIN (implícitamente Amazon ES).
        # Migramos a "MARKETPLACE:ASIN" para evitar choques entre tiendas.
        key = key if ":" in key else f"ES:{key}"
        migrated[key] = info
    return migrated


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def save_products_snapshot(products):
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
            }
            for info in products.values()
        ],
    }
    SNAPSHOT_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        },
        timeout=15,
    )
    resp.raise_for_status()


def send_telegram_photo(photo_url, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML",
        },
        timeout=15,
    )
    resp.raise_for_status()


def send_telegram_photo_bytes(image_bytes, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption,
            "parse_mode": "HTML",
        },
        files={"photo": ("product.jpg", image_bytes, "image/jpeg")},
        timeout=15,
    )
    resp.raise_for_status()


FLAG_FILES = {
    "ES": Path(__file__).parent / "assets" / "flags" / "es.png",
    "UK": Path(__file__).parent / "assets" / "flags" / "gb.png",
    "US": Path(__file__).parent / "assets" / "flags" / "us.png",
}


def watermark_product_image(image_bytes, marketplace_code):
    photo = Image.open(BytesIO(image_bytes)).convert("RGBA")

    margin = int(photo.width * 0.035)

    flag_file = FLAG_FILES.get(marketplace_code)
    if flag_file and flag_file.exists():
        flag_w = int(photo.width * 0.16)
        flag_icon = Image.open(flag_file).convert("RGBA")
        flag_h = int(flag_w * flag_icon.height / flag_icon.width)
        flag = flag_icon.resize((flag_w, flag_h))
        flag_bordered = Image.new("RGBA", (flag_w + 4, flag_h + 4), (255, 255, 255, 255))
        flag_bordered.paste(flag, (2, 2))
        flag_x = photo.width - margin - flag_bordered.width
        photo.alpha_composite(flag_bordered, (flag_x, margin))

    output = BytesIO()
    photo.convert("RGB").save(output, format="JPEG", quality=90)
    return output.getvalue()


def normalize_cookies(raw_cookies):
    normalized = []
    for c in raw_cookies:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "sameSite": SAMESITE_MAP.get(str(c.get("sameSite")).lower(), "Lax"),
        }
        if c.get("expirationDate") is not None:
            cookie["expires"] = c["expirationDate"]
        if "httpOnly" in c:
            cookie["httpOnly"] = c["httpOnly"]
        if "secure" in c:
            cookie["secure"] = c["secure"]
        normalized.append(cookie)
    return normalized


async def new_context(browser):
    """Un único contexto para toda la ejecución. Las cookies de cada
    marketplace están limitadas a su propio dominio (atributo `domain` de
    cada cookie), así que el navegador solo las envía cuando corresponde —
    no hace falta un contexto por tienda."""
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        locale="es-ES",
        viewport={"width": 1366, "height": 900},
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    for marketplace in MARKETPLACES:
        cookies_file = marketplace["cookies_file"]
        if cookies_file.exists():
            raw_cookies = json.loads(cookies_file.read_text(encoding="utf-8"))
            await context.add_cookies(normalize_cookies(raw_cookies))
    return context


async def try_click_continue(page, label, marketplace):
    try:
        body_text = (await page.inner_text("body")).lower()
        if marketplace["interstitial_marker"] not in body_text:
            return

        continue_button = await page.query_selector(marketplace["continue_button_pattern"])
        if not continue_button:
            return
        print(f"↪️ Interstitial detectado en {label} ({marketplace['code']}), haciendo clic para continuar.")
        await continue_button.click(timeout=5000)
        await page.wait_for_timeout(random.uniform(1500, 3000))
    except Exception as e:
        print(f"⚠️ No se pudo hacer clic en el interstitial para {label} ({marketplace['code']}): {e!r}")


async def discover_products(page, label, url, marketplace):
    """Descubre todos los productos y su estado (nombre, precio, disponibilidad)
    directamente desde las tarjetas de la página de la tienda, sin necesidad de
    visitar cada ficha de producto individual."""
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)

    await try_click_continue(page, label, marketplace)

    for _ in range(6):
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(800)

    title = await page.title()
    print(f"ℹ️ [{marketplace['code']}/{label}] Título de la página cargada: {title!r}")
    print(f"ℹ️ [{marketplace['code']}/{label}] URL final tras la carga: {page.url}")

    body_text = (await page.inner_text("body")).lower()
    if any(marker in body_text for marker in CAPTCHA_MARKERS):
        print(f"❌ Amazon devolvió una verificación anti-bot (captcha) en vez de la tienda ({marketplace['code']}/{label}).")

    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    DEBUG_DIR.mkdir(exist_ok=True)
    await page.screenshot(path=str(DEBUG_DIR / f"store_page_{marketplace['code']}_{slug}.png"), full_page=True)
    (DEBUG_DIR / f"store_page_{marketplace['code']}_{slug}.html").write_text(await page.content(), encoding="utf-8")

    tiles = await page.eval_on_selector_all(
        "[data-asin]",
        """els => els.map(el => {
            const asin = el.getAttribute('data-asin');
            const titleEl = el.querySelector('h2[aria-label]');
            const name = titleEl ? titleEl.getAttribute('aria-label') : null;
            const priceEl = el.querySelector('[data-cy="price-recipe"] .a-price .a-offscreen');
            const price = priceEl ? priceEl.textContent.trim() : null;
            const originalPriceEl = el.querySelector('[data-cy="price-recipe"] .a-text-price .a-offscreen');
            const originalPrice = originalPriceEl ? originalPriceEl.textContent.trim() : null;
            const hasAddToCart = !!el.querySelector('[data-cy="add-to-cart"]');
            const imageEl = el.querySelector('img.s-image');
            const image = imageEl ? imageEl.src : null;
            const text = el.innerText || '';
            return { asin, name, price, originalPrice, hasAddToCart, image, text };
        })""",
    )

    debug_asin = os.environ.get("DEBUG_ASIN")

    products = {}
    for t in tiles:
        asin = t.get("asin")
        if debug_asin and asin == debug_asin:
            print(f"🐛 DEBUG [{marketplace['code']}/{label}] tile raw: {t}")
        if not asin or not ASIN_VALID_RE.match(asin) or asin in products:
            continue

        text = t.get("text") or ""
        text_lower = text.lower()
        if t.get("hasAddToCart"):
            status = "compra_directa"
        elif marketplace["invitation_marker"] in text_lower:
            status = "invitacion"
        else:
            status = "no_disponible"

        if debug_asin and asin == debug_asin:
            print(f"🐛 DEBUG [{marketplace['code']}/{label}] {asin}: computed status={status}")

        stock_match = marketplace["stock_count_re"].search(text)

        products[asin] = {
            "name": t.get("name") or asin,
            "price": clean_price(t.get("price")),
            "original_price": clean_price(t.get("originalPrice")),
            "image": t.get("image"),
            "stock": stock_match.group(1) if stock_match else None,
            "status": status,
        }

    if debug_asin and debug_asin not in {t.get("asin") for t in tiles}:
        print(f"🐛 DEBUG [{marketplace['code']}/{label}] {debug_asin} NO aparece entre las {len(tiles)} tarjetas encontradas en esta página.")

    # Algunos widgets de la tienda (ej. carruseles "ProductShowcase") no
    # incluyen precio/disponibilidad en la tarjeta, solo un enlace al
    # producto. Esos ASIN se devuelven aparte para comprobarlos a mano.
    hrefs = await page.eval_on_selector_all("a[href*='/dp/']", "els => els.map(e => e.href)")
    fallback_asins = set()
    for href in hrefs:
        match = ASIN_HREF_RE.search(href)
        if match and match.group(1) not in products:
            fallback_asins.add(match.group(1))

    return products, fallback_asins


async def check_single_product(page, asin, marketplace):
    """Comprobación individual de respaldo para productos cuya tarjeta de
    tienda no expone precio/disponibilidad directamente."""
    url = f"https://www.{marketplace['domain']}/dp/{asin}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(random.uniform(1200, 2500))
        await try_click_continue(page, asin, marketplace)

        title_el = await page.query_selector("#productTitle")
        name = (await title_el.inner_text()).strip() if title_el else asin

        buy_button = await page.query_selector("#add-to-cart-button, #buy-now-button")

        # Igual que discover_products: buscar el marcador de invitación en
        # todo el texto de la buybox, no solo el botón "solicitar invitación"
        # — esa fase concreta no tiene botón cuando ya se solicitó antes
        # ("Invitación solicitada, ¡gracias!"), pero sigue siendo estado de
        # invitación real.
        buybox_el = await page.query_selector("#buybox, #desktop_buybox")
        buybox_text = (await buybox_el.inner_text()) if buybox_el else ""
        buybox_text_lower = buybox_text.lower()

        if buy_button:
            status = "compra_directa"
        elif marketplace["invitation_marker"] in buybox_text_lower:
            status = "invitacion"
        else:
            status = "no_disponible"

        price_el = await page.query_selector(".a-price .a-offscreen")
        price = clean_price((await price_el.inner_text()) if price_el else None)

        image_el = await page.query_selector("#landingImage, #imgTagWrapperId img")
        image = (await image_el.get_attribute("src")) if image_el else None

        availability_el = await page.query_selector("#availability")
        availability_text = (await availability_el.inner_text()) if availability_el else ""
        stock_match = marketplace["stock_count_re"].search(availability_text or buybox_text)
        stock = stock_match.group(1) if stock_match else None

        return {"name": name, "price": price, "original_price": None, "image": image, "stock": stock, "status": status}
    except Exception as e:
        print(f"⚠️ Error comprobando {asin} ({marketplace['code']}) individualmente: {e!r}")
        return None


async def main():
    state = load_state()
    products = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await new_context(browser)
        page = await context.new_page()

        for marketplace in MARKETPLACES:
            marketplace_products = {}
            fallback_asins = set()

            for label, url in marketplace["pages"]:
                print(f"🔍 Descubriendo productos en la tienda ({marketplace['code']}/{label})...")
                try:
                    page_products, page_fallback = await discover_products(page, label, url, marketplace)
                    print(f"📦 [{marketplace['code']}/{label}] {len(page_products)} productos encontrados")
                    marketplace_products.update(page_products)
                    fallback_asins.update(page_fallback)
                except Exception as e:
                    print(f"❌ No se pudo cargar la página de la tienda ({marketplace['code']}/{label}): {e!r}")

            fallback_asins -= marketplace_products.keys()
            if fallback_asins and not marketplace.get("allow_individual_fallback", True):
                print(f"⏭️ [{marketplace['code']}] Omitiendo {len(fallback_asins)} productos sin datos en la tarjeta (fallback individual desactivado para este marketplace).")
            elif fallback_asins:
                print(f"🔎 [{marketplace['code']}] Comprobando individualmente {len(fallback_asins)} productos sin datos en la tarjeta...")
                for asin in fallback_asins:
                    await asyncio.sleep(random.uniform(2, 5))
                    result = await check_single_product(page, asin, marketplace)
                    if result:
                        marketplace_products[asin] = result

            if marketplace.get("exclude_out_of_stock"):
                out_of_stock = {a for a, i in marketplace_products.items() if i["status"] == "no_disponible"}
                if out_of_stock:
                    print(f"⏭️ [{marketplace['code']}] Omitiendo {len(out_of_stock)} productos agotados (no se añaden ni a la web ni al estado).")
                    for asin in out_of_stock:
                        del marketplace_products[asin]

            excluded_by_name = {
                a for a, i in marketplace_products.items() if is_excluded_by_name(i["name"])
            }
            if excluded_by_name:
                print(f"⏭️ [{marketplace['code']}] Omitiendo {len(excluded_by_name)} productos no relevantes por nombre (fundas/accesorios).")
                for asin in excluded_by_name:
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
        print("❌ No se encontró ningún producto en ninguna tienda.")
        sys.exit(1)

    print(f"📦 Total combinado: {len(products)} productos únicos")

    for key, info in products.items():
        status = info["status"]
        name = info["name"]
        prev = state.get(key, {})
        prev_status = prev.get("status")
        prev_stock = prev.get("stock")

        status_changed = status in ("compra_directa", "invitacion") and status != prev_status
        stock_decreased = (
            status in ("compra_directa", "invitacion")
            and info.get("stock") is not None
            and prev_stock is not None
            and int(info["stock"]) < int(prev_stock)
        )

        # Solo se envían alertas de Telegram para España. El resto de
        # marketplaces (UK, US) se siguen detectando y guardando en el
        # estado/snapshot para la web, pero no generan mensajes.
        if (status_changed or stock_decreased) and info["marketplace_code"] == "ES":
            link = info["link"]

            if info["price"] and info["original_price"] and info["original_price"] != info["price"]:
                price_line = f"💰 <s>{info['original_price']}</s> <b>{info['price']}</b>"
            elif info["price"]:
                price_line = f"💰 <b>{info['price']}</b>"
            else:
                price_line = ""

            stock_line = f"📊 <b>SÓLO QUEDA(N) {info['stock']} EN STOCK</b>" if info.get("stock") else ""

            if status == "compra_directa":
                header = "🟢 <b>¡Disponible de nuevo! #CompraDirecta</b>"
            else:
                header = "🎟️ <b>¡Disponible por invitación! #Invitación</b>"

            if status == "compra_directa":
                cta = f'📦 <a href="{link}">Cómpralo ya</a>'
            else:
                cta = f'📦 <a href="{link}">Solicitar invitación</a>'

            store_line = f"<b>{info['store_label']} {info['flag']}</b>"
            website_line = f'🌐 <a href="{WEBSITE_URL}">Ver todos los productos disponibles</a>'

            message = "\n\n".join(
                part for part in [f"<b>{name}</b>", store_line, header, price_line, stock_line, cta] if part
            )
            message += f"\n\n\n{website_line}"
            if DRY_RUN:
                print(f"🧪 [DRY_RUN] Se habría enviado ({info['marketplace_code']}/{status}): {name}")
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
                    print(f"✅ Alerta enviada ({info['marketplace_code']}/{status}): {name}")
                except Exception as e:
                    print(f"❌ Error enviando Telegram para {name}: {e!r}")

        state[key] = {"name": name, "status": status, "stock": info.get("stock")}

    save_state(state)
    save_products_snapshot(products)
    print("✅ Comprobación completada.")


if __name__ == "__main__":
    asyncio.run(main())


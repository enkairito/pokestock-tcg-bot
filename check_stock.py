import asyncio
import json
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from patchright.async_api import async_playwright

STORE_PAGES = [
    ("Todos los productos", "https://www.amazon.es/stores/page/4CC86B6A-CAD9-4B47-A949-86C99C87A382"),
    ("Disponible de nuevo", "https://www.amazon.es/stores/page/41180886-559D-47A1-9CEB-5BF332812A91"),
]
AFFILIATE_TAG = "enkairito-21"
WEBSITE_URL = "https://enkairito.github.io/wheresthatstock/"
STATE_FILE = Path(__file__).parent / "state.json"
SNAPSHOT_FILE = Path(__file__).parent / "products_snapshot.json"
DEBUG_DIR = Path(__file__).parent / "debug"
COOKIES_FILE = Path(__file__).parent / "amazon_cookies.json"

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
INVITATION_MARKER = "invitaci"
INTERSTITIAL_MARKER = "haz clic en el botón de abajo"
STOCK_COUNT_RE = re.compile(r"queda\(?n?\)?\s+(\d+)\s+en stock", re.IGNORECASE)

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
    for info in state.values():
        if "status" not in info and "available" in info:
            info["status"] = "compra_directa" if info["available"] else "no_disponible"
    return state


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def save_products_snapshot(products):
    snapshot = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "products": [
            {
                "asin": asin,
                "name": info["name"],
                "image": info.get("image"),
                "price": info.get("price"),
                "original_price": info.get("original_price"),
                "status": info["status"],
                "stock": info.get("stock"),
                "link": f"https://www.amazon.es/dp/{asin}?tag={AFFILIATE_TAG}",
            }
            for asin, info in products.items()
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
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        locale="es-ES",
        viewport={"width": 1366, "height": 900},
        extra_http_headers={"Accept-Language": "es-ES,es;q=0.9"},
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    if COOKIES_FILE.exists():
        raw_cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        await context.add_cookies(normalize_cookies(raw_cookies))
    return context


async def try_click_continue(page, label):
    try:
        body_text = (await page.inner_text("body")).lower()
        if INTERSTITIAL_MARKER not in body_text:
            return

        continue_button = await page.query_selector("text=/seguir comprando/i")
        if not continue_button:
            return
        print(f"↪️ Interstitial 'seguir comprando' detectado en {label}, haciendo clic para continuar.")
        await continue_button.click(timeout=5000)
        await page.wait_for_timeout(random.uniform(1500, 3000))
    except Exception as e:
        print(f"⚠️ No se pudo hacer clic en 'seguir comprando' para {label}: {e!r}")


async def discover_products(page, label, url):
    """Descubre todos los productos y su estado (nombre, precio, disponibilidad)
    directamente desde las tarjetas de la página de la tienda, sin necesidad de
    visitar cada ficha de producto individual."""
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)

    await try_click_continue(page, label)

    for _ in range(6):
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(800)

    title = await page.title()
    print(f"ℹ️ [{label}] Título de la página cargada: {title!r}")
    print(f"ℹ️ [{label}] URL final tras la carga: {page.url}")

    body_text = (await page.inner_text("body")).lower()
    if any(marker in body_text for marker in CAPTCHA_MARKERS):
        print(f"❌ Amazon devolvió una verificación anti-bot (captcha) en vez de la tienda ({label}).")

    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    DEBUG_DIR.mkdir(exist_ok=True)
    await page.screenshot(path=str(DEBUG_DIR / f"store_page_{slug}.png"), full_page=True)
    (DEBUG_DIR / f"store_page_{slug}.html").write_text(await page.content(), encoding="utf-8")

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

    products = {}
    for t in tiles:
        asin = t.get("asin")
        if not asin or not ASIN_VALID_RE.match(asin) or asin in products:
            continue

        text = t.get("text") or ""
        text_lower = text.lower()
        if t.get("hasAddToCart"):
            status = "compra_directa"
        elif INVITATION_MARKER in text_lower:
            status = "invitacion"
        else:
            status = "no_disponible"

        stock_match = STOCK_COUNT_RE.search(text)

        products[asin] = {
            "name": t.get("name") or asin,
            "price": t.get("price"),
            "original_price": t.get("originalPrice"),
            "image": t.get("image"),
            "stock": stock_match.group(1) if stock_match else None,
            "status": status,
        }

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


async def check_single_product(page, asin):
    """Comprobación individual de respaldo para productos cuya tarjeta de
    tienda no expone precio/disponibilidad directamente."""
    url = f"https://www.amazon.es/dp/{asin}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(random.uniform(1200, 2500))
        await try_click_continue(page, asin)

        title_el = await page.query_selector("#productTitle")
        name = (await title_el.inner_text()).strip() if title_el else asin

        buy_button = await page.query_selector("#add-to-cart-button, #buy-now-button")
        invitation_button = await page.query_selector("text=/solicitar invitaci[oó]n/i")

        if buy_button:
            status = "compra_directa"
        elif invitation_button:
            status = "invitacion"
        else:
            status = "no_disponible"

        price_el = await page.query_selector(".a-price .a-offscreen")
        price = (await price_el.inner_text()).strip() if price_el else None

        image_el = await page.query_selector("#landingImage, #imgTagWrapperId img")
        image = (await image_el.get_attribute("src")) if image_el else None

        availability_el = await page.query_selector("#availability")
        availability_text = (await availability_el.inner_text()) if availability_el else ""
        stock_match = STOCK_COUNT_RE.search(availability_text)
        stock = stock_match.group(1) if stock_match else None

        return {"name": name, "price": price, "original_price": None, "image": image, "stock": stock, "status": status}
    except Exception as e:
        print(f"⚠️ Error comprobando {asin} individualmente: {e!r}")
        return None


async def main():
    state = load_state()
    products = {}
    fallback_asins = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await new_context(browser)
        page = await context.new_page()

        for label, url in STORE_PAGES:
            print(f"🔍 Descubriendo productos en la tienda ({label})...")
            try:
                page_products, page_fallback = await discover_products(page, label, url)
                print(f"📦 [{label}] {len(page_products)} productos encontrados")
                products.update(page_products)
                fallback_asins.update(page_fallback)
            except Exception as e:
                print(f"❌ No se pudo cargar la página de la tienda ({label}): {e!r}")

        fallback_asins -= products.keys()
        if fallback_asins:
            print(f"🔎 Comprobando individualmente {len(fallback_asins)} productos sin datos en la tarjeta...")
            for asin in fallback_asins:
                await asyncio.sleep(random.uniform(2, 5))
                result = await check_single_product(page, asin)
                if result:
                    products[asin] = result

        await browser.close()

    if not products:
        print("❌ No se encontró ningún producto en ninguna página.")
        sys.exit(1)

    print(f"📦 Total combinado: {len(products)} productos únicos")

    for asin, info in products.items():
        status = info["status"]
        name = info["name"]
        prev = state.get(asin, {})
        prev_status = prev.get("status")
        prev_stock = prev.get("stock")

        status_changed = status in ("compra_directa", "invitacion") and status != prev_status
        stock_decreased = (
            status in ("compra_directa", "invitacion")
            and info.get("stock") is not None
            and prev_stock is not None
            and int(info["stock"]) < int(prev_stock)
        )

        if status_changed or stock_decreased:
            link = f"https://www.amazon.es/dp/{asin}?tag={AFFILIATE_TAG}"

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

            store_line = "<b>Amazon ES 🇪🇸</b>"
            website_line = f'🌐 <a href="{WEBSITE_URL}">Ver todos los productos disponibles</a>'

            message = "\n\n".join(
                part for part in [f"<b>{name}</b>", store_line, header, price_line, stock_line, cta] if part
            )
            message += f"\n\n\n{website_line}"
            if DRY_RUN:
                print(f"🧪 [DRY_RUN] Se habría enviado ({status}): {name}")
            else:
                try:
                    if info.get("image"):
                        send_telegram_photo(info["image"], message)
                    else:
                        send_telegram_message(message)
                    print(f"✅ Alerta enviada ({status}): {name}")
                except Exception as e:
                    print(f"❌ Error enviando Telegram para {name}: {e!r}")

        state[asin] = {"name": name, "status": status, "stock": info.get("stock")}

    save_state(state)
    save_products_snapshot(products)
    print("✅ Comprobación completada.")


if __name__ == "__main__":
    asyncio.run(main())

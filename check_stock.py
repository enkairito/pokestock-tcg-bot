import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path

import requests
from playwright.async_api import async_playwright

STORE_URL = "https://www.amazon.es/stores/page/70E78EA6-79CB-4678-9249-717F2A13EB77"
AFFILIATE_TAG = "enkairito-21"
STATE_FILE = Path(__file__).parent / "state.json"
DEBUG_DIR = Path(__file__).parent / "debug"

CAPTCHA_MARKERS = [
    "introduzca los caracteres",
    "enter the characters you see below",
    "api-services-support@amazon.com",
    "robot check",
]

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")

UNAVAILABLE_PHRASES = [
    "no disponible",
    "actualmente no disponible",
    "no se conocen fechas",
    "no está disponible",
]


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


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


async def new_context(browser):
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        locale="es-ES",
        viewport={"width": 1366, "height": 900},
        extra_http_headers={"Accept-Language": "es-ES,es;q=0.9"},
    )
    await context.route(
        re.compile(r"\.(png|jpg|jpeg|gif|webp|woff2?|ttf)(\?.*)?$"),
        lambda route: route.abort(),
    )
    return context


async def discover_products(browser):
    context = await new_context(browser)
    page = await context.new_page()
    await page.goto(STORE_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)

    for _ in range(6):
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(800)

    title = await page.title()
    print(f"ℹ️ Título de la página cargada: {title!r}")
    print(f"ℹ️ URL final tras la carga: {page.url}")

    body_text = (await page.inner_text("body")).lower()
    if any(marker in body_text for marker in CAPTCHA_MARKERS):
        print("❌ Amazon devolvió una verificación anti-bot (captcha) en vez de la tienda.")

    DEBUG_DIR.mkdir(exist_ok=True)
    await page.screenshot(path=str(DEBUG_DIR / "store_page.png"), full_page=True)
    (DEBUG_DIR / "store_page.html").write_text(await page.content(), encoding="utf-8")

    anchors = await page.eval_on_selector_all(
        "a[href*='/dp/'], a[href*='/gp/product/'], [data-asin]",
        """els => els.map(e => ({
            href: e.href || null,
            asin: e.getAttribute('data-asin'),
            text: e.innerText
        }))""",
    )

    products = {}
    for a in anchors:
        asin = None
        if a.get("href"):
            match = ASIN_RE.search(a["href"])
            if match:
                asin = match.group(1)
        if not asin and a.get("asin"):
            asin = a["asin"]
        if not asin:
            continue
        name = (a["text"] or "").strip()
        if asin not in products or (name and not products[asin]["name"]):
            products[asin] = {"name": name or products.get(asin, {}).get("name", asin)}

    await context.close()
    return products


async def check_product_stock(browser, asin):
    context = await new_context(browser)
    page = await context.new_page()
    url = f"https://www.amazon.es/dp/{asin}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(random.uniform(1200, 2500))

        title_el = await page.query_selector("#productTitle")
        title = (await title_el.inner_text()).strip() if title_el else None

        availability_el = await page.query_selector("#availability")
        availability_text = ((await availability_el.inner_text()) if availability_el else "").strip().lower()

        buy_button = await page.query_selector("#add-to-cart-button, #buy-now-button")

        unavailable = any(phrase in availability_text for phrase in UNAVAILABLE_PHRASES)
        available = bool(buy_button) and not unavailable

        return available, title
    except Exception as e:
        print(f"⚠️ Error comprobando {asin}: {e!r}")
        return None, None
    finally:
        await context.close()


async def main():
    state = load_state()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])

        print("🔍 Descubriendo productos en la tienda...")
        try:
            products = await discover_products(browser)
        except Exception as e:
            print(f"❌ No se pudo cargar la página de la tienda: {e!r}")
            await browser.close()
            sys.exit(1)

        print(f"📦 {len(products)} productos encontrados")

        for asin, info in products.items():
            await asyncio.sleep(random.uniform(2, 5))
            available, title = await check_product_stock(browser, asin)
            if available is None:
                continue

            name = title or info["name"] or asin
            prev = state.get(asin, {})
            was_available = prev.get("available", False)

            if available and not was_available:
                link = f"https://www.amazon.es/dp/{asin}?tag={AFFILIATE_TAG}"
                message = (
                    "🔥 <b>¡Disponible de nuevo!</b>\n\n"
                    f"<b>{name}</b>\n\n"
                    f'👉 <a href="{link}">Comprar en Amazon</a>\n\n'
                    "#PokeStockTCG"
                )
                try:
                    send_telegram_message(message)
                    print(f"✅ Alerta enviada: {name}")
                except Exception as e:
                    print(f"❌ Error enviando Telegram para {name}: {e!r}")

            state[asin] = {"name": name, "available": available}

        await browser.close()

    save_state(state)
    print("✅ Comprobación completada.")


if __name__ == "__main__":
    asyncio.run(main())

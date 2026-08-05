import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path

import requests
from patchright.async_api import async_playwright

STORE_URL = (
    "https://www.amazon.es/stores/page/70E78EA6-79CB-4678-9249-717F2A13EB77"
)
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
DRY_RUN = os.environ.get("DRY_RUN") == "1"

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

INVITATION_MARKER = "invitaci"

MAX_QTY_RE = re.compile(r"m[aá]x(?:imo)?\.?\s*(\d+)\s*unidad", re.IGNORECASE)


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
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
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


async def extract_price_info(page):
    current = await page.eval_on_selector(
        "span.priceToPay .a-offscreen, #corePriceDisplay_desktop_feature_div .a-price .a-offscreen, "
        "#priceblock_dealprice, #priceblock_ourprice, .a-price .a-offscreen",
        "el => el.textContent.trim()",
    ) if await page.query_selector(
        "span.priceToPay .a-offscreen, #corePriceDisplay_desktop_feature_div .a-price .a-offscreen, "
        "#priceblock_dealprice, #priceblock_ourprice, .a-price .a-offscreen"
    ) else None

    original = await page.eval_on_selector(
        "span.basisPrice .a-offscreen, .a-price.a-text-price .a-offscreen",
        "el => el.textContent.trim()",
    ) if await page.query_selector("span.basisPrice .a-offscreen, .a-price.a-text-price .a-offscreen") else None

    discount_el = await page.query_selector(".savingsPercentage")
    discount_pct = ((await discount_el.inner_text()).strip()) if discount_el else None

    return current, original, discount_pct


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
        invitation_button = await page.query_selector("text=/solicitar invitaci[oó]n/i")
        body_text = (await page.inner_text("body")).lower()

        if any(marker in body_text for marker in CAPTCHA_MARKERS):
            print(f"❌ Amazon devolvió una verificación anti-bot (captcha) al comprobar {asin}.")
            DEBUG_DIR.mkdir(exist_ok=True)
            await page.screenshot(path=str(DEBUG_DIR / f"product_{asin}.png"), full_page=True)
            (DEBUG_DIR / f"product_{asin}.html").write_text(await page.content(), encoding="utf-8")
            return None

        if title_el is None:
            print(f"⚠️ No se encontró #productTitle para {asin}: posible bloqueo o página distinta a la esperada.")
            DEBUG_DIR.mkdir(exist_ok=True)
            await page.screenshot(path=str(DEBUG_DIR / f"product_{asin}.png"), full_page=True)
            (DEBUG_DIR / f"product_{asin}.html").write_text(await page.content(), encoding="utf-8")
            return None

        unavailable = any(phrase in availability_text for phrase in UNAVAILABLE_PHRASES)

        if unavailable:
            status = "no_disponible"
        elif buy_button:
            status = "compra_directa"
        elif invitation_button or INVITATION_MARKER in body_text:
            status = "invitacion"
        else:
            status = "no_disponible"

        current, original, discount_pct = await extract_price_info(page)

        max_qty = None
        qty_match = MAX_QTY_RE.search(body_text)
        if qty_match:
            max_qty = qty_match.group(1)

        return {
            "status": status,
            "title": title,
            "price": current,
            "original_price": original,
            "discount_pct": discount_pct,
            "max_qty": max_qty,
        }
    except Exception as e:
        print(f"⚠️ Error comprobando {asin}: {e!r}")
        return None
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
            result = await check_product_stock(browser, asin)
            if result is None:
                continue

            name = result["title"] or info["name"] or asin
            status = result["status"]
            prev = state.get(asin, {})
            prev_status = prev.get("status")

            if status in ("compra_directa", "invitacion") and status != prev_status:
                link = f"https://www.amazon.es/dp/{asin}?tag={AFFILIATE_TAG}"

                if result["price"] and result["original_price"] and result["discount_pct"]:
                    price_line = f"💰 <s>{result['original_price']}</s> <b>{result['price']}</b> ({result['discount_pct']})"
                elif result["price"]:
                    price_line = f"💰 <b>{result['price']}</b>"
                else:
                    price_line = ""

                max_qty_line = f" (máx. {result['max_qty']} unidades)" if result["max_qty"] else ""

                if status == "compra_directa":
                    header = f"🟢 <b>¡Disponible ahora! #CompraDirecta</b>{max_qty_line}"
                    cta = f'📦 <a href="{link}">Comprar en Amazon</a>'
                else:
                    header = "🎟️ <b>¡Disponible por invitación! #Invitación</b>"
                    cta = f'📦 <a href="{link}">Solicitar invitación en Amazon</a>'

                message = "\n\n".join(
                    part for part in [f"<b>{name}</b>", header, price_line, cta, "#PokeStockTCG"] if part
                )
                if DRY_RUN:
                    print(f"🧪 [DRY_RUN] Se habría enviado ({status}): {name}")
                else:
                    try:
                        send_telegram_message(message)
                        print(f"✅ Alerta enviada ({status}): {name}")
                    except Exception as e:
                        print(f"❌ Error enviando Telegram para {name}: {e!r}")

            state[asin] = {"name": name, "status": status}

        await browser.close()

    save_state(state)
    print("✅ Comprobación completada.")


if __name__ == "__main__":
    asyncio.run(main())

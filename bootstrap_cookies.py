import asyncio
import json
from pathlib import Path

from patchright.async_api import async_playwright

COOKIES_FILE = Path(__file__).parent / "amazon_cookies.json"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await browser.new_context(locale="es-ES")
        page = await context.new_page()
        await page.goto("https://www.amazon.es", wait_until="domcontentloaded")

        print("Navega por amazon.es normalmente: acepta cookies, busca algún producto,")
        print("inicia sesión si quieres que las cookies incluyan tu sesión logueada.")
        input("Cuando hayas terminado, pulsa Enter aquí para guardar las cookies... ")

        cookies = await context.cookies()
        COOKIES_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ Guardadas {len(cookies)} cookies en {COOKIES_FILE}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

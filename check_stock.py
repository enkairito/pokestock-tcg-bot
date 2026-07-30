import json
import os
import random
import time
import sys
import requests
from bs4 import BeautifulSoup

PRODUCTS_FILE = "products.json"
STATE_FILE = "state.json"

try:
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
except ImportError:
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

UNAVAILABLE_PHRASES = [
    "actualmente no disponible",
    "no disponible",
    "no hay existencias",
    "producto no disponible temporalmente",
    "no se aceptan pedidos",
]


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_available(html):
    soup = BeautifulSoup(html, "html.parser")

    availability_div = soup.find(id="availability") or soup.find(
        id="availabilityInsideBuyBox_feature_div"
    )
    if availability_div:
        avail_text = availability_div.get_text(" ", strip=True).lower()
        for phrase in UNAVAILABLE_PHRASES:
            if phrase in avail_text:
                return False
        return True

    page_text = soup.get_text(" ", strip=True).lower()
    for phrase in UNAVAILABLE_PHRASES:
        if phrase in page_text:
            return False

    buy_button = soup.find(id="add-to-cart-button") or soup.find(id="buy-now-button")
    return buy_button is not None


def extract_price(soup):
    price_tag = soup.select_one("#corePrice_feature_div .a-offscreen") or soup.select_one(
        "span.a-price .a-offscreen"
    )
    if price_tag:
        return price_tag.get_text(strip=True)
    return None


def extract_image(soup):
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        return og_image["content"]
    landing_image = soup.find(id="landingImage")
    if landing_image and landing_image.get("src"):
        return landing_image["src"]
    return None


def fetch_page(url):
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "es-ES,es;q=0.9",
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    return response.text


def build_affiliate_url(url, tag):
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}tag={tag}"


def send_telegram_message(text, image_url=None):
    if image_url:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": image_url,
            "caption": text,
            "parse_mode": "HTML",
        }
    else:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
    response = requests.post(api_url, data=payload, timeout=15)
    response.raise_for_status()


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el entorno.", file=sys.stderr)
        sys.exit(1)

    products = load_json(PRODUCTS_FILE, [])
    state = load_json(STATE_FILE, {})

    for product in products:
        name = product["name"]
        url = product["url"]
        tag = product["affiliate_tag"]
        label = product.get("label", "disponible")

        try:
            html = fetch_page(url)
            soup = BeautifulSoup(html, "html.parser")
            available = is_available(html)
        except requests.RequestException as e:
            print(f"[WARN] No se pudo comprobar '{name}': {e}", file=sys.stderr)
            time.sleep(random.uniform(2, 5))
            continue

        was_available = state.get(url, {}).get("available", False)

        if available and not was_available:
            affiliate_url = build_affiliate_url(url, tag)
            price = extract_price(soup)
            image_url = extract_image(soup)

            message_lines = [
                f"<b>{name}</b>",
                f"¡Disponible! #{label}",
                "",
            ]
            if price:
                message_lines.append(price)
                message_lines.append("")
            message_lines.append(affiliate_url)
            message = "\n".join(message_lines)

            send_telegram_message(message, image_url=image_url)
            print(f"[INFO] Notificado: {name}")

        state[url] = {"available": available}
        time.sleep(random.uniform(2, 5))

    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()

from stock_logic import ALERT_STATUSES, alert_changes, confirmed_price_fields, price_to_float, write_snapshot
import asyncio
import html
import json
import os
import random
import re
import sys
import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import requests
from patchright.async_api import async_playwright
from PIL import Image

MARKETPLACES = [
    {
        "code": "ES",
        "domain": "amazon.es",
        "flag": "",
        "store_label": "Amazon ES",
        "tag": "enkairito-21",
        "cookies_file": Path(__file__).parent / "amazon_cookies.json",
        # "invit" (no "invitaci") a propósito: las tarjetas de páginas de
        # búsqueda a veces sirven texto en inglés ("Available by invitation")
        # aunque el resto de la página cargue en español — descubierto
        # 2026-08-31 al ver que productos "por invitación" reales se
        # clasificaban como agotados y desaparecían de la web. "invit" es
        # prefijo común a "invitación" e "invitation", igual que ya hacían
        # UK/US.
        "invitation_marker": "invit",
        "interstitial_marker": "haz clic en el botón de abajo",
        "continue_button_pattern": "text=/seguir comprando/i",
        "stock_count_re": re.compile(r"queda\(?n?\)?\s+(\d+)\s+en stock", re.IGNORECASE),
        "pages": [
            ("Todos los productos", "https://www.amazon.es/stores/page/4CC86B6A-CAD9-4B47-A949-86C99C87A382"),
            ("Disponible de nuevo", "https://www.amazon.es/stores/page/41180886-559D-47A1-9CEB-5BF332812A91"),
            ("Novedades", "https://www.amazon.es/stores/page/70E78EA6-79CB-4678-9249-717F2A13EB77"),
            ("Latas", "https://www.amazon.es/stores/page/3B2D9610-364D-4EDB-B35C-6B6886294B25"),
            ("Sobres", "https://www.amazon.es/stores/page/53D7A570-688A-4BE3-A88E-A2B3B4366336"),
            ("Cajas ETB", "https://www.amazon.es/stores/page/B6BBCF35-68EA-4F2C-8440-227644DCBAF1"),
            ("Cajas de Colección", "https://www.amazon.es/stores/page/90B837D1-73B6-42CD-9DAF-8B2B12B49901"),
            ("Otros", "https://www.amazon.es/stores/page/9DAE367E-008E-4F93-8D0A-6F556F2F77A0"),
            ("Colecciones premium", "https://www.amazon.es/stores/page/F25CACFF-2F58-430E-A6FF-0606896DD0FA"),
            # Búsqueda en todo Amazon.es (no solo nuestra tienda) filtrada a
            # "vendido por Amazon España" (p_6) + departamento Juguetes
            # (p_72), para pillar stock que la tienda propia no cubre.
            # Desactivada a propósito 2026-08-31: el link todavía no está
            # bien ajustado (coló cosas como juegos de mesa/Scrabble ajenos
            # al TCG). Retomar mañana con el filtro de categoría Pokémon
            # (p_123) puesto — ver commits 437f1f3/3f14760/dbbf967.
            # ("Búsqueda general", "https://www.amazon.es/s?k=pokemon+JCC&rh=p_72%3A831280031%2Cp_6%3AA1AT7YVPFBWXBL"),
        ],
        # Subconjunto de "pages" que representa categorías de producto reales
        # (a diferencia de "Todos los productos"/"Disponible de nuevo"/
        # "Novedades", que son vistas transversales del mismo catálogo).
        # Se usa para etiquetar cada producto con en qué categoría(s) de
        # Amazon aparece, para poder filtrar por ello en la web.
        "category_pages": {
            "Latas", "Sobres", "Cajas ETB", "Cajas de Colección",
            "Otros", "Colecciones premium",
        },
        "allow_individual_fallback": True,
        # Solicitado explícitamente: no interesa mantener productos agotados
        # en el estado/web para ES tampoco (mismo criterio que UK/US).
        "exclude_out_of_stock": True,
        # Productos añadidos a mano uno por uno (encontrados por el dueño
        # navegando, no por ninguna página de tienda ni búsqueda). Se
        # comprueban visitando su ficha individual directamente, y se les
        # suma el coste de entrega al precio (add_delivery_fee) porque
        # suelen ser de vendedores terceros sin envío gratis. "max_price"
        # opcional: si el precio final (con entrega incluida) lo supera, no
        # se incluye esta vez — solicitado para no avisar de precios caros.
        "extra_asins": {"B0FMYKCKK6": {"max_price": 150}},
    },
    {
        "code": "UK",
        "domain": "amazon.co.uk",
        "flag": "🇬🇧",
        "store_label": "Amazon UK",
        "tag": "wtsuk-21",
        "cookies_file": Path(__file__).parent / "amazon_cookies_uk.json",
        # Patrones en español, no en inglés: desde 2026-09-01 el
        # descubrimiento para este marketplace pasa por búsquedas en
        # amazon.es (no amazon.co.uk), así que la página siempre carga en
        # español — ver nota de "pages" más abajo y el bug de idioma de ES
        # (commit 628cad7) que ya nos enseñó esto por las malas.
        "invitation_marker": "invit",
        "interstitial_marker": "haz clic en el botón de abajo",
        "continue_button_pattern": "text=/seguir comprando/i",
        "stock_count_re": re.compile(r"queda\(?n?\)?\s+(\d+)\s+en stock", re.IGNORECASE),
        "pages": [
            # Antes apuntaba a la propia tienda en amazon.co.uk; sustituida
            # 2026-09-01 por una búsqueda en amazon.es filtrada al vendedor
            # Amazon UK (p_6) + marca Pokémon (p_123) + ASIN de Global
            # Store, ya que centralizamos todo el descubrimiento (no solo
            # el link de compra) en amazon.es.
            ("Global Store search (ES)", "https://www.amazon.es/s?k=Elite+Trainer+Box&i=toys&rh=n%3A20500119031%2Cp_123%3A325733%2Cp_n_is-global-store-asin%3A26402202031%2Cp_6%3AA2EL6K6KDM9FO1&s=relevancerank"),
        ],
        # No visitar fichas de producto individuales en este marketplace —
        # solo la página de tienda indicada arriba. Solicitado explícitamente
        # tras ver redirecciones inesperadas (a Barclays) al comprobar
        # productos individuales de Amazon.co.uk.
        "allow_individual_fallback": False,
        # Centralización de ventas en Amazon ES (decidido 2026-08-29). Desde
        # que el descubrimiento en sí pasa por amazon.es (2026-09-01), el
        # ASIN capturado ya es el real de amazon.es, así que el link de
        # compra enlaza directo a la ficha (visto en link_for_product) en
        # vez de a una búsqueda por nombre como antes.
        "asin_is_amazon_es": True,
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
        # Patrones en español, no en inglés: desde 2026-09-01 el
        # descubrimiento pasa por búsquedas en amazon.es (no amazon.com),
        # así que la página siempre carga en español.
        "invitation_marker": "invit",
        "interstitial_marker": "haz clic en el botón de abajo",
        "continue_button_pattern": "text=/seguir comprando/i",
        "stock_count_re": re.compile(r"queda\(?n?\)?\s+(\d+)\s+en stock", re.IGNORECASE),
        "pages": [
            # Antes apuntaba a la propia tienda en amazon.com (2 páginas);
            # sustituida 2026-09-01 por una búsqueda en amazon.es filtrada
            # al vendedor Amazon US (p_6) + marca Pokémon (p_123) + ASIN de
            # Global Store, ya que centralizamos todo el descubrimiento (no
            # solo el link de compra) en amazon.es.
            ("Global Store search (ES)", "https://www.amazon.es/s?k=Elite+Trainer+Box&rh=n%3A20500119031%2Cn%3A26907148031%2Cp_6%3AA8ZZTUQ8GZK8C%2Cp_123%3A325733%2Cp_n_is-global-store-asin%3A26402202031"),
        ],
        # Mismo criterio de precaución que UK: no visitar fichas de producto
        # individuales ni incluir agotados en la web/estado.
        "allow_individual_fallback": False,
        "exclude_out_of_stock": True,
        # Ver nota de "asin_is_amazon_es" en UK: mismo motivo.
        "asin_is_amazon_es": True,
    },
]

# El Corte Inglés no es Amazon (sin ASIN, sin flujo de invitación, sin
# cookies de sesión necesarias — la búsqueda es pública) así que tiene su
# propia configuración y su propio discover, en vez de encajarlo en
# MARKETPLACES. Usamos la URL ya filtrada a la categoría "Juguetes" en vez
# de "Todo" (la que da el buscador por defecto) porque "Todo" mezcla
# resultados de Libros/Videojuegos/etc. que no son el producto en sí.
#
# NOTA: la solicitud de afiliación vía Awin sigue pendiente de aprobación
# (issue #2) — "tag" queda vacío y el link es directo al producto sin
# tracking. En cuanto se apruebe, construir aquí el enlace de afiliado
# (deep link de Awin) en vez de la URL directa.
ECI_STORE = {
    "code": "ECI",
    "flag": "",
    "store_label": "El Corte Inglés",
    "tag": None,
    "pages": [
        ("JCC Pokémon", "https://www.elcorteingles.es/juguetes/search-nwx/?s=pokemon+jcc&stype=text_box_multi"),
        # La URL de categoría "cromos/.../brand::Pokémon/" que se pidió
        # originalmente no filtra nada en el sitio real de ECI (el "::" no es
        # un filtro válido ahí) — devuelve cromos de fútbol Panini/Adrenalyn,
        # no de Pokémon. Confirmado a mano el 2026-09-16 en el runner de
        # GitHub (el sitio bloquea peticiones directas desde IPs
        # residenciales/locales, así que no se pudo repetir la prueba en
        # local). Esta búsqueda por texto, con el mismo patrón que "JCC
        # Pokémon", sí encuentra los productos reales (cajas/blísteres/latas
        # del 30 Aniversario, Baraja Combate, Ultra Premium Collection...).
        ("Cromos Pokémon", "https://www.elcorteingles.es/juguetes/search-nwx/?s=pokemon+cromos&stype=text_box_multi"),
    ],
}
ECI_ID_RE = re.compile(r"^product-([A-Za-z0-9]+)$")
ECI_PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€")

# Carrefour, igual que El Corte Inglés: sin ASIN, búsqueda pública, su propia
# config. IMPORTANTE — descubierto a base de romperlo varias veces el
# 2026-09-15: la página SOLO devuelve los resultados de la búsqueda si se
# navega directo a la URL con "query"/"filter" y NO se interactúa con nada
# (ni el banner de cookies) antes de leer el DOM — aceptar/rechazar cookies
# dispara un re-render que vacía la rejilla de resultados y deja la home
# genérica. Así que aquí no se hace click en ningún banner de consentimiento.
# La URL es la búsqueda "pokemon 30th aniversario" filtrada a la marca
# Bandai, limpia de los parámetros de tracking de un anuncio de Google Ads
# (gclid/gad_source/gbraid/etc., no aportan nada a la búsqueda en sí).
CARREFOUR_STORE = {
    "code": "CAR",
    "flag": "",
    "store_label": "Carrefour",
    "tag": None,
    "pages": [
        ("Pokémon 30º Aniversario", "https://www.carrefour.es/?filter=brand%3Abandai&query=pokemon%2030th%20aniversario"),
    ],
}
CARREFOUR_ID_RE = re.compile(r"/([A-Za-z0-9]+-\d+)/p/?$")

# Toys"R"Us (toysrus.es) está detrás de un WAF que devuelve 451 a las IPs de
# datacenter (confirmado desde GitHub Actions) y un challenge de Cloudflare
# si se simula la interacción real del buscador desde un navegador
# automatizado — scrapear su HTML no es viable. Pero el buscador de la web
# es en realidad un widget de Empathy.co (SaaS de búsqueda de terceros) que
# llama a ``api.empathy.co``, sin esas protecciones: responde JSON limpio a
# una simple petición HTTP, sin cookies ni sesión. Descubierto el 2026-09-16
# inspeccionando las peticiones de red de una búsqueda real en el sitio. Los
# códigos de producto (K1091126, etc.) y la URL canónica del producto vienen
# directamente en la respuesta.
TOYSRUS_STORE = {
    "code": "TRU",
    "flag": "",
    "store_label": "Toys\"R\"Us",
    "tag": None,
    "pages": [
        ("Pokémon TCG", "pokemon tcg"),
    ],
}
TOYSRUS_SEARCH_API = "https://api.empathy.co/search/v1/query/toysrus/search"

# Deep-link de afiliación aprobado en TradeDoubler el 2026-09-18 (programa
# ToysRus ES = 211811, sitio Where's That Stock = 3496377). El generador de
# TradeDoubler codifica la landing page dejando "(" y ")" sin escapar (el
# resto de caracteres especiales sí, incluida cada "%" ya presente en la URL
# — por eso es quote() normal y no solo un "url encode" simple).
TOYSRUS_AFFILIATE_PROGRAM_ID = "211811"
TOYSRUS_AFFILIATE_SITE_ID = "3496377"


def toysrus_affiliate_link(url):
    encoded = quote(url, safe="()")
    return (
        f"https://clk.tradedoubler.com/click?p={TOYSRUS_AFFILIATE_PROGRAM_ID}"
        f"&a={TOYSRUS_AFFILIATE_SITE_ID}&url={encoded}"
    )

# Fnac queda temporalmente como catálogo estático. El runner de GitHub
# recibe un 403 de Datadome incluso con cookies recientes, así que estos
# productos se publican como "sin confirmar" sin visitar Fnac, actualizar
# estado ni generar alertas. Se podrá reactivar el scraper cuando exista una
# fuente estable.
FNAC_CATALOG_FILE = Path(__file__).parent / "fnac_catalog.json"

STATUS_COPY = {
    "compra_directa": ("#COMPRADIRECTA", "📦", "CÓMPRALO YA"),
    "invitacion": ("#INVITACIÓN", "🎟️", "SOLICITAR INVITACIÓN"),
    "preventa": ("#PREVENTA", "🗓️", "RESERVAR AHORA"),
}

# Estados que merece la pena avisar cuando se alcanzan (status_changed) o
# cuyo stock merece la pena vigilar (stock_decreased) — no_disponible queda
# fuera a propósito. Import compartido por check_onepiece.py/check_magic.py
# para no duplicar la tupla en cada sitio.


WEBSITE_URL = "https://wheresthatstock.com/"
STATE_FILE = Path(__file__).parent / "state.json"
SNAPSHOT_FILE = Path(__file__).parent / "products_snapshot.json"
EVENTS_FILE = Path(__file__).parent / "events.json"
DEBUG_DIR = Path(__file__).parent / "debug"

# Feed público de actividad de la web (distinto de los avisos de Telegram):
# a diferencia de Telegram, que solo avisa de las tiendas españolas para no
# saturar, el feed incluye todas las tiendas porque la web ya las muestra y el
# stock de todas y un restock en UK/US es igual de relevante para quien la
# visita. Se limita a los últimos N eventos (más reciente al final) para
# que el JSON no crezca sin límite.
MAX_EVENTS = 60


def load_events():
    if not EVENTS_FILE.exists():
        return []
    return json.loads(EVENTS_FILE.read_text(encoding="utf-8"))


def save_events(events):
    EVENTS_FILE.write_text(json.dumps(events[-MAX_EVENTS:], ensure_ascii=False, indent=2), encoding="utf-8")


def build_event(info, event_type, prev_price=None):
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "asin": info["asin"],
        "marketplace": info["marketplace_code"],
        "store_label": info["store_label"],
        "flag": info["flag"],
        "name": info["name"],
        "image": info.get("image"),
        "price": info.get("price"),
        "prev_price": prev_price,
        "link": info["link"],
    }

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


EXCLUDED_NAME_KEYWORDS = ["funda", "sleeve", "sleeves", "lego"]

# Pokémon marca el idioma de la edición en el propio título entre dos puntos,
# ej. "(290-55964 : C : FR : 10 :)" — "FR" ahí es la edición francesa
# colándose vía cross-border en Amazon.es. Pedido explícitamente descartarla
# (2026-08-31, tras verla aparecer tanto en UK/US como en la búsqueda general
# de ES). No usar un simple "in" como con EXCLUDED_NAME_KEYWORDS: "fr" suelto
# aparecería dentro de palabras normales.
FRENCH_EDITION_RE = re.compile(r":\s*FR\s*:")


def is_excluded_by_name(name):
    """Filtra accesorios (ej. fundas de cartas) que aparecen en los
    resultados de búsqueda de la tienda pero no son el producto en sí.
    Cubre ES ("funda") e inglés ("sleeve"/"sleeves") ya que el filtro se
    aplica por igual a ES/UK/US. También filtra ediciones en francés
    coladas cross-border (ver FRENCH_EDITION_RE)."""
    name_lower = (name or "").lower()
    if any(keyword in name_lower for keyword in EXCLUDED_NAME_KEYWORDS):
        return True
    return bool(FRENCH_EDITION_RE.search(name or ""))


def _strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def is_relevant_by_name(name):
    """Algunas páginas de la tienda (sobre todo las más pequeñas, con pocos
    productos reales) traen widgets de Amazon tipo "también te puede
    interesar" con productos totalmente ajenos (cargadores, LEGO, fundas de
    cartas de otros juegos...). Esos ASIN sueltos se cuelan por el fallback
    de enlaces /dp/ de discover_products() y, si tienen botón de compra, se
    tratarían como stock nuevo real. Exigimos que el nombre mencione
    "Pokémon" antes de aceptar cualquier producto, en vez de solo excluir
    palabras concretas."""
    return "pokemon" in _strip_accents((name or "").lower())


# Pistas de categoría por nombre de producto, aplicadas a todas las tiendas
# (a diferencia de "category_pages", que solo cubre lo que Amazon ES lista
# en sus páginas de categoría). Se van añadiendo patrones a medida que se
# detectan casos reales; son regex (sin distinguir mayúsculas/acentos, ya
# que se comparan sobre el nombre sin acentos y con re.IGNORECASE).
CATEGORY_NAME_HINTS = {
    "Cajas ETB": [r"caja de entre\w*", r"caja entrenador elite", r"elite trainer box"],
    "Latas": [r"\blatas?\b"],
    "Colecciones premium": [r"colecci\w*\s+premiu\w*", r"premium collection"],
}

CATEGORY_NAME_PATTERNS = {
    category: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for category, patterns in CATEGORY_NAME_HINTS.items()
}


def categorize_by_name(name):
    if not name:
        return set()
    text = _strip_accents(name)
    return {
        category
        for category, patterns in CATEGORY_NAME_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    }


def assign_categories(name, page_categories=()):
    """Combina las categorías detectadas por página de Amazon con las
    detectadas por nombre. Si un producto no encaja en ninguna, se marca
    como "Otros" en vez de dejarlo sin categoría."""
    categories = set(page_categories) | categorize_by_name(name)
    return sorted(categories) if categories else ["Otros"]


STATUS_PRIORITY = {"compra_directa": 3, "preventa": 2, "invitacion": 1, "no_disponible": 0}


def merge_product_record(existing, new):
    """Cuando el mismo producto aparece en varias páginas de la tienda,
    Amazon a veces muestra precio/estado distintos según el widget (visto
    con Colección Primer Compañero Serie 3: 17,99€ invitación en la ficha
    individual vs 28,98€ compra directa en la tarjeta de "Novedades").
    Nos quedamos con el registro completo (precio+estado+stock) de una
    sola página, para no mezclar campos de fuentes distintas:
    1. Preferimos compra_directa sobre preventa sobre invitacion sobre
       no_disponible — es la opción más ventajosa/accionable para el
       usuario (ver STATUS_PRIORITY).
    2. A igualdad de estado, nos quedamos con el precio más bajo."""
    if existing is None:
        return new
    existing_priority = STATUS_PRIORITY.get(existing.get("status"), 0)
    new_priority = STATUS_PRIORITY.get(new.get("status"), 0)
    if new_priority != existing_priority:
        return new if new_priority > existing_priority else existing

    existing_price = price_to_float(existing.get("price"))
    new_price = price_to_float(new.get("price"))
    if new_price is not None and (existing_price is None or new_price < existing_price):
        return new
    return existing if existing_price is not None else new


PREVENTA_MARKER = "preventa"


def determine_status(has_buy_signal, text_lower, marketplace):
    """Regla de estado compartida entre discover_products (tarjetas de
    tienda) y check_single_product (ficha individual). Preventa se mira
    antes que el botón de compra a propósito: un producto en preventa
    también tiene botón de compra/reserva (has_buy_signal=True), así que
    sin este orden se clasificaría como compra_directa normal — visto en
    B0H6H5C3ZL (Magic: Star Trek Draft Night), texto "Cómpralo en
    preventa ya."/"Precio de Preventa Garantizado" tanto en la ficha
    individual como en la tarjeta de tienda."""
    if PREVENTA_MARKER in text_lower:
        return "preventa"
    if has_buy_signal:
        return "compra_directa"
    if marketplace["invitation_marker"] in text_lower:
        return "invitacion"
    return "no_disponible"


EUR_PREFIX_RE = re.compile(r"^EUR\s*([\d,]+)\.(\d{2})$")


def clean_price(value):
    """Amazon a veces renderiza el precio tachado como el texto literal
    'null' cuando el producto no tiene precio de referencia (visto en
    Amazon.co.uk). Lo tratamos como si no hubiera precio.

    Amazon UK/US a veces muestran el precio en euros con formato inglés
    ("EUR 171.40", visto el 2026-08-25 — probablemente la sesión tiene
    guardada esa preferencia de divisa) en vez del $/£ nativo. Lo
    normalizamos al mismo formato "171,40 €" que usamos en el resto del
    sitio, para que se vea consistente y price_to_float() lo pueda parsear."""
    if not value:
        return None
    value = value.strip()
    if not value or value.lower() == "null":
        return None
    eur_match = EUR_PREFIX_RE.match(value)
    if eur_match:
        integer_part = eur_match.group(1).replace(",", "")
        return f"{integer_part},{eur_match.group(2)} €"
    # Normaliza "24,99€" (sin espacio, visto en el span visible de
    # "Precio mediano") a "24,99 €" para que sea consistente con el resto.
    return re.sub(r"(\d)€$", r"\1 €", value)

PRICE_NUMBER_RE = re.compile(r"(\d+(?:\.\d{3})*),(\d{2})")
DELIVERY_FEE_RE = re.compile(r"Entrega por\s+(\d+(?:\.\d{3})*,\d{2})\s*€")


def add_delivery_fee(price_str, buybox_text):
    """Para productos de vendedores terceros sin envío gratis (ej.
    'Entrega por 11,41 €'), suma el coste de entrega al precio para que el
    precio trackeado sea el coste real total. Solo se usa para productos
    añadidos individualmente (ver "extra_asins"); los de las páginas de
    tienda son casi todos envío gratis Prime, así que no hace falta ahí."""
    fee_match = DELIVERY_FEE_RE.search(buybox_text or "")
    if not fee_match or not price_str:
        return price_str
    price_num = price_to_float(price_str)
    fee_num = price_to_float(fee_match.group(1) + " €")
    if price_num is None or fee_num is None:
        return price_str
    total = price_num + fee_num
    integer_part, decimal_part = f"{total:.2f}".split(".")
    # Separador de miles "." al estilo español, solo si hace falta.
    integer_part = f"{int(integer_part):,}".replace(",", ".")
    return f"{integer_part},{decimal_part} €"




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
    write_snapshot(SNAPSHOT_FILE, products, game="Pokémon")


def load_static_fnac_products():
    """Carga ofertas históricas de Fnac solo para publicarlas en la web.

    No se mezclan con el estado ni con las alertas: su disponibilidad se
    presenta siempre como no confirmada hasta que el scraper pueda volver a
    comprobar Fnac de forma fiable.
    """
    if not FNAC_CATALOG_FILE.exists():
        return {}
    data = json.loads(FNAC_CATALOG_FILE.read_text(encoding="utf-8"))
    rows = data.get("products") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("fnac_catalog.json debe contener una lista 'products'")

    products = {}
    for row in rows:
        product_id = str(row.get("asin") or "").strip()
        name = str(row.get("name") or "").strip()
        link = str(row.get("link") or "").strip()
        if not product_id or not name or not link.startswith("https://www.fnac.es/"):
            raise ValueError(f"Producto estático de Fnac incompleto: {product_id or '<sin id>'}")
        products[f"FNAC:{product_id}"] = {
            "asin": product_id,
            "marketplace_code": "FNAC",
            "store_label": "Fnac",
            "flag": "",
            "name": name,
            "image": row.get("image"),
            "price": row.get("price"),
            "original_price": row.get("original_price"),
            "status": "sin_confirmar",
            "stock": None,
            "link": link,
            "categories": assign_categories(name),
            "first_seen": row.get("first_seen"),
        }
    return products


def _telegram_post(method, data, files=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    resp = requests.post(url, data=data, files=files, timeout=15)
    resp.raise_for_status()


def send_telegram_message(text):
    _telegram_post(
        "sendMessage",
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        },
    )


def send_telegram_photo(photo_url, caption):
    _telegram_post(
        "sendPhoto",
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML",
        },
    )


def send_telegram_photo_bytes(image_bytes, caption):
    _telegram_post(
        "sendPhoto",
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption,
            "parse_mode": "HTML",
        },
        files={"photo": ("product.jpg", image_bytes, "image/jpeg")},
    )


FLAG_FILES = {
    # Las tiendas españolas (ES/ECI/CAR/FNAC/TRU/GAME/MM) no llevan bandera —
    # se da por hecho que son tiendas españolas, igual que en la web (ver
    # FLAG_ICONS en product-utils.js). Solo UK/US la necesitan para
    # distinguirse.
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
    # GAME tampoco es un "marketplace" — mismo motivo que Fnac arriba.
    if GAME_STORE["cookies_file"].exists():
        raw_cookies = json.loads(GAME_STORE["cookies_file"].read_text(encoding="utf-8"))
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


class ScrapeError(RuntimeError):
    """La consulta no permite afirmar cuál es el stock actual."""


async def discover_products(page, label, url, marketplace):
    """Descubre todos los productos y su estado (nombre, precio, disponibilidad)
    directamente desde las tarjetas de la página de la tienda, sin necesidad de
    visitar cada ficha de producto individual."""
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if response is None or response.status >= 400:
        raise ScrapeError(f"Respuesta HTTP inválida en {marketplace['code']}/{label}")
    await page.wait_for_timeout(3000)

    await try_click_continue(page, label, marketplace)

    for _ in range(6):
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(800)

    title = await page.title()
    print(f"ℹ️ [{marketplace['code']}/{label}] Título de la página cargada: {title!r}")
    print(f"ℹ️ [{marketplace['code']}/{label}] URL final tras la carga: {page.url}")

    body_text = (await page.inner_text("body")).lower()
    is_captcha = any(marker in body_text for marker in CAPTCHA_MARKERS)
    if is_captcha:
        print(f"❌ Amazon devolvió una verificación anti-bot (captcha) en vez de la tienda ({marketplace['code']}/{label}).")

    # Volcado de captura/HTML solo cuando hace falta depurar (DEBUG=1) o
    # cuando hay un captcha real que investigar — evita escribir 6 capturas +
    # 6 HTMLs en cada ejecución de producción sin que nadie los consuma.
    if is_captcha or os.environ.get("DEBUG") == "1":
        slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        DEBUG_DIR.mkdir(exist_ok=True)
        await page.screenshot(path=str(DEBUG_DIR / f"store_page_{marketplace['code']}_{slug}.png"), full_page=True)
        (DEBUG_DIR / f"store_page_{marketplace['code']}_{slug}.html").write_text(await page.content(), encoding="utf-8")

    if is_captcha:
        raise ScrapeError(f"Captcha en {marketplace['code']}/{label}")

    tiles = await page.eval_on_selector_all(
        "[data-asin]",
        """els => els.map(el => {
            const asin = el.getAttribute('data-asin');
            const titleEl = el.querySelector('h2[aria-label]');
            const name = titleEl ? titleEl.getAttribute('aria-label') : null;
            const priceEl = el.querySelector('[data-cy="price-recipe"] .a-price .a-offscreen');
            const price = priceEl ? priceEl.textContent.trim() : null;
            // El precio tachado ("Precio mediano") a veces trae "null" en el
            // span accesible (.a-offscreen) y el valor real solo está en el
            // span visible (aria-hidden) hermano — probar ambos.
            // OJO: no basta con ".a-text-price" — el precio por unidad
            // ("0,04 €/unidad" en packs grandes) usa esa misma clase, y sin
            // este filtro se colaba como si fuera el precio anterior. El
            // precio tachado real siempre lleva data-a-strike="true".
            const originalPriceBox = el.querySelector('[data-cy="price-recipe"] .a-text-price[data-a-strike="true"]');
            let originalPrice = null;
            if (originalPriceBox) {
                const offscreen = originalPriceBox.querySelector('.a-offscreen');
                const offscreenText = offscreen ? offscreen.textContent.trim() : null;
                if (offscreenText && offscreenText.toLowerCase() !== 'null') {
                    originalPrice = offscreenText;
                } else {
                    const visible = originalPriceBox.querySelector('[aria-hidden="true"]');
                    originalPrice = visible ? visible.textContent.trim() : null;
                }
            }
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
        # Un contenedor sin nombre no demuestra que el producto esté agotado.
        # Su enlace podrá comprobarse mediante el fallback individual.
        if not t.get("name"):
            continue

        text = t.get("text") or ""
        text_lower = text.lower()

        # Las páginas de búsqueda (a diferencia de las páginas de tienda
        # propias) mezclan resultados patrocinados con los orgánicos —
        # pedido explícitamente que se descarten, ya que no son hallazgos
        # reales de stock sino colocaciones pagadas de terceros.
        if "patrocinado" in text_lower or "sponsored" in text_lower:
            continue

        status = determine_status(t.get("hasAddToCart"), text_lower, marketplace)

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

    if not products and not (fallback_asins and marketplace.get("allow_individual_fallback", True)):
        raise ScrapeError(f"Sin productos verificables en {marketplace['code']}/{label}; se conserva la publicación anterior")
    return products, fallback_asins


async def discover_bestsellers_products(page, label, url, marketplace):
    """Descubre productos desde una página "Más vendidos" de Amazon
    (gp/bestsellers/...) — usada para Nintendo/PlayStation/Xbox. Su
    plantilla de tarjeta es distinta a la de resultados de búsqueda: no
    tiene ``h2[aria-label]`` ni botón "Añadir a la cesta"; el título es el
    primer enlace a "/dp/" que no sea el de la imagen (que va con
    aria-hidden), y el precio usa una clase con sufijo generado
    (``p13n-sc-price``, comprobado el 2026-09-17), así que se busca por
    coincidencia parcial. Un producto sin precio visible en la tarjeta se
    marca no_disponible directamente (comprobado a mano que casi siempre
    es un agotado real). A veces (visto en producción el 2026-09-17, ~1 de
    cada 6 páginas) la rejilla tarda más de lo normal en cargar y una
    carrera fija de scrolls la pilla vacía, así que el scroll se repite
    hasta que el recuento de tarjetas se estabiliza en vez de un número
    fijo de iteraciones, igual que discover_eci_products."""
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if response is None or response.status >= 400:
        raise ScrapeError(f"Respuesta HTTP inválida en {marketplace['code']}/{label}")
    await page.wait_for_timeout(3000)

    prev_count = -1
    for _ in range(15):
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(700)
        count = await page.eval_on_selector_all("[data-asin]", "els => els.filter(e => e.getAttribute('data-asin')).length")
        if count == prev_count:
            break
        prev_count = count

    title = await page.title()
    print(f"ℹ️ [{marketplace['code']}/{label}] Título de la página cargada: {title!r}")

    body_text = (await page.inner_text("body")).lower()
    if any(marker in body_text for marker in CAPTCHA_MARKERS):
        raise ScrapeError(f"Captcha en {marketplace['code']}/{label}")

    tiles = await page.eval_on_selector_all(
        "[data-asin]",
        """els => els.filter(e => e.getAttribute('data-asin')).map(el => {
            const asin = el.getAttribute('data-asin');
            const titleLink = el.querySelector('a[href*="/dp/"]:not([aria-hidden="true"])');
            const name = titleLink ? titleLink.textContent.trim() : null;
            const priceEl = el.querySelector('[class*="p13n-sc-price"]');
            const price = priceEl ? priceEl.textContent.trim() : null;
            const imageEl = el.querySelector('img');
            const image = imageEl ? imageEl.src : null;
            return { asin, name, price, image };
        })""",
    )

    products = {}
    for t in tiles:
        asin = t.get("asin")
        if not asin or not ASIN_VALID_RE.match(asin) or asin in products:
            continue
        if not t.get("name"):
            continue
        # Sin comprobación individual a propósito: en la práctica, un
        # producto sin precio en la tarjeta resulta agotado casi siempre
        # (15 de 16 comprobados a mano el 2026-09-17) — no compensa el
        # coste de visitar cada ficha para confirmar el caso raro restante.
        price = clean_price(t.get("price"))
        products[asin] = {
            "name": t["name"],
            "price": price,
            "original_price": None,
            "image": t.get("image"),
            "stock": None,
            "status": "compra_directa" if price else "no_disponible",
        }

    if not products:
        raise ScrapeError(f"Sin productos verificables en {marketplace['code']}/{label}; se conserva la publicación anterior")
    return products, set()


async def discover_eci_products(page, label, url):
    """Descubre productos de El Corte Inglés desde una página de búsqueda.
    Sin cookies/sesión (búsqueda pública) y sin flujo de invitación —
    solo compra_directa (botón "Añadir" presente) o no_disponible."""
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if response is None or response.status >= 400:
        raise ScrapeError(f"Respuesta HTTP inválida en ECI/{label}")
    await page.wait_for_timeout(2500)

    prev_count = -1
    for _ in range(15):
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(700)
        count = await page.eval_on_selector_all("article[id^='product-']", "els => els.length")
        if count == prev_count:
            break
        prev_count = count

    title = await page.title()
    print(f"ℹ️ [ECI/{label}] Título de la página cargada: {title!r}")

    body_text = (await page.inner_text("body")).lower()
    if any(marker in body_text for marker in CAPTCHA_MARKERS):
        raise ScrapeError(f"Captcha en ECI/{label}")

    tiles = await page.eval_on_selector_all(
        "article[id^='product-']",
        """els => els.map(el => {
            const name = el.getAttribute('aria-label');
            const linkEl = el.querySelector('[data-url]');
            const relUrl = linkEl ? linkEl.getAttribute('data-url') : null;
            const priceEl = el.querySelector('[class*="price"]');
            const priceText = priceEl ? priceEl.textContent.trim() : null;
            const imageEl = el.querySelector('img');
            const image = imageEl ? imageEl.src : null;
            const buttons = Array.from(el.querySelectorAll('button')).map(b => (b.textContent || '').trim());
            const hasAddButton = buttons.some(t => t.toLowerCase() === 'añadir');
            return { id: el.id, name, relUrl, priceText, image, hasAddButton };
        })""",
    )

    products = {}
    for t in tiles:
        match = ECI_ID_RE.match(t.get("id") or "")
        if not match:
            continue
        product_id = match.group(1)
        if product_id in products:
            continue
        if not t.get("name") or not t.get("relUrl"):
            raise ScrapeError(f"Tarjeta incompleta en ECI/{label}: {product_id}")

        price_match = ECI_PRICE_RE.search(t.get("priceText") or "")
        rel_url = t.get("relUrl")

        products[product_id] = {
            "name": t.get("name") or product_id,
            "price": f"{price_match.group(1)} €" if price_match else None,
            "original_price": None,
            "image": t.get("image"),
            "stock": None,
            # NOTA: solo hemos visto productos con stock hasta ahora — el
            # patrón de "no_disponible" (botón "Añadir" ausente) es
            # best-guess sin verificar todavía contra un producto agotado
            # real. Revisar la primera vez que aparezca uno.
            "status": "compra_directa" if t.get("hasAddButton") else "no_disponible",
            "url": f"https://www.elcorteingles.es{rel_url}" if rel_url else None,
        }

    if not products:
        raise ScrapeError(f"Sin productos verificables en ECI/{label}; se conserva la publicación anterior")
    return products


async def discover_carrefour_products(page, label, url):
    """Descubre productos de Carrefour desde una página de búsqueda. Sin
    cookies/sesión (búsqueda pública). No hace clic en el banner de
    cookies — ver la nota junto a CARREFOUR_STORE, romper esa regla vacía
    la rejilla de resultados."""
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if response is None or response.status >= 400:
        raise ScrapeError(f"Respuesta HTTP inválida en Carrefour/{label}")
    await page.wait_for_timeout(3500)

    prev_count = -1
    for _ in range(10):
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(700)
        count = await page.eval_on_selector_all('article[data-test="search-grid-result"]', "els => els.length")
        if count == prev_count:
            break
        prev_count = count

    title = await page.title()
    print(f"ℹ️ [Carrefour/{label}] Título de la página cargada: {title!r}")

    body_text = (await page.inner_text("body")).lower()
    if any(marker in body_text for marker in CAPTCHA_MARKERS):
        raise ScrapeError(f"Captcha en Carrefour/{label}")

    tiles = await page.eval_on_selector_all(
        'article[data-test="search-grid-result"]',
        """els => els.map(el => {
            const link = el.querySelector('a[data-test="result-link"]');
            const nameEl = el.querySelector('a[data-test="result-title"]');
            const priceEl = el.querySelector('[data-test="result-current-price"]');
            const statusEl = el.querySelector('[data-test="result-add-to-cart"]');
            const imageEl = el.querySelector('img[data-test="result-picture-image"]');
            return {
                href: link ? link.getAttribute('href') : null,
                name: nameEl ? nameEl.textContent.trim() : null,
                priceText: priceEl ? priceEl.textContent.trim() : null,
                statusText: statusEl ? statusEl.textContent.trim() : null,
                image: imageEl ? imageEl.src : null,
            };
        })""",
    )

    products = {}
    for t in tiles:
        href = t.get("href") or ""
        match = CARREFOUR_ID_RE.search(href)
        if not match:
            continue
        product_id = match.group(1)
        if product_id in products:
            continue
        if not t.get("name") or not href:
            raise ScrapeError(f"Tarjeta incompleta en Carrefour/{label}: {product_id}")

        price_match = ECI_PRICE_RE.search(t.get("priceText") or "")
        status_text = _strip_accents((t.get("statusText") or "").lower())

        products[product_id] = {
            "name": t.get("name"),
            "price": f"{price_match.group(1)} €" if price_match else None,
            "original_price": None,
            "image": t.get("image"),
            "stock": None,
            # NOTA: al 2026-09-15 todo lo listado es preventa ("Lanzamiento
            # el ...", botón "Agotado temporalmente") — no hay todavía
            # ningún producto comprable de verdad para confirmar el texto
            # exacto del botón cuando SÍ hay stock. Revisar en cuanto
            # alguno lo tenga.
            "status": "no_disponible" if "agotado" in status_text or "no disponible" in status_text else "compra_directa",
            "url": href,
        }

    if not products:
        raise ScrapeError(f"Sin productos verificables en Carrefour/{label}; se conserva la publicación anterior")
    return products


async def discover_toysrus_products(page, label, query):
    """Descubre productos Pokémon llamando directamente a la API de búsqueda
    (Empathy.co) que usa toysrus.es internamente, en vez de scrapear su HTML
    — ver la nota junto a TOYSRUS_STORE. Sin cookies ni sesión."""
    products = {}
    start = 0
    rows = 100
    num_found = None
    while num_found is None or start < num_found:
        response = await page.request.get(
            TOYSRUS_SEARCH_API,
            params={
                "query": query,
                "start": str(start),
                "rows": str(rows),
                "instance": "toysrus",
                "lang": "es",
                "scope": "desktop",
                "currency": "EUR",
            },
        )
        if response.status >= 400:
            raise ScrapeError(f"Respuesta HTTP inválida en ToysRUs/{label} (status {response.status})")
        data = await response.json()
        catalog = data.get("catalog") or {}
        content = catalog.get("content") or []
        num_found = catalog.get("numFound", len(content))

        for item in content:
            product_id = item.get("id") or item.get("__id")
            name = item.get("name")
            if not product_id or not name or product_id in products:
                continue
            price = item.get("price")
            products[product_id] = {
                "name": name,
                "price": f"{price:.2f} €".replace(".", ",") if isinstance(price, (int, float)) else None,
                "original_price": None,
                "image": item.get("image"),
                "stock": None,
                "status": "compra_directa" if item.get("availability") else "no_disponible",
                "url": item.get("url"),
            }

        if not content:
            break
        start += len(content)

    if not products:
        raise ScrapeError(f"Sin productos verificables en ToysRUs/{label}; se conserva la publicación anterior")
    return products


# GAME.es vende cromos/cartas coleccionables (incluido Pokémon) bajo su
# categoría "Merchandising", además de videojuegos. Confirmado el
# 2026-09-18: la búsqueda pública devuelve una rejilla real de tarjetas
# ``.search-item`` (id="search-item-<código>"), con nombre y precio ya en
# los atributos ``data-list-item-*`` del enlace principal — no hace falta
# aceptar el banner de cookies para que el DOM esté poblado, solo para que
# no lo tape visualmente (por eso el intento de clic va en un try/except,
# sin bloquear si no aparece).
#
# El estado se lee del texto de ``.buy--type`` dentro de la tarjeta:
# "Comprar" (disponible) y "Reservar" (preventa) son los únicos que hemos
# visto usar de verdad; "Próximamente" y "ver ficha" (agotado — comprobado
# a mano visitando la ficha, que muestra "agotado"/"avísame") se tratan
# igual que cualquier texto desconocido: no disponible, por seguridad.
GAME_STORE = {
    "code": "GAME",
    "flag": "",
    "store_label": "GAME",
    "tag": None,
    # Cookies opcionales — sobre todo por "CookieConsent" (ya aceptado), que
    # evita el banner de consentimiento por completo en vez de depender del
    # clic en new_context/discover_game_products, más frágil.
    "cookies_file": Path(__file__).parent / "game_cookies.json",
    "pages": [
        ("Pokémon Aniversario", "https://www.game.es/buscar/pokemon%20aniversario"),
    ],
}
GAME_ID_RE = re.compile(r"^search-item-(\d+)$")
GAME_STATUS_MAP = {"comprar": "compra_directa", "reservar": "preventa"}


async def discover_game_products(page, label, url):
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if response is None or response.status >= 400:
        raise ScrapeError(f"Respuesta HTTP inválida en GAME/{label}")
    await page.wait_for_timeout(2500)

    try:
        accept_btn = await page.query_selector("#onetrust-accept-btn-handler, button:has-text('Aceptar')")
        if accept_btn:
            await accept_btn.click()
            await page.wait_for_timeout(500)
    except Exception:
        pass

    prev_count = -1
    for _ in range(10):
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(600)
        count = await page.eval_on_selector_all(".search-item", "els => els.length")
        if count == prev_count:
            break
        prev_count = count

    title = await page.title()
    print(f"ℹ️ [GAME/{label}] Título de la página cargada: {title!r}")

    body_text = (await page.inner_text("body")).lower()
    if any(marker in body_text for marker in CAPTCHA_MARKERS):
        raise ScrapeError(f"Captcha en GAME/{label}")

    tiles = await page.eval_on_selector_all(
        ".search-item",
        """els => els.map(el => {
            const link = el.querySelector('a.figure');
            const name = link ? link.getAttribute('data-list-item-name') : null;
            const price = link ? link.getAttribute('data-list-item-price') : null;
            const href = link ? link.getAttribute('href') : null;
            const img = el.querySelector('img');
            const image = img ? (img.getAttribute('data-src') || img.src) : null;
            const buyType = el.querySelector('.buy--type');
            const statusText = buyType ? buyType.textContent.trim() : '';
            return { id: el.id, name, price, href, image, statusText };
        })""",
    )

    products = {}
    for t in tiles:
        match = GAME_ID_RE.match(t.get("id") or "")
        if not match:
            continue
        product_id = match.group(1)
        if product_id in products or not t.get("name") or not t.get("href"):
            continue

        status_text = _strip_accents((t.get("statusText") or "").lower())
        price_raw = t.get("price")
        try:
            price = f"{float(price_raw):.2f} €".replace(".", ",") if price_raw else None
        except ValueError:
            price = None
        href = t["href"]

        products[product_id] = {
            "name": t["name"],
            "price": price,
            "original_price": None,
            "image": t.get("image"),
            "stock": None,
            "status": GAME_STATUS_MAP.get(status_text, "no_disponible"),
            "url": href if href.startswith("http") else f"https://www.game.es{href}",
        }

    if not products:
        raise ScrapeError(f"Sin productos verificables en GAME/{label}; se conserva la publicación anterior")
    return products


# MediaMarkt (mediamarkt.es) sirve su búsqueda ya renderizada en el HTML
# (confirmado el 2026-09-18 con una petición HTTP directa, sin navegador: 200
# OK, CF-Cache-Status HIT, sin challenge ni bloqueo — no hace falta cookies
# ni pasar por la API interna como con ToysRUs). Cada tarjeta es un
# ``article[data-test="mms-product-card"]``; el precio aparece dos veces en
# el mismo bloque (una versión corta "39,–€" y la completa "39,00€"), así que
# se extrae con regex sobre el texto completo en vez de fiarse de un único
# selector. La disponibilidad se lee del sufijo de
# ``[data-test^="mms-cofr-delivery_"]`` ("AVAILABLE" cuando hay stock; si no
# existe ese bloque, se trata como agotado).
MEDIAMARKT_STORE = {
    "code": "MM",
    "flag": "",
    "store_label": "MediaMarkt",
    "tag": None,
    "pages": [
        ("Pokémon TCG", "https://www.mediamarkt.es/es/search.html?query=pokemon%20tcg"),
    ],
}
MEDIAMARKT_PRICE_RE = re.compile(r"(\d+,\d{2})\s*€")
MEDIAMARKT_ID_RE = re.compile(r"-(\d+)\.html$")


async def discover_mediamarkt_products(page, label, url):
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if response is None or response.status >= 400:
        raise ScrapeError(f"Respuesta HTTP inválida en MediaMarkt/{label} (status {response.status if response else 'sin respuesta'})")
    await page.wait_for_timeout(1500)

    body_text = (await page.inner_text("body")).lower()
    if any(marker in body_text for marker in CAPTCHA_MARKERS):
        raise ScrapeError(f"Captcha en MediaMarkt/{label}")

    tiles = await page.eval_on_selector_all(
        'article[data-test="mms-product-card"]',
        """els => els.map(el => {
            const link = el.querySelector('a[data-test="mms-router-link-product-list-item-link_mp"]');
            const name = el.querySelector('[data-test="product-title"]');
            const img = el.querySelector('[data-test="product-image"] img');
            const priceBox = el.querySelector('[data-test="mms-price"]');
            const delivery = el.querySelector('[data-test^="mms-cofr-delivery_"]');
            return {
                href: link ? link.getAttribute('href') : null,
                name: name ? name.textContent.trim() : null,
                image: img ? img.getAttribute('src') : null,
                priceText: priceBox ? priceBox.textContent : null,
                deliveryState: delivery ? delivery.getAttribute('data-test') : null,
            };
        })""",
    )

    products = {}
    for t in tiles:
        href = t.get("href")
        name = t.get("name")
        if not href or not name:
            continue
        id_match = MEDIAMARKT_ID_RE.search(href)
        if not id_match:
            continue
        product_id = id_match.group(1)
        if product_id in products:
            continue

        price_match = MEDIAMARKT_PRICE_RE.search(t.get("priceText") or "")
        price = f"{price_match.group(1)} €" if price_match else None
        available = bool(t.get("deliveryState") and t["deliveryState"].endswith("AVAILABLE"))

        products[product_id] = {
            "name": name,
            "price": price,
            "original_price": None,
            "image": t.get("image"),
            "stock": None,
            "status": "compra_directa" if available else "no_disponible",
            "url": href if href.startswith("http") else f"https://www.mediamarkt.es{href}",
        }

    if not products:
        raise ScrapeError(f"Sin productos verificables en MediaMarkt/{label}; se conserva la publicación anterior")
    return products


# TodoConsolas (todoconsolas.com, tienda de segunda mano) usa un widget de
# búsqueda de terceros llamado Motive (de ahí los parámetros "mot_p"/"mot_q"
# de la URL de la ficha del sitio) para la búsqueda con filtros — la página
# de resultados se rellena por JavaScript dentro de la propia portada, así
# que ni una petición HTTP simple ni un render de página sin más lo detectan
# (confirmado el 2026-09-19 con capturas de red reales: el título y el HTML
# nunca cambian, todo pasa por una llamada a la API). Esa API
# (search.api.motive.co) responde JSON limpio, sin cookies ni bloqueo,
# incluida disponibilidad real (``availability.allow_order``/``stock``) y
# reserva (``f4: ["Sí"]``) — bastante mejor que intentar leer el HTML
# público de todoconsolas.com, que si bloquea a Playwright/GitHub Actions
# con 429/403 de forma consistente.
# facet_f8=Coleccionismo + facet_brand=The+Pokemon+Company confirmados a
# mano en el sitio como los filtros que dan los 57 resultados reales (no la
# búsqueda genérica "pokemon tcg", que mezcla vídeojuegos/merchandising no
# relacionados con el TCG).
TODOCONSOLAS_STORE = {
    "code": "TC",
    "flag": "",
    "store_label": "TodoConsolas",
    "tag": None,
    "pages": [("Pokémon TCG", "pokemon tcg")],
}
TODOCONSOLAS_SEARCH_API = "https://search.api.motive.co/search"
TODOCONSOLAS_ENGINE_ID = "6f845b3e-ebfd-4e7b-9a9c-73e963abd5d8"


async def discover_todoconsolas_products(page, label, query):
    session_id = str(uuid.uuid4())
    rows = 95  # la API rechaza rows >= 96 con un 400
    docs = []
    start = 0
    while True:
        response = await page.request.get(
            TODOCONSOLAS_SEARCH_API,
            params={
                "x-engine-id": TODOCONSOLAS_ENGINE_ID,
                "x-origin": "default",
                "x-query-session-id": session_id,
                "x-search-id": session_id,
                "internal": "true",
                "query": query,
                "start": str(start),
                "rows": str(rows),
                "origin": "url:external",
                "variantSelector": "false",
                "facet_f8": "Coleccionismo",
                "facet_brand": "The Pokemon Company",
            },
        )
        if response.status >= 400:
            raise ScrapeError(f"Respuesta HTTP inválida en TodoConsolas/{label} (status {response.status})")
        data = await response.json()
        page_docs = (data.get("hits") or {}).get("docs") or []
        docs.extend(page_docs)
        total = (data.get("pagination") or {}).get("total", len(docs))
        start += rows
        if not page_docs or start >= total:
            break

    products = {}
    for doc in docs:
        product_id = doc.get("id")
        name = doc.get("name")
        if not product_id or not name:
            continue

        availability = doc.get("availability") or {}
        allow_order = bool(availability.get("allow_order"))
        is_reservable = "Sí" in (doc.get("f4") or [])
        if allow_order:
            status = "compra_directa"
        elif is_reservable:
            status = "preventa"
        else:
            status = "no_disponible"

        price = doc.get("price") or {}
        price_value = price.get("regular")
        images = doc.get("images") or []

        products[product_id] = {
            "name": name,
            "price": f"{price_value:.2f} €".replace(".", ",") if isinstance(price_value, (int, float)) else None,
            "original_price": None,
            "image": images[0]["url"] if images else None,
            "stock": availability.get("stock"),
            "status": status,
            "url": doc.get("url"),
        }

    if not products:
        raise ScrapeError(f"Sin productos verificables en TodoConsolas/{label}; se conserva la publicación anterior")
    return products


async def check_single_product(page, asin, marketplace, include_delivery=False):
    """Comprobación individual de respaldo para productos cuya tarjeta de
    tienda no expone precio/disponibilidad directamente.

    include_delivery: suma el coste de entrega al precio (ver
    add_delivery_fee) — solo para productos añadidos a mano vía
    "extra_asins", donde a menudo son de vendedores terceros sin envío
    gratis."""
    url = f"https://www.{marketplace['domain']}/dp/{asin}"
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if response is None or response.status >= 400:
            raise ScrapeError(f"Respuesta HTTP inválida para {asin}")
        await page.wait_for_timeout(random.uniform(1200, 2500))
        await try_click_continue(page, asin, marketplace)

        title_el = await page.query_selector("#productTitle")
        if not title_el:
            raise ScrapeError(f"Ficha sin título verificable: {asin}")
        name = (await title_el.inner_text()).strip()
        if not name:
            raise ScrapeError(f"Ficha sin nombre verificable: {asin}")

        buy_button = await page.query_selector("#add-to-cart-button, #buy-now-button")

        # Igual que discover_products: buscar el marcador de invitación en
        # todo el texto de la buybox, no solo el botón "solicitar invitación"
        # — esa fase concreta no tiene botón cuando ya se solicitó antes
        # ("Invitación solicitada, ¡gracias!"), pero sigue siendo estado de
        # invitación real.
        buybox_el = await page.query_selector("#buybox, #desktop_buybox")
        buybox_text = (await buybox_el.inner_text()) if buybox_el else ""
        status = determine_status(buy_button, buybox_text.lower(), marketplace)

        price_el = await page.query_selector(".a-price .a-offscreen")
        price = clean_price((await price_el.inner_text()) if price_el else None)
        if include_delivery:
            price = add_delivery_fee(price, buybox_text)

        image_el = await page.query_selector("#landingImage, #imgTagWrapperId img")
        image = (await image_el.get_attribute("src")) if image_el else None

        availability_el = await page.query_selector("#availability")
        availability_text = (await availability_el.inner_text()) if availability_el else ""
        stock_match = marketplace["stock_count_re"].search(availability_text or buybox_text)
        stock = stock_match.group(1) if stock_match else None

        return {"name": name, "price": price, "original_price": None, "image": image, "stock": stock, "status": status}
    except Exception as e:
        raise ScrapeError(f"No se pudo comprobar {asin} ({marketplace['code']})") from e


async def main():
    state = load_state()
    events = load_events()
    products = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await new_context(browser)
        page = await context.new_page()

        for marketplace in MARKETPLACES:
            marketplace_products = {}
            fallback_asins = set()
            category_pages = marketplace.get("category_pages", set())
            asin_categories = defaultdict(set)

            for label, url in marketplace["pages"]:
                print(f"🔍 Descubriendo productos en la tienda ({marketplace['code']}/{label})...")
                try:
                    page_products, page_fallback = await discover_products(page, label, url, marketplace)
                    print(f"📦 [{marketplace['code']}/{label}] {len(page_products)} productos encontrados")
                    for asin, info in page_products.items():
                        marketplace_products[asin] = merge_product_record(marketplace_products.get(asin), info)
                        if label in category_pages:
                            asin_categories[asin].add(label)
                    fallback_asins.update(page_fallback)
                except Exception as e:
                    print(f"❌ No se pudo cargar la página de la tienda ({marketplace['code']}/{label}): {e!r}")
                    raise

            fallback_asins -= marketplace_products.keys()
            if fallback_asins and not marketplace.get("allow_individual_fallback", True):
                print(f"⏭️ [{marketplace['code']}] Omitiendo {len(fallback_asins)} productos sin datos en la tarjeta (fallback individual desactivado para este marketplace).")
            elif fallback_asins:
                print(f"🔎 [{marketplace['code']}] Comprobando individualmente {len(fallback_asins)} productos sin datos en la tarjeta...")
                skipped_irrelevant = 0
                for i, asin in enumerate(fallback_asins):
                    if i > 0:
                        await asyncio.sleep(random.uniform(2, 5))
                    result = await check_single_product(page, asin, marketplace)
                    if not result:
                        continue
                    # Este ASIN viene de un enlace suelto en la página (no de
                    # una tarjeta de producto real), así que es el punto donde
                    # se cuelan widgets de "también te puede interesar" con
                    # productos totalmente ajenos (visto con un cargador
                    # Anker, un set de LEGO...). Filtramos aquí mismo, antes
                    # de darlo por bueno, en vez de confiar solo en el filtro
                    # general de más abajo.
                    if not is_relevant_by_name(result["name"]) or is_excluded_by_name(result["name"]):
                        skipped_irrelevant += 1
                        continue
                    marketplace_products[asin] = result
                if skipped_irrelevant:
                    print(f"⏭️ [{marketplace['code']}] Omitiendo {skipped_irrelevant} productos ajenos encontrados por enlace suelto (no son Pokémon o son accesorios).")

            extra_asins = {
                a: cfg for a, cfg in marketplace.get("extra_asins", {}).items()
                if a not in marketplace_products
            }
            if extra_asins:
                print(f"🔎 [{marketplace['code']}] Comprobando {len(extra_asins)} productos añadidos a mano...")
                for i, (asin, cfg) in enumerate(extra_asins.items()):
                    if i > 0:
                        await asyncio.sleep(random.uniform(2, 5))
                    result = await check_single_product(page, asin, marketplace, include_delivery=True)
                    if not result:
                        continue
                    max_price = cfg.get("max_price")
                    price_num = price_to_float(result.get("price"))
                    if max_price is not None and price_num is not None and price_num > max_price:
                        print(f"⏭️ [{marketplace['code']}] {asin} supera el precio máximo ({result['price']} > {max_price} €), no se incluye esta vez.")
                        continue
                    marketplace_products[asin] = result

            if marketplace.get("exclude_out_of_stock"):
                out_of_stock = {a for a, i in marketplace_products.items() if i["status"] == "no_disponible"}
                if out_of_stock:
                    print(f"⏭️ [{marketplace['code']}] Omitiendo {len(out_of_stock)} productos agotados de la web (pero sí se actualiza su estado, para poder detectar el próximo restock).")
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

            excluded_by_name = {
                a for a, i in marketplace_products.items() if is_excluded_by_name(i["name"])
            }
            if excluded_by_name:
                print(f"⏭️ [{marketplace['code']}] Omitiendo {len(excluded_by_name)} productos no relevantes por nombre (fundas/accesorios).")
                for asin in excluded_by_name:
                    del marketplace_products[asin]

            not_pokemon = {
                a for a, i in marketplace_products.items() if not is_relevant_by_name(i["name"])
            }
            if not_pokemon:
                print(f"⏭️ [{marketplace['code']}] Omitiendo {len(not_pokemon)} productos ajenos a Pokémon (colados por el fallback de enlaces sueltos).")
                for asin in not_pokemon:
                    del marketplace_products[asin]

            for asin, info in marketplace_products.items():
                info["asin"] = asin
                info["marketplace_code"] = marketplace["code"]
                info["store_label"] = marketplace["store_label"]
                info["flag"] = marketplace["flag"]
                if marketplace.get("asin_is_amazon_es"):
                    info["link"] = f"https://www.amazon.es/dp/{asin}?tag=enkairito-21"
                else:
                    info["link"] = f"https://www.{marketplace['domain']}/dp/{asin}?tag={marketplace['tag']}"
                info["categories"] = assign_categories(info["name"], asin_categories.get(asin, ()))
                products[f"{marketplace['code']}:{asin}"] = info

        eci_products = {}
        for label, url in ECI_STORE["pages"]:
            print(f"🔍 Descubriendo productos en la tienda (ECI/{label})...")
            try:
                page_products = await discover_eci_products(page, label, url)
                print(f"📦 [ECI/{label}] {len(page_products)} productos encontrados")
                eci_products.update(page_products)
            except Exception as e:
                print(f"❌ No se pudo cargar la página de El Corte Inglés ({label}): {e!r}")
                raise

        eci_out_of_stock = {a for a, i in eci_products.items() if i["status"] == "no_disponible"}
        if eci_out_of_stock:
            print(f"⏭️ [ECI] Omitiendo {len(eci_out_of_stock)} productos agotados de la web (pero sí se actualiza su estado, para poder detectar el próximo restock).")
            for product_id in eci_out_of_stock:
                key = f"ECI:{product_id}"
                prev_first_seen = state.get(key, {}).get("first_seen")
                state[key] = {
                    "name": eci_products[product_id]["name"],
                    "status": "no_disponible",
                    "stock": None,
                    "first_seen": prev_first_seen or datetime.now(timezone.utc).isoformat(),
                }
                del eci_products[product_id]

        eci_excluded_by_name = {
            a for a, i in eci_products.items() if is_excluded_by_name(i["name"])
        }
        if eci_excluded_by_name:
            print(f"⏭️ [ECI] Omitiendo {len(eci_excluded_by_name)} productos no relevantes por nombre (fundas/accesorios).")
            for product_id in eci_excluded_by_name:
                del eci_products[product_id]

        eci_not_pokemon = {
            a for a, i in eci_products.items() if not is_relevant_by_name(i["name"])
        }
        if eci_not_pokemon:
            print(f"⏭️ [ECI] Omitiendo {len(eci_not_pokemon)} productos ajenos a Pokémon.")
            for product_id in eci_not_pokemon:
                del eci_products[product_id]

        for product_id, info in eci_products.items():
            info["asin"] = product_id
            info["marketplace_code"] = ECI_STORE["code"]
            info["store_label"] = ECI_STORE["store_label"]
            info["flag"] = ECI_STORE["flag"]
            info["link"] = info["url"]
            info["categories"] = assign_categories(info["name"])
            products[f"ECI:{product_id}"] = info

        # Igual que Fnac (ver comentario junto a ese bloque): Carrefour es
        # una incorporación reciente y todavía no se ha demostrado tan
        # estable como Amazon/ECI — un fallo puntual suyo no debe impedir
        # publicar lo que sí se ha comprobado bien del resto de tiendas.
        carrefour_products = {}
        for label, url in CARREFOUR_STORE["pages"]:
            print(f"🔍 Descubriendo productos en la tienda (Carrefour/{label})...")
            try:
                page_products = await discover_carrefour_products(page, label, url)
                print(f"📦 [Carrefour/{label}] {len(page_products)} productos encontrados")
                carrefour_products.update(page_products)
            except Exception as e:
                print(f"⚠️ No se pudo cargar la página de Carrefour ({label}), se omite esta vez: {e!r}")

        carrefour_out_of_stock = {a for a, i in carrefour_products.items() if i["status"] == "no_disponible"}
        if carrefour_out_of_stock:
            print(f"⏭️ [Carrefour] Omitiendo {len(carrefour_out_of_stock)} productos agotados de la web (pero sí se actualiza su estado, para poder detectar el próximo restock).")
            for product_id in carrefour_out_of_stock:
                key = f"CAR:{product_id}"
                prev_first_seen = state.get(key, {}).get("first_seen")
                state[key] = {
                    "name": carrefour_products[product_id]["name"],
                    "status": "no_disponible",
                    "stock": None,
                    "first_seen": prev_first_seen or datetime.now(timezone.utc).isoformat(),
                }
                del carrefour_products[product_id]

        carrefour_excluded_by_name = {
            a for a, i in carrefour_products.items() if is_excluded_by_name(i["name"])
        }
        if carrefour_excluded_by_name:
            print(f"⏭️ [Carrefour] Omitiendo {len(carrefour_excluded_by_name)} productos no relevantes por nombre (fundas/accesorios).")
            for product_id in carrefour_excluded_by_name:
                del carrefour_products[product_id]

        carrefour_not_pokemon = {
            a for a, i in carrefour_products.items() if not is_relevant_by_name(i["name"])
        }
        if carrefour_not_pokemon:
            print(f"⏭️ [Carrefour] Omitiendo {len(carrefour_not_pokemon)} productos ajenos a Pokémon.")
            for product_id in carrefour_not_pokemon:
                del carrefour_products[product_id]

        for product_id, info in carrefour_products.items():
            info["asin"] = product_id
            info["marketplace_code"] = CARREFOUR_STORE["code"]
            info["store_label"] = CARREFOUR_STORE["store_label"]
            info["flag"] = CARREFOUR_STORE["flag"]
            info["link"] = info["url"]
            info["categories"] = assign_categories(info["name"])
            products[f"CAR:{product_id}"] = info

        # Toys"R"Us es una fuente pública adicional. Se aísla para que una
        # caída temporal de una tienda extra no impida que
        # se publiquen los datos fiables de las demás. Afiliación aprobada
        # en TradeDoubler el 2026-09-18 — el enlace de compra usa el
        # deep-link (toysrus_affiliate_link), no la URL directa.
        toysrus_products = {}
        for label, query in TOYSRUS_STORE["pages"]:
            print(f"🔍 Descubriendo productos en la tienda (ToysRUs/{label})...")
            try:
                page_products = await discover_toysrus_products(page, label, query)
                print(f"📦 [ToysRUs/{label}] {len(page_products)} productos encontrados")
                toysrus_products.update(page_products)
            except Exception as e:
                print(f"⚠️ No se pudo cargar la página de ToysRUs ({label}), se omite esta vez: {e!r}")

        toysrus_out_of_stock = {a for a, i in toysrus_products.items() if i["status"] == "no_disponible"}
        if toysrus_out_of_stock:
            print(f"⏭️ [ToysRUs] Omitiendo {len(toysrus_out_of_stock)} productos agotados de la web (pero sí se actualiza su estado).")
            for product_id in toysrus_out_of_stock:
                key = f"TRU:{product_id}"
                prev_first_seen = state.get(key, {}).get("first_seen")
                state[key] = {
                    "name": toysrus_products[product_id]["name"],
                    "status": "no_disponible",
                    "stock": None,
                    "first_seen": prev_first_seen or datetime.now(timezone.utc).isoformat(),
                }
                del toysrus_products[product_id]

        toysrus_excluded_by_name = {
            a for a, i in toysrus_products.items() if is_excluded_by_name(i["name"])
        }
        if toysrus_excluded_by_name:
            print(f"⏭️ [ToysRUs] Omitiendo {len(toysrus_excluded_by_name)} productos no relevantes por nombre.")
            for product_id in toysrus_excluded_by_name:
                del toysrus_products[product_id]

        toysrus_not_pokemon = {
            a for a, i in toysrus_products.items() if not is_relevant_by_name(i["name"])
        }
        if toysrus_not_pokemon:
            print(f"⏭️ [ToysRUs] Omitiendo {len(toysrus_not_pokemon)} productos ajenos a Pokémon.")
            for product_id in toysrus_not_pokemon:
                del toysrus_products[product_id]

        for product_id, info in toysrus_products.items():
            info["asin"] = product_id
            info["marketplace_code"] = TOYSRUS_STORE["code"]
            info["store_label"] = TOYSRUS_STORE["store_label"]
            info["flag"] = TOYSRUS_STORE["flag"]
            info["link"] = toysrus_affiliate_link(info["url"])
            info["categories"] = assign_categories(info["name"])
            products[f"TRU:{product_id}"] = info

        # GAME es una fuente pública adicional, igual de aislada que
        # ToysRUs — sin afiliación todavía, se usa el enlace directo.
        game_products = {}
        for label, url in GAME_STORE["pages"]:
            print(f"🔍 Descubriendo productos en la tienda (GAME/{label})...")
            try:
                page_products = await discover_game_products(page, label, url)
                print(f"📦 [GAME/{label}] {len(page_products)} productos encontrados")
                game_products.update(page_products)
            except Exception as e:
                print(f"⚠️ No se pudo cargar la página de GAME ({label}), se omite esta vez: {e!r}")

        game_out_of_stock = {a for a, i in game_products.items() if i["status"] == "no_disponible"}
        if game_out_of_stock:
            print(f"⏭️ [GAME] Omitiendo {len(game_out_of_stock)} productos agotados de la web (pero sí se actualiza su estado).")
            for product_id in game_out_of_stock:
                key = f"GAME:{product_id}"
                prev_first_seen = state.get(key, {}).get("first_seen")
                state[key] = {
                    "name": game_products[product_id]["name"],
                    "status": "no_disponible",
                    "stock": None,
                    "first_seen": prev_first_seen or datetime.now(timezone.utc).isoformat(),
                }
                del game_products[product_id]

        game_excluded_by_name = {
            a for a, i in game_products.items() if is_excluded_by_name(i["name"])
        }
        if game_excluded_by_name:
            print(f"⏭️ [GAME] Omitiendo {len(game_excluded_by_name)} productos no relevantes por nombre.")
            for product_id in game_excluded_by_name:
                del game_products[product_id]

        game_not_pokemon = {
            a for a, i in game_products.items() if not is_relevant_by_name(i["name"])
        }
        if game_not_pokemon:
            print(f"⏭️ [GAME] Omitiendo {len(game_not_pokemon)} productos ajenos a Pokémon.")
            for product_id in game_not_pokemon:
                del game_products[product_id]

        for product_id, info in game_products.items():
            info["asin"] = product_id
            info["marketplace_code"] = GAME_STORE["code"]
            info["store_label"] = GAME_STORE["store_label"]
            info["flag"] = GAME_STORE["flag"]
            info["link"] = info["url"]
            info["categories"] = assign_categories(info["name"])
            products[f"GAME:{product_id}"] = info

        # MediaMarkt: desactivado temporalmente a petición del usuario
        # (2026-09-25) — no se descubren productos nuevos ni se actualiza su
        # estado mientras esté así. Para reactivar, volver a poner el bucle
        # de siempre (ver historial de git de esta línea).
        mediamarkt_products = {}
        MEDIAMARKT_ENABLED = False
        if MEDIAMARKT_ENABLED:
            for label, url in MEDIAMARKT_STORE["pages"]:
                print(f"🔍 Descubriendo productos en la tienda (MediaMarkt/{label})...")
                try:
                    page_products = await discover_mediamarkt_products(page, label, url)
                    print(f"📦 [MediaMarkt/{label}] {len(page_products)} productos encontrados")
                    mediamarkt_products.update(page_products)
                except Exception as e:
                    print(f"⚠️ No se pudo cargar la página de MediaMarkt ({label}), se omite esta vez: {e!r}")

        mediamarkt_out_of_stock = {
            a for a, i in mediamarkt_products.items() if i["status"] == "no_disponible"
        }
        if mediamarkt_out_of_stock:
            print(f"⏭️ [MediaMarkt] Omitiendo {len(mediamarkt_out_of_stock)} productos agotados de la web (pero sí se actualiza su estado).")
            for product_id in mediamarkt_out_of_stock:
                key = f"MM:{product_id}"
                prev_first_seen = state.get(key, {}).get("first_seen")
                state[key] = {
                    "name": mediamarkt_products[product_id]["name"],
                    "status": "no_disponible",
                    "stock": None,
                    "first_seen": prev_first_seen or datetime.now(timezone.utc).isoformat(),
                }
                del mediamarkt_products[product_id]

        mediamarkt_excluded_by_name = {
            a for a, i in mediamarkt_products.items() if is_excluded_by_name(i["name"])
        }
        if mediamarkt_excluded_by_name:
            print(f"⏭️ [MediaMarkt] Omitiendo {len(mediamarkt_excluded_by_name)} productos no relevantes por nombre.")
            for product_id in mediamarkt_excluded_by_name:
                del mediamarkt_products[product_id]

        mediamarkt_not_pokemon = {
            a for a, i in mediamarkt_products.items() if not is_relevant_by_name(i["name"])
        }
        if mediamarkt_not_pokemon:
            print(f"⏭️ [MediaMarkt] Omitiendo {len(mediamarkt_not_pokemon)} productos ajenos a Pokémon.")
            for product_id in mediamarkt_not_pokemon:
                del mediamarkt_products[product_id]

        for product_id, info in mediamarkt_products.items():
            info["asin"] = product_id
            info["marketplace_code"] = MEDIAMARKT_STORE["code"]
            info["store_label"] = MEDIAMARKT_STORE["store_label"]
            info["flag"] = MEDIAMARKT_STORE["flag"]
            info["link"] = info["url"]
            info["categories"] = assign_categories(info["name"])
            products[f"MM:{product_id}"] = info

        # TodoConsolas: mismo patrón, sin afiliación todavía.
        todoconsolas_products = {}
        for label, url in TODOCONSOLAS_STORE["pages"]:
            print(f"🔍 Descubriendo productos en la tienda (TodoConsolas/{label})...")
            try:
                page_products = await discover_todoconsolas_products(page, label, url)
                print(f"📦 [TodoConsolas/{label}] {len(page_products)} productos encontrados")
                todoconsolas_products.update(page_products)
            except Exception as e:
                print(f"⚠️ No se pudo cargar la página de TodoConsolas ({label}), se omite esta vez: {e!r}")

        todoconsolas_excluded_by_name = {
            a for a, i in todoconsolas_products.items() if is_excluded_by_name(i["name"])
        }
        if todoconsolas_excluded_by_name:
            print(f"⏭️ [TodoConsolas] Omitiendo {len(todoconsolas_excluded_by_name)} productos no relevantes por nombre.")
            for product_id in todoconsolas_excluded_by_name:
                del todoconsolas_products[product_id]

        todoconsolas_not_pokemon = {
            a for a, i in todoconsolas_products.items() if not is_relevant_by_name(i["name"])
        }
        if todoconsolas_not_pokemon:
            print(f"⏭️ [TodoConsolas] Omitiendo {len(todoconsolas_not_pokemon)} productos ajenos a Pokémon.")
            for product_id in todoconsolas_not_pokemon:
                del todoconsolas_products[product_id]

        for product_id, info in todoconsolas_products.items():
            info["asin"] = product_id
            info["marketplace_code"] = TODOCONSOLAS_STORE["code"]
            info["store_label"] = TODOCONSOLAS_STORE["store_label"]
            info["flag"] = TODOCONSOLAS_STORE["flag"]
            info["link"] = info["url"]
            info["categories"] = assign_categories(info["name"])
            products[f"TC:{product_id}"] = info

        await browser.close()

    print(f"📦 Total combinado: {len(products)} productos únicos")

    for key, info in products.items():
        status = info["status"]
        name = info["name"]
        prev = state.get(key, {})
        prev_price = prev.get("price")
        first_seen = prev.get("first_seen") or datetime.now(timezone.utc).isoformat()
        info["first_seen"] = first_seen

        status_changed, stock_decreased, price_decreased = alert_changes(info, prev)

        # Feed público de la web: a diferencia de Telegram (solo ES/ECI),
        # aquí registramos las 4 tiendas. Solo restock y bajada de precio —
        # "stock_decreased" a solas es demasiado ruidoso/poco interesante
        # para un timeline público (es solo el contador bajando, no un
        # evento que alguien quiera leer).
        if status_changed:
            events.append(build_event(info, "restock"))
        elif price_decreased:
            events.append(build_event(info, "price_drop", prev_price=prev_price))

        send_failed = False

        # Solo se envían alertas de Telegram para tiendas españolas (Amazon
        # ES, El Corte Inglés, Carrefour, ToysRUs y GAME). El resto de
        # marketplaces (UK, US) se siguen detectando y guardando en el
        # estado/snapshot para la web, pero no generan mensajes.
        if (status_changed or stock_decreased or price_decreased) and info["marketplace_code"] in ("ES", "ECI", "CAR", "TRU", "GAME", "MM", "TC"):
            # El nombre, el precio y el enlace vienen del scraping de
            # Amazon/El Corte Inglés/Carrefour/ToysRUs — datos
            # externos que no controlamos — y el mensaje se manda con parse_mode:
            # HTML, así que hay que
            # escaparlos o un título de producto con '<'/'>'/'&' rompería el
            # parseo (o, peor, colaría markup/enlaces falsos en el mensaje).
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

            store_bits = " ".join(part for part in [info["store_label"], info["flag"], hashtag] if part)
            store_line = f"<b>{store_bits}</b>"
            website_line = f'🌐 <a href="{WEBSITE_URL}">Ver todos los productos disponibles</a>'

            message = "\n\n".join(
                part for part in [f"<b>{safe_name}</b>", store_line, price_change_line, price_line, stock_line, cta] if part
            )
            message += f"\n\n{website_line}"
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
                    send_failed = True
                # Espaciamos los envíos para no toparnos con el límite de
                # velocidad de la API de Telegram cuando cambian de golpe
                # muchos productos (visto con el lanzamiento de un set nuevo).
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

    static_fnac_products = load_static_fnac_products()
    print(f"📌 [Fnac] Publicando {len(static_fnac_products)} productos estáticos sin comprobar stock.")
    save_state(state)
    save_products_snapshot({**products, **static_fnac_products})
    save_events(events)
    print("✅ Comprobación completada.")


if __name__ == "__main__":
    asyncio.run(main())


"""Fichas estáticas persistentes y actividad por juego a partir de snapshots.

No deduce que un producto esté agotado por su ausencia: conserva su ficha
con disponibilidad sin confirmar. Se ejecuta sobre el checkout de publicación.
"""
import argparse
from datetime import date
import html
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from gaming_product_filter import is_non_gaming_product
from product_grouping import DEFAULT_OVERRIDES as DEFAULT_GROUP_OVERRIDES, build_groups
from stock_logic import alert_changes, price_to_float

SOURCES = ("products.json", "onepiece.json", "magic.json", "lorcana.json", "yugioh.json", "nintendo.json", "playstation.json", "xbox.json", "accesorios.json")
GAMING_SOURCES = {"nintendo.json", "playstation.json", "xbox.json"}
NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
ORIGIN = "https://wheresthatstock.com"
ACTIVE_STATUSES = {"compra_directa", "preventa", "invitacion"}
# "sin_confirmar" y cualquier estado nuevo caen en OutOfStock: sin confirmación
# reciente no debe anunciarse como comprable en resultados de búsqueda.
AVAILABILITY = {
    "compra_directa": "https://schema.org/InStock",
    "preventa": "https://schema.org/PreOrder",
    "invitacion": "https://schema.org/LimitedAvailability",
    "no_disponible": "https://schema.org/OutOfStock",
}
# Por "_src" y no por "game": así un accesorio con game="Magic" (categories
# incluye "Magic" solo para el filtro de /accesorios) enlaza a donde el
# producto es de verdad navegable, no a /magic — is_accessory() en cada
# check_*.py lo desvía antes de llegar a la categoría del juego, así que
# nunca aparece listado ahí.
CATEGORY_LINKS = {
    "products.json": ("/pokemontcg", "Pokémon TCG"),
    "onepiece.json": ("/onepiece", "One Piece TCG"),
    "magic.json": ("/magic", "Magic: The Gathering"),
    "lorcana.json": ("/lorcana", "Disney Lorcana"),
    "yugioh.json": ("/yugioh", "Yu-Gi-Oh!"),
    "nintendo.json": ("/nintendo", "Nintendo"),
    "playstation.json": ("/playstation", "PlayStation"),
    "xbox.json": ("/xbox", "Xbox"),
    "accesorios.json": ("/accesorios", "Accesorios"),
}
STORE_ASSETS = {
    "ES": "/assets/amazon-logo.png",
    "UK": "/assets/amazon-logo.png",
    "US": "/assets/amazon-logo.png",
    "ECI": "/assets/eci-logo.webp",
    "CAR": "/assets/carrefour-logo.webp",
    "FNAC": "/assets/fnac-logo.webp",
    "TRU": "/assets/toysrus-logo.png",
    "GAME": "/assets/game-logo.png",
    "MM": "/assets/mediamarkt-logo.png",
    "TC": "/assets/todoconsolas-logo.jpg",
}
FLAG_ASSETS = {"UK": "/assets/flags/gb.png", "US": "/assets/flags/us.png"}


def breadcrumb_ld(*crumbs):
    """crumbs: pares (nombre, url). El primero siempre es Inicio."""
    items = [
        {"@type": "ListItem", "position": i + 1, "name": name, "item": url}
        for i, (name, url) in enumerate(crumbs)
    ]
    ld = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}
    return json.dumps(ld, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def clean_gaming_feed(data, source):
    """Filter invalid Gaming rows at the publication boundary as a second
    line of defence. This also removes already archived false positives on
    the next build instead of preserving them forever as unconfirmed."""
    if source not in GAMING_SOURCES or not isinstance(data.get("products"), list):
        return data, []
    removed = [
        product for product in data["products"]
        if is_non_gaming_product(product.get("asin"), product.get("name"))
    ]
    if not removed:
        return data, []
    rejected = {product_id(product) for product in removed}
    return {
        **data,
        "products": [
            product for product in data["products"]
            if product_id(product) not in rejected
        ],
    }, removed


def product_id(product):
    parts = (product["marketplace"], product["asin"])
    if any(not isinstance(part, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts):
        raise ValueError("Identificador de producto inválido")
    return "-".join(parts)


def update_catalog(previous, snapshot, source, events):
    if not isinstance(snapshot.get("products"), list) or not snapshot.get("updated_at"):
        raise ValueError("Snapshot incompleto")
    stamp = snapshot["updated_at"]
    rows = {product_id(p): dict(p) for p in previous.get("products", [])}
    seen = set()
    events = list(events)
    for product in snapshot["products"]:
        key = product_id(product)
        seen.add(key)
        old = rows.get(key, {})
        if previous and previous.get("updated_at") != stamp and source != "accesorios.json":
            restock, _, price_drop = alert_changes(product, old)
            if restock or price_drop:
                events.append({**product, "ts": stamp, "type": "restock" if restock else "price_drop",
                               "prev_price": old.get("price"), "_src": source,
                               "id": f"{key}:{stamp}:{'restock' if restock else 'price_drop'}"})
        observed = snapshot.get("source_updates", {}).get(product.get("source")) or (old.get("last_seen") if source == "accesorios.json" else None) or stamp
        rows[key] = {**product, "_src": source, "last_seen": observed, "checked_at": observed}
    for key, product in rows.items():
        if key not in seen and product.get("status") != "sin_confirmar":
            product.update(status="sin_confirmar", stock=None, checked_at=stamp)
    # Reintentar la misma publicación no duplica eventos ni cambia sus fechas.
    unique = {e.get("id") or f"{e.get('asin')}:{e.get('marketplace')}:{e.get('ts')}:{e.get('type')}": e for e in events}
    events = sorted(unique.values(), key=lambda e: e.get("ts", ""))[-200:]
    return {"updated_at": stamp, "products": list(rows.values())}, events


def _safe_https_url(value):
    parsed = urlparse(value or "")
    return value if parsed.scheme == "https" and parsed.netloc else ""


def _status_text(status):
    return {
        "compra_directa": "Disponible en la última comprobación",
        "invitacion": "Disponible por invitación",
        "preventa": "Preventa disponible",
        "no_disponible": "Agotado en la última comprobación",
    }.get(status, "Disponibilidad sin confirmar")


def _render_related_products(products):
    if not products:
        return ""
    cards = []
    for product in products:
        key = product_id(product)
        name = html.escape(product.get("name") or "Producto")
        image = _safe_https_url(product.get("image"))
        image_html = (
            f'<img src="{html.escape(image, quote=True)}" alt="{name}" loading="lazy" width="220" height="220">'
            if image else '<span class="related-product-placeholder" aria-hidden="true">Sin imagen</span>'
        )
        price = html.escape(product.get("price") or "Consultar precio")
        cards.append(
            f'<a class="related-product-card" href="/producto/{key}">'
            f'{image_html}<span class="related-product-name">{name}</span>'
            f'<span class="related-product-price">{price}</span></a>'
        )
    return (
        '<section class="related-products" aria-labelledby="related-products-title">'
        '<h2 id="related-products-title">Productos relacionados</h2>'
        '<div class="related-products-grid">' + "".join(cards) + '</div></section>'
    )


def _render_product_history(events):
    if not events:
        return ""
    items = []
    for event in events[:5]:
        kind = event.get("type")
        if kind == "price_drop":
            previous = html.escape(event.get("prev_price") or "precio anterior")
            current = html.escape(event.get("price") or "nuevo precio")
            message = f"Bajó de {previous} a {current}"
        else:
            message = "Volvió a estar disponible"
        stamp = html.escape((event.get("ts") or "")[:10])
        items.append(f'<li><time datetime="{stamp}">{stamp}</time><span>{message}</span></li>')
    return (
        '<section class="product-history" aria-labelledby="product-history-title">'
        '<h2 id="product-history-title">Historial reciente</h2><ul>' + "".join(items) + '</ul></section>'
    )


def render_page(template, product, set_entry=None, related=None, history=None, group=None):
    name = html.escape(product.get("name") or "Producto")
    key = product_id(product)
    description = html.escape(f"{product.get('name', 'Producto')}: última disponibilidad observada y enlace a la tienda.", quote=True)
    embedded = json.dumps(product, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    output = re.sub(r"<title>.*?</title>", lambda _: f"<title>{name} — Where's That Stock</title>", template, count=1, flags=re.S)
    output = re.sub(r'<meta name="description"[^>]*>', lambda _: f'<meta name="description" content="{description}">', output, count=1)
    output = output.replace('<meta name="robots" content="noindex">', '')
    page_url = group.get("url") if group else f"{ORIGIN}/producto/{key}"
    output = output.replace('</head>', f'<link rel="canonical" href="{html.escape(page_url, quote=True)}">\n</head>', 1)
    product_image = _safe_https_url(product.get("image"))
    image = product_image or f"{ORIGIN}/assets/brand/social.png"
    metadata = {
        "og:type": "website", "og:site_name": "Where's That Stock",
        "og:title": product.get("name") or "Producto",
        "og:description": f"Consulta este producto en {product.get('store_label') or 'la tienda'} y guárdalo en tus favoritos.",
        "og:url": page_url, "og:image": image,
        "twitter:card": "summary_large_image",
    }
    tags = "\n".join(f'<meta {"name" if label.startswith("twitter:") else "property"}="{label}" content="{html.escape(value, quote=True)}">' for label, value in metadata.items())
    output = output.replace('</head>', tags + '\n</head>', 1)
    offer = {
        "@type": "Offer",
        "url": f"{ORIGIN}/producto/{key}",
        # Los seis snapshots (incl. UK/US) publican precio en euros; revisar
        # si algún origen empieza a traer otra moneda.
        "priceCurrency": "EUR",
        "availability": AVAILABILITY.get(product.get("status"), "https://schema.org/OutOfStock"),
        "seller": {"@type": "Organization", "name": product.get("store_label") or "Amazon"},
    }
    price = price_to_float(product.get("price"))
    if price is not None:
        offer["price"] = f"{price:.2f}"
    product_ld = {
        "@context": "https://schema.org", "@type": "Product",
        "name": product.get("name") or "Producto", "image": image,
        "sku": product.get("asin"), "offers": offer,
    }
    ld_json = json.dumps(product_ld, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    output = output.replace('</head>', f'<script type="application/ld+json">{ld_json}</script>\n</head>', 1)
    category_path, category_label = CATEGORY_LINKS.get(product.get("_src"), CATEGORY_LINKS["products.json"])
    crumbs_json = breadcrumb_ld(
        ("Inicio", f"{ORIGIN}/"),
        (category_label, f"{ORIGIN}{category_path}"),
        (product.get("name") or "Producto", page_url),
    )
    output = output.replace('</head>', f'<script type="application/ld+json">{crumbs_json}</script>\n</head>', 1)
    status = _status_text(product.get("status"))
    store = html.escape(product.get("store_label") or "la tienda")
    price_text = html.escape(product.get("price") or "")
    checked = html.escape((product.get("checked_at") or product.get("last_seen") or "")[:10])
    category_path, category_label = CATEGORY_LINKS.get(product.get("_src"), CATEGORY_LINKS["products.json"])
    image_html = (
        f'<img src="{html.escape(product_image, quote=True)}" alt="{name}" width="420" height="420" fetchpriority="high">'
        if product_image else '<span class="product-image-placeholder"><span>Imagen no disponible</span></span>'
    )
    body = (
        '<div class="product-detail">'
        f'<div class="product-detail-img product-static-image">{image_html}</div>'
        '<div class="product-detail-body">'
        f'<div class="product-detail-store"><span>{store}</span><span class="badge status">{html.escape(status)}</span></div>'
        f'<h1>{name}</h1><p>{html.escape(status)}.</p>'
        f'<p class="product-context">Categoría: <a href="{category_path}">{html.escape(category_label)}</a>. '
        f'Última comprobación: <time datetime="{checked}">{checked or "sin fecha"}</time>.</p>'
        + (f'<div class="price-row"><span class="price">{price_text}</span></div>' if price_text else "")
    )
    link = _safe_https_url(product.get("link"))
    if link:
        body += f'<div class="detail-actions"><a class="buy-btn" href="{html.escape(link, quote=True)}" rel="noopener sponsored">Consultar en la tienda</a></div>'
    body += '</div></div>'
    output, count = re.subn(r'(<div id="product-detail"[^>]*>).*?(</div>)', lambda m: m[1] + body + m[2], output, count=1, flags=re.S)
    if count != 1:
        raise ValueError("Falta el contenedor product-detail en la plantilla")
    # Fuera de #product-detail a propósito: producto.js reescribe ese
    # contenedor entero al hidratar y se llevaría el enlace por delante.
    if set_entry:
        slug, config = set_entry
        color = GAME_COLORS.get(config.get("game"), "")
        # El nombre completo lleva el juego delante para que el <title> de la
        # ficha de set sea descriptivo; aquí sobra, ya se sabe dónde estás.
        label = html.escape(config.get("short_name") or config.get("name") or slug)
        style = f' style="--game-color:{color}"' if color else ""
        output = output.replace("<!--SET-LINK-->", f'  <a class="back-link set-link" href="/set/{slug}"{style}>Ver todo lo de {label} →</a>', 1)
    output = output.replace("<!--SET-LINK-->", "")
    if group:
        output = output.replace("<!--PRODUCT-HISTORY-->", _render_group_reference(group, key) + "\n  <!--PRODUCT-HISTORY-->", 1)
    output = re.sub(r"[ \t]*<!--PRODUCT-HISTORY-->", _render_product_history(history or []), output, count=1)
    output = re.sub(r"[ \t]*<!--RELATED-PRODUCTS-->", _render_related_products(related or []), output, count=1)
    return output.replace('<script src="/producto.js"></script>', f'<script id="product-data" type="application/json">{embedded}</script>\n<script src="/producto.js"></script>')


def _offer_is_available(offer):
    return offer.get("status") in ACTIVE_STATUSES


def _group_status_text(status):
    return {
        "compra_directa": "Disponible",
        "invitacion": "Por invitación",
        "preventa": "Preventa",
    }.get(status, "No disponible")


def _format_eur(value):
    return f"{value:.2f}".replace(".", ",") + " €"


def _offer_sort_key(offer):
    price = price_to_float(offer.get("price"))
    return (not _offer_is_available(offer), price is None, price if price is not None else float("inf"), offer.get("store") or "", offer.get("offer_id") or "")


def _store_logo(marketplace, store, available, compact=False):
    asset = STORE_ASSETS.get(marketplace)
    flag = FLAG_ASSETS.get(marketplace)
    state = "Disponible" if available else "No disponible"
    classes = "group-store-logo" + (" is-unavailable" if not available else "") + (" is-compact" if compact else "")
    content = (
        f'<img src="{asset}" alt="" width="72" height="32" loading="lazy">'
        if asset else f'<span class="group-store-fallback">{html.escape(store[:12])}</span>'
    )
    if flag:
        content += f'<img class="group-store-flag" src="{flag}" alt="{html.escape(marketplace)}" width="18" height="12" loading="lazy">'
    return f'<span class="{classes}" role="img" aria-label="{html.escape(store)}: {state}" title="{html.escape(store)} · {state}">{content}</span>'


def _render_group_reference(group, current_offer_id):
    offers = sorted(group.get("offers") or [], key=_offer_sort_key)
    if len(offers) < 2:
        return ""
    stores = {offer.get("store") or offer.get("marketplace") or "Tienda" for offer in offers}
    links = []
    for offer in offers:
        store = offer.get("store") or offer.get("marketplace") or "Tienda"
        current = offer.get("offer_id") == current_offer_id
        label = f'{store}: {offer.get("price") or "consultar precio"}'
        logo = _store_logo(offer.get("marketplace"), store, _offer_is_available(offer), compact=True)
        if current:
            links.append(f'<span class="group-offer-chip is-current">{logo}<span>{html.escape(label)} · esta oferta</span></span>')
        else:
            links.append(f'<a class="group-offer-chip" href="/producto/{html.escape(offer.get("offer_id") or "", quote=True)}">{logo}<span>{html.escape(label)}</span></a>')
    return (
        '<section class="product-group-callout" aria-labelledby="product-group-callout-title">'
        '<div><span class="group-kicker">Comparador de precios</span>'
        f'<h2 id="product-group-callout-title">Disponible en {len(stores)} tiendas</h2>'
        '<p>Esta oferta pertenece a un producto con precios de varias tiendas.</p></div>'
        f'<a class="buy-btn" href="/producto/{html.escape(group.get("id") or "", quote=True)}">Comparar todos los precios</a>'
        '<div class="product-group-chips">' + "".join(links) + '</div></section>'
    )


def render_group_page(template, group):
    """Genera la ficha C indexable con todas las ofertas del producto."""
    group_id = group.get("id") or ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,95}", group_id):
        raise ValueError("Identificador de grupo inválido")
    raw_name = group.get("name") or "Producto Pokémon TCG"
    name = html.escape(raw_name)
    url = f"{ORIGIN}/producto/{group_id}"
    offers = sorted(group.get("offers") or [], key=_offer_sort_key)
    available = [offer for offer in offers if _offer_is_available(offer)]
    priced = [(price_to_float(offer.get("price")), offer) for offer in available]
    priced = [(price, offer) for price, offer in priced if price is not None]
    best_price, best_offer = min(priced, default=(None, available[0] if available else (offers[0] if offers else None)), key=lambda item: item[0] if item[0] is not None else float("inf"))
    image = next((_safe_https_url(offer.get("image")) for offer in ([best_offer] if best_offer else []) + offers if offer and _safe_https_url(offer.get("image"))), "")
    stores = sorted({offer.get("store") or offer.get("marketplace") or "Tienda" for offer in offers})
    description_text = f"Compara el precio y el stock de {raw_name} en {len(stores)} tiendas."
    if best_price is not None:
        description_text += f" Disponible desde {_format_eur(best_price)}."
    description = html.escape(description_text, quote=True)

    output = re.sub(r"<title>.*?</title>", lambda _: f"<title>{name}: precios y stock — Where's That Stock</title>", template, count=1, flags=re.S)
    output = re.sub(r'<meta name="description"[^>]*>', lambda _: f'<meta name="description" content="{description}">', output, count=1)
    output = output.replace('<meta name="robots" content="noindex">', '')
    output = output.replace('</head>', f'<link rel="canonical" href="{url}">\n</head>', 1)
    social_image = image or f"{ORIGIN}/assets/brand/social.png"
    metadata = {
        "og:type": "website", "og:site_name": "Where's That Stock",
        "og:title": f"{raw_name}: compara precios y stock",
        "og:description": description_text, "og:url": url, "og:image": social_image,
        "twitter:card": "summary_large_image",
    }
    tags = "\n".join(f'<meta {"name" if label.startswith("twitter:") else "property"}="{label}" content="{html.escape(value, quote=True)}">' for label, value in metadata.items())
    output = output.replace('</head>', tags + '\n</head>', 1)

    schema_offers = []
    for offer in offers:
        schema_offer = {
            "@type": "Offer",
            "url": offer.get("product_url") or f"{ORIGIN}/producto/{offer.get('offer_id')}",
            "priceCurrency": "EUR",
            "availability": AVAILABILITY.get(offer.get("status"), "https://schema.org/OutOfStock"),
            "seller": {"@type": "Organization", "name": offer.get("store") or offer.get("marketplace") or "Tienda"},
        }
        price = price_to_float(offer.get("price"))
        if price is not None:
            schema_offer["price"] = f"{price:.2f}"
        schema_offers.append(schema_offer)
    priced_values = [price_to_float(offer.get("price")) for offer in available]
    priced_values = [price for price in priced_values if price is not None]
    offer_schema = {"@type": "AggregateOffer", "priceCurrency": "EUR", "offerCount": len(offers), "offers": schema_offers}
    if priced_values:
        offer_schema.update(lowPrice=f"{min(priced_values):.2f}", highPrice=f"{max(priced_values):.2f}")
    product_ld = {"@context": "https://schema.org", "@type": "Product", "name": raw_name, "image": social_image, "sku": group_id, "offers": offer_schema}
    ld_json = json.dumps(product_ld, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    crumbs_json = breadcrumb_ld(("Inicio", f"{ORIGIN}/"), ("Pokémon TCG", f"{ORIGIN}/pokemontcg"), (raw_name, url))
    output = output.replace('</head>', f'<script type="application/ld+json">{ld_json}</script>\n<script type="application/ld+json">{crumbs_json}</script>\n</head>', 1)

    image_html = (
        f'<img src="{html.escape(image, quote=True)}" alt="{name}" width="420" height="420" fetchpriority="high">'
        if image else '<span class="product-image-placeholder"><span>Imagen no disponible</span></span>'
    )
    store_states = {}
    for offer in offers:
        key = offer.get("marketplace") or offer.get("store") or ""
        current = store_states.setdefault(key, {"marketplace": offer.get("marketplace"), "store": offer.get("store") or offer.get("marketplace") or "Tienda", "available": False})
        current["available"] = current["available"] or _offer_is_available(offer)
    logos = "".join(_store_logo(item["marketplace"], item["store"], item["available"]) for item in store_states.values())
    availability_text = f'{len(available)} oferta{"s" if len(available) != 1 else ""} disponible{"s" if len(available) != 1 else ""}' if available else "Sin ofertas disponibles ahora"
    hero = (
        '<div class="product-detail group-product-detail">'
        f'<div class="product-detail-img product-static-image">{image_html}</div>'
        '<div class="product-detail-body"><span class="group-kicker">Comparador Pokémon TCG</span>'
        f'<h1>{name}</h1><p class="group-availability-summary">{html.escape(availability_text)} en {len(stores)} tiendas.</p>'
        + (f'<div class="group-best-price"><span>Mejor precio disponible</span><strong>{_format_eur(best_price)}</strong></div>' if best_price is not None else '<div class="group-best-price"><span>Precio</span><strong>Consultar en tiendas</strong></div>')
        + f'<div class="group-store-strip" aria-label="Tiendas que venden este producto">{logos}</div>'
        + f'<a class="buy-btn" href="#precios">Ver precios en {len(stores)} tiendas</a></div></div>'
    )
    output, count = re.subn(r'(<div id="product-detail"[^>]*>).*?(</div>)', lambda match: match[1] + hero + match[2], output, count=1, flags=re.S)
    if count != 1:
        raise ValueError("Falta el contenedor product-detail en la plantilla")

    rows = []
    for offer in offers:
        store = offer.get("store") or offer.get("marketplace") or "Tienda"
        active = _offer_is_available(offer)
        logo = _store_logo(offer.get("marketplace"), store, active)
        status = _group_status_text(offer.get("status"))
        offer_name = html.escape(offer.get("name") or raw_name)
        price = html.escape(offer.get("price") or "Consultar precio")
        product_url = f'/producto/{html.escape(offer.get("offer_id") or "", quote=True)}'
        merchant = _safe_https_url(offer.get("link"))
        action = (
            f'<a class="buy-btn" href="{html.escape(merchant, quote=True)}" target="_blank" rel="noopener sponsored">Comprar</a>'
            if active and merchant else '<span class="buy-btn disabled" aria-disabled="true">No disponible</span>'
        )
        rows.append(
            f'<article class="group-offer-row{" is-unavailable" if not active else ""}">'
            f'<div class="group-offer-store">{logo}<div><strong>{html.escape(store)}</strong><span class="badge status">{html.escape(status)}</span></div></div>'
            f'<div class="group-offer-product"><a href="{product_url}">{offer_name}</a><span>Ver historial y detalles de esta oferta</span></div>'
            f'<div class="group-offer-price"><strong>{price}</strong><span>{"Precio actual" if active else "Último precio observado"}</span></div>'
            f'<div class="group-offer-actions">{action}<a class="utility-button" href="{product_url}">Ver ficha</a></div></article>'
        )
    comparison = (
        '<section class="group-offers" id="precios" aria-labelledby="group-offers-title">'
        '<div class="group-section-heading"><div><span class="group-kicker">Precios por tienda</span>'
        f'<h2 id="group-offers-title">Compara {len(offers)} ofertas</h2></div><p>Ordenadas por disponibilidad y precio.</p></div>'
        + "".join(rows) + '</section>'
        '<section class="group-disclaimer"><h2>Antes de comprar</h2><p>Comprueba el idioma, el modelo y las condiciones en la tienda. Los precios y el stock pueden cambiar desde la última comprobación.</p></section>'
    )
    output = output.replace("<!--SET-LINK-->", "")
    output = output.replace("<!--PRODUCT-HISTORY-->", comparison, 1)
    output = output.replace("<!--RELATED-PRODUCTS-->", "", 1)
    output = output.replace('<span id="live-text">Cargando...</span>', '<span id="live-text">comparando tiendas</span>')
    output = output.replace('<script src="/producto.js"></script>', '')
    embedded = json.dumps(group, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return output.replace('</body>', f'<script id="product-group-data" type="application/json">{embedded}</script>\n</body>', 1)


def _normalize(text):
    stripped = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in stripped if unicodedata.category(c) != "Mn")


MONTHS_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def _human_date(iso_date):
    try:
        year, month, day = iso_date.split("-")
        return f"{int(day)} de {MONTHS_ES[int(month) - 1]} de {year}"
    except (ValueError, IndexError, AttributeError, TypeError):
        return iso_date or ""


GAME_COLORS = {
    "Pokémon": "#D4A017", "One Piece": "#C0392B", "Magic": "#5B3FA6",
    "Lorcana": "#B23A6B", "Yu-Gi-Oh!": "#8B5A2B",
}


def match_sets(sets, products):
    """Empareja productos y sets una sola vez: lo usan tanto las fichas de
    producto (para enlazar a su expansión) como las propias fichas de set.

    El juego acota la búsqueda: una palabra clave corta ("hobbit") no debe
    arrastrar un producto de otro juego que la mencione por casualidad. Ojo:
    config["game"] tiene que coincidir EXACTO con el de los productos
    ("One Piece", no "One Piece TCG") o el set se queda vacío sin avisar."""
    matches, owner = {}, {}
    for slug, config in sets.items():
        keyword = _normalize(config.get("match"))
        game = config.get("game")
        matched = []
        for key, product in products.items():
            if not keyword or keyword not in _normalize(product.get("name")):
                continue
            if game and product.get("game") != game:
                continue
            matched.append(product)
            owner.setdefault(key, slug)
        matched.sort(key=lambda p: p.get("checked_at") or p.get("last_seen") or "", reverse=True)
        matches[slug] = (config, matched)
    return matches, owner


RELATED_STOPWORDS = {
    "para", "con", "del", "las", "los", "una", "uno", "the", "and",
    "edition", "edicion", "espanol", "ingles", "juego", "game", "pack",
}


def _product_words(product):
    return {
        word for word in re.findall(r"[a-z0-9]+", _normalize(product.get("name")))
        if len(word) > 2 and word not in RELATED_STOPWORDS
    }


def related_products(product_key, product, products, limit=4):
    """Devuelve alternativas navegables y prioriza similitud real del título.

    No intenta afirmar que dos listings sean el mismo producto: para eso hace
    falta una identidad normalizada independiente de la tienda. Estos enlaces
    solo sirven como descubrimiento contextual e interlinking.
    """
    words = _product_words(product)
    categories = set(product.get("categories") or [])
    candidates = []
    for key, candidate in products.items():
        if key == product_key or candidate.get("status") not in ACTIVE_STATUSES:
            continue
        common_words = len(words & _product_words(candidate))
        common_categories = len(categories & set(candidate.get("categories") or []))
        same_source = candidate.get("_src") == product.get("_src")
        same_game = bool(product.get("game") and candidate.get("game") == product.get("game"))
        if not (same_source or same_game or common_categories or common_words >= 2):
            continue
        score = common_words * 5 + common_categories * 3 + same_game * 3 + same_source * 2
        candidates.append((score, candidate.get("checked_at") or "", key, candidate))
    candidates.sort(reverse=True, key=lambda item: item[:3])
    return [candidate for _, _, _, candidate in candidates[:limit]]


def render_set_page(template, slug, config, matched):
    """Une la ficha editorial de sets.json (mantenida a mano, igual que el
    calendario) con los productos ya emparejados por match_sets. No se
    guarda "set" en el catálogo: se recalcula en cada publicación, así que
    basta con editar sets.json para que un producto entre o salga del hub."""
    name = html.escape(config.get("name") or slug)
    blurb = config.get("blurb") or ""
    canonical = f"{ORIGIN}/set/{slug}"
    output = re.sub(r"<title>.*?</title>", lambda _: f"<title>{name} — Where's That Stock</title>", template, count=1, flags=re.S)
    description = html.escape(blurb, quote=True)
    output = re.sub(r'<meta name="description"[^>]*>', lambda _: f'<meta name="description" content="{description}">', output, count=1)
    output = output.replace('<meta name="robots" content="noindex">', '')
    output = output.replace('</head>', f'<link rel="canonical" href="{canonical}">\n</head>', 1)
    metadata = {"og:title": config.get("name") or slug, "og:description": blurb, "og:url": canonical}
    tags = "\n".join(f'<meta property="{label}" content="{html.escape(value, quote=True)}">' for label, value in metadata.items() if value)
    output = output.replace('</head>', tags + '\n</head>', 1)
    crumbs_json = breadcrumb_ld(
        ("Inicio", f"{ORIGIN}/"),
        ("Expansiones", f"{ORIGIN}/set/"),
        (config.get("name") or slug, canonical),
    )
    output = output.replace('</head>', f'<script type="application/ld+json">{crumbs_json}</script>\n</head>', 1)

    payload = {
        "name": config.get("name") or slug, "blurb": blurb,
        "release_date_human": _human_date(config.get("release_date")),
        "article": config.get("article"), "products": matched,
    }
    embedded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    output = output.replace(
        '<script id="set-data" type="application/json">{}</script>',
        f'<script id="set-data" type="application/json">{embedded}</script>', 1,
    )
    return output, matched


def render_set_index(template, entries):
    """Índice de expansiones. Recibe ya calculado el recuento de productos
    disponibles por set para no repetir el emparejamiento."""
    output = template.replace('<meta name="robots" content="noindex">', '')
    output = output.replace('</head>', f'<link rel="canonical" href="{ORIGIN}/set/">\n</head>', 1)
    crumbs_json = breadcrumb_ld(("Inicio", f"{ORIGIN}/"), ("Expansiones", f"{ORIGIN}/set/"))
    output = output.replace('</head>', f'<script type="application/ld+json">{crumbs_json}</script>\n</head>', 1)
    embedded = json.dumps(entries, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return output.replace(
        '<script id="sets-data" type="application/json">[]</script>',
        f'<script id="sets-data" type="application/json">{embedded}</script>', 1,
    )


def _new_urlset():
    return ET.Element(f"{{{NS}}}urlset")


def _read_core_sitemap(folder):
    """Carga el sitemap editorial y migra el sitemap único antiguo.

    En la primera ejecución sitemap.xml aún es un urlset con todo mezclado;
    después pasa a ser un sitemapindex y la fuente estable es
    sitemap-core.xml. Las fichas se regeneran siempre desde los catálogos.
    """
    core_path = folder / "sitemap-core.xml"
    source_path = core_path if core_path.exists() else folder / "sitemap.xml"
    if not source_path.exists():
        return _new_urlset()
    root = ET.parse(source_path).getroot()
    if root.tag != f"{{{NS}}}urlset":
        return _new_urlset()
    for entry in list(root.findall(f"{{{NS}}}url")):
        location = entry.findtext(f"{{{NS}}}loc") or ""
        if location.startswith(f"{ORIGIN}/producto/"):
            root.remove(entry)
    return root


def _valid_lastmod(value):
    candidate = (value or "")[:10]
    try:
        parsed = date.fromisoformat(candidate)
    except ValueError:
        return ""
    return candidate if parsed <= date.today() else ""


def _sitemap_entries(root):
    return {
        entry.findtext(f"{{{NS}}}loc"): entry
        for entry in root.findall(f"{{{NS}}}url")
        if entry.findtext(f"{{{NS}}}loc")
    }


def _set_sitemap_entry(root, entries, url, lastmod=""):
    entry = entries.get(url)
    if entry is None:
        entry = ET.SubElement(root, f"{{{NS}}}url")
        ET.SubElement(entry, f"{{{NS}}}loc").text = url
        entries[url] = entry
    modified = entry.find(f"{{{NS}}}lastmod")
    safe_lastmod = _valid_lastmod(lastmod)
    if safe_lastmod:
        if modified is None:
            modified = ET.SubElement(entry, f"{{{NS}}}lastmod")
        modified.text = safe_lastmod
    elif modified is not None:
        entry.remove(modified)


def _write_xml(path, root):
    ET.register_namespace("", NS)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def write_sitemaps(folder, products, set_matches, groups=None):
    """Separa URLs editoriales y productos comprables.

    Las fichas sin confirmar siguen publicadas con HTTP 200 y enlaces
    contextuales, pero no compiten en el sitemap con URLs que tienen stock.
    """
    core_root = _read_core_sitemap(folder)
    core_entries = _sitemap_entries(core_root)
    # Limpia fechas futuras heredadas también en páginas editoriales.
    for entry in core_entries.values():
        modified = entry.find(f"{{{NS}}}lastmod")
        if modified is not None and not _valid_lastmod(modified.text):
            entry.remove(modified)

    newest_set = ""
    for slug, (_, matched) in set_matches.items():
        lastmod = max((p.get("checked_at") or p.get("last_seen") or "" for p in matched), default="")
        safe_lastmod = _valid_lastmod(lastmod)
        _set_sitemap_entry(core_root, core_entries, f"{ORIGIN}/set/{slug}", safe_lastmod)
        newest_set = max(newest_set, safe_lastmod)
    if set_matches:
        _set_sitemap_entry(core_root, core_entries, f"{ORIGIN}/set/", newest_set)

    products_root = _new_urlset()
    product_entries = {}
    groups = groups or []
    grouped_offer_ids = {
        offer.get("offer_id")
        for group in groups for offer in group.get("offers") or []
    }
    for key, product in sorted(products.items()):
        if product.get("status") not in ACTIVE_STATUSES or key in grouped_offer_ids:
            continue
        _set_sitemap_entry(
            products_root,
            product_entries,
            f"{ORIGIN}/producto/{key}",
            product.get("checked_at") or product.get("last_seen") or "",
        )
    for group in groups:
        offers = group.get("offers") or []
        if not any(_offer_is_available(offer) for offer in offers):
            continue
        _set_sitemap_entry(
            products_root,
            product_entries,
            group.get("url") or f"{ORIGIN}/producto/{group.get('id')}",
            max((offer.get("checked_at") or offer.get("last_seen") or "" for offer in offers), default=""),
        )

    index_root = ET.Element(f"{{{NS}}}sitemapindex")
    for filename in ("sitemap-core.xml", "sitemap-products.xml"):
        entry = ET.SubElement(index_root, f"{{{NS}}}sitemap")
        ET.SubElement(entry, f"{{{NS}}}loc").text = f"{ORIGIN}/{filename}"

    _write_xml(folder / "sitemap-core.xml", core_root)
    _write_xml(folder / "sitemap-products.xml", products_root)
    _write_xml(folder / "sitemap.xml", index_root)
    return ["sitemap-core.xml", "sitemap-products.xml", "sitemap.xml"]


def build_site(folder, sources):
    folder = Path(folder)
    template_path = folder / "producto.html"
    if not template_path.exists():
        raise ValueError("Falta la plantilla de fichas producto.html")
    changed = []
    for source in sources:
        if source not in SOURCES:
            continue
        slug = Path(source).stem
        archive_path = folder / f"catalog-{slug}.json"
        events_path = folder / f"activity-{slug}.json"
        previous, archived_rejected = clean_gaming_feed(read_json(archive_path, {}), source)
        snapshot_path = folder / source
        snapshot = read_json(snapshot_path, {})
        snapshot, snapshot_rejected = clean_gaming_feed(snapshot, source)
        if snapshot_rejected:
            snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            changed.append(source)
        rejected_by_id = {
            product_id(product): product
            for product in archived_rejected + snapshot_rejected
        }
        for product in rejected_by_id.values():
            print(f"Descartado de {source}: {product.get('name') or product.get('asin')}")
        events = read_json(events_path, read_json(folder / "events.json", []) if source == "products.json" else [])
        if source in GAMING_SOURCES:
            events = [
                event for event in events
                if not is_non_gaming_product(event.get("asin"), event.get("name"))
            ]
        catalog, events = update_catalog(previous, snapshot, source, events)
        for path, data in ((archive_path, catalog), (events_path, events)):
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            changed.append(path.name)
    products = {}
    for source in SOURCES:
        for product in read_json(folder / f"catalog-{Path(source).stem}.json", {}).get("products", []):
            key = product_id(product)
            # Un registro archivado no debe ocultar otro origen que aún lo lista.
            old = products.get(key)
            if old is None or (product.get("status") != "sin_confirmar", product.get("last_seen", "")) > (old.get("status") != "sin_confirmar", old.get("last_seen", "")):
                products[key] = product

    overrides_path = folder / "product-group-overrides.json"
    if overrides_path.exists():
        group_overrides = read_json(overrides_path, DEFAULT_GROUP_OVERRIDES)
    else:
        group_overrides = DEFAULT_GROUP_OVERRIDES
        overrides_path.write_text(json.dumps(group_overrides, ensure_ascii=False, indent=2), encoding="utf-8")
        changed.append(overrides_path.name)
    grouped_products, grouping_review = build_groups(list(products.values()), group_overrides)
    for path, payload in (
        (folder / "product-groups.json", grouped_products),
        (folder / "grouping-review.json", grouping_review),
    ):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        changed.append(path.name)

    sets_path = folder / "sets.json"
    set_template_path = folder / "set.html"
    sets = read_json(sets_path, {}) if set_template_path.exists() else {}
    for slug in sets:
        if not re.fullmatch(r"[a-z0-9-]+", slug):
            raise ValueError(f"Slug de set inválido: {slug}")
    set_matches, set_owner = match_sets(sets, products)

    history_by_product = {}
    for source in SOURCES:
        for event in read_json(folder / f"activity-{Path(source).stem}.json", []):
            try:
                key = product_id(event)
            except (KeyError, ValueError):
                continue
            event_day = (event.get("ts") or "")[:10]
            # Algunos retailers fluctúan durante el día y pueden generar el
            # mismo restock varias veces. Para la ficha importa el día del
            # cambio, no cada ejecución del cron. Las bajadas con precios
            # distintos sí se conservan como eventos separados.
            event_key = ":".join((
                key, event_day, event.get("type") or "",
                str(event.get("prev_price") or "") if event.get("type") == "price_drop" else "",
                str(event.get("price") or "") if event.get("type") == "price_drop" else "",
            ))
            history_by_product.setdefault(key, {})[event_key] = event
    history_by_product = {
        key: sorted(events.values(), key=lambda event: event.get("ts") or "", reverse=True)
        for key, events in history_by_product.items()
    }

    template = template_path.read_text(encoding="utf-8")
    (folder / "producto").mkdir(exist_ok=True)
    groups = grouped_products.get("groups") or []
    group_by_offer = {
        offer.get("offer_id"): group
        for group in groups for offer in group.get("offers") or []
    }
    for key, product in products.items():
        name = f"producto/{key}.html"
        slug = set_owner.get(key)
        entry = (slug, set_matches[slug][0]) if slug else None
        related = related_products(key, product, products)
        history = history_by_product.get(key, [])
        (folder / name).write_text(
            render_page(template, product, entry, related=related, history=history, group=group_by_offer.get(key)),
            encoding="utf-8",
        )
        changed.append(name)
    for group in groups:
        name = f"producto/{group['id']}.html"
        (folder / name).write_text(render_group_page(template, group), encoding="utf-8")
        changed.append(name)
    valid_product_pages = {f"{key}.html" for key in products} | {f"{group['id']}.html" for group in groups}
    for path in (folder / "producto").glob("*.html"):
        if path.name not in valid_product_pages:
            path.unlink()
            changed.append(f"producto/{path.name}")

    if sets:
        set_template = set_template_path.read_text(encoding="utf-8")
        (folder / "set").mkdir(exist_ok=True)
        index_entries = []
        for slug, (config, matched) in set_matches.items():
            output, _ = render_set_page(set_template, slug, config, matched)
            name = f"set/{slug}.html"
            (folder / name).write_text(output, encoding="utf-8")
            changed.append(name)
            index_entries.append({
                "slug": slug, "name": config.get("name") or slug, "game": config.get("game"),
                "blurb": config.get("blurb") or "", "release_date": config.get("release_date") or "",
                "release_date_human": _human_date(config.get("release_date")),
                "available": sum(1 for p in matched if p.get("status") not in ("sin_confirmar", "no_disponible")),
            })
        index_template_path = folder / "set-index.html"
        if index_template_path.exists():
            # Más recientes primero: es el orden con el que se mira una lista
            # de expansiones, al revés que el calendario.
            index_entries.sort(key=lambda e: e["release_date"], reverse=True)
            (folder / "set" / "index.html").write_text(
                render_set_index(index_template_path.read_text(encoding="utf-8"), index_entries), encoding="utf-8")
            changed.append("set/index.html")

    changed.extend(write_sitemaps(folder, products, set_matches, groups))
    return changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", required=True)
    args = parser.parse_args()
    print(f"Generados {len(build_site(args.web, SOURCES))} archivos")

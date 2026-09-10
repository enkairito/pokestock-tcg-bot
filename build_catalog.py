"""Fichas estáticas persistentes y actividad por juego a partir de snapshots.

No deduce que un producto esté agotado por su ausencia: conserva su ficha
con disponibilidad sin confirmar. Se ejecuta sobre el checkout de publicación.
"""
import argparse
import html
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from stock_logic import alert_changes, price_to_float

SOURCES = ("products.json", "onepiece.json", "magic.json", "lorcana.json", "yugioh.json", "accesorios.json")
NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
ORIGIN = "https://wheresthatstock.com"
# "sin_confirmar" y cualquier estado nuevo caen en OutOfStock: sin confirmación
# reciente no debe anunciarse como comprable en resultados de búsqueda.
AVAILABILITY = {
    "compra_directa": "https://schema.org/InStock",
    "preventa": "https://schema.org/PreOrder",
    "invitacion": "https://schema.org/LimitedAvailability",
    "no_disponible": "https://schema.org/OutOfStock",
}


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


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


def render_page(template, product):
    name = html.escape(product.get("name") or "Producto")
    key = product_id(product)
    description = html.escape(f"{product.get('name', 'Producto')}: última disponibilidad observada y enlace a la tienda.", quote=True)
    embedded = json.dumps(product, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    output = re.sub(r"<title>.*?</title>", lambda _: f"<title>{name} — Where's That Stock</title>", template, count=1, flags=re.S)
    output = re.sub(r'<meta name="description"[^>]*>', lambda _: f'<meta name="description" content="{description}">', output, count=1)
    output = output.replace('<meta name="robots" content="noindex">', '')
    output = output.replace('</head>', f'<link rel="canonical" href="{ORIGIN}/producto/{key}">\n</head>', 1)
    image = product.get("image") or ""
    parsed_image = urlparse(image)
    if parsed_image.scheme != "https" or not parsed_image.netloc:
        image = f"{ORIGIN}/assets/brand/social.png"
    metadata = {
        "og:type": "website", "og:site_name": "Where's That Stock",
        "og:title": product.get("name") or "Producto",
        "og:description": f"Consulta este producto en {product.get('store_label') or 'la tienda'} y guárdalo en tus favoritos.",
        "og:url": f"{ORIGIN}/producto/{key}", "og:image": image,
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
    status = {"compra_directa": "Disponible en la última comprobación", "invitacion": "Disponible por invitación", "preventa": "Preventa", "no_disponible": "Agotado"}.get(product.get("status"), "Disponibilidad sin confirmar; ya no aparece en el listado actual")
    # Contenido real en el HTML inicial, incluso sin JavaScript o para buscadores.
    body = f'<h1>{name}</h1><p>{status}.</p><p>Última vez visto: {html.escape(product.get("last_seen", ""))}.</p>'
    link = product.get("link") or ""
    if link.startswith("https://"):
        body += f'<p><a href="{html.escape(link, quote=True)}" rel="noopener sponsored">Consultar en la tienda</a></p>'
    output, count = re.subn(r'(<div id="product-detail"[^>]*>).*?(</div>)', lambda m: m[1] + body + m[2], output, count=1, flags=re.S)
    if count != 1:
        raise ValueError("Falta el contenedor product-detail en la plantilla")
    return output.replace('<script src="/producto.js"></script>', f'<script id="product-data" type="application/json">{embedded}</script>\n<script src="/producto.js"></script>')


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


def render_set_page(template, slug, config, products):
    """Une la ficha editorial de sets.json (mantenida a mano, igual que el
    calendario) con los productos cuyo nombre contiene su palabra clave
    ("match"). No se guarda "set" en el catálogo: se recalcula en cada
    publicación a partir de los datos ya combinados, así que basta con
    editar sets.json para que un producto entre o salga del hub."""
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

    keyword = _normalize(config.get("match"))
    matched = [p for p in products.values() if keyword and keyword in _normalize(p.get("name"))]
    matched.sort(key=lambda p: p.get("checked_at") or p.get("last_seen") or "", reverse=True)

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
        previous = read_json(archive_path, {})
        events = read_json(events_path, read_json(folder / "events.json", []) if source == "products.json" else [])
        catalog, events = update_catalog(previous, read_json(folder / source, {}), source, events)
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
    template = template_path.read_text(encoding="utf-8")
    (folder / "producto").mkdir(exist_ok=True)
    for key, product in products.items():
        name = f"producto/{key}.html"
        (folder / name).write_text(render_page(template, product), encoding="utf-8")
        changed.append(name)

    sets_path = folder / "sets.json"
    set_template_path = folder / "set.html"
    sets = read_json(sets_path, {}) if set_template_path.exists() else {}
    set_matches = {}
    if sets:
        set_template = set_template_path.read_text(encoding="utf-8")
        (folder / "set").mkdir(exist_ok=True)
        for slug, config in sets.items():
            if not re.fullmatch(r"[a-z0-9-]+", slug):
                raise ValueError(f"Slug de set inválido: {slug}")
            output, matched = render_set_page(set_template, slug, config, products)
            name = f"set/{slug}.html"
            (folder / name).write_text(output, encoding="utf-8")
            changed.append(name)
            set_matches[slug] = (config, matched)

    sitemap = folder / "sitemap.xml"
    if sitemap.exists():
        tree = ET.parse(sitemap)
        root = tree.getroot()
        entries = {entry.findtext(f"{{{NS}}}loc"): entry for entry in root.findall(f"{{{NS}}}url")}
        def upsert(url, lastmod):
            entry = entries.get(url)
            if entry is None:
                entry = ET.SubElement(root, f"{{{NS}}}url")
                ET.SubElement(entry, f"{{{NS}}}loc").text = url
            modified = entry.find(f"{{{NS}}}lastmod")
            if modified is None:
                modified = ET.SubElement(entry, f"{{{NS}}}lastmod")
            modified.text = lastmod
        for key, product in products.items():
            upsert(f"{ORIGIN}/producto/{key}", product["checked_at"][:10])
        for slug, (config, matched) in set_matches.items():
            lastmod = max((p.get("checked_at") or "" for p in matched), default="") or config.get("release_date") or ""
            if lastmod:
                upsert(f"{ORIGIN}/set/{slug}", lastmod[:10])
        ET.register_namespace("", NS)
        tree.write(sitemap, encoding="UTF-8", xml_declaration=True)
        changed.append("sitemap.xml")
    return changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", required=True)
    args = parser.parse_args()
    print(f"Generados {len(build_site(args.web, SOURCES))} archivos")

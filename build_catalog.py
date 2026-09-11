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

SOURCES = ("products.json", "onepiece.json", "magic.json", "lorcana.json", "yugioh.json", "nintendo.json", "playstation.json", "xbox.json", "accesorios.json")
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


def render_page(template, product, set_entry=None):
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
    category_path, category_label = CATEGORY_LINKS.get(product.get("_src"), CATEGORY_LINKS["products.json"])
    crumbs_json = breadcrumb_ld(
        ("Inicio", f"{ORIGIN}/"),
        (category_label, f"{ORIGIN}{category_path}"),
        (product.get("name") or "Producto", f"{ORIGIN}/producto/{key}"),
    )
    output = output.replace('</head>', f'<script type="application/ld+json">{crumbs_json}</script>\n</head>', 1)
    status = {"compra_directa": "Disponible en la última comprobación", "invitacion": "Disponible por invitación", "preventa": "Preventa", "no_disponible": "Agotado"}.get(product.get("status"), "Disponibilidad sin confirmar; ya no aparece en el listado actual")
    # Contenido real en el HTML inicial, incluso sin JavaScript o para buscadores.
    body = f'<h1>{name}</h1><p>{status}.</p><p>Última vez visto: {html.escape(product.get("last_seen", ""))}.</p>'
    link = product.get("link") or ""
    if link.startswith("https://"):
        body += f'<p><a href="{html.escape(link, quote=True)}" rel="noopener sponsored">Consultar en la tienda</a></p>'
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
    sets_path = folder / "sets.json"
    set_template_path = folder / "set.html"
    sets = read_json(sets_path, {}) if set_template_path.exists() else {}
    for slug in sets:
        if not re.fullmatch(r"[a-z0-9-]+", slug):
            raise ValueError(f"Slug de set inválido: {slug}")
    set_matches, set_owner = match_sets(sets, products)

    template = template_path.read_text(encoding="utf-8")
    (folder / "producto").mkdir(exist_ok=True)
    for key, product in products.items():
        name = f"producto/{key}.html"
        slug = set_owner.get(key)
        entry = (slug, set_matches[slug][0]) if slug else None
        (folder / name).write_text(render_page(template, product, entry), encoding="utf-8")
        changed.append(name)

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
        newest_set = ""
        for slug, (config, matched) in set_matches.items():
            lastmod = max((p.get("checked_at") or "" for p in matched), default="") or config.get("release_date") or ""
            if lastmod:
                upsert(f"{ORIGIN}/set/{slug}", lastmod[:10])
                newest_set = max(newest_set, lastmod[:10])
        if newest_set:
            upsert(f"{ORIGIN}/set/", newest_set)
        ET.register_namespace("", NS)
        tree.write(sitemap, encoding="UTF-8", xml_declaration=True)
        changed.append("sitemap.xml")
    return changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", required=True)
    args = parser.parse_args()
    print(f"Generados {len(build_site(args.web, SOURCES))} archivos")

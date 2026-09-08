"""Fichas estáticas persistentes y actividad por juego a partir de snapshots.

No deduce que un producto esté agotado por su ausencia: conserva su ficha
con disponibilidad sin confirmar. Se ejecuta sobre el checkout de publicación.
"""
import argparse
import html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from stock_logic import alert_changes

SOURCES = ("products.json", "onepiece.json", "magic.json", "lorcana.json", "yugioh.json", "accesorios.json")
NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
ORIGIN = "https://wheresthatstock.com"


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
    sitemap = folder / "sitemap.xml"
    if sitemap.exists():
        tree = ET.parse(sitemap)
        root = tree.getroot()
        entries = {entry.findtext(f"{{{NS}}}loc"): entry for entry in root.findall(f"{{{NS}}}url")}
        for key, product in products.items():
            url = f"{ORIGIN}/producto/{key}"
            entry = entries.get(url)
            if entry is None:
                entry = ET.SubElement(root, f"{{{NS}}}url")
                ET.SubElement(entry, f"{{{NS}}}loc").text = url
            modified = entry.find(f"{{{NS}}}lastmod")
            if modified is None:
                modified = ET.SubElement(entry, f"{{{NS}}}lastmod")
            modified.text = product["checked_at"][:10]
        ET.register_namespace("", NS)
        tree.write(sitemap, encoding="UTF-8", xml_declaration=True)
        changed.append("sitemap.xml")
    return changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", required=True)
    args = parser.parse_args()
    print(f"Generados {len(build_site(args.web, SOURCES))} archivos")

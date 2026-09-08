"""Actualiza <lastmod> a la fecha de hoy (UTC) para las páginas de
wheresthatstock cuyo contenido cambia con cada ejecución del bot (listados
de stock). Las páginas editoriales (noticias, calendario de lanzamientos)
no se tocan aquí — su lastmod se actualiza a mano al publicar contenido
nuevo, no en cada run del cron.

Uso: python update_sitemap_lastmod.py --sitemap /ruta/a/sitemap.xml
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
ET.register_namespace("", NS)

DYNAMIC_PAGES = {
    "https://wheresthatstock.com/",
    "https://wheresthatstock.com/pokemontcg",
    "https://wheresthatstock.com/ofertas",
    "https://wheresthatstock.com/accesorios",
    "https://wheresthatstock.com/sobres",
    "https://wheresthatstock.com/cajas-etb",
    "https://wheresthatstock.com/cajas-de-coleccion",
    "https://wheresthatstock.com/colecciones-premium",
    "https://wheresthatstock.com/latas",
    "https://wheresthatstock.com/onepiece",
    "https://wheresthatstock.com/magic",
    "https://wheresthatstock.com/lorcana",
    "https://wheresthatstock.com/yugioh",
}


def update_sitemap(sitemap):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    tree = ET.parse(sitemap)
    root = tree.getroot()
    updated = 0
    for url_el in root.findall(f"{{{NS}}}url"):
        loc_el = url_el.find(f"{{{NS}}}loc")
        if loc_el is None or loc_el.text not in DYNAMIC_PAGES:
            continue
        lastmod_el = url_el.find(f"{{{NS}}}lastmod")
        if lastmod_el is None:
            lastmod_el = ET.SubElement(url_el, f"{{{NS}}}lastmod")
            url_el.insert(1, lastmod_el)
        lastmod_el.text = today
        updated += 1

    tree.write(sitemap, encoding="UTF-8", xml_declaration=True)
    print(f"sitemap.xml: {updated} URLs actualizadas a lastmod={today}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sitemap", required=True)
    args = parser.parse_args()
    update_sitemap(args.sitemap)


if __name__ == "__main__":
    sys.exit(main())

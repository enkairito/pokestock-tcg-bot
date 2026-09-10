import argparse
import json
from pathlib import Path


SOURCES = ("accessories", "onepiece", "magic", "lorcana", "yugioh")


def product_source(product):
    """Migra los registros publicados antes de añadir el origen explícito."""
    if product.get("source"):
        return product["source"]
    return "onepiece" if "One Piece" in (product.get("categories") or []) else "accessories"


def merge_snapshots(base, new, source):
    """Reemplaza un origen completo y conserva una sola fila por tienda/ASIN.

    El último registro gana: los duplicados históricos se añadían al final,
    y una observación nueva debe prevalecer sobre la que ya estaba publicada.
    La categoría describe el juego; no determina quién publica el producto.
    """
    if source not in SOURCES:
        raise ValueError(f"Origen de accesorios desconocido: {source}")
    products = {}
    for product in base.get("products", []):
        owner = product_source(product)
        if owner != source:
            products[(product["marketplace"], product["asin"])] = {**product, "source": owner}
    for product in new["products"]:
        products[(product["marketplace"], product["asin"])] = {**product, "source": source}
    updates = {**base.get("source_updates", {}), source: new.get("updated_at")}
    return {"updated_at": new.get("updated_at"), "source_updates": updates,
            "products": list(products.values())}


def main():
    parser = argparse.ArgumentParser(
        description="Combina accesorios.json publicado con un snapshot nuevo, sin pisar "
        "lo que el otro workflow (Pokémon/One Piece) haya publicado."
    )
    parser.add_argument("--base", required=True, help="accesorios.json ya publicado (puede no existir aún)")
    parser.add_argument("--new", required=True, help="snapshot recién generado en esta ejecución")
    parser.add_argument("--out", required=True, help="ruta de salida")
    parser.add_argument("--source", required=True, choices=SOURCES, help="Proceso que genera el snapshot, independiente del juego o categoría.")
    args = parser.parse_args()

    base_path = Path(args.base)
    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else {"products": []}
    new = json.loads(Path(args.new).read_text(encoding="utf-8"))

    merged = merge_snapshots(base, new, args.source)
    Path(args.out).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Merge accesorios ({args.source}): {len(new['products'])} observados, {len(merged['products'])} productos únicos publicados")


if __name__ == "__main__":
    main()

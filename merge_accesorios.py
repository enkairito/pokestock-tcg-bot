import argparse
import json
from pathlib import Path


def is_mine(product, tag):
    categories = product.get("categories") or []
    if tag:
        return tag in categories
    return not categories


def main():
    parser = argparse.ArgumentParser(
        description="Combina accesorios.json publicado con un snapshot nuevo, sin pisar "
        "lo que el otro workflow (Pokémon/One Piece) haya publicado."
    )
    parser.add_argument("--base", required=True, help="accesorios.json ya publicado (puede no existir aún)")
    parser.add_argument("--new", required=True, help="snapshot recién generado en esta ejecución")
    parser.add_argument("--out", required=True, help="ruta de salida")
    parser.add_argument("--tag", default="", help='Categoría que "es mía" en este snapshot, ej. "One Piece". Vacío = productos sin categoría (Pokémon).')
    args = parser.parse_args()

    base_path = Path(args.base)
    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else {"products": []}
    new = json.loads(Path(args.new).read_text(encoding="utf-8"))

    kept = [p for p in base.get("products", []) if not is_mine(p, args.tag)]
    merged_products = kept + new.get("products", [])

    merged = {"updated_at": new.get("updated_at"), "products": merged_products}
    Path(args.out).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Merge accesorios: {len(kept)} conservados + {len(new.get('products', []))} nuevos = {len(merged_products)} total")


if __name__ == "__main__":
    main()

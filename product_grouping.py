"""Agrupa ofertas de Pokémon TCG y genera una cola de revisión manual.

La agrupación automática es deliberadamente conservadora: EAN/GTIN exacto
o una firma de título exacta entre tiendas distintas. Las coincidencias
probables se sugieren, pero nunca se fusionan sin confirmación.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import re
import unicodedata
from pathlib import Path


ORIGIN = "https://wheresthatstock.com"
ACTIVE_STATUSES = {"compra_directa", "preventa", "invitacion"}
SAFE_GROUP_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,95}")
IDENTIFIER_FIELDS = ("gtin", "ean", "upc", "barcode")
DEFAULT_OVERRIDES = {
    "version": 1,
    "groups": {},
    "assign": {},
    "separate": [],
    "ignore": [],
}

STOPWORDS = {
    "a", "al", "and", "con", "de", "del", "el", "en", "for", "la",
    "las", "los", "of", "para", "por", "the", "un", "una", "y",
    "card", "cards", "carta", "cartas", "game", "jcc", "juego", "tcg",
}

GENERIC_MATCH_TOKENS = {
    "pokemon", "magic", "gathering", "one", "piece", "lorcana", "yugioh",
    "nintendo", "playstation", "xbox", "scarlet", "violet", "sword", "shield",
    "mega", "evolution", "edition", "espanol", "ingles", "japones", "ex",
    "caja", "sobre", "pack", "boosterpack", "boosterbox", "etb", "elite",
    "trainer", "collection", "coleccion", "premium", "baraja", "combate",
    "liga", "lata", "tin", "bundle", "cartas", "cards",
}

TOKEN_ALIASES = {
    "espanola": "espanol", "spanish": "espanol", "castellano": "espanol",
    "english": "ingles", "japanese": "japones", "japan": "japones",
    "boosters": "sobre", "booster": "sobre", "sobres": "sobre",
    "packs": "pack", "paquetes": "pack", "paquete": "pack",
    "cajas": "caja", "boxes": "caja", "box": "caja",
    "edicion": "edition", "version": "edition",
    "elite": "elite", "entrenador": "trainer",
}


def _normalize(value):
    value = unicodedata.normalize("NFD", str(value or "").lower())
    return "".join(char for char in value if unicodedata.category(char) != "Mn")


def is_pokemon_tcg(product):
    """Mantiene el comparador aislado del resto de juegos y de Gaming."""
    return _normalize(product.get("game")) == "pokemon"


def offer_id(product):
    marketplace = product.get("marketplace")
    identifier = product.get("asin")
    if not isinstance(marketplace, str) or not isinstance(identifier, str):
        raise ValueError("Oferta sin marketplace o identificador")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", marketplace) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise ValueError("Identificador de oferta inválido")
    return f"{marketplace}-{identifier}"


def _language(product):
    text = _normalize(product.get("name"))
    checks = (
        ("espanol", ("espanol", "castellano", "spanish")),
        ("ingles", ("ingles", "english")),
        ("japones", ("japones", "japanese", " japon ", " jp ")),
        ("frances", ("frances", "french")),
        ("aleman", ("aleman", "german")),
    )
    padded = f" {text} "
    for language, markers in checks:
        if any(marker in padded for marker in markers):
            return language
    return ""


def _product_type(product):
    text = _normalize(product.get("name"))
    categories = " ".join(_normalize(value) for value in product.get("categories") or [])
    combined = f"{text} {categories}"
    patterns = (
        ("etb", ("elite trainer box", "caja de entrenador elite", "caja entrenador elite", " etb ")),
        ("booster_box", ("booster box", "caja de sobres", "caja sobres", "display")),
        ("booster_pack", ("sobre de mejora", "sobre individual", " booster pack", " sobre ")),
        ("bundle", ("bundle", "lote", "pack de", " pack ")),
        ("tin", ("mini tin", "lata", " tin ")),
        ("collection", ("coleccion", "collection", "caja ex", "box set")),
        ("deck", ("mazo", "deck", "baraja")),
        ("console", ("consola", "console", "playstation 5", "xbox series", "nintendo switch")),
        ("controller", ("mando", "controller", "joy-con", "joy con")),
        ("videogame", ("videojuego", "video game", "juego para", "ps5", "ps4")),
        ("accessory", ("funda", "sleeve", "album", "binder", "tapete", "playmat")),
    )
    padded = f" {combined} "
    for product_type, markers in patterns:
        if any(marker in padded for marker in markers):
            return product_type
    return ""


def _quantity_markers(product):
    text = _normalize(product.get("name"))
    patterns = (
        r"\b(\d{1,3})\s*(?:sobres?|boosters?|packs?|paquetes?)\b",
        r"\b(?:caja|box|display)\s+(?:de\s+)?(\d{1,3})\b",
        r"\b(\d{1,4})\s*(?:cartas?|cards?|fundas?|sleeves?)\b",
    )
    return tuple(sorted({int(match) for pattern in patterns for match in re.findall(pattern, text)}))


def _codes(product):
    text = _normalize(product.get("name")).upper()
    return tuple(sorted(set(re.findall(r"\b(?:OP|EB|PRB|DP|ST|BT|SV|ME|M)[- ]?\d{1,3}[A-Z]?\b", text))))


def _family(product):
    if product.get("game"):
        return _normalize(product["game"])
    source = product.get("_src") or product.get("source") or ""
    categories = " ".join(product.get("categories") or [])
    return _normalize(f"{source} {categories}").strip()


def _tokens(product):
    text = _normalize(product.get("name"))
    # Las frases equivalentes se reducen antes de tokenizar para que el orden
    # y la traducción habitual de un retailer no impidan sugerir candidatos.
    phrases = {
        "caja de entrenador elite": "etb",
        "caja entrenador elite": "etb",
        "elite trainer box": "etb",
        "caja de sobres": "boosterbox",
        "booster box": "boosterbox",
        "sobre de mejora": "boosterpack",
        "trading card game": "",
        "pokemon jcc": "pokemon",
    }
    for old, new in phrases.items():
        text = text.replace(old, new)
    values = []
    for token in re.findall(r"[a-z0-9]+", text):
        token = TOKEN_ALIASES.get(token, token)
        if token not in STOPWORDS and len(token) > 1:
            values.append(token)
    return tuple(values)


def _identifier(product):
    for field in IDENTIFIER_FIELDS:
        raw = re.sub(r"\D", "", str(product.get(field) or ""))
        if 8 <= len(raw) <= 14:
            return f"gtin:{raw}"
    return ""


def identity_signature(product):
    identifier = _identifier(product)
    if identifier:
        return identifier
    tokens = tuple(sorted(_tokens(product)))
    if len(tokens) < 3:
        return ""
    return "|".join((
        _family(product), _product_type(product), _language(product),
        ",".join(map(str, _quantity_markers(product))),
        ",".join(_codes(product)), " ".join(tokens),
    ))


def _slug(value, limit=62):
    slug = re.sub(r"[^a-z0-9]+", "-", _normalize(value)).strip("-")
    return (slug[:limit].rstrip("-") or "producto")


def _automatic_group_id(products, signature):
    name = _canonical_product(products).get("name") or "producto"
    digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:10]
    return f"{_slug(name)}-{digest}"


def _canonical_product(products):
    def quality(product):
        name = product.get("name") or ""
        spanish = _language(product) in ("", "espanol")
        local = product.get("marketplace") in ("ES", "ECI", "CAR", "FNAC", "TRU")
        # Evita elegir títulos diminutos, pero penaliza el texto promocional
        # interminable de algunos marketplaces.
        useful_length = min(len(name), 120) - max(len(name) - 160, 0)
        return spanish, local, useful_length, name
    return max(products, key=quality)


def _compatible(left, right):
    if _family(left) != _family(right):
        return False
    left_type, right_type = _product_type(left), _product_type(right)
    if left_type and right_type and left_type != right_type:
        return False
    left_language, right_language = _language(left), _language(right)
    if left_language and right_language and left_language != right_language:
        return False
    left_quantity, right_quantity = _quantity_markers(left), _quantity_markers(right)
    if left_quantity and right_quantity and left_quantity != right_quantity:
        return False
    left_codes, right_codes = set(_codes(left)), set(_codes(right))
    if left_codes and right_codes and not left_codes.intersection(right_codes):
        return False
    return True


def similarity(left, right):
    if not _compatible(left, right):
        return 0.0
    left_tokens, right_tokens = set(_tokens(left)), set(_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    distinctive_overlap = (
        (left_tokens - GENERIC_MATCH_TOKENS)
        & (right_tokens - GENERIC_MATCH_TOKENS)
    )
    code_overlap = set(_codes(left)) & set(_codes(right))
    if not distinctive_overlap and not code_overlap:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    sequence = SequenceMatcher(None, " ".join(_tokens(left)), " ".join(_tokens(right))).ratio()
    code_bonus = 0.12 if code_overlap else 0.0
    type_bonus = 0.08 if _product_type(left) and _product_type(left) == _product_type(right) else 0.0
    return min(1.0, round(jaccard * 0.65 + sequence * 0.35 + code_bonus + type_bonus, 4))


def _offer_payload(product):
    key = offer_id(product)
    return {
        "offer_id": key,
        "marketplace": product.get("marketplace"),
        "store": product.get("store_label") or product.get("marketplace"),
        "name": product.get("name") or "Producto",
        "price": product.get("price"),
        "original_price": product.get("original_price"),
        "status": product.get("status"),
        "last_seen": product.get("last_seen"),
        "checked_at": product.get("checked_at"),
        "link": product.get("link"),
        "image": product.get("image"),
        "product_url": f"{ORIGIN}/producto/{key}",
    }


def _group_payload(group_id, products, method, metadata=None):
    metadata = metadata or {}
    canonical = _canonical_product(products)
    offers = sorted(
        (_offer_payload(product) for product in products),
        key=lambda offer: (offer["status"] not in ACTIVE_STATUSES, offer["store"] or "", offer["offer_id"]),
    )
    return {
        "id": group_id,
        "url": f"{ORIGIN}/comparar/{group_id}",
        "name": metadata.get("name") or canonical.get("name") or "Producto",
        "game": metadata.get("game") or canonical.get("game"),
        "category": metadata.get("category") or ((canonical.get("categories") or [None])[0]),
        "language": metadata.get("language") or _language(canonical) or None,
        "method": method,
        "offers": offers,
    }


def validate_overrides(overrides):
    if not isinstance(overrides, dict):
        raise ValueError("product-group-overrides.json debe ser un objeto")
    groups = overrides.get("groups") or {}
    assignments = overrides.get("assign") or {}
    if not isinstance(groups, dict) or not isinstance(assignments, dict):
        raise ValueError("groups y assign deben ser objetos")
    if any(not isinstance(metadata, dict) for metadata in groups.values()):
        raise ValueError("Cada entrada de groups debe ser un objeto")
    separate_values = overrides.get("separate") or []
    ignored_values = overrides.get("ignore") or []
    if not isinstance(separate_values, list) or not isinstance(ignored_values, list):
        raise ValueError("separate e ignore deben ser listas")
    separate, ignored = set(separate_values), set(ignored_values)
    group_ids = list(groups) + list(assignments.values())
    invalid_groups = [group_id for group_id in group_ids if not isinstance(group_id, str) or not SAFE_GROUP_ID.fullmatch(group_id)]
    if invalid_groups:
        raise ValueError(f"IDs de grupo inválidos: {', '.join(sorted(map(str, invalid_groups)))}")
    offer_ids = list(assignments) + separate_values + ignored_values
    if any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key) for key in offer_ids):
        raise ValueError("IDs de oferta inválidos en las decisiones manuales")
    assigned = set(assignments)
    conflicts = (assigned & separate) | (assigned & ignored) | (separate & ignored)
    if conflicts:
        raise ValueError(f"Ofertas con decisiones manuales incompatibles: {', '.join(sorted(conflicts))}")


def build_groups(products, overrides=None, suggestion_limit=3):
    overrides = {**DEFAULT_OVERRIDES, **(overrides or {})}
    validate_overrides(overrides)
    products = [product for product in products if is_pokemon_tcg(product)]
    products_by_id = {offer_id(product): product for product in products}
    assignments = overrides.get("assign") or {}
    separate = set(overrides.get("separate") or [])
    ignored = set(overrides.get("ignore") or [])
    reserved = set(assignments) | separate | ignored

    automatic_buckets = defaultdict(list)
    for key, product in products_by_id.items():
        if key in reserved:
            continue
        signature = identity_signature(product)
        if signature:
            automatic_buckets[signature].append(product)

    grouped = {}
    for signature, bucket in automatic_buckets.items():
        marketplaces = {product.get("marketplace") for product in bucket}
        if len(bucket) < 2 or len(marketplaces) < 2:
            continue
        group_id = _automatic_group_id(bucket, signature)
        grouped[group_id] = {"products": bucket, "method": "automatic_exact"}

    unknown_override_ids = []
    manual_products = defaultdict(list)
    for key, group_id in assignments.items():
        product = products_by_id.get(key)
        if product is None:
            unknown_override_ids.append(key)
            continue
        manual_products[group_id].append(product)
    metadata_by_group = overrides.get("groups") or {}
    for group_id, bucket in manual_products.items():
        existing = grouped.setdefault(group_id, {"products": [], "method": "manual"})
        existing["method"] = "manual"
        seen = {offer_id(product) for product in existing["products"]}
        existing["products"].extend(product for product in bucket if offer_id(product) not in seen)

    groups = [
        _group_payload(group_id, entry["products"], entry["method"], metadata_by_group.get(group_id))
        for group_id, entry in grouped.items() if entry["products"]
    ]
    groups.sort(key=lambda group: (group.get("game") or "", group["name"], group["id"]))
    offer_to_group = {
        offer["offer_id"]: group["id"]
        for group in groups for offer in group["offers"]
    }

    review_items = []
    candidate_pool = [
        product for key, product in products_by_id.items()
        if key not in ignored and key not in separate
    ]
    for key, product in products_by_id.items():
        if key in offer_to_group or key in ignored or key in separate:
            continue
        suggestions = []
        for candidate in candidate_pool:
            candidate_id = offer_id(candidate)
            if candidate_id == key or candidate.get("marketplace") == product.get("marketplace"):
                continue
            score = similarity(product, candidate)
            if score < 0.5:
                continue
            suggestions.append({
                "offer_id": candidate_id,
                "group_id": offer_to_group.get(candidate_id),
                "name": candidate.get("name") or "Producto",
                "store": candidate.get("store_label") or candidate.get("marketplace"),
                "price": candidate.get("price"),
                "score": score,
            })
        suggestions.sort(key=lambda candidate: (-candidate["score"], candidate["offer_id"]))
        review_items.append({
            **_offer_payload(product),
            "game": product.get("game"),
            "category": ((product.get("categories") or [None])[0]),
            "reason": "possible_matches" if suggestions else "no_match",
            "suggestions": suggestions[:suggestion_limit],
        })
    review_items.sort(key=lambda item: (
        item["status"] not in ACTIVE_STATUSES,
        item["reason"] != "possible_matches",
        item.get("game") or "",
        item["name"],
    ))

    timestamp = max((product.get("checked_at") or product.get("last_seen") or "" for product in products), default="")
    summary = {
        "offers": len(products_by_id),
        "grouped_offers": len(offer_to_group),
        "groups": len(groups),
        "pending_review": len(review_items),
        "possible_matches": sum(item["reason"] == "possible_matches" for item in review_items),
        "reviewed_separate": len(separate),
        "ignored": len(ignored),
        "unknown_override_ids": sorted(unknown_override_ids),
    }
    return (
        {"version": 1, "updated_at": timestamp, "summary": summary, "groups": groups},
        {"version": 1, "updated_at": timestamp, "summary": summary, "items": review_items},
    )


def read_products(web_root):
    products = {}
    for path in Path(web_root).glob("catalog-*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for product in payload.get("products") or []:
            product = dict(product)
            product.setdefault("_src", path.name.removeprefix("catalog-").replace(".json", ".json"))
            key = offer_id(product)
            previous = products.get(key)
            if previous is None or (product.get("status") in ACTIVE_STATUSES, product.get("last_seen") or "") > (previous.get("status") in ACTIVE_STATUSES, previous.get("last_seen") or ""):
                products[key] = product
    return list(products.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", required=True, help="Checkout del frontend con catalog-*.json")
    parser.add_argument("--output", help="Carpeta de salida; por defecto, el propio checkout")
    args = parser.parse_args()
    web_root = Path(args.web)
    output = Path(args.output) if args.output else web_root
    output.mkdir(parents=True, exist_ok=True)
    overrides_path = web_root / "product-group-overrides.json"
    overrides = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.exists() else DEFAULT_OVERRIDES
    groups, review = build_groups(read_products(web_root), overrides)
    generated_at = datetime.now(timezone.utc).isoformat()
    groups["generated_at"] = generated_at
    review["generated_at"] = generated_at
    for name, payload in (("product-groups.json", groups), ("grouping-review.json", review)):
        (output / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(groups["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()

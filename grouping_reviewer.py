"""Panel local para revisar agrupaciones de productos Pokémon TCG."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
import unicodedata
import webbrowser

from product_grouping import DEFAULT_OVERRIDES, build_groups, offer_id, read_products


MAX_REQUEST_BYTES = 16_384


class ReviewError(ValueError):
    pass


def _slug(value):
    normalized = unicodedata.normalize("NFD", str(value or "").lower())
    ascii_value = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")[:58].rstrip("-") or "producto-pokemon"


def _manual_group_id(left, right):
    ids = sorted((offer_id(left), offer_id(right)))
    digest = hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()[:10]
    return f"{_slug(left.get('name'))}-{digest}"


class ReviewStore:
    def __init__(self, web_root):
        self.web_root = Path(web_root).resolve()
        self.overrides_path = self.web_root / "product-group-overrides.json"
        self.products = read_products(self.web_root)
        if not self.products:
            raise ReviewError(f"No se encontraron catálogos en {self.web_root}")
        self.products_by_id = {offer_id(product): product for product in self.products}
        self.overrides = self._read_overrides()
        self.history = []
        self.lock = threading.RLock()
        self._rebuild()

    def _read_overrides(self):
        if not self.overrides_path.exists():
            return deepcopy(DEFAULT_OVERRIDES)
        try:
            saved = json.loads(self.overrides_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ReviewError(f"No se pudo leer {self.overrides_path}: {error}") from error
        return {**deepcopy(DEFAULT_OVERRIDES), **saved}

    def _rebuild(self):
        self.groups, self.review = build_groups(self.products, self.overrides)

    def _write(self):
        temporary = self.overrides_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.overrides, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.overrides_path)

    def _snapshot(self):
        self.history.append(deepcopy(self.overrides))
        if len(self.history) > 50:
            self.history.pop(0)

    def _review_item(self, source_id):
        return next((item for item in self.review["items"] if item["offer_id"] == source_id), None)

    def _assert_current_pair(self, source_id, candidate_id):
        item = self._review_item(source_id)
        if item is None:
            raise ReviewError("La oferta ya no está pendiente de revisión")
        if candidate_id not in {candidate["offer_id"] for candidate in item["suggestions"]}:
            raise ReviewError("La coincidencia ya no está disponible; recarga el panel")
        return item

    def _clean_exclusive_decisions(self, offer_ids):
        ids = set(offer_ids)
        self.overrides["separate"] = [key for key in self.overrides.get("separate", []) if key not in ids]
        self.overrides["ignore"] = [key for key in self.overrides.get("ignore", []) if key not in ids]

    def _same_product(self, source_id, candidate_id):
        item = self._assert_current_pair(source_id, candidate_id)
        left = self.products_by_id[source_id]
        right = self.products_by_id[candidate_id]
        memberships = {
            group["id"]: [offer["offer_id"] for offer in group["offers"]]
            for group in self.groups["groups"]
        }
        group_by_offer = {
            member: group_id for group_id, members in memberships.items() for member in members
        }
        group_ids = {
            group_by_offer.get(source_id), group_by_offer.get(candidate_id),
            (self.overrides.get("assign") or {}).get(source_id),
            (self.overrides.get("assign") or {}).get(candidate_id),
        } - {None}
        if len(group_ids) > 1:
            raise ReviewError("Las ofertas ya pertenecen a grupos diferentes")
        group_id = next(iter(group_ids), None) or _manual_group_id(left, right)
        members = set(memberships.get(group_id, [])) | {source_id, candidate_id}
        assignments = self.overrides.setdefault("assign", {})
        for member in members:
            assignments[member] = group_id
        self._clean_exclusive_decisions(members)
        self.overrides.setdefault("groups", {}).setdefault(group_id, {
            "name": item.get("name") or left.get("name") or "Producto Pokémon",
            "game": "Pokémon",
        })
        pair = frozenset((source_id, candidate_id))
        self.overrides["reject_pairs"] = [
            saved for saved in self.overrides.get("reject_pairs", []) if frozenset(saved) != pair
        ]

    def _reject_pair(self, source_id, candidate_id):
        self._assert_current_pair(source_id, candidate_id)
        pair = sorted((source_id, candidate_id))
        existing = {tuple(sorted(saved)) for saved in self.overrides.get("reject_pairs", [])}
        if tuple(pair) not in existing:
            self.overrides.setdefault("reject_pairs", []).append(pair)

    def _single_offer_decision(self, source_id, destination):
        if self._review_item(source_id) is None:
            raise ReviewError("La oferta ya no está pendiente de revisión")
        self.overrides.setdefault("assign", {}).pop(source_id, None)
        other = "ignore" if destination == "separate" else "separate"
        self.overrides[other] = [key for key in self.overrides.get(other, []) if key != source_id]
        values = self.overrides.setdefault(destination, [])
        if source_id not in values:
            values.append(source_id)

    def apply(self, action, source_id=None, candidate_id=None):
        with self.lock:
            if action == "undo":
                if not self.history:
                    raise ReviewError("No hay ninguna decisión que deshacer")
                self.overrides = self.history.pop()
                self._write()
                self._rebuild()
                return self.state()
            if action not in {"same", "reject", "separate", "ignore"}:
                raise ReviewError("Acción desconocida")
            if not isinstance(source_id, str) or source_id not in self.products_by_id:
                raise ReviewError("Oferta de origen inválida")
            if action in {"same", "reject"}:
                if not isinstance(candidate_id, str) or candidate_id not in self.products_by_id:
                    raise ReviewError("Oferta candidata inválida")
            self._snapshot()
            try:
                if action == "same":
                    self._same_product(source_id, candidate_id)
                elif action == "reject":
                    self._reject_pair(source_id, candidate_id)
                else:
                    self._single_offer_decision(source_id, action)
                self._write()
                self._rebuild()
            except Exception:
                self.overrides = self.history.pop()
                raise
            return self.state()

    def reopen_groups(self, group_ids):
        """Devuelve grupos manuales a la cola para poder revisarlos otra vez."""
        with self.lock:
            requested = set(group_ids)
            known = set((self.overrides.get("groups") or {})) | set((self.overrides.get("assign") or {}).values())
            missing = sorted(requested - known)
            if missing:
                raise ReviewError(f"Grupos desconocidos: {', '.join(missing)}")
            self._snapshot()
            self.overrides["assign"] = {
                key: group_id for key, group_id in (self.overrides.get("assign") or {}).items()
                if group_id not in requested
            }
            for group_id in requested:
                self.overrides.setdefault("groups", {}).pop(group_id, None)
            self._write()
            self._rebuild()
            return self.state()

    def state(self):
        with self.lock:
            possible = [item for item in self.review["items"] if item["suggestions"]]
            summary = dict(self.review["summary"])
            summary["possible_items"] = len(possible)
            return {
                "summary": summary,
                "items": possible,
                "can_undo": bool(self.history),
                "overrides_path": str(self.overrides_path),
            }


def make_handler(store, token, html):
    class ReviewHandler(BaseHTTPRequestHandler):
        def _json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                body = html.replace("__REVIEW_TOKEN__", token).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                self._json(200, store.state())
            else:
                self._json(404, {"error": "No encontrado"})

        def do_POST(self):
            if self.path != "/api/decision":
                self._json(404, {"error": "No encontrado"})
                return
            if not secrets.compare_digest(self.headers.get("X-Reviewer-Token", ""), token):
                self._json(403, {"error": "Token local inválido"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > MAX_REQUEST_BYTES:
                    raise ReviewError("Petición demasiado grande o vacía")
                payload = json.loads(self.rfile.read(size).decode("utf-8"))
                state = store.apply(
                    payload.get("action"), payload.get("source_id"), payload.get("candidate_id")
                )
            except (ReviewError, json.JSONDecodeError, UnicodeDecodeError) as error:
                self._json(400, {"error": str(error)})
                return
            except OSError as error:
                self._json(500, {"error": f"No se pudo guardar el archivo de decisiones: {error}"})
                return
            self._json(200, state)

        def log_message(self, format_string, *args):
            return

    return ReviewHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", default="../wheresthatstock", help="Checkout del frontend")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reopen-group", action="append", default=[], help="Grupo manual que volverá a la cola")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("El puerto debe estar entre 1 y 65535")

    store = ReviewStore(args.web)
    if args.reopen_group:
        state = store.reopen_groups(args.reopen_group)
        print(f"Grupos reabiertos. Comparaciones disponibles: {state['summary']['possible_items']}")
    html_path = Path(__file__).with_name("grouping_reviewer.html")
    html = html_path.read_text(encoding="utf-8")
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store, token, html))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Revisor de Pokémon TCG: {url}")
    print(f"Guardando decisiones en: {store.overrides_path}")
    print("Pulsa Ctrl+C para cerrar.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nRevisor cerrado.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

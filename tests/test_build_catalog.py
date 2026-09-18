import json
import tempfile
import unittest
from pathlib import Path
from build_catalog import build_site, clean_gaming_feed, match_sets, render_group_page, render_page, render_set_index, render_set_page, update_catalog, product_id

PRODUCT = {"asin": "B000000001", "marketplace": "ES", "name": "Magic Booster", "status": "compra_directa", "price": "10,00 €", "game": "Magic"}
TEMPLATE = '<html><head><title>Producto</title><meta name="description" content=""><meta name="robots" content="noindex"></head><body><div id="product-detail">Cargando</div><!--PRODUCT-HISTORY--><!--RELATED-PRODUCTS--><script src="/producto.js"></script></body></html>'
SET_TEMPLATE = '<html><head><title>Set</title><meta name="description" content=""><meta name="robots" content="noindex"></head><body><script id="set-data" type="application/json">{}</script></body></html>'
INDEX_TEMPLATE = '<html><head><title>Sets</title><meta name="robots" content="noindex"></head><body><script id="sets-data" type="application/json">[]</script></body></html>'


class CatalogTests(unittest.TestCase):
    def snapshot(self, products, hour=10):
        return {"updated_at": f"2026-09-08T{hour:02}:00:00Z", "products": products}

    def product_group(self):
        return {
            "id": "pokemon-destined-rivals-etb-es-1234567890",
            "url": "https://wheresthatstock.com/producto/pokemon-destined-rivals-etb-es-1234567890",
            "name": "Pokémon Destined Rivals ETB Español",
            "game": "Pokémon",
            "offers": [
                {
                    "offer_id": "CAR-A1", "marketplace": "CAR", "store": "Carrefour",
                    "name": "ETB Destined Rivals", "price": "49,99 €", "status": "compra_directa",
                    "link": "https://example.test/carrefour", "image": "https://example.test/etb.jpg",
                    "product_url": "https://wheresthatstock.com/producto/CAR-A1", "checked_at": "2026-09-08",
                },
                {
                    "offer_id": "FNAC-B2", "marketplace": "FNAC", "store": "Fnac",
                    "name": "ETB Destined Rivals agotada", "price": "54,99 €", "status": "no_disponible",
                    "link": "https://example.test/fnac", "image": "https://example.test/etb-2.jpg",
                    "product_url": "https://wheresthatstock.com/producto/FNAC-B2", "checked_at": "2026-09-07",
                },
            ],
        }

    def test_group_page_renders_price_comparison_seo_and_structured_data(self):
        group = self.product_group()
        page = render_group_page(TEMPLATE, group)
        self.assertIn('<link rel="canonical" href="' + group["url"] + '">', page)
        self.assertIn("Compara 2 ofertas", page)
        self.assertIn("49,99 €", page)
        self.assertIn("/assets/carrefour-logo.webp", page)
        self.assertIn("/assets/fnac-logo.webp", page)
        self.assertIn("is-unavailable", page)
        self.assertIn('rel="noopener sponsored"', page)
        self.assertNotIn('<script src="/producto.js"></script>', page)
        product_schema = json.loads(page.split('<script type="application/ld+json">', 1)[1].split('</script>', 1)[0])
        self.assertEqual(product_schema["@type"], "Product")
        self.assertEqual(product_schema["offers"]["@type"], "AggregateOffer")
        self.assertEqual(product_schema["offers"]["lowPrice"], "49.99")
        self.assertEqual(product_schema["offers"]["offerCount"], 2)

    def test_store_offer_page_links_and_canonicalize_to_group(self):
        group = self.product_group()
        product = {**PRODUCT, "asin": "A1", "marketplace": "CAR", "store_label": "Carrefour"}
        page = render_page(TEMPLATE, product, group=group)
        self.assertIn(f'<link rel="canonical" href="{group["url"]}">', page)
        self.assertIn("Comparar todos los precios", page)
        self.assertIn('/producto/FNAC-B2', page)
        self.assertIn("esta oferta", page)

    def test_store_offer_page_shows_only_one_chip_per_store(self):
        group = self.product_group()
        group["offers"].append({
            "offer_id": "CAR-C3", "marketplace": "CAR", "store": "Carrefour",
            "name": "Otra variante", "price": "39,99 €", "status": "compra_directa",
            "link": "https://example.test/carrefour-variant",
            "product_url": "https://wheresthatstock.com/producto/CAR-C3",
        })
        product = {**PRODUCT, "asin": "A1", "marketplace": "CAR", "store_label": "Carrefour"}
        page = render_page(TEMPLATE, product, group=group)
        self.assertIn("Disponible en 2 tiendas", page)
        self.assertNotIn('/producto/CAR-C3', page)
        self.assertIn('/producto/FNAC-B2', page)

    def test_disappearance_preserves_details_without_claiming_sold_out(self):
        first, events = update_catalog({}, self.snapshot([PRODUCT]), "magic.json", [])
        self.assertEqual(events, [])
        missing, events = update_catalog(first, self.snapshot([], 11), "magic.json", events)
        self.assertEqual(missing["products"][0]["name"], PRODUCT["name"])
        self.assertEqual(missing["products"][0]["status"], "sin_confirmar")
        self.assertEqual(missing["products"][0]["last_seen"], first["updated_at"])
        back, events = update_catalog(missing, self.snapshot([PRODUCT], 12), "magic.json", events)
        self.assertEqual(events[0]["type"], "restock")
        self.assertEqual(events[0]["game"], "Magic")
        self.assertEqual(update_catalog(back, self.snapshot([PRODUCT], 12), "magic.json", events), (back, events))

    def test_price_drop_and_feed_limit(self):
        first, _ = update_catalog({}, self.snapshot([PRODUCT]), "magic.json", [])
        _, events = update_catalog(first, self.snapshot([{**PRODUCT, "price": "8,00 €"}], 11), "magic.json", [])
        self.assertEqual(events[0]["type"], "price_drop")
        self.assertEqual(events[0]["prev_price"], "10,00 €")
        history = [{"id": str(i), "ts": "2026-09-01T00:00:00Z"} for i in range(300)]
        _, events = update_catalog(first, self.snapshot([PRODUCT], 11), "magic.json", history)
        self.assertEqual(len(events), 200)

    def test_static_html_escapes_external_data_and_is_indexable(self):
        malicious = {**PRODUCT, "name": '</script><script>alert("x")</script>', "last_seen": "2026-09-08"}
        page = render_page(TEMPLATE, malicious)
        self.assertNotIn('<script>alert', page)
        self.assertIn('\\u003c/script', page)
        self.assertIn('rel="canonical"', page)
        self.assertNotIn('content="noindex"', page)
        for value in ('../escape', 'a/b', '', 'a"onclick=x'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                product_id({**PRODUCT, "asin": value})

    def test_product_schema_maps_status_to_availability_and_parses_price(self):
        page = render_page(TEMPLATE, PRODUCT)
        ld = json.loads(page.split('<script type="application/ld+json">', 1)[1].split('</script>', 1)[0])
        self.assertEqual(ld["@type"], "Product")
        self.assertEqual(ld["sku"], "B000000001")
        self.assertEqual(ld["offers"]["availability"], "https://schema.org/InStock")
        self.assertEqual(ld["offers"]["price"], "10.00")
        self.assertEqual(ld["offers"]["priceCurrency"], "EUR")
        for status, availability in (("preventa", "PreOrder"), ("invitacion", "LimitedAvailability"),
                                      ("no_disponible", "OutOfStock"), ("sin_confirmar", "OutOfStock")):
            page = render_page(TEMPLATE, {**PRODUCT, "status": status})
            self.assertIn(f"https://schema.org/{availability}", page)

    def test_product_schema_omits_price_when_unparseable_and_escapes_name(self):
        page = render_page(TEMPLATE, {**PRODUCT, "price": None, "name": '</script><script>alert("x")</script>'})
        ld_json = page.split('<script type="application/ld+json">', 1)[1].split('</script>', 1)[0]
        self.assertNotIn('<script>alert', ld_json)
        ld = json.loads(ld_json.replace('\\u003c', '<').replace('\\u003e', '>').replace('\\u0026', '&'))
        self.assertNotIn("price", ld["offers"])

    def test_build_keeps_page_but_prioritizes_only_active_products_in_sitemap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "producto.html").write_text(TEMPLATE, encoding="utf-8")
            (root / "sitemap.xml").write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>', encoding="utf-8")
            path = root / "magic.json"
            path.write_text(json.dumps(self.snapshot([PRODUCT])), encoding="utf-8")
            build_site(root, ["magic.json"])
            path.write_text(json.dumps(self.snapshot([], 11)), encoding="utf-8")
            build_site(root, ["magic.json"])
            page = root / "producto" / "ES-B000000001.html"
            self.assertIn("Disponibilidad sin confirmar", page.read_text(encoding="utf-8"))
            self.assertNotIn("/producto/ES-B000000001", (root / "sitemap-products.xml").read_text())
            index = (root / "sitemap.xml").read_text(encoding="utf-8")
            self.assertIn("sitemap-core.xml", index)
            self.assertIn("sitemap-products.xml", index)

    def test_gaming_quality_gate_removes_invalid_current_and_archived_rows(self):
        valid = {**PRODUCT, "name": "Stardew Valley", "game": "Nintendo"}
        invalid = {**PRODUCT, "asin": "0571226167", "name": "The Bell Jar: Sylvia Plath", "game": "PlayStation"}
        cleaned, removed = clean_gaming_feed(self.snapshot([valid, invalid]), "playstation.json")
        self.assertEqual([product["asin"] for product in cleaned["products"]], [valid["asin"]])
        self.assertEqual([product["asin"] for product in removed], [invalid["asin"]])
        untouched, removed = clean_gaming_feed(self.snapshot([invalid]), "magic.json")
        self.assertEqual(untouched["products"], [invalid])
        self.assertEqual(removed, [])

    def test_build_prunes_rejected_gaming_page_and_sitemap_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = {**PRODUCT, "asin": "0571226167", "name": "The Bell Jar: Sylvia Plath", "game": "PlayStation"}
            (root / "producto.html").write_text(TEMPLATE, encoding="utf-8")
            (root / "producto").mkdir()
            stale_page = root / "producto" / "ES-0571226167.html"
            stale_page.write_text("stale", encoding="utf-8")
            (root / "sitemap.xml").write_text(
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://wheresthatstock.com/producto/ES-0571226167</loc></url>'
                '</urlset>', encoding="utf-8")
            (root / "playstation.json").write_text(json.dumps(self.snapshot([invalid])), encoding="utf-8")
            build_site(root, ["playstation.json"])
            self.assertFalse(stale_page.exists())
            self.assertNotIn("ES-0571226167", (root / "sitemap-products.xml").read_text(encoding="utf-8"))
            self.assertEqual(json.loads((root / "playstation.json").read_text(encoding="utf-8"))["products"], [])

    def test_sharing_metadata_is_static_and_does_not_publish_an_old_price(self):
        product = {**PRODUCT, "name": 'Booster "special" & friends', "image": "https://example.test/card.jpg", "store_label": "Amazon"}
        page = render_page(TEMPLATE, product)
        self.assertIn('property="og:image" content="https://example.test/card.jpg"', page)
        self.assertIn('property="og:title" content="Booster &quot;special&quot; &amp; friends"', page)
        self.assertIn('property="og:url" content="https://wheresthatstock.com/producto/ES-B000000001"', page)
        page = render_page(TEMPLATE, {**product, "image": "javascript:alert(1)"})
        self.assertIn('property="og:image" content="https://wheresthatstock.com/assets/brand/social.png"', page)

    def test_product_page_breadcrumb_links_to_its_source_category(self):
        page = render_page(TEMPLATE, {**PRODUCT, "_src": "onepiece.json"})
        self.assertIn('"@type": "BreadcrumbList"', page)
        self.assertIn('"name": "One Piece TCG", "item": "https://wheresthatstock.com/onepiece"', page)
        self.assertIn('https://wheresthatstock.com/producto/ES-B000000001', page)
        # Un accesorio con game="Magic" no aparece listado en /magic (is_accessory
        # lo desvía antes en cada check_*.py) — las migas tienen que enlazar a
        # /accesorios, donde el producto sí es navegable de verdad.
        accessory = {**PRODUCT, "_src": "accesorios.json", "game": "Magic", "categories": ["Magic"]}
        page = render_page(TEMPLATE, accessory)
        self.assertIn('"name": "Accesorios", "item": "https://wheresthatstock.com/accesorios"', page)
        self.assertNotIn('"item": "https://wheresthatstock.com/magic"', page)

    def test_set_page_and_index_have_breadcrumbs(self):
        config = {"game": "Magic", "name": "Test Set", "match": "booster"}
        page, _ = render_set_page(SET_TEMPLATE, "test-set", config, [])
        self.assertIn('"name": "Expansiones", "item": "https://wheresthatstock.com/set/"', page)
        self.assertIn('"name": "Test Set", "item": "https://wheresthatstock.com/set/test-set"', page)
        index_page = render_set_index(INDEX_TEMPLATE, [])
        self.assertIn('"@type": "BreadcrumbList"', index_page)

    def test_set_page_matches_products_by_keyword_and_strips_noindex(self):
        config = {"game": "Magic", "name": "Test Set", "release_date": "2026-08-28",
                   "article": "noticia-test", "match": "booster", "blurb": "Blurb de prueba."}
        products = {"ES:B000000001": PRODUCT, "ES:B000000002": {**PRODUCT, "asin": "B000000002", "name": "Magic Commander Deck"}}
        matches, owner = match_sets({"test-set": config}, products)
        config, matched = matches["test-set"]
        self.assertEqual([p["asin"] for p in matched], ["B000000001"])
        self.assertEqual(owner, {"ES:B000000001": "test-set"})
        page, _ = render_set_page(SET_TEMPLATE, "test-set", config, matched)
        self.assertIn("<title>Test Set — Where's That Stock</title>", page)
        self.assertIn('rel="canonical" href="https://wheresthatstock.com/set/test-set"', page)
        self.assertNotIn('content="noindex"', page)
        self.assertIn('"release_date_human": "28 de agosto de 2026"', page)
        self.assertIn('"article": "noticia-test"', page)
        self.assertIn("B000000001", page)
        self.assertNotIn("B000000002", page)

    def test_set_page_escapes_malicious_names_in_embedded_json(self):
        config = {"name": "Test Set", "match": "booster"}
        malicious = {**PRODUCT, "name": '</script><script>alert("x")</script> booster'}
        matched = match_sets({"test-set": config}, {"ES:B000000001": malicious})[0]["test-set"][1]
        self.assertEqual(len(matched), 1)
        page, _ = render_set_page(SET_TEMPLATE, "test-set", config, matched)
        self.assertNotIn('<script>alert', page)
        self.assertIn('\\u003c/script', page)

    def test_set_game_filter_excludes_same_keyword_from_another_game(self):
        # Un "game" que no coincide exactamente con el de los productos deja
        # la ficha vacía en silencio: el fallo real que tuvo op17 al añadir
        # el filtro ("One Piece TCG" en sets.json vs "One Piece" en los datos).
        products = {"ES:B1": {**PRODUCT, "asin": "B1", "name": "Hobbit Booster", "game": "Magic"},
                    "ES:B2": {**PRODUCT, "asin": "B2", "name": "Hobbit Promo", "game": "Pokémon"}}
        matched = match_sets({"h": {"game": "Magic", "match": "hobbit"}}, products)[0]["h"][1]
        self.assertEqual([p["asin"] for p in matched], ["B1"])
        mismatched = match_sets({"h": {"game": "Magic: The Gathering", "match": "hobbit"}}, products)[0]["h"][1]
        self.assertEqual(mismatched, [])
        unfiltered = match_sets({"h": {"match": "hobbit"}}, products)[0]["h"][1]
        self.assertEqual(len(unfiltered), 2)

    def test_product_page_links_to_its_set_outside_the_hydrated_container(self):
        entry = ("test-set", {"game": "Magic", "name": "Test Set"})
        page = render_page(TEMPLATE.replace('<div id="product-detail">', '<!--SET-LINK-->\n<div id="product-detail">'), PRODUCT, entry)
        self.assertIn('href="/set/test-set"', page)
        self.assertIn("--game-color:#5B3FA6", page)
        # producto.js reescribe #product-detail entero al hidratar: si el
        # enlace cayera dentro, desaparecería en cuanto cargue el JS.
        detail = page.split('<div id="product-detail">')[1]
        self.assertNotIn('href="/set/test-set"', detail)
        without = render_page(TEMPLATE.replace('<div id="product-detail">', '<!--SET-LINK-->\n<div id="product-detail">'), PRODUCT)
        self.assertNotIn("/set/", without)
        self.assertNotIn("<!--SET-LINK-->", without)

    def test_set_index_counts_only_products_that_can_be_bought(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "producto.html").write_text(TEMPLATE, encoding="utf-8")
            (root / "set.html").write_text(SET_TEMPLATE, encoding="utf-8")
            (root / "set-index.html").write_text(INDEX_TEMPLATE, encoding="utf-8")
            (root / "sitemap.xml").write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>', encoding="utf-8")
            (root / "sets.json").write_text(json.dumps({"test-set": {
                "game": "Magic", "name": "Test Set", "match": "booster", "release_date": "2026-01-01",
            }}), encoding="utf-8")
            (root / "magic.json").write_text(json.dumps(self.snapshot([
                PRODUCT, {**PRODUCT, "asin": "B000000002", "status": "no_disponible"},
            ])), encoding="utf-8")
            changed = build_site(root, ["magic.json"])
            self.assertIn("set/index.html", changed)
            index = (root / "set" / "index.html").read_text(encoding="utf-8")
            self.assertNotIn('content="noindex"', index)
            self.assertIn('"available": 1', index)
            self.assertIn('"release_date_human": "1 de enero de 2026"', index)
            self.assertIn("https://wheresthatstock.com/set/", (root / "sitemap-core.xml").read_text(encoding="utf-8"))

    def test_build_generates_set_pages_and_sitemap_entries_when_sets_json_present(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "producto.html").write_text(TEMPLATE, encoding="utf-8")
            (root / "set.html").write_text(SET_TEMPLATE, encoding="utf-8")
            (root / "sitemap.xml").write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>', encoding="utf-8")
            (root / "sets.json").write_text(json.dumps({"test-set": {
                "name": "Test Set", "match": "booster", "release_date": "2026-01-01",
            }}), encoding="utf-8")
            (root / "magic.json").write_text(json.dumps(self.snapshot([PRODUCT])), encoding="utf-8")
            changed = build_site(root, ["magic.json"])
            self.assertIn("set/test-set.html", changed)
            page = (root / "set" / "test-set.html").read_text(encoding="utf-8")
            self.assertIn("B000000001", page)
            sitemap = (root / "sitemap-core.xml").read_text(encoding="utf-8")
            self.assertIn("https://wheresthatstock.com/set/test-set", sitemap)

    def test_future_release_date_is_never_used_as_sitemap_lastmod(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "producto.html").write_text(TEMPLATE, encoding="utf-8")
            (root / "set.html").write_text(SET_TEMPLATE, encoding="utf-8")
            (root / "set-index.html").write_text(INDEX_TEMPLATE, encoding="utf-8")
            (root / "sitemap.xml").write_text(
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://wheresthatstock.com/</loc><lastmod>2099-12-31</lastmod></url>'
                '</urlset>', encoding="utf-8")
            (root / "sets.json").write_text(json.dumps({"future-set": {
                "name": "Future Set", "match": "does-not-match", "release_date": "2099-12-31",
            }}), encoding="utf-8")
            (root / "magic.json").write_text(json.dumps(self.snapshot([PRODUCT])), encoding="utf-8")
            build_site(root, ["magic.json"])
            core = (root / "sitemap-core.xml").read_text(encoding="utf-8")
            self.assertIn("https://wheresthatstock.com/set/future-set", core)
            self.assertNotIn("2099-12-31", core)

    def test_product_page_contains_static_context_related_products_and_history(self):
        related = {**PRODUCT, "asin": "B000000002", "name": "Magic Booster especial", "image": "https://example.test/related.jpg"}
        event = {**PRODUCT, "type": "price_drop", "prev_price": "12,00 €", "price": "10,00 €", "ts": "2026-09-08T10:00:00Z"}
        page = render_page(TEMPLATE, {**PRODUCT, "_src": "magic.json", "checked_at": "2026-09-08"}, related=[related], history=[event])
        self.assertIn("Categoría:", page)
        self.assertIn('href="/magic"', page)
        self.assertIn("Productos relacionados", page)
        self.assertIn("/producto/ES-B000000002", page)
        self.assertIn("Historial reciente", page)
        self.assertIn("Bajó de 12,00 € a 10,00 €", page)

    def test_build_deduplicates_history_shared_by_legacy_activity_feeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "producto.html").write_text(TEMPLATE, encoding="utf-8")
            (root / "sitemap.xml").write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>', encoding="utf-8")
            (root / "magic.json").write_text(json.dumps(self.snapshot([PRODUCT])), encoding="utf-8")
            event = {**PRODUCT, "id": "first-id", "type": "restock", "ts": "2026-09-08T09:00:00Z"}
            duplicate = {**event, "id": "second-id", "ts": "2026-09-08T11:00:00Z"}
            (root / "activity-magic.json").write_text(json.dumps([event]), encoding="utf-8")
            (root / "activity-products.json").write_text(json.dumps([duplicate]), encoding="utf-8")
            build_site(root, ["magic.json"])
            page = (root / "producto" / "ES-B000000001.html").read_text(encoding="utf-8")
            self.assertEqual(page.count("Volvió a estar disponible"), 1)

    def test_build_without_sets_json_does_not_create_set_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "producto.html").write_text(TEMPLATE, encoding="utf-8")
            (root / "sitemap.xml").write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>', encoding="utf-8")
            (root / "magic.json").write_text(json.dumps(self.snapshot([PRODUCT])), encoding="utf-8")
            build_site(root, ["magic.json"])
            self.assertFalse((root / "set").exists())

    def test_build_generates_groups_review_queue_and_persistent_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "producto.html").write_text(TEMPLATE, encoding="utf-8")
            (root / "sitemap.xml").write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>', encoding="utf-8")
            first_store = {**PRODUCT, "name": "Pokémon Destined Rivals Elite Trainer Box", "game": "Pokémon"}
            other_store = {**first_store, "asin": "B000000002", "marketplace": "UK"}
            (root / "products.json").write_text(json.dumps(self.snapshot([first_store, other_store])), encoding="utf-8")
            changed = build_site(root, ["products.json"])
            self.assertIn("product-groups.json", changed)
            self.assertIn("grouping-review.json", changed)
            self.assertIn("product-group-overrides.json", changed)
            groups = json.loads((root / "product-groups.json").read_text(encoding="utf-8"))
            self.assertEqual(groups["summary"]["groups"], 1)
            self.assertEqual(groups["summary"]["grouped_offers"], 2)
            group = groups["groups"][0]
            self.assertTrue((root / "producto" / f"{group['id']}.html").exists())
            group_page = (root / "producto" / f"{group['id']}.html").read_text(encoding="utf-8")
            self.assertIn("Compara 2 ofertas", group_page)
            sitemap = (root / "sitemap-products.xml").read_text(encoding="utf-8")
            self.assertIn(group["url"], sitemap)
            self.assertNotIn("/producto/ES-B000000001", sitemap)
            self.assertNotIn("/producto/UK-B000000002", sitemap)
            overrides = json.loads((root / "product-group-overrides.json").read_text(encoding="utf-8"))
            self.assertEqual(overrides["assign"], {})

    def test_set_slug_is_restricted_to_safe_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "producto.html").write_text(TEMPLATE, encoding="utf-8")
            (root / "set.html").write_text(SET_TEMPLATE, encoding="utf-8")
            (root / "sitemap.xml").write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>', encoding="utf-8")
            (root / "sets.json").write_text(json.dumps({"../escape": {"match": "booster"}}), encoding="utf-8")
            (root / "magic.json").write_text(json.dumps(self.snapshot([PRODUCT])), encoding="utf-8")
            with self.assertRaises(ValueError):
                build_site(root, ["magic.json"])

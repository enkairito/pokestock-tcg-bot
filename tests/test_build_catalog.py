import json
import tempfile
import unittest
from pathlib import Path
from build_catalog import build_site, match_sets, render_page, render_set_page, update_catalog, product_id

PRODUCT = {"asin": "B000000001", "marketplace": "ES", "name": "Magic Booster", "status": "compra_directa", "price": "10,00 €", "game": "Magic"}
TEMPLATE = '<html><head><title>Producto</title><meta name="description" content=""><meta name="robots" content="noindex"></head><body><div id="product-detail">Cargando</div><script src="/producto.js"></script></body></html>'
SET_TEMPLATE = '<html><head><title>Set</title><meta name="description" content=""><meta name="robots" content="noindex"></head><body><script id="set-data" type="application/json">{}</script></body></html>'
INDEX_TEMPLATE = '<html><head><title>Sets</title><meta name="robots" content="noindex"></head><body><script id="sets-data" type="application/json">[]</script></body></html>'


class CatalogTests(unittest.TestCase):
    def snapshot(self, products, hour=10):
        return {"updated_at": f"2026-09-08T{hour:02}:00:00Z", "products": products}

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

    def test_build_keeps_page_and_sitemap_after_product_disappears(self):
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
            self.assertIn("/producto/ES-B000000001", (root / "sitemap.xml").read_text())

    def test_sharing_metadata_is_static_and_does_not_publish_an_old_price(self):
        product = {**PRODUCT, "name": 'Booster "special" & friends', "image": "https://example.test/card.jpg", "store_label": "Amazon"}
        page = render_page(TEMPLATE, product)
        self.assertIn('property="og:image" content="https://example.test/card.jpg"', page)
        self.assertIn('property="og:title" content="Booster &quot;special&quot; &amp; friends"', page)
        self.assertIn('property="og:url" content="https://wheresthatstock.com/producto/ES-B000000001"', page)
        page = render_page(TEMPLATE, {**product, "image": "javascript:alert(1)"})
        self.assertIn('property="og:image" content="https://wheresthatstock.com/assets/brand/social.png"', page)

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
            self.assertIn("https://wheresthatstock.com/set/", (root / "sitemap.xml").read_text(encoding="utf-8"))

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
            sitemap = (root / "sitemap.xml").read_text(encoding="utf-8")
            self.assertIn("https://wheresthatstock.com/set/test-set", sitemap)

    def test_build_without_sets_json_does_not_create_set_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "producto.html").write_text(TEMPLATE, encoding="utf-8")
            (root / "sitemap.xml").write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>', encoding="utf-8")
            (root / "magic.json").write_text(json.dumps(self.snapshot([PRODUCT])), encoding="utf-8")
            build_site(root, ["magic.json"])
            self.assertFalse((root / "set").exists())

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

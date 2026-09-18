import unittest

from product_grouping import build_groups, identity_signature, similarity, validate_overrides


def product(identifier, marketplace, name, **extra):
    return {
        "asin": identifier,
        "marketplace": marketplace,
        "store_label": marketplace,
        "name": name,
        "game": "Pokémon",
        "status": "compra_directa",
        "price": "59,99 €",
        "checked_at": "2026-09-18T10:00:00Z",
        "categories": ["Cajas ETB"],
        "_src": "products.json",
        **extra,
    }


class ProductGroupingTests(unittest.TestCase):
    def test_exact_gtin_groups_different_retailer_titles(self):
        offers = [
            product("A1", "CAR", "Caja Pokémon Rivales Predestinados", ean="1234567890123"),
            product("B1", "FNAC", "Pokémon ETB Destined Rivals", gtin="1234567890123"),
        ]
        groups, review = build_groups(offers)
        self.assertEqual(groups["summary"]["groups"], 1)
        self.assertEqual(groups["summary"]["grouped_offers"], 2)
        self.assertEqual(review["items"], [])
        self.assertTrue(groups["groups"][0]["url"].startswith("https://wheresthatstock.com/comparar/"))

    def test_equivalent_translated_title_signature_is_conservative_but_automatic(self):
        left = product("A1", "CAR", "Pokémon Caja de Entrenador Elite Destined Rivals Español")
        right = product("B1", "FNAC", "Destined Rivals Pokémon Elite Trainer Box Spanish")
        self.assertEqual(identity_signature(left), identity_signature(right))
        groups, _ = build_groups([left, right])
        self.assertEqual(groups["summary"]["groups"], 1)

    def test_language_and_pack_quantity_prevent_false_merges(self):
        spanish = product("A1", "CAR", "Pokémon Destined Rivals ETB Español")
        english = product("B1", "FNAC", "Pokémon Destined Rivals ETB English")
        two_packs = product("C1", "TRU", "Pokémon Destined Rivals pack 2 sobres")
        three_packs = product("D1", "ECI", "Pokémon Destined Rivals pack 3 sobres")
        self.assertEqual(similarity(spanish, english), 0)
        self.assertEqual(similarity(two_packs, three_packs), 0)
        groups, review = build_groups([spanish, english, two_packs, three_packs])
        self.assertEqual(groups["groups"], [])
        self.assertEqual(len(review["items"]), 4)

    def test_probable_match_is_saved_for_review_without_being_merged(self):
        left = product("A1", "CAR", "Pokémon colección premium Mega Zygarde ex Español")
        right = product("B1", "FNAC", "Caja Pokémon Zygarde premium Español")
        groups, review = build_groups([left, right])
        self.assertEqual(groups["groups"], [])
        self.assertTrue(any(item["suggestions"] for item in review["items"]))

    def test_manual_assignment_wins_and_can_join_unrelated_titles(self):
        offers = [
            product("A1", "CAR", "Nombre abreviado de Carrefour"),
            product("B1", "FNAC", "Nombre comercial completamente distinto"),
        ]
        overrides = {
            "version": 1,
            "groups": {"destined-rivals-etb-es": {"name": "ETB Destined Rivals", "game": "Pokémon"}},
            "assign": {"CAR-A1": "destined-rivals-etb-es", "FNAC-B1": "destined-rivals-etb-es"},
            "separate": [],
            "ignore": [],
        }
        groups, review = build_groups(offers, overrides)
        self.assertEqual(groups["groups"][0]["method"], "manual")
        self.assertEqual(groups["groups"][0]["name"], "ETB Destined Rivals")
        self.assertEqual({offer["offer_id"] for offer in groups["groups"][0]["offers"]}, {"CAR-A1", "FNAC-B1"})
        self.assertEqual(review["items"], [])

    def test_separate_and_ignore_remove_review_noise(self):
        offers = [product("A1", "CAR", "Producto único"), product("B1", "FNAC", "Falso positivo")]
        overrides = {"separate": ["CAR-A1"], "ignore": ["FNAC-B1"]}
        groups, review = build_groups(offers, overrides)
        self.assertEqual(groups["summary"]["reviewed_separate"], 1)
        self.assertEqual(groups["summary"]["ignored"], 1)
        self.assertEqual(review["items"], [])

    def test_conflicting_manual_decisions_fail_loudly(self):
        with self.assertRaises(ValueError):
            validate_overrides({
                "groups": {}, "assign": {"CAR-A1": "valid-group"},
                "separate": ["CAR-A1"], "ignore": [],
            })

    def test_malformed_manual_file_fails_with_a_clear_validation_error(self):
        with self.assertRaisesRegex(ValueError, "IDs de grupo inválidos"):
            validate_overrides({"groups": {}, "assign": {"CAR-A1": 123}})
        with self.assertRaisesRegex(ValueError, "separate e ignore deben ser listas"):
            validate_overrides({"separate": "CAR-A1"})
        with self.assertRaisesRegex(ValueError, "Cada entrada de groups"):
            validate_overrides({"groups": {"valid-group": "nombre"}})


if __name__ == "__main__":
    unittest.main()

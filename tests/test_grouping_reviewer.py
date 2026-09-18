import json
import tempfile
import unittest
from pathlib import Path

from grouping_reviewer import ReviewError, ReviewStore


def product(identifier, marketplace, name):
    return {
        "asin": identifier,
        "marketplace": marketplace,
        "store_label": marketplace,
        "name": name,
        "game": "Pokémon",
        "status": "compra_directa",
        "price": "49,99 €",
        "image": f"https://example.com/{identifier}.jpg",
        "link": f"https://example.com/buy/{identifier}",
        "categories": ["Colecciones"],
        "checked_at": "2026-09-18T10:00:00Z",
    }


class GroupingReviewerTests(unittest.TestCase):
    def make_store(self, root):
        products = [
            product("A1", "CAR", "Pokémon colección premium Mega Zygarde ex Español"),
            product("B1", "FNAC", "Caja Pokémon Zygarde premium Español"),
        ]
        (root / "catalog-products.json").write_text(
            json.dumps({"updated_at": "2026-09-18T10:00:00Z", "products": products}),
            encoding="utf-8",
        )
        return ReviewStore(root)

    def test_same_product_persists_both_offers_and_can_be_undone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.make_store(root)
            self.assertEqual(len(store.state()["items"]), 2)

            state = store.apply("same", "CAR-A1", "FNAC-B1")
            self.assertEqual(state["summary"]["groups"], 1)
            self.assertEqual(state["summary"]["grouped_offers"], 2)
            saved = json.loads((root / "product-group-overrides.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["assign"]["CAR-A1"], saved["assign"]["FNAC-B1"])

            restored = store.apply("undo")
            self.assertEqual(restored["summary"]["groups"], 0)
            self.assertEqual(len(restored["items"]), 2)

    def test_reject_pair_removes_only_that_suggestion_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.make_store(root)
            state = store.apply("reject", "CAR-A1", "FNAC-B1")
            self.assertEqual(state["summary"]["rejected_pairs"], 1)
            self.assertEqual(state["items"], [])
            saved = json.loads((root / "product-group-overrides.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["reject_pairs"], [["CAR-A1", "FNAC-B1"]])

    def test_adding_offer_to_automatic_group_keeps_every_existing_member(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared_name = "Pokémon colección premium Mega Zygarde ex Español"
            products = [
                product("A1", "CAR", shared_name),
                product("B1", "FNAC", shared_name),
                product("C1", "TRU", "Caja Pokémon Zygarde premium Español"),
            ]
            (root / "catalog-products.json").write_text(
                json.dumps({"updated_at": "2026-09-18T10:00:00Z", "products": products}),
                encoding="utf-8",
            )
            store = ReviewStore(root)
            state = store.apply("same", "TRU-C1", "CAR-A1")
            self.assertEqual(state["summary"]["grouped_offers"], 3)
            assignments = store.overrides["assign"]
            self.assertEqual({assignments[key] for key in ("CAR-A1", "FNAC-B1", "TRU-C1")}, {assignments["CAR-A1"]})

    def test_unique_and_ignore_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.make_store(root)
            store.apply("separate", "CAR-A1")
            saved = json.loads((root / "product-group-overrides.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["separate"], ["CAR-A1"])
            self.assertEqual(saved["ignore"], [])

    def test_stale_or_unknown_decisions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(Path(directory))
            with self.assertRaises(ReviewError):
                store.apply("same", "CAR-A1", "MISSING-X")
            with self.assertRaises(ReviewError):
                store.apply("made-up", "CAR-A1")


if __name__ == "__main__":
    unittest.main()

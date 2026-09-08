import unittest

from merge_accesorios import merge_snapshots


def product(asin="B000000001", categories=None, **extra):
    return {"marketplace": "ES", "asin": asin, "categories": categories or [], **extra}


def snapshot(*products):
    return {"updated_at": "2026-09-08T08:00:00+00:00", "products": list(products)}


class MergeAccessoriesTests(unittest.TestCase):
    def test_new_onepiece_run_preserves_daily_accessory_timestamp(self):
        old = {**snapshot(product()), "source_updates": {"accessories": "2026-09-06T08:00:00Z"}}
        merged = merge_snapshots(old, snapshot(), "onepiece")
        self.assertEqual(merged["source_updates"]["accessories"], "2026-09-06T08:00:00Z")
        self.assertEqual(merged["source_updates"]["onepiece"], snapshot()["updated_at"])

    def test_republishing_categorized_accessories_is_idempotent(self):
        new = snapshot(product(categories=["Sin asociar"]), product("B000000002", ["Pokémon"]))
        first = merge_snapshots(snapshot(), new, "accessories")
        self.assertEqual(merge_snapshots(first, new, "accessories"), first)

    def test_migrates_duplicates_and_replaces_old_prices(self):
        old = product(categories=["Sin asociar"], price="10,00 €")
        new = product(categories=["Sin asociar"], price="8,00 €")
        merged = merge_snapshots(snapshot(*([old] * 10)), snapshot(new), "accessories")
        self.assertEqual(merged["products"], [{**new, "source": "accessories"}])

    def test_removes_disappeared_products_but_preserves_other_source(self):
        onepiece = product("B000000002", ["One Piece"])
        merged = merge_snapshots(snapshot(product(), onepiece), snapshot(), "accessories")
        self.assertEqual(merged["products"], [{**onepiece, "source": "onepiece"}])

    def test_explicit_source_takes_precedence_over_game_category(self):
        generic = product(categories=["One Piece"], source="accessories")
        merged = merge_snapshots(snapshot(generic), snapshot(), "onepiece")
        self.assertEqual(merged["products"], [generic])

    def test_cleans_duplicates_in_the_other_source_too(self):
        older = product(categories=["Sin asociar"], price="10,00 €")
        newer = {**older, "price": "8,00 €"}
        merged = merge_snapshots(snapshot(older, newer), snapshot(), "onepiece")
        self.assertEqual(merged["products"], [{**newer, "source": "accessories"}])

    def test_same_asin_in_different_marketplaces_is_preserved(self):
        new = snapshot(product(), product(marketplace="UK"))
        merged = merge_snapshots(snapshot(), new, "accessories")
        self.assertEqual(len(merged["products"]), 2)

    def test_overlapping_sources_publish_the_new_observation_once(self):
        old = product(categories=["One Piece"], source="onepiece", price="10,00 €")
        new = product(categories=["One Piece"], price="8,00 €")
        merged = merge_snapshots(snapshot(old), snapshot(new, new), "accessories")
        self.assertEqual(merged["products"], [{**new, "source": "accessories"}])

    def test_missing_product_list_is_an_error_not_an_empty_snapshot(self):
        with self.assertRaises(KeyError):
            merge_snapshots(snapshot(product()), {}, "accessories")


if __name__ == "__main__":
    unittest.main()

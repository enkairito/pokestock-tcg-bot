import unittest
from stock_logic import alert_changes


class AlertTests(unittest.TestCase):
    def test_restock_price_drop_and_stock_count_are_independent(self):
        before = {"status": "no_disponible", "price": "1.234,56 €", "stock": "5"}
        after = {"status": "compra_directa", "price": "999,00 €", "stock": "2"}
        self.assertEqual(alert_changes(after, before), (True, True, True))
        self.assertEqual(alert_changes(after, after), (False, False, False))

    def test_invitation_and_preorder_do_not_emit_price_drop(self):
        for status in ("invitacion", "preventa", "no_disponible"):
            with self.subTest(status=status):
                self.assertFalse(alert_changes({"status": status, "price": "1,00 €"}, {"status": status, "price": "2,00 €"})[2])

    def test_unknown_prices_or_counts_do_not_create_changes(self):
        self.assertEqual(alert_changes({"status": "compra_directa", "price": "sin precio"}, {"status": "compra_directa", "price": "10,00 €", "stock": 1}), (False, False, False))

import unittest
from stock_logic import alert_changes, confirmed_price_fields


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

    def test_small_price_drops_do_not_emit_alerts(self):
        before = {"status": "compra_directa", "price": "100,00 €"}
        self.assertFalse(alert_changes({"status": "compra_directa", "price": "99,50 €"}, before)[2])
        self.assertFalse(alert_changes({"status": "compra_directa", "price": "99,01 €"}, before)[2])

    def test_price_drop_alert_uses_percentage_or_absolute_threshold(self):
        before = {"status": "compra_directa", "price": "100,00 €"}
        self.assertTrue(alert_changes({"status": "compra_directa", "price": "98,99 €"}, before)[2])
        self.assertTrue(alert_changes({"status": "compra_directa", "price": "98,00 €"}, before)[2])

        # Una diferencia de exactamente 2 € cuenta como bajada relevante.
        before_high = {"status": "compra_directa", "price": "200,00 €"}
        self.assertTrue(alert_changes({"status": "compra_directa", "price": "198,00 €"}, before_high)[2])

    def test_one_run_price_spike_does_not_create_a_false_drop(self):
        state = {"status": "compra_directa", "price": "108,23 €", "stock": "4"}

        spike = {"status": "compra_directa", "price": "110,01 €", "stock": "4"}
        self.assertFalse(alert_changes(spike, state)[2])
        spike_fields = confirmed_price_fields(spike["price"], state)
        state = {"status": state["status"], "stock": state["stock"], **spike_fields}
        self.assertEqual(state["price"], "108,23 €")
        self.assertEqual(state["pending_price"], "110,01 €")

        recovery = {"status": "compra_directa", "price": "108,23 €", "stock": "4"}
        self.assertFalse(alert_changes(recovery, state)[2])
        recovery_fields = confirmed_price_fields(recovery["price"], state)
        state = {"status": state["status"], "stock": state["stock"], **recovery_fields}
        self.assertEqual(state["price"], "108,23 €")
        self.assertNotIn("pending_price", state)

    def test_persistent_price_increase_becomes_the_new_reference(self):
        state = {"price": "108,23 €"}

        first = confirmed_price_fields("110,01 €", state)
        self.assertEqual(first["price"], "108,23 €")
        second = confirmed_price_fields("110,01 €", {**state, **first})
        self.assertEqual(second, {"price": "110,01 €"})

        current = {"status": "compra_directa", "price": "108,23 €"}
        previous = {"status": "compra_directa", **second}
        self.assertTrue(alert_changes(current, previous)[2])

    def test_missing_price_keeps_the_confirmed_reference(self):
        self.assertEqual(
            confirmed_price_fields(None, {"price": "108,23 €"}),
            {"price": "108,23 €"},
        )

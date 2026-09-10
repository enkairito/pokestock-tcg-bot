import importlib
import os
import unittest
from unittest.mock import patch

# Importar los scripts no debe requerir secretos reales en las pruebas.
ENV = {"DRY_RUN": "1"}
for game in ("", "ONEPIECE_", "MAGIC_", "LORCANA_", "YUGIOH_"):
    ENV[f"TELEGRAM_{game}BOT_TOKEN"] = "test-token"
    ENV[f"TELEGRAM_{game}CHAT_ID"] = "test-chat"
with patch.dict(os.environ, ENV):
    magic = importlib.import_module("check_magic")
    lorcana = importlib.import_module("check_lorcana")
    yugioh = importlib.import_module("check_yugioh")
    onepiece = importlib.import_module("check_onepiece")


class AccessoryClassificationTests(unittest.TestCase):
    MODULES = {
        "magic": (magic, "Funda Ultra Pro compatible con Magic: The Gathering (100 uds)", "Magic: The Gathering Play Booster"),
        "lorcana": (lorcana, "Estuche para cartas Lorcana TCG", "Lorcana TCG Booster Pack"),
        "yugioh": (yugioh, "Funda Yu-Gi-Oh! Card Sleeves (50 uds)", "Yu-Gi-Oh! Display (24)"),
        "onepiece": (onepiece, "Funda One Piece TCG (Sleeve, 60 uds)", "One Piece TCG Booster Pack"),
    }

    def test_accessory_keywords_are_detected_regardless_of_accents_or_case(self):
        for name, (module, accessory_name, _) in self.MODULES.items():
            with self.subTest(game=name):
                self.assertTrue(module.is_accessory(accessory_name))
                self.assertTrue(module.is_accessory(accessory_name.upper()))

    def test_real_tcg_products_are_not_misclassified_as_accessories(self):
        for name, (module, _, product_name) in self.MODULES.items():
            with self.subTest(game=name):
                self.assertFalse(module.is_accessory(product_name))

    def test_every_game_writes_its_own_accessories_snapshot_path(self):
        for name, (module, *_rest) in self.MODULES.items():
            with self.subTest(game=name):
                self.assertEqual(module.ACCESORIOS_SNAPSHOT_FILE.name, f"accesorios_{name}_snapshot.json")


if __name__ == "__main__":
    unittest.main()

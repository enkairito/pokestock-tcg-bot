import copy
import importlib
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


# Importar los scripts no debe requerir secretos reales en las pruebas.
ENV = {"DRY_RUN": "1"}
for game in ("", "ONEPIECE_", "MAGIC_", "LORCANA_", "YUGIOH_"):
    ENV[f"TELEGRAM_{game}BOT_TOKEN"] = "test-token"
    ENV[f"TELEGRAM_{game}CHAT_ID"] = "test-chat"
with patch.dict(os.environ, ENV):
    MODULES = [importlib.import_module(name) for name in (
        "check_stock", "check_onepiece", "check_magic", "check_lorcana",
        "check_yugioh", "check_accessories",
    )]
stock = MODULES[0]


def observation(status="no_disponible"):
    return {"name": "Pokémon One Piece Magic Lorcana Yu-Gi-Oh Booster", "status": status,
            "price": "10,00 €", "original_price": None, "image": None, "stock": None}


@contextmanager
def isolated_run(module, fail_second_page=False, dry_run=False, observed_status="no_disponible"):
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        folder = Path(directory)
        state_path = folder / "state.json"
        snapshot_path = folder / "snapshot.json"
        state_path.write_text(json.dumps({"ES:B000000001": observation("compra_directa")}), encoding="utf-8")
        snapshot_path.write_text(json.dumps({"updated_at": "previous", "products": [observation()]}), encoding="utf-8")
        stack.enter_context(patch.object(module, "STATE_FILE", state_path))
        stack.enter_context(patch.object(module, "SNAPSHOT_FILE", snapshot_path))
        for name in ("EVENTS_FILE", "ACCESORIOS_SNAPSHOT_FILE"):
            if hasattr(module, name):
                stack.enter_context(patch.object(module, name, folder / name))
        browser = MagicMock()
        browser.close = AsyncMock()
        context = MagicMock()
        context.new_page = AsyncMock(return_value=object())
        playwright = MagicMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=playwright)
        manager.__aexit__ = AsyncMock(return_value=False)
        stack.enter_context(patch.object(module, "async_playwright", return_value=manager))
        stack.enter_context(patch.object(module, "new_context", AsyncMock(return_value=context)))
        stack.enter_context(patch("requests.post", side_effect=AssertionError("No network in tests")))
        stack.enter_context(patch("requests.get", side_effect=AssertionError("No network in tests")))
        stack.enter_context(patch("builtins.print"))
        if hasattr(module, "DRY_RUN"):
            stack.enter_context(patch.object(module, "DRY_RUN", dry_run))
        stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
        config_name = next((n for n in vars(module) if n.endswith("_MARKETPLACE")), None)
        if config_name:
            config = copy.deepcopy(getattr(module, config_name))
            config["pages"] = [("first", "https://example.test/1"), ("second", "https://example.test/2")]
            stack.enter_context(patch.object(module, config_name, config))
        if module is stock:
            config = copy.deepcopy(stock.MARKETPLACES[0])
            config["pages"] = [("first", "https://example.test/1"), ("second", "https://example.test/2")]
            config["extra_asins"] = {}
            stack.enter_context(patch.object(module, "MARKETPLACES", [config]))
            stack.enter_context(patch.object(module, "discover_eci_products", AsyncMock(return_value={"ECI001": observation()})))
        result = ({"B000000001": observation(observed_status)}, set())
        discovery = AsyncMock(side_effect=[copy.deepcopy(result), stock.ScrapeError("Second page failed")]) if fail_second_page else AsyncMock(side_effect=lambda *args: copy.deepcopy(result))
        stack.enter_context(patch.object(module, "discover_products", discovery))
        yield state_path, snapshot_path


class CompleteRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_preserves_existing_files(self):
        for module in MODULES:
            with self.subTest(script=module.__name__), isolated_run(module, dry_run=True) as (state, snapshot):
                for name in ("EVENTS_FILE", "ACCESORIOS_SNAPSHOT_FILE"):
                    if hasattr(module, name):
                        getattr(module, name).write_text("[]", encoding="utf-8")
                before = {path.name: path.read_bytes() for path in state.parent.iterdir()}
                await module.main()
                self.assertEqual({path.name: path.read_bytes() for path in state.parent.iterdir()}, before)

    async def test_dry_run_does_not_create_production_files(self):
        for module in MODULES:
            with self.subTest(script=module.__name__), isolated_run(module, dry_run=True) as (state, snapshot):
                state.unlink()
                snapshot.unlink()
                await module.main()
                self.assertEqual(list(state.parent.iterdir()), [])

    async def test_simulated_restock_remains_pending_for_real_run(self):
        for module in MODULES[:-1]:
            with self.subTest(script=module.__name__), isolated_run(
                module, dry_run=True, observed_status="compra_directa"
            ) as (state, snapshot), patch.object(module, "send_telegram_message") as send:
                state.write_text(json.dumps({"ES:B000000001": observation()}), encoding="utf-8")
                await module.main()
                send.assert_not_called()
                self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["ES:B000000001"]["status"], "no_disponible")
                with patch.object(module, "DRY_RUN", False):
                    await module.main()
                send.assert_called_once()
                self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["ES:B000000001"]["status"], "compra_directa")

    async def test_all_six_scripts_save_sold_out_state_and_empty_snapshot(self):
        for module in MODULES:
            with self.subTest(script=module.__name__), isolated_run(module) as (state, snapshot):
                await module.main()
                self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["ES:B000000001"]["status"], "no_disponible")
                self.assertEqual(json.loads(snapshot.read_text(encoding="utf-8"))["products"], [])
                self.assertNotEqual(json.loads(snapshot.read_text(encoding="utf-8"))["updated_at"], "previous")

    async def test_partial_failure_preserves_previous_state_and_snapshot(self):
        for module in MODULES:
            with self.subTest(script=module.__name__), isolated_run(module, fail_second_page=True) as (state, snapshot):
                previous_state, previous_snapshot = state.read_bytes(), snapshot.read_bytes()
                with self.assertRaises(stock.ScrapeError):
                    await module.main()
                self.assertEqual(state.read_bytes(), previous_state)
                self.assertEqual(snapshot.read_bytes(), previous_snapshot)


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def page(self, tiles=(), body="store", status=200):
        page = MagicMock()
        page.goto = AsyncMock(return_value=SimpleNamespace(status=status))
        page.wait_for_timeout = AsyncMock()
        page.mouse.wheel = AsyncMock()
        page.title = AsyncMock(return_value="Store")
        page.inner_text = AsyncMock(return_value=body)
        page.screenshot = AsyncMock()
        page.content = AsyncMock(return_value="<html>captcha</html>")
        page.eval_on_selector_all = AsyncMock(side_effect=lambda selector, script: list(tiles) if selector == "[data-asin]" else [])
        return page

    async def test_valid_sold_out_card_is_a_successful_observation(self):
        tile = {"asin": "B000000001", "name": "Magic Booster", "text": "No disponible", "hasAddToCart": False}
        with patch("builtins.print"):
            products, fallback = await stock.discover_products(self.page([tile]), "test", "https://example.test", stock.MARKETPLACES[0])
        self.assertEqual(products["B000000001"]["status"], "no_disponible")
        self.assertFalse(fallback)

    async def test_missing_cards_or_titles_are_not_evidence_of_sold_out_stock(self):
        for tiles in ([], [{"asin": "B000000001", "name": None}]):
            with self.subTest(tiles=tiles), patch("builtins.print"), self.assertRaises(stock.ScrapeError):
                await stock.discover_products(self.page(tiles), "test", "https://example.test", stock.MARKETPLACES[0])

    async def test_http_errors_abort_before_parsing(self):
        page = self.page(status=503)
        with self.assertRaises(stock.ScrapeError):
            await stock.discover_products(page, "test", "https://example.test", stock.MARKETPLACES[0])
        page.eval_on_selector_all.assert_not_called()

    async def test_captcha_aborts_instead_of_publishing_empty_stock(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(stock, "DEBUG_DIR", Path(directory)), patch("builtins.print"):
            with self.assertRaises(stock.ScrapeError):
                await stock.discover_products(self.page(body="robot check"), "test", "https://example.test", stock.MARKETPLACES[0])

    async def test_incomplete_individual_product_is_a_failed_consultation(self):
        page = self.page()
        page.query_selector = AsyncMock(return_value=None)
        with self.assertRaises(stock.ScrapeError):
            await stock.check_single_product(page, "B000000001", stock.MARKETPLACES[0])

    async def test_eci_distinguishes_missing_cards_from_sold_out_cards(self):
        for tiles in ([], [{"id": "product-ECI001", "name": "Pokémon Booster", "relUrl": "/product/ECI001", "hasAddButton": False}]):
            with self.subTest(tiles=tiles), patch("builtins.print"):
                page = self.page()
                page.eval_on_selector_all = AsyncMock(side_effect=lambda selector, script: len(tiles) if "els.length" in script else tiles)
                if not tiles:
                    with self.assertRaises(stock.ScrapeError):
                        await stock.discover_eci_products(page, "test", "https://example.test")
                else:
                    products = await stock.discover_eci_products(page, "test", "https://example.test")
                    self.assertEqual(products["ECI001"]["status"], "no_disponible")

    def toysrus_api_page(self, content, status=200, num_found=None):
        page = MagicMock()
        response = MagicMock()
        response.status = status
        response.json = AsyncMock(return_value={
            "catalog": {"content": content, "numFound": num_found if num_found is not None else len(content)},
        })
        page.request.get = AsyncMock(return_value=response)
        return page

    async def test_toysrus_extracts_product_code_price_and_status(self):
        content = [{
            "id": "K1091126",
            "name": "Pokémon - Sobre de cartas",
            "price": 5.99,
            "availability": True,
            "image": "https://images.example/card.jpg",
            "url": "https://www.toysrus.es/pokemon-sobre/p/K1091126",
        }]
        page = self.toysrus_api_page(content)
        with patch("builtins.print"):
            products = await stock.discover_toysrus_products(page, "test", "pokemon tcg")
        self.assertEqual(products["K1091126"]["name"], "Pokémon - Sobre de cartas")
        self.assertEqual(products["K1091126"]["price"], "5,99 €")
        self.assertEqual(products["K1091126"]["status"], "compra_directa")
        self.assertEqual(products["K1091126"]["url"], content[0]["url"])

    async def test_toysrus_marks_unavailable_cards(self):
        content = [{
            "id": "K1091127",
            "name": "Pokémon - Caja de cartas",
            "price": 29.99,
            "availability": False,
            "image": None,
            "url": "https://www.toysrus.es/pokemon-caja/p/K1091127",
        }]
        page = self.toysrus_api_page(content)
        with patch("builtins.print"):
            products = await stock.discover_toysrus_products(page, "test", "pokemon tcg")
        self.assertEqual(products["K1091127"]["status"], "no_disponible")

    async def test_link_only_page_requires_enabled_individual_fallback(self):
        for enabled in (True, False):
            with self.subTest(fallback_enabled=enabled), patch("builtins.print"):
                page = self.page()
                page.eval_on_selector_all = AsyncMock(side_effect=lambda selector, script: [] if selector == "[data-asin]" else ["https://example.test/dp/B000000001"])
                config = {**stock.MARKETPLACES[0], "allow_individual_fallback": enabled}
                if enabled:
                    products, fallback = await stock.discover_products(page, "test", "https://example.test", config)
                    self.assertEqual(fallback, {"B000000001"})
                else:
                    with self.assertRaises(stock.ScrapeError):
                        await stock.discover_products(page, "test", "https://example.test", config)


if __name__ == "__main__":
    unittest.main()

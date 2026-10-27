import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from core import market, rates


class MarketParsingTests(unittest.TestCase):
    def test_exchange_units_and_bid_ask_direction(self):
        nobitex = {"status": "ok", "bids": [["2700000", "20"], ["2699000", "30"]],
                   "asks": [["2702000", "50"], ["2701000", "40"]]}
        self.assertEqual(market.parse_nobitex(nobitex), {"usdt": {"buy": 270000, "sell": 270100}})
        wallex = {"success": True, "result": {"symbols": {"USDTTMN": {
            "baseAsset": "USDT", "quoteAsset": "TMN", "stats": {"bidPrice": "270000", "askPrice": "270100"}}}}}
        self.assertEqual(market.parse_wallex(wallex), {"usdt": {"buy": 270000, "sell": 270100}})
        wallex["result"]["symbols"]["USDTTMN"]["quoteAsset"] = "USDT"
        self.assertEqual(market.parse_wallex(wallex), {})
        nobitex["asks"] = [["2600000", "10"]]
        self.assertEqual(market.parse_nobitex(nobitex), {})

    def test_tgju_index_is_not_fabricated_buy_or_sell(self):
        html = '<tr data-market-row="price_rub" data-title="<span>ignore > 9999999</span>" data-price="33,750"><td>wrong 111</td></tr>'
        html += '<tr data-market-row="price_dollar_rl" data-price="2,700,000"></tr>'
        self.assertEqual(market.parse_tgju(html), {"rub": {"last": 3375}, "usd": {"last": 270000}})

    def test_non_finite_values_are_rejected(self):
        for value in ("nan", "inf", "-1", "0", None):
            self.assertEqual(rates._num(value), 0)

    def test_missing_cash_cells_do_not_read_the_next_currency(self):
        html = '''<tr onclick="currencies-price/rub'"><td class="buyPrice">33,000</td></tr>
                  <tr onclick="currencies-price/usd'"><td class="buyPrice">2,690,000</td><td class="sellPrice">2,700,000</td></tr>'''
        self.assertIsNone(rates._extract(html, "rub"))
        self.assertEqual(rates._extract(html, "usd"), {"buy": 269000, "sell": 270000})


class MarketFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_reference_never_uses_comparison_and_expires_old_cache(self):
        with patch.object(rates, "_CACHE", {"rub": {"buy": 3300, "sell": 3350}}), \
             patch.object(rates, "_CACHE_TS", time.time() - 400), \
             patch.object(rates, "_REFERENCE_LOCK", asyncio.Lock()), \
             patch.object(rates, "_fetch_bonbast", AsyncMock(side_effect=ValueError("offline"))), \
             patch.object(rates, "_fetch_alanchand", AsyncMock()) as comparison:
            self.assertEqual(await rates.fetch_rates(force=True), {})
            comparison.assert_not_called()
            self.assertTrue(rates.reference_status()["stale"])

    async def test_comparison_does_not_modify_reference_and_marks_failures_stale(self):
        reference = {"rub": {"buy": 3300, "sell": 3350}, "usd": {"buy": 269000, "sell": 270000}}
        good = {"rates": {"usdt": {"buy": 270000, "sell": 270100}}}
        providers = {name: AsyncMock(return_value=good) for name in market._PROVIDERS}
        providers["tgju"] = AsyncMock(side_effect=ValueError("offline"))
        old = {"rates": {"rub": {"last": 3370}}, "fetched_at": time.time() - 120, "stale": False}
        with patch.object(market, "_CACHE", {"tgju": old}), patch.object(market, "_ATTEMPT_TS", 0), \
             patch.object(market, "_LOCK", asyncio.Lock()), patch.object(market, "_PROVIDERS", providers), \
             patch.object(market, "fetch_rates", AsyncMock(return_value=reference)) as fetch_reference, \
             patch.object(market, "reference_status", return_value={"fetched_at": time.time(), "stale": False}):
            snapshots = await market.fetch_market(force=True)
            self.assertEqual(snapshots["bonbast"]["rates"], reference)
            self.assertTrue(snapshots["tgju"]["stale"])
            self.assertEqual(snapshots["tgju"]["rates"], old["rates"])
            self.assertNotEqual(snapshots["bonbast"]["rates"], snapshots["nobitex"]["rates"])
            # Rapid refreshes are cached and do not hit upstream APIs again.
            await market.fetch_market(force=True)
            self.assertEqual(fetch_reference.await_count, 1)
            output = market.render_market(snapshots)
            self.assertIn("مرجع معاملات روبل و دلار: Bonbast", output)
            self.assertIn("آخرین نرخ ذخیره‌شده", output)
            self.assertIn("قیمت شاخص", output)
            self.assertLess(len(output), 4096)

    async def test_alanchand_tether_is_an_index_in_toman(self):
        html = '''<a href="https://alanchand.com/en/crypto-price/usdt">Tether</a>
                  <span class="fw-bold text-primary fs-5">2,700,690 <span></span></span>'''
        response = AsyncMock()
        response.text = html
        response.raise_for_status = lambda: None
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=response)):
            self.assertEqual((await rates._fetch_alanchand())["usdt"], {"last": 270069})

    async def test_missing_tether_price_does_not_use_bitcoin_price(self):
        html = '''<a href="https://alanchand.com/en/crypto-price/usdt">Tether</a><span>unavailable</span>
                  <div class="col-md-4 mb-3"><a href="crypto-price/btc">Bitcoin</a>
                  <span class="fw-bold text-primary">200,000,000,000</span></div>'''
        response = AsyncMock()
        response.text = html
        response.raise_for_status = lambda: None
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=response)):
            self.assertNotIn("usdt", await rates._fetch_alanchand())

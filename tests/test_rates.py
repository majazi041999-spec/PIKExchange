import unittest
from unittest.mock import AsyncMock, patch

from core.rates import compute_tier_rate
from core.utils import parse_int


class TierPricingTests(unittest.IsolatedAsyncioTestCase):
    async def test_offset_subtracts_directly_from_site_rate(self):
        tier = {"mult": 0.9108, "offset": parse_int("-۲۷۰"), "pricing_mode": "offset"}
        with patch("core.rates.site_base_toman", AsyncMock(return_value=3350)):
            self.assertEqual(await compute_tier_rate({}, tier), 3080)

    async def test_multiplier_ignores_saved_offset(self):
        tier = {"mult": 0.9, "offset": -270, "pricing_mode": "multiplier"}
        with patch("core.rates.site_base_toman", AsyncMock(return_value=3350)):
            self.assertEqual(await compute_tier_rate({}, tier), 3015)

    async def test_positive_and_zero_offset(self):
        with patch("core.rates.site_base_toman", AsyncMock(return_value=3350)):
            for offset in (0, 270):
                tier = {"mult": 0.9, "offset": offset, "pricing_mode": "offset"}
                self.assertEqual(await compute_tier_rate({}, tier), 3350 + offset)

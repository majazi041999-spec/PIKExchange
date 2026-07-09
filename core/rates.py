"""دریافت نرخ لحظه‌ای از alanchand و محاسبه‌ی نرخ محصولات.

نرخ‌های سایت به **ریال** هستند و اینجا به **تومان** (÷۱۰) تبدیل می‌شوند.
یک کش کوتاه‌مدت هم داریم تا در هر کلیک کاربر به سایت درخواست نزنیم.
"""
import logging
import re
import time
from typing import Dict, Optional

import httpx

from core.config import RATE_URL

logger = logging.getLogger("pik.rates")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
}

# کش: مقدار خام سایت (تومان) + زمان دریافت
_CACHE: Dict[str, Dict[str, float]] = {}
_CACHE_TS: float = 0.0
CACHE_TTL = 60  # ثانیه


def _extract(html: str, code: str) -> Optional[Dict[str, float]]:
    """buy/sell یک ارز را از HTML سایت استخراج می‌کند (به تومان)."""
    # سطر مربوطه با onclick شامل currencies-price/<code>' مشخص می‌شود
    pattern = (
        r"currencies-price/" + re.escape(code) + r"'"      # لنگر روی همان ارز
        r".*?buyPrice[^>]*>\s*([\d,]+)"                      # قیمت خرید
        r".*?sellPrice[^>]*>\s*([\d,]+)"                     # قیمت فروش
    )
    m = re.search(pattern, html, re.S | re.I)
    if not m:
        return None
    try:
        buy_rial = float(m.group(1).replace(",", ""))
        sell_rial = float(m.group(2).replace(",", ""))
    except ValueError:
        return None
    if buy_rial <= 0 or sell_rial <= 0:
        return None
    # ریال ← تومان
    return {"buy": buy_rial / 10.0, "sell": sell_rial / 10.0}


async def fetch_rates(force: bool = False) -> Dict[str, Dict[str, float]]:
    """نرخ روبل و دلار را برمی‌گرداند: {'rub': {'buy','sell'}, 'usd': {...}} به تومان.

    در صورت خطا، آخرین کش موفق برگردانده می‌شود (اگر باشد).
    """
    global _CACHE, _CACHE_TS
    now = time.time()
    if not force and _CACHE and (now - _CACHE_TS) < CACHE_TTL:
        return _CACHE

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=_HEADERS) as client:
            resp = await client.get(RATE_URL)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.warning("fetch rates failed: %s", e)
        return _CACHE  # ممکن است خالی باشد

    data: Dict[str, Dict[str, float]] = {}
    for code in ("rub", "usd"):
        parsed = _extract(html, code)
        if parsed:
            data[code] = parsed

    if data.get("rub"):  # حداقل روبل باید موجود باشد تا کش را معتبر بدانیم
        _CACHE = {**_CACHE, **data}
        _CACHE_TS = now
        logger.info(
            "rates updated | rub buy=%.0f sell=%.0f | usd buy=%.0f",
            data["rub"]["buy"], data["rub"]["sell"],
            data.get("usd", {}).get("buy", 0),
        )
    return _CACHE


def _column_value(rate: Dict[str, float], column: str) -> float:
    if column == "sell":
        return rate.get("sell", 0.0)
    if column == "avg":
        return (rate.get("buy", 0.0) + rate.get("sell", 0.0)) / 2.0
    return rate.get("buy", 0.0)  # پیش‌فرض buy


async def site_base_toman(product: dict) -> Optional[float]:
    """نرخ مبنای سایت (تومان) برای یک محصول را برمی‌گرداند."""
    rates = await fetch_rates()
    base = product.get("base", "rub")
    rate = rates.get(base)
    if not rate:
        return None
    value = _column_value(rate, product.get("column", "buy"))
    return value if value > 0 else None


async def compute_tier_rate(product: dict, tier: dict) -> Optional[int]:
    """نرخ یک پله از محصول پله‌ای را حساب می‌کند (تومان به ازای هر واحد)."""
    base = await site_base_toman(product)
    if base is None:
        return None
    return int(round(base * float(tier.get("mult", 1.0))))


async def compute_simple_rate(product: dict) -> Optional[int]:
    """نرخ یک محصول ساده را حساب می‌کند.

    اگر ``mode == manual`` و ``manual > 0`` باشد همان نرخ دستی، وگرنه فرمول سایت.
    """
    mode = product.get("mode", "formula")
    manual = int(product.get("manual", 0) or 0)
    if mode == "manual" and manual > 0:
        return manual
    base = await site_base_toman(product)
    if base is None:
        # اگر فرمول ممکن نبود ولی نرخ دستی داریم، از همان استفاده کن
        return manual or None
    return int(round(base * float(product.get("mult", 1.0))))

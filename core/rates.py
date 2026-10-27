"""Bonbast transaction reference in toman, with short-lived caching.

Alanchand is read separately for the comparison dashboard; its rial values are
converted to toman and never replace the transaction reference.
"""
import asyncio
import logging
import math
import re
import time
from typing import Dict, Optional

import httpx

from core.config import RATE_URL
from core.utils import en_digits

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
REFERENCE_MAX_AGE = 300
_REFERENCE_LOCK = asyncio.Lock()
_REFERENCE_ERROR = False


def _extract(html: str, code: str) -> Optional[Dict[str, float]]:
    """buy/sell یک ارز را از HTML سایت استخراج می‌کند (به تومان)."""
    row = re.search(r'currencies-price/' + re.escape(code) + r'["\'][\s\S]*?</tr>', html)
    if not row:
        return None
    buy = re.search(r'buyPrice[^>]*>\s*([\d,٬]+)', row[0])
    sell = re.search(r'sellPrice[^>]*>\s*([\d,٬]+)', row[0])
    buy_rial = _num(buy[1]) if buy else 0
    sell_rial = _num(sell[1]) if sell else 0
    if not buy_rial or not sell_rial or buy_rial > sell_rial:
        return None
    # ریال ← تومان
    return {"buy": buy_rial / 10.0, "sell": sell_rial / 10.0}


async def _fetch_alanchand() -> Dict[str, Dict[str, float]]:
    """نرخ روبل/دلار و شاخص تتر از alanchand (ریال ← تومان)."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=_HEADERS) as client:
        resp = await client.get(RATE_URL)
        resp.raise_for_status()
        html = resp.text
    data: Dict[str, Dict[str, float]] = {}
    for code in ("rub", "usd"):
        parsed = _extract(html, code)
        if parsed:
            data[code] = parsed
    card = re.search(r'crypto-price/usdt["\']([\s\S]*?)(?=<div class="col-md-4 mb-3">|$)', html)
    tether = re.search(r'class="fw-bold[^"\n]*"[^>]*>\s*([\d,]+)', card[1]) if card else None
    if tether:
        value = _num(tether.group(1)) / 10
        if value > 0:
            data["usdt"] = {"last": value}
    return data


def _num(v) -> float:
    try:
        value = float(en_digits(v).replace(",", "").replace("٬", "").replace("٫", ".").strip())
        return value if math.isfinite(value) and value > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


async def _fetch_bonbast() -> Dict[str, Dict[str, float]]:
    """نرخ روبل/دلار از bonbast.com (مقادیر به تومان هستند).

    روش:
      ۱) GET صفحهٔ اصلی با کوکی ``rf_bb=true; st_bb=0`` و استخراج param.
      ۲) POST به ‎/json‎ با بدنهٔ ``param=<token>``.
      ۳) پاسخ JSON کلیدهای ``usd1/usd2`` و ``rub1/rub2`` دارد (تومان).
    اگر ساختار عوض شود، نرخ معتبر برنمی‌گردد و منبع دیگری جایگزین نمی‌شود.
    """
    headers = {**_HEADERS, "Cookie": "rf_bb=true; st_bb=0"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
        home = await client.get("https://bonbast.com/")
        home.raise_for_status()
        # توکن داخل: $.post('/json', {param: "<token>"}, ...)
        m = re.search(r"""\$\.post\(\s*['"]/json['"]\s*,\s*\{\s*param\s*:\s*['"]([^'"]+)['"]""", home.text)
        if not m:
            return {}
        token = m.group(1)
        resp = await client.post("https://bonbast.com/json", data={"param": token})
        resp.raise_for_status()
        j = resp.json()

    # ستون‌های جدول bonbast: X1=فروش، X2=خرید — مقادیر به تومان
    data: Dict[str, Dict[str, float]] = {}
    for code in ("rub", "usd"):
        sell = _num(j.get(f"{code}1"))
        buy = _num(j.get(f"{code}2"))
        if sell > 0 and buy > 0 and buy <= sell:
            data[code] = {"buy": buy, "sell": sell}
    return data


_LAST_SOURCE: str = ""


async def fetch_rates(force: bool = False) -> Dict[str, Dict[str, float]]:
    """Only Bonbast determines transaction rates; comparison providers cannot replace it."""
    global _CACHE, _CACHE_TS, _LAST_SOURCE, _REFERENCE_ERROR
    async with _REFERENCE_LOCK:
        now = time.time()
        if not force and _CACHE and (now - _CACHE_TS) < CACHE_TTL:
            return _CACHE
        try:
            data = await _fetch_bonbast()
            if not data.get("rub"):
                raise ValueError("no ruble quote")
        except Exception as e:
            _REFERENCE_ERROR = True
            logger.warning("Bonbast reference unavailable (%s)", type(e).__name__)
            return _CACHE if _CACHE and time.time() - _CACHE_TS <= REFERENCE_MAX_AGE else {}

        _CACHE = data
        _CACHE_TS = time.time()
        _LAST_SOURCE = "bonbast"
        _REFERENCE_ERROR = False
        logger.info("Bonbast reference refreshed | rub sell=%.0f", data["rub"]["sell"])
        return _CACHE


def reference_status() -> dict:
    return {"fetched_at": _CACHE_TS, "stale": _REFERENCE_ERROR or time.time() - _CACHE_TS >= CACHE_TTL}


def last_source() -> str:
    return _LAST_SOURCE


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


# ─────────────────────────── نرخ دلار به روبل (بانک روسیه) ───────────────────────────
# سایت خود اسبربانک از خارج روسیه API قابل‌دسترسی ندارد؛ بانک مرکزی روسیه (CBR) نرخ
# رسمی و پایدار می‌دهد و مبنای همان نرخی است که بانک‌ها (از جمله اسبر) رویش اسپرد می‌گذارند.
# اگر ادمین نرخ دستی (نرخ دقیق اسبر) را وارد کند، همان اولویت دارد.
_USD_RUB_URLS = [
    "https://www.cbr-xml-daily.ru/daily_json.js",
]
_USD_RUB_CACHE: float = 0.0
_USD_RUB_TS: float = 0.0
USD_RUB_TTL = 600  # ۱۰ دقیقه


async def fetch_usd_rub(force: bool = False) -> Optional[float]:
    """نرخ دلار به روبل (روبل به‌ازای هر دلار) را از بانک مرکزی روسیه برمی‌گرداند."""
    global _USD_RUB_CACHE, _USD_RUB_TS
    now = time.time()
    if not force and _USD_RUB_CACHE and (now - _USD_RUB_TS) < USD_RUB_TTL:
        return _USD_RUB_CACHE

    for url in _USD_RUB_URLS:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=_HEADERS) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            usd = data.get("Valute", {}).get("USD", {})
            value = float(usd.get("Value") or 0)
            nominal = float(usd.get("Nominal") or 1) or 1
            rub_per_usd = value / nominal
            if rub_per_usd > 0:
                _USD_RUB_CACHE = rub_per_usd
                _USD_RUB_TS = now
                logger.info("usd/rub updated | %.2f RUB per USD", rub_per_usd)
                return rub_per_usd
        except Exception as e:
            logger.warning("fetch usd/rub failed (%s): %s", url, e)
    return _USD_RUB_CACHE or None


async def site_base_value(product: dict) -> Optional[float]:
    """مقدار مبنای یک محصول ساده را برمی‌گرداند (قبل از اعمال ضریب و آفست).

    * base = ``rub``/``usd`` → نرخ تومانی همان ارز از alanchand.
    * base = ``usd_rub``     → نرخ دلار به روبل (روبل/دلار) از بانک روسیه.
    """
    base = product.get("base", "rub")
    if base == "usd_rub":
        return await fetch_usd_rub()
    return await site_base_toman(product)


async def compute_tier_rate(product: dict, tier: dict) -> Optional[int]:
    """نرخ یک پله از محصول پله‌ای را حساب می‌کند (تومان به ازای هر واحد).

    خرید روبل: فقط یکی از ضریب یا آفست فعال است. محصولات قدیمی بدون
    pricing_mode فرمول ترکیبی خود را حفظ می‌کنند.
    """
    base = await site_base_toman(product)
    if base is None:
        return None
    mult = float(tier.get("mult", 1.0))
    offset = float(tier.get("offset", 0) or 0)
    if tier.get("pricing_mode") == "offset":
        return int(round(base + offset))
    if tier.get("pricing_mode") == "multiplier":
        return int(round(base * mult))
    return int(round(base * mult + offset))


async def compute_simple_rate(product: dict) -> Optional[int]:
    """نرخ نهایی یک محصول ساده = مبنا × ضریب + آفست.

    «مبنا» نرخ دستی است (اگر mode=manual و manual>0) وگرنه مقدار مبنای منبع.
    آفست یک عدد ثابت است که به نتیجه اضافه/کم می‌شود (مثلاً ‎-۳۰۰‎ یا ‎-۸‎).
    """
    mult = float(product.get("mult", 1.0))
    offset = float(product.get("offset", 0) or 0)
    manual = int(product.get("manual", 0) or 0)
    mode = product.get("mode", "formula")

    if mode == "manual" and manual > 0:
        reference = float(manual)
    else:
        reference = await site_base_value(product)
        if reference is None:
            reference = float(manual) if manual > 0 else None

    if reference is None:
        return None
    return int(round(reference * mult + offset))

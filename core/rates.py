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


async def _fetch_alanchand() -> Dict[str, Dict[str, float]]:
    """نرخ روبل/دلار از alanchand (ریال ← تومان)."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=_HEADERS) as client:
        resp = await client.get(RATE_URL)
        resp.raise_for_status()
        html = resp.text
    data: Dict[str, Dict[str, float]] = {}
    for code in ("rub", "usd"):
        parsed = _extract(html, code)
        if parsed:
            data[code] = parsed
    return data


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").replace("٬", "").strip())
    except (TypeError, ValueError):
        return 0.0


async def _fetch_bonbast() -> Dict[str, Dict[str, float]]:
    """نرخ روبل/دلار از bonbast.com (مقادیر به تومان هستند).

    روش: توکن پویا از صفحهٔ اصلی استخراج و سپس POST به ‎/json‎.
    اگر ساختار سایت تغییر کند یا در دسترس نباشد، دیکشنری خالی برمی‌گردد.
    """
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=_HEADERS) as client:
        home = await client.get("https://bonbast.com/")
        home.raise_for_status()
        # bonbast یک جفت کلید/مقدار پویا داخل $.post('/json', {...}) می‌گذارد
        m = re.search(r"\$\.post\(\s*['\"]/json['\"]\s*,\s*\{\s*['\"](\w+)['\"]\s*:\s*['\"](\w+)['\"]", home.text)
        payload = {m.group(1): m.group(2)} if m else {}
        resp = await client.post(
            "https://bonbast.com/json", data=payload,
            headers={**_HEADERS, "X-Requested-With": "XMLHttpRequest",
                     "Referer": "https://bonbast.com/"},
        )
        resp.raise_for_status()
        j = resp.json()

    data: Dict[str, Dict[str, float]] = {}
    for code in ("rub", "usd"):
        sell = _num(j.get(f"{code}1"))   # فروش
        buy = _num(j.get(f"{code}2"))    # خرید
        if buy > 0 or sell > 0:
            data[code] = {"buy": buy or sell, "sell": sell or buy}
    return data


_SOURCES = {
    "bonbast": _fetch_bonbast,
    "alanchand": _fetch_alanchand,
}
_LAST_SOURCE: str = ""


async def _source_order() -> list:
    from core.db import get_setting

    raw = await get_setting("rate_sources", "bonbast,alanchand")
    order = [s.strip() for s in raw.split(",") if s.strip() in _SOURCES]
    return order or ["bonbast", "alanchand"]


async def fetch_rates(force: bool = False) -> Dict[str, Dict[str, float]]:
    """نرخ روبل و دلار (تومان) را با اولویت منابع برمی‌گرداند.

    منابع به‌ترتیب امتحان می‌شوند (پیش‌فرض: bonbast سپس alanchand)؛ اولین منبعی که
    نرخ روبل معتبر بدهد استفاده می‌شود. در صورت شکست همه، آخرین کش موفق برمی‌گردد.
    """
    global _CACHE, _CACHE_TS, _LAST_SOURCE
    now = time.time()
    if not force and _CACHE and (now - _CACHE_TS) < CACHE_TTL:
        return _CACHE

    for name in await _source_order():
        fn = _SOURCES.get(name)
        if not fn:
            continue
        try:
            data = await fn()
        except Exception as e:
            logger.warning("rate source '%s' failed: %s", name, e)
            continue
        if data.get("rub"):
            _CACHE = {**_CACHE, **data}
            _CACHE_TS = now
            _LAST_SOURCE = name
            logger.info(
                "rates from %s | rub buy=%.0f sell=%.0f | usd buy=%.0f",
                name, data["rub"]["buy"], data["rub"]["sell"],
                data.get("usd", {}).get("buy", 0),
            )
            return _CACHE

    logger.warning("all rate sources failed; using cache")
    return _CACHE


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
    """نرخ یک پله از محصول پله‌ای را حساب می‌کند (تومان به ازای هر واحد)."""
    base = await site_base_toman(product)
    if base is None:
        return None
    return int(round(base * float(tier.get("mult", 1.0))))


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

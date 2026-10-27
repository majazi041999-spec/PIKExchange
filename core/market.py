"""Independent display quotes. These providers never supply transaction rates."""
import asyncio
import logging
import re
import time
from copy import deepcopy
from datetime import datetime
from html import escape

import httpx

from core.jalali import TEHRAN_TZ, jalali_datetime
from core.rates import _HEADERS, _fetch_alanchand, _num, fetch_rates, reference_status
from core.utils import fa_digits, num

logger = logging.getLogger("pik.market")
TTL = 60
REFRESH_MIN_INTERVAL = 10
SOURCE_TIMEOUT = 12
SOURCE_MAX_AGE = 300
_CACHE = {}
_ATTEMPT_TS = 0.0
_LOCK = asyncio.Lock()

SOURCES = {
    "bonbast": {"name": "Bonbast · بن‌بست", "url": "https://bonbast.com/", "currencies": ("rub", "usd")},
    "alanchand": {"name": "الان‌چند", "url": "https://alanchand.com/en", "currencies": ("rub", "usd", "usdt")},
    "tgju": {"name": "TGJU · طلا و ارز", "url": "https://www.tgju.org/", "currencies": ("rub", "usd")},
    "nobitex": {"name": "نوبیتکس", "url": "https://nobitex.ir/", "currencies": ("usdt",)},
    "wallex": {"name": "والکس", "url": "https://wallex.ir/", "currencies": ("usdt",)},
}


async def _get(url):
    async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), follow_redirects=True, headers=_HEADERS) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response


def parse_tgju(html):
    """Read the displayed cash index, in rials. Never infer a buy/sell spread."""
    data = {}
    for code, slug in (("rub", "price_rub"), ("usd", "price_dollar_rl")):
        # data-title can contain HTML and > signs; bound by the actual </tr>.
        row = re.search(r'data-market-row=["\']' + slug + r'["\'](?P<row>[\s\S]*?)</tr>', html)
        if not row:
            continue
        value = re.search(r'data-price=["\']([^"\']+)["\']', row["row"])
        if value and (price := _num(value[1])):
            data[code] = {"last": price / 10}
    return data


def parse_nobitex(body):
    if body.get("status") != "ok":
        return {}
    bid = max((_num(row[0]) for row in body.get("bids", []) if len(row) >= 2 and _num(row[1])), default=0)
    ask = min((price for row in body.get("asks", []) if len(row) >= 2 and _num(row[1]) and (price := _num(row[0]))), default=0)
    if not bid or not ask or bid > ask:
        return {}
    return {"usdt": {"buy": bid / 10, "sell": ask / 10}}


def parse_wallex(body):
    if body.get("success") is not True:
        return {}
    market = body.get("result", {}).get("symbols", {}).get("USDTTMN", {})
    if market.get("baseAsset") != "USDT" or market.get("quoteAsset") != "TMN":
        return {}
    stats = market.get("stats", {})
    buy, sell = _num(stats.get("bidPrice")), _num(stats.get("askPrice"))
    return {"usdt": {"buy": buy, "sell": sell}} if buy and sell and buy <= sell else {}


async def _alanchand():
    return {"rates": await _fetch_alanchand()}


async def _tgju():
    return {"rates": parse_tgju((await _get("https://www.tgju.org/")).text)}


async def _nobitex():
    body = (await _get("https://apiv2.nobitex.ir/v3/orderbook/USDTIRT")).json()
    return {"rates": parse_nobitex(body), "source_at": _num(body.get("lastUpdate")) / 1000}


async def _wallex():
    return {"rates": parse_wallex((await _get("https://api.wallex.ir/v1/markets")).json())}


_PROVIDERS = {"alanchand": _alanchand, "tgju": _tgju, "nobitex": _nobitex, "wallex": _wallex}


async def fetch_market(force=False):
    """Fetch sources concurrently, retaining failed quotes with an explicit stale flag."""
    global _ATTEMPT_TS
    async with _LOCK:
        age = time.time() - _ATTEMPT_TS
        if _CACHE and age < (REFRESH_MIN_INTERVAL if force else TTL):
            return deepcopy(_CACHE)

        async def load(name):
            try:
                if name == "bonbast":
                    rates = await asyncio.wait_for(fetch_rates(force=force), SOURCE_TIMEOUT)
                    result = {"rates": rates, **reference_status()}
                else:
                    result = await asyncio.wait_for(_PROVIDERS[name](), SOURCE_TIMEOUT)
                    result.update(fetched_at=time.time(), stale=False)
                if not result.get("rates"):
                    raise ValueError("no valid quotes")
                source_at = result.get("source_at")
                if source_at and (time.time() - source_at > SOURCE_MAX_AGE or source_at > time.time() + 60):
                    result["stale"] = True
                _CACHE[name] = result
            except Exception as exc:
                logger.warning("comparison source %s unavailable (%s)", name, type(exc).__name__)
                previous = _CACHE.get(name, {"rates": {}, "fetched_at": 0})
                _CACHE[name] = {**previous, "stale": True}

        await asyncio.gather(*(load(name) for name in SOURCES))
        _ATTEMPT_TS = time.time()
        return deepcopy(_CACHE)


def _clock(timestamp):
    return fa_digits(datetime.fromtimestamp(timestamp, TEHRAN_TZ).strftime("%H:%M:%S"))


def render_market(snapshots):
    lines = ["📈 <b>مقایسه نرخ بازار</b>", "تمام مبالغ به تومان، برای هر یک واحد ارز",
             "⭐ <b>مرجع معاملات روبل و دلار: Bonbast</b>", f"🕓 زمان مشاهده: {jalali_datetime()}"]
    for currency, title in (("rub", "🇷🇺 روبل"), ("usd", "🇺🇸 دلار"), ("usdt", "₮ تتر")):
        lines.extend(["", "━━━━━━━━━━━━━━", f"<b>{title}</b>"])
        for name, info in SOURCES.items():
            if currency not in info["currencies"]:
                continue
            snapshot = snapshots.get(name, {})
            quote = snapshot.get("rates", {}).get(currency)
            label = f'<a href="{info["url"]}">{escape(info["name"])}</a>'
            if name == "bonbast":
                label = "⭐ " + label
            if not quote:
                lines.append(f"• {label}: دریافت نشد ⚠️")
                continue
            if quote.get("buy") and quote.get("sell"):
                price = f"خرید <b>{num(quote['buy'])}</b> | فروش <b>{num(quote['sell'])}</b>"
            else:
                price = f"قیمت شاخص <b>{num(quote['last'])}</b>"
            stale = snapshot.get("stale") or time.time() - snapshot.get("fetched_at", 0) > SOURCE_MAX_AGE
            stamp = _clock(snapshot["fetched_at"]) if snapshot.get("fetched_at") else "—"
            status = "⚠️ آخرین نرخ ذخیره‌شده" if stale else "دریافت"
            lines.extend([f"• {label}", f"  {price}", f"  {status}: {stamp}"])
            if snapshot.get("source_at"):
                lines.append(f"  زمان بازار: {_clock(snapshot['source_at'])}")
    lines.extend(["", "خرید/فروش از دید منبع است؛ در صرافی‌ها بهترین سفارش بازار نمایش داده می‌شود.",
                  "قیمت شاخص، نرخ خرید یا فروش نیست. زمان «دریافت» مربوط به دریافت ربات است."])
    return "\n".join(lines)

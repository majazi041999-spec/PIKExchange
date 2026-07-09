"""تعریف محصولات صرافی و پیکربندی پیش‌فرض آن‌ها.

هر محصول یکی از این نوع‌هاست:

* ``rub_tiered``  : خرید روبل با پله‌های حجمی (تینکوف). هر پله ضریب خودش را دارد.
* ``rub_single``  : خرید روبل تک‌فیش (یک نرخ ثابت با یک ضریب).
* ``simple``      : یک محصول تک‌نرخی (دلار نقدی، دلار مهردار، تومان با روبل).

منبع نرخ هر محصول:
* ``base``   : ``rub`` یا ``usd`` — کدام ارز از سایت مبنا باشد.
* ``column`` : ``buy`` | ``sell`` | ``avg`` — کدام ستون سایت.
* ``mode``   : ``formula`` (نرخ سایت × ضریب) یا ``manual`` (نرخ دستی ادمین).

همه‌ی نرخ‌ها به **تومان** ذخیره و نمایش داده می‌شوند (مقدار ریالی سایت ÷ ۱۰).
"""
from copy import deepcopy
from typing import Dict, List, Optional

# ترتیب نمایش در منوی اصلی
# همه‌ی محصولات (برای پنل مدیریت)
PRODUCT_ORDER: List[str] = [
    "rub_c2c",
    "usd_stamped_sell",
    "usd_cash_buy",
    "toman_with_rub",
]

# ساختار منوی اصلی کاربر — می‌تواند «محصول» مستقیم یا «دسته» (زیرمنو) باشد.
MAIN_MENU: List[dict] = [
    {"kind": "product", "id": "rub_c2c"},
    {"kind": "category", "id": "usd", "title": "💵 خرید - فروش دلار",
     "style": "primary", "members": ["usd_stamped_sell", "usd_cash_buy"]},
    {"kind": "product", "id": "toman_with_rub"},
]


def get_category(cid: str) -> Optional[dict]:
    for entry in MAIN_MENU:
        if entry.get("kind") == "category" and entry.get("id") == cid:
            return entry
    return None

DEFAULT_PRODUCTS: Dict[str, dict] = {
    "rub_c2c": {
        "title": "🇷🇺 خرید روبل",
        "type": "rub_tiered",
        "base": "rub",
        "column": "sell",   # طبق نظر مالک، مبنا نرخ «فروش» سایت است
        "style": "success",
        "tiers": [
            {"key": "t1", "label": "نرخ تینکفی - زیر ۲۵ میلیون", "min": 0, "max": 25_000_000, "mult": 0.93},
            {"key": "t2", "label": "نرخ تینکفی - ۲۵ تا ۱۵۰ میلیون", "min": 25_000_000, "max": 150_000_000, "mult": 0.92},
            {"key": "t3", "label": "نرخ تینکفی - بالای ۱۵۰ میلیون", "min": 150_000_000, "max": 0, "mult": 0.915},
            {"key": "single", "label": "نرخ لحظه‌ای تک‌فیش", "min": 0, "max": 0, "mult": 0.965},
        ],
    },
    "usd_stamped_sell": {
        "title": "💵 فروش دلار مهردار یا کهنه",
        "type": "simple",
        "base": "usd",
        "column": "sell",
        "style": "primary",
        "mode": "manual",   # پیش‌فرض دستی؛ اگر manual=0 باشد از فرمول استفاده می‌شود
        "mult": 1.0,
        "manual": 0,
        "unit": "دلار",
    },
    "usd_cash_buy": {
        "title": "💵 خرید دلار نقدی",
        "type": "simple",
        "base": "usd",
        "column": "buy",
        "style": "primary",
        "mode": "formula",
        "mult": 1.0,
        "manual": 0,
        "unit": "دلار",
    },
    "toman_with_rub": {
        "title": "💰 خرید تومان با روبل",
        "type": "simple",
        "base": "rub",
        "column": "sell",
        "style": "primary",
        "mode": "formula",
        "mult": 1.0,
        "manual": 0,
        "unit": "روبل",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = deepcopy(val)
    return out


async def get_products() -> Dict[str, dict]:
    """محصولات را با اعمال بازنویسی‌های ذخیره‌شده در دیتابیس برمی‌گرداند."""
    from core.db import get_json

    overrides = await get_json("products", {})
    merged: Dict[str, dict] = {}
    for pid, default in DEFAULT_PRODUCTS.items():
        merged[pid] = _deep_merge(default, overrides.get(pid, {}))
    return merged


async def get_product(pid: str) -> dict | None:
    products = await get_products()
    return products.get(pid)


async def save_product(pid: str, data: dict) -> None:
    """بازنویسی محصول را ذخیره می‌کند (کل دیکشنری محصول)."""
    from core.db import get_json, set_json

    overrides = await get_json("products", {})
    overrides[pid] = data
    await set_json("products", overrides)

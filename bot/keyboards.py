"""کیبوردهای اینلاین ربات (با دکمه‌های رنگی اختیاری تلگرام).

رنگ دکمه‌ها با پارامتر ``style`` تلگرام ساخته می‌شود:
    • ``primary`` → آبی · ``success`` → سبز · ``danger`` → قرمز

رنگی‌بودن دکمه‌ها با یک کلید سراسری کنترل می‌شود (پیش‌فرض: خاموش). ادمین می‌تواند
از پنل آن را روشن/خاموش کند؛ مقدار در دیتابیس (کلید ``buttons_colored``) ذخیره می‌شود.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.products import MAIN_MENU

# وضعیت رنگی‌بودن دکمه‌ها (در startup از دیتابیس بارگذاری می‌شود)
_COLOR_ENABLED = False


def set_color_enabled(value: bool) -> None:
    global _COLOR_ENABLED
    _COLOR_ENABLED = bool(value)


def color_enabled() -> bool:
    return _COLOR_ENABLED


def styled_btn(text: str, data: str | None = None, style: str | None = None,
               url: str | None = None) -> InlineKeyboardButton:
    """دکمه‌ی اینلاین؛ رنگ فقط وقتی حالت رنگی روشن باشد و نسخه پشتیبانی کند اعمال می‌شود."""
    kwargs = {"text": text}
    if url:
        kwargs["url"] = url
    else:
        kwargs["callback_data"] = data
    if style and _COLOR_ENABLED:
        try:
            return InlineKeyboardButton(**kwargs, style=style)
        except Exception:
            pass
    return InlineKeyboardButton(**kwargs)


_btn = styled_btn


async def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    from core.products import get_products

    products = await get_products()
    kb = InlineKeyboardBuilder()
    for entry in MAIN_MENU:
        if entry.get("kind") == "category":
            kb.row(_btn(entry["title"], f"cat:{entry['id']}", style=entry.get("style", "primary")))
        else:
            p = products.get(entry["id"])
            if p:
                kb.row(_btn(p["title"], f"p:{entry['id']}", style=p.get("style", "primary")))
    kb.row(
        _btn("👛 کیف پول", "wallet", style="success"),
        _btn("📋 معاملات من", "mytx", style="primary"),
    )
    kb.row(_btn("🆘 پشتیبانی و ارتباط با ما", "support", style="primary"))
    if is_admin:
        kb.row(_btn("⚙️ پنل مدیریت", "admin", style="danger"))
    return kb.as_markup()


async def category_kb(cid: str) -> InlineKeyboardMarkup:
    """زیرمنوی یک دسته: فهرست محصولات عضو."""
    from core.products import get_category, get_products

    cat = get_category(cid)
    products = await get_products()
    kb = InlineKeyboardBuilder()
    if cat:
        for pid in cat.get("members", []):
            p = products.get(pid)
            if p:
                kb.row(_btn(p["title"], f"p:{pid}", style=p.get("style", "primary")))
    kb.row(_btn("🔙 بازگشت به منوی اصلی", "menu"))
    return kb.as_markup()


def back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("🔙 بازگشت به منوی اصلی", "menu", style="primary")]]
    )


def tiers_kb(pid: str, tiers: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in tiers:
        kb.row(_btn(f"💹 {t['label']}", f"tier:{pid}:{t['key']}", style="primary"))
    kb.row(_btn("🔙 بازگشت به منوی اصلی", "menu"))
    return kb.as_markup()


def agree_kb(pid: str, tier_key: str = "-") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ موافقت با قوانین و دریافت کارت", f"agree:{pid}:{tier_key}", style="success")],
            [_btn("🔙 بازگشت", "menu")],
        ]
    )


def card_kb(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📤 ارسال فیش واریز", f"receipt:{tx_id}", style="success")],
            [_btn("🔙 بازگشت به منوی اصلی", "menu")],
        ]
    )


def admin_review_kb(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("✅ تأیید فیش و بستن نرخ", f"txok:{tx_id}", style="success"),
                _btn("❌ رد فیش", f"txno:{tx_id}", style="danger"),
            ]
        ]
    )


def contact_user_kb(tg_id: int, username: str | None = None) -> InlineKeyboardMarkup:
    """دکمه‌های ارتباط ادمین با کاربر بعد از تأیید معامله."""
    rows = []
    if username:
        rows.append([_btn(f"👤 گفتگوی مستقیم (@{username})",
                          url=f"https://t.me/{username}", style="primary")])
    rows.append([_btn("✉️ پیام به کاربر از طریق ربات", f"amsg:{tg_id}", style="success")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

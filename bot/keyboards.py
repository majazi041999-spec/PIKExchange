"""کیبوردهای اینلاین ربات."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.products import PRODUCT_ORDER


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


async def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    from core.products import get_products

    products = await get_products()
    kb = InlineKeyboardBuilder()
    for pid in PRODUCT_ORDER:
        p = products.get(pid)
        if p:
            kb.row(_btn(p["title"], f"p:{pid}"))
    kb.row(
        _btn("👛 کیف پول / موجودی", "wallet"),
        _btn("📋 معاملات من", "mytx"),
    )
    kb.row(_btn("📞 پشتیبانی / ارتباط با ما", "support"))
    if is_admin:
        kb.row(_btn("🛠 پنل مدیریت", "admin"))
    return kb.as_markup()


def back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("🔙 بازگشت به منوی اصلی", "menu")]]
    )


def tiers_kb(pid: str, tiers: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in tiers:
        kb.row(_btn(f"نرخ لحظه‌ای — {t['label']}", f"tier:{pid}:{t['key']}"))
    kb.row(_btn("🔙 بازگشت", "menu"))
    return kb.as_markup()


def agree_kb(pid: str, tier_key: str = "-") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ موافقت با قوانین و دریافت کارت", f"agree:{pid}:{tier_key}")],
            [_btn("🔙 بازگشت", "menu")],
        ]
    )


def card_kb(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📤 ارسال فیش واریز", f"receipt:{tx_id}")],
            [_btn("🔙 بازگشت به منوی اصلی", "menu")],
        ]
    )


def admin_review_kb(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("✅ تأیید معامله", f"txok:{tx_id}"),
                _btn("❌ رد", f"txno:{tx_id}"),
            ]
        ]
    )

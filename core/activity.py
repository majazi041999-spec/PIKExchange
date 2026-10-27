"""Notify all administrators about actual quotes displayed to users."""
import asyncio
import logging
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.config import ADMIN_IDS, is_admin
from core.db import get_or_create_user
from core.jalali import jalali_datetime
from core.subscribers import get_subscriber, record_quote
from core.utils import fa_digits, money

logger = logging.getLogger("pik.activity")


async def track_quote(callback, product_id, product, rate, tier_label=""):
    if is_admin(callback.from_user.id):
        return
    try:
        actor = callback.from_user
        user = await get_or_create_user(actor.id, actor.username, actor.full_name)
        request_id = await record_quote(callback.id, user["id"], product_id, product["title"], tier_label,
                                        rate, product.get("currency", "تومان"))
        if request_id is None:
            return
        profile = await get_subscriber(actor.id)
        text = ("👀 <b>درخواست نرخ جدید</b>\n\n"
                f"👤 {escape(actor.full_name[:100])}\n"
                f"شناسه: <code>{actor.id}</code>\n"
                f"یوزرنیم: {escape('@' + actor.username) if actor.username else 'ندارد'}\n"
                f"محصول: {escape(product['title'])}\n")
        if tier_label:
            text += f"گزینه: {escape(tier_label)}\n"
        text += (f"نرخ دیده‌شده: <b>{money(rate, product.get('currency', 'تومان'))}</b>\n"
                 f"تعداد درخواست‌های این کاربر: {fa_digits(profile['quote_count'])}\n"
                 f"🕓 {jalali_datetime()}")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="👤 کاربر و بن / آنبن", callback_data=f"subs:view:{actor.id}:0")]])

        async def send(admin_id):
            try:
                await callback.bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=kb)
            except Exception as exc:
                logger.warning("quote notification failed for admin %s (%s)", admin_id, type(exc).__name__)
        await asyncio.gather(*(send(aid) for aid in set(ADMIN_IDS) if aid))
    except Exception:
        logger.exception("quote activity recording failed")

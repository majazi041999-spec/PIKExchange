"""Administrator subscriber directory and ban controls, eight subscribers per page."""
from html import escape
from math import ceil

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin import IsAdmin, _b
from core.config import is_admin
from core.jalali import jalali_from_db
from core import subscribers as directory
from core.utils import fa_digits, money

router = Router()
router.message.filter(IsAdmin(), F.chat.type == "private")
router.callback_query.filter(IsAdmin(), F.message.chat.type == "private")
PAGE_SIZE = 8


async def show_list(msg, page=0):
    counts = await directory.stats()
    pages = max(1, ceil(counts["total"] / PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    users = await directory.list_subscribers(PAGE_SIZE, page * PAGE_SIZE)
    text = ("👥 <b>مشترکین ربات</b>\n\n"
            f"کل: <b>{fa_digits(counts['total'])}</b> | فعال: <b>{fa_digits(counts['active'])}</b> | بن‌شده: <b>{fa_digits(counts['blocked'])}</b>\n"
            f"صفحه {fa_digits(page + 1)} از {fa_digits(pages)}\n\n"
            "برای دیدن مشخصات، سابقه درخواست نرخ و بن / آنبن، کاربر را انتخاب کنید.")
    kb = InlineKeyboardBuilder()
    for user in users:
        name = (user["full_name"] or user["username"] or str(user["telegram_id"]))[:40]
        marker = "⛔️" if user["is_blocked"] else "👤"
        kb.row(_b(f"{marker} {name} · {fa_digits(user['quote_count'])} درخواست نرخ", f"subs:view:{user['telegram_id']}:{page}"))
    nav = []
    if page:
        nav.append(_b("⬅️ قبلی", f"subs:list:{page - 1}"))
    if page + 1 < pages:
        nav.append(_b("بعدی ➡️", f"subs:list:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(_b("🔄 تازه‌سازی", f"subs:list:{page}"), _b("🔙 مدیریت", "admin"))
    await msg.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")


async def show_user(msg, telegram_id, page=0):
    user = await directory.get_subscriber(telegram_id)
    if not user:
        await msg.answer("کاربر یافت نشد.")
        return
    name = escape(user["full_name"] or "بدون نام")
    username = escape("@" + user["username"]) if user["username"] else "ندارد"
    status = "⛔️ بن‌شده" if user["is_blocked"] else "✅ فعال"
    text = (f"👤 <b>{name}</b>\n\nشناسه: <code>{telegram_id}</code>\nیوزرنیم: {username}\n"
            f"وضعیت: {status}\nعضویت: {jalali_from_db(user['created_at'])}\n"
            f"درخواست نرخ: <b>{fa_digits(user['quote_count'])}</b>\n"
            f"آخرین درخواست: {jalali_from_db(user['last_quote_at']) or 'ندارد'}\n")
    quotes = await directory.recent_quotes(user["id"])
    if quotes:
        text += "\n<b>آخرین نرخ‌های دیده‌شده:</b>\n"
        for quote in quotes:
            label = quote["tier_label"] or quote["product_title"]
            text += f"• {escape(label[:100])}: {money(quote['rate'], quote['currency'])}\n  {jalali_from_db(quote['created_at'])}\n"
    kb = InlineKeyboardBuilder()
    if not is_admin(telegram_id):
        action = "unban" if user["is_blocked"] else "ban"
        kb.row(_b("✅ آنبن کاربر" if user["is_blocked"] else "⛔️ بن کاربر", f"subs:{action}:{telegram_id}:{page}"))
    else:
        text += "\n🛠 ادمین ربات"
    kb.row(_b("🔄 تازه‌سازی", f"subs:view:{telegram_id}:{page}"), _b("👥 مشترکین", f"subs:list:{page}"))
    await msg.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())


@router.message(Command("subscribers"))
async def command_subscribers(msg: Message, state: FSMContext):
    await state.clear()
    await show_list(msg)


@router.callback_query(F.data.startswith("subs:"))
async def callbacks(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    parts = cb.data.split(":")
    action = parts[1]
    if action == "list":
        await cb.answer()
        await show_list(cb.message, int(parts[2]))
        return
    if action not in ("view", "ban", "unban") or len(parts) != 4:
        return await cb.answer("گزینه نامعتبر")
    telegram_id, page = int(parts[2]), int(parts[3])
    if action in ("ban", "unban"):
        try:
            found = await directory.set_blocked(telegram_id, action == "ban")
        except ValueError as exc:
            return await cb.answer(str(exc), show_alert=True)
        if not found:
            return await cb.answer("کاربر یافت نشد.", show_alert=True)
    await cb.answer("ذخیره شد" if action != "view" else None)
    await show_user(cb.message, telegram_id, page)

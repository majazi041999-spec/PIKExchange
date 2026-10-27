"""Private administrator archive for customer bank accounts and name search."""
from html import escape
from math import ceil

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin import IsAdmin, _b
from bot.keyboards import _copy_btn
from core import card_archive as archive
from core.banks import BANKS, bank_button, bank_html, emoji_pack_url
from core.subscribers import get_subscriber
from core.utils import fa_digits

router = Router()
router.message.filter(IsAdmin(), F.chat.type == "private")
router.callback_query.filter(IsAdmin(), F.message.chat.type == "private")
PAGE_SIZE = 8


class ArchiveFlow(StatesGroup):
    search = State()
    owner = State()
    details = State()


async def show_list(msg, state, page=0):
    data = await state.get_data()
    query, user_tg_id = data.get("archive_query", ""), data.get("archive_user")
    items, count = await archive.list_cards(query, user_tg_id, page)
    pages = max(1, ceil(count / PAGE_SIZE))
    if page >= pages:
        page = pages - 1
        items, count = await archive.list_cards(query, user_tg_id, page)
    text = "🗂 <b>بایگانی کارت‌های کاربران</b>\n\n"
    if query:
        text += f"جست‌وجوی نام: <b>{escape(query)}</b>\n"
    if user_tg_id:
        profile = await get_subscriber(user_tg_id)
        text += f"کاربر: {escape(profile['full_name'] or str(user_tg_id)) if profile else user_tg_id}\n"
    text += f"{fa_digits(count)} کارت · صفحه {fa_digits(page + 1)} از {fa_digits(pages)}\n\n"
    text += "نام را انتخاب کنید تا کارت، شبا و اطلاعات حساب را ببینید و کپی کنید." if items else "هنوز کارتی در این فهرست نیست."
    kb = InlineKeyboardBuilder()
    kb.row(_b("➕ افزودن با نام", "arc:add"), _b("🔎 جست‌وجوی نام", "arc:search"))
    for item in items:
        button = bank_button(item["bank_id"], f"arc:view:{item['id']}")
        tail = (item["card_number"] or item["iban"] or item["account"] or item["phone"])[-4:]
        button.text = f"{item['owner_name'][:35]} · {BANKS[item['bank_id']]['name']} · {tail}"
        kb.row(button)
    nav = []
    if page:
        nav.append(_b("قبلی", f"arc:page:{page - 1}"))
    if page + 1 < pages:
        nav.append(_b("بعدی", f"arc:page:{page + 1}"))
    if nav:
        kb.row(*nav)
    if query or user_tg_id:
        kb.row(_b("همه کارت‌ها", "arc:home"))
    if emoji_pack_url():
        kb.row(InlineKeyboardButton(text="🎨 پک ایموجی بانک‌ها", url=emoji_pack_url()))
    kb.row(_b("🔙 مدیریت", "admin"))
    await msg.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())


async def show_card(msg, card_id):
    item = await archive.get_card(card_id)
    if not item:
        return await msg.answer("کارت یافت نشد.")
    text = f"{bank_html(item['bank_id'])}\n\n👤 <b>{escape(item['owner_name'])}</b>\n"
    kb = InlineKeyboardBuilder()
    for field, label in (("card_number", "کارت"), ("iban", "شبا"), ("account", "حساب"), ("phone", "تلفن")):
        if item[field]:
            text += f"{label}: <code>{escape(item[field])}</code>\n"
            button = _copy_btn(f"📋 کپی {label}", item[field])
            if button:
                kb.row(button)
    if item["note"]:
        text += f"\n📝 {escape(item['note'])}"
    if item["user_tg_id"]:
        text += f"\nشناسه کاربر: <code>{item['user_tg_id']}</code>"
        kb.row(_b("👤 پروفایل کاربر", f"subs:view:{item['user_tg_id']}:0"))
    kb.row(_b("✏️ ویرایش", f"arc:edit:{card_id}"), _b("🗑 حذف از بایگانی", f"arc:delete:{card_id}"))
    kb.row(_b("🔙 فهرست", "arc:page:0"))
    await msg.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())


async def choose_bank(msg, state, country=None):
    data = await state.get_data()
    kb = InlineKeyboardBuilder()
    if country:
        buttons = [bank_button(bank_id, f"arc:bank:{bank_id}") for bank_id, bank in BANKS.items() if bank["country"] == country]
        for index in range(0, len(buttons), 2):
            kb.row(*buttons[index:index + 2])
        kb.row(_b("کشور دیگر", "arc:countries"))
    else:
        kb.row(_b("🇮🇷 بانک‌های ایران", "arc:country:ir"), _b("🇷🇺 بانک‌های روسیه", "arc:country:ru"))
    kb.row(_b("انصراف", "arc:home"))
    await msg.answer(f"👤 <b>{escape(data['owner_name'])}</b>\nبانک را انتخاب کنید:", parse_mode="HTML", reply_markup=kb.as_markup())


@router.message(Command("cards"))
async def command_cards(msg: Message, state: FSMContext):
    await state.clear()
    await show_list(msg, state)


@router.callback_query(F.data.startswith("arc:"))
async def callbacks(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    action = parts[1]
    await cb.answer()
    if action == "home":
        await state.clear()
        return await show_list(cb.message, state)
    if action == "user":
        await state.clear()
        await state.update_data(archive_user=int(parts[2]))
        return await show_list(cb.message, state)
    if action == "page":
        await state.set_state(None)
        return await show_list(cb.message, state, max(0, int(parts[2])))
    if action == "view":
        await state.set_state(None)
        return await show_card(cb.message, int(parts[2]))
    if action == "search":
        await state.set_state(ArchiveFlow.search)
        return await cb.message.answer("نام یا بخشی از نام صاحب حساب را بفرستید؛ فارسی، لاتین یا روسی.\nانصراف: /cancel")
    if action in ("add", "edit"):
        data = await state.get_data()
        user_tg_id = data.get("archive_user")
        item = await archive.get_card(int(parts[2])) if action == "edit" else None
        if action == "edit" and item is None:
            return await cb.message.answer("کارت یافت نشد.")
        await state.update_data(edit_card_id=item["id"] if item else None, link_user=item["user_tg_id"] if item else user_tg_id,
                                owner_name=None, previous_owner=None)
        profile = await get_subscriber(user_tg_id) if user_tg_id else None
        if profile and action == "add" and profile["full_name"]:
            await state.update_data(owner_name=profile["full_name"])
            await state.set_state(None)
            return await choose_bank(cb.message, state)
        await state.set_state(ArchiveFlow.owner)
        prompt = "نام و نام خانوادگی صاحب حساب را بفرستید."
        if item:
            prompt += f"\nنام فعلی: {item['owner_name']}\nبرای حفظ همین نام: -"
            await state.update_data(previous_owner=item["owner_name"])
        return await cb.message.answer(prompt + "\nانصراف: /cancel", parse_mode=None)
    if action in ("country", "countries", "bank"):
        data = await state.get_data()
        if not data.get("owner_name"):
            return await cb.message.answer("ابتدا «افزودن با نام» را بزنید.")
        if action in ("country", "countries"):
            return await choose_bank(cb.message, state, parts[2] if action == "country" else None)
        bank_id = parts[2]
        if bank_id not in BANKS:
            return await cb.message.answer("بانک نامعتبر است.")
        await state.update_data(bank_id=bank_id)
        await state.set_state(ArchiveFlow.details)
        return await cb.message.answer(
            f"{bank_html(bank_id)}\n👤 <b>{escape(data['owner_name'])}</b>\n\n"
            "شماره کارت را تنها بفرستید، یا اطلاعات را با این قالب وارد کنید:\n"
            "<pre>کارت: شماره کارت\nشبا: IR...\nحساب: شماره حساب\nتلفن: +7...\nتوضیح: توضیح دلخواه</pre>\n"
            "فقط خط‌هایی را که دارید بنویسید.\nانصراف: /cancel", parse_mode="HTML")
    if action == "delete":
        kb = InlineKeyboardBuilder()
        kb.row(_b("بله، حذف از بایگانی", f"arc:confirmdelete:{parts[2]}"), _b("بازگشت", f"arc:view:{parts[2]}"))
        return await cb.message.answer("این کارت از بایگانی حذف شود؟", reply_markup=kb.as_markup())
    if action == "confirmdelete":
        await archive.delete_card(int(parts[2]))
        await state.set_state(None)
        await cb.message.answer("از بایگانی حذف شد.")
        return await show_list(cb.message, state)


@router.message(ArchiveFlow.search, ~F.text.startswith("/"))
async def search(msg: Message, state: FSMContext):
    query = (msg.text or "").strip()
    if not query or len(query) > 100:
        return await msg.answer("نام معتبر، حداکثر ۱۰۰ کاراکتر، بفرستید.")
    await state.update_data(archive_query=query)
    await state.set_state(None)
    await show_list(msg, state)


@router.message(ArchiveFlow.owner, ~F.text.startswith("/"))
async def owner(msg: Message, state: FSMContext):
    data = await state.get_data()
    name = (msg.text or "").strip()
    if name == "-":
        name = data.get("previous_owner", "")
    if not name or len(name) > 100 or "\n" in name:
        return await msg.answer("نام معتبر، در یک خط و حداکثر ۱۰۰ کاراکتر، بفرستید.")
    await state.update_data(owner_name=name)
    await state.set_state(None)
    await choose_bank(msg, state)


@router.message(ArchiveFlow.details, ~F.text.startswith("/"))
async def details(msg: Message, state: FSMContext):
    data = await state.get_data()
    try:
        parsed = archive.parse_details(msg.text or "", data["bank_id"])
        card_id = await archive.save_card(data["owner_name"], data["bank_id"], parsed, msg.from_user.id,
                                          data.get("link_user"), data.get("edit_card_id"))
    except ValueError as exc:
        return await msg.answer(str(exc), parse_mode=None)
    await state.set_state(None)
    await state.update_data(edit_card_id=None)
    await msg.answer("✅ کارت در بایگانی ذخیره شد.")
    await show_card(msg, card_id)

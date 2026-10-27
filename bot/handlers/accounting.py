"""Private administrator ledger: incoming toman and outgoing roubles."""
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin import IsAdmin, _b
from core import accounting as ledger
from core.jalali import jalali_from_db
from core.utils import fa_digits

router = Router()
router.message.filter(IsAdmin(), F.chat.type == "private")
router.callback_query.filter(IsAdmin(), F.message.chat.type == "private")
PAGE_SIZE = 8


class AccountingFlow(StatesGroup):
    entry = State()
    editing = State()


STATUS = {"draft": "پیش‌نویس", "pending": "در انتظار وصول", "posted": "قطعی", "void": "باطل"}
FIELDS = {"name": "نام", "amount": "مبلغ", "note": "شرح"}


def _kb(rows):
    kb = InlineKeyboardBuilder()
    for row in rows:
        kb.row(*[_b(label, callback) for label, callback in row])
    return kb.as_markup()


def report_text(items, sums, offset=0):
    lines = [
        "📒 <b>دفتر ورودی و خروجی</b>",
        f"🟢 جمع ورودی: <b>{ledger.display_amount(sums['IRR']['in'], 'IRR')}</b>",
        f"🔴 جمع خروجی: <b>{ledger.display_amount(sums['RUB']['out'], 'RUB')}</b>",
        "تومان و روبل جداگانه گزارش می‌شوند.",
        "جمع‌ها فقط شامل اسناد قطعی هستند.",
    ]
    if sums['IRR']['out']:
        lines.append(f"خروجی قدیمی به تومان: <b>{ledger.display_amount(sums['IRR']['out'])}</b>")
    if sums['RUB']['in']:
        lines.append(f"ورودی روبلی: <b>{ledger.display_amount(sums['RUB']['in'], 'RUB')}</b>")
    if sums["pending"]:
        lines.append(f"⏳ در انتظار وصول: {fa_digits(sums['pending'])} سند")
    lines.extend(["", f"<b>فهرست اسناد · صفحه {fa_digits(offset // PAGE_SIZE + 1)}</b>"])
    if not items:
        lines.append("هنوز سندی در این صفحه وجود ندارد.")
    for entry in items:
        icon, direction = ("🟢", "ورودی") if entry["direction"] == "in" else ("🔴", "خروجی")
        name = escape(ledger.entry_name(entry)[:80])
        lines.extend([
            "━━━━━━━━━━━━━━━━",
            f"{icon} <b>{name}</b> — {direction}",
            f"💵 <b>{ledger.display_entry(entry)}</b>",
            f"سند {fa_digits(entry['id'])} · {STATUS[entry['status']]}",
            f"🗓 {escape(jalali_from_db(entry['created_at']))}",
        ])
    return "\n".join(lines)


async def show_report(msg, offset=0):
    items = await ledger.list_entries(PAGE_SIZE + 1, offset)
    has_more = len(items) > PAGE_SIZE
    items = items[:PAGE_SIZE]
    rows = [[("➕ ثبت ورودی", "acc:new:in"), ("➖ ثبت خروجی", "acc:new:out")]]
    buttons = [(f"سند {fa_digits(e['id'])}", f"acc:view:{e['id']}") for e in items]
    rows.extend([buttons[i:i + 2] for i in range(0, len(buttons), 2)])
    navigation = []
    if offset:
        navigation.append(("جدیدتر", f"acc:list:{max(0, offset - PAGE_SIZE)}"))
    if has_more:
        navigation.append(("قدیمی‌تر", f"acc:list:{offset + PAGE_SIZE}"))
    if navigation:
        rows.append(navigation)
    rows.extend([[("🔄 تازه‌سازی", "acc:home")], [("🔙 مدیریت", "admin")]])
    await msg.answer(report_text(items, await ledger.totals(), offset), parse_mode="HTML", reply_markup=_kb(rows))


@router.message(Command("accounting"))
async def command_home(msg: Message, state: FSMContext):
    await state.clear()
    await show_report(msg)


@router.callback_query(F.data == "acc:home")
async def home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await show_report(cb.message)


@router.callback_query(F.data.startswith("acc:new:"))
async def new_entry(cb: CallbackQuery, state: FSMContext):
    direction = cb.data.rsplit(":", 1)[1]
    if direction not in ("in", "out"):
        return await cb.answer("گزینه نامعتبر")
    await state.clear()
    await state.set_state(AccountingFlow.entry)
    await state.update_data(direction=direction)
    await cb.answer()
    title = "ورودی" if direction == "in" else "خروجی"
    unit = ledger.CURRENCIES[ledger.currency_for_direction(direction)]['label']
    example = '۱۰۰٬۰۰۰٬۰۰۰' if direction == 'in' else '۳۰٬۰۰۰'
    await cb.message.answer(
        f"✍️ ثبت {title} به {unit}\n\nنام و مبلغ را به این شکل بفرستید:\nمیرحسین - {example}\n\n"
        f"مبلغ این سند به {unit} است. نوشتن «{unit}» اختیاری است.\nبرای انصراف /cancel را بزنید.",
        parse_mode=None, reply_markup=_kb([[("بازگشت", "acc:home")]]))


@router.message(AccountingFlow.entry, ~F.text.startswith("/"))
async def receive_entry(msg: Message, state: FSMContext):
    data = await state.get_data()
    try:
        entry_id = await ledger.create_manual_draft(msg.from_user.id, data["direction"], msg.text or "")
    except ValueError as exc:
        await msg.answer(str(exc), parse_mode=None)
        return
    await state.clear()
    await show_entry(msg, entry_id)


async def show_entry(msg, entry_id):
    entry = await ledger.get_entry(entry_id)
    if not entry:
        await msg.answer("سند یافت نشد.")
        return
    direction = "ورودی" if entry["direction"] == "in" else "خروجی"
    text = (f"📒 <b>سند {fa_digits(entry_id)} · {direction}</b>\n\n"
            f"👤 <b>{escape(ledger.entry_name(entry))}</b>\n"
            f"💵 <b>{ledger.display_entry(entry)}</b>\n"
            f"وضعیت: {STATUS[entry['status']]}\n"
            f"🗓 {escape(jalali_from_db(entry['created_at']))}")
    if entry["note"]:
        text += f"\n📝 {escape(entry['note'])}"
    suffix = f"{entry_id}:{entry['revision']}"
    rows = []
    if entry["status"] == "draft":
        text += "\n\nنام، مبلغ و جهت را بررسی و ثبت را تأیید کنید."
        if not entry['amount_minor']:
            text += f"\nمبلغ جدید را به {entry['currency_label']} وارد کنید."
        rows.append([(f"✏️ {'مبلغ به ' + entry['currency_label'] if field == 'amount' else title}", f"acc:edit:{field}:{suffix}") for field, title in FIELDS.items()])
        rows.append([("🔄 تغییر ورودی/خروجی", f"acc:do:direction:{suffix}")])
        if entry["receipt"].get("status", "manual") in ("success", "manual"):
            rows.append([("✅ تأیید و ثبت", f"acc:do:posted:{suffix}")])
        rows.append([("⏳ ثبت در انتظار وصول", f"acc:do:pending:{suffix}")])
    if entry["status"] == "pending":
        rows.append([("✅ تأیید وصول و ثبت قطعی", f"acc:do:settle:{suffix}")])
    if entry["status"] != "void":
        rows.append([("ابطال سند", f"acc:void:{suffix}")])
    rows.append([("📋 فهرست و جمع‌ها", "acc:home")])
    await msg.answer(text, parse_mode="HTML", reply_markup=_kb(rows))


@router.callback_query(F.data.startswith("acc:list:"))
async def entries(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    offset = max(0, int(cb.data.rsplit(":", 1)[1]))
    await cb.answer()
    await show_report(cb.message, offset)


@router.callback_query(F.data.startswith("acc:view:"))
async def view(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await show_entry(cb.message, int(cb.data.rsplit(":", 1)[1]))


@router.callback_query(F.data.startswith("acc:edit:"))
async def edit(cb: CallbackQuery, state: FSMContext):
    _, _, field, entry_id, revision = cb.data.split(":")
    if field == 'amount_rial':
        field = 'amount'
    if field not in FIELDS:
        return await cb.answer("این گزینه تغییر کرده؛ سند را دوباره باز کنید.", show_alert=True)
    entry = await ledger.get_entry(int(entry_id))
    if not entry or entry["status"] != "draft" or entry["revision"] != int(revision):
        return await cb.answer("سند را دوباره باز کنید.", show_alert=True)
    await state.set_state(AccountingFlow.editing)
    await state.update_data(entry_id=int(entry_id), revision=int(revision), field=field)
    await cb.answer()
    prompts = {"amount": f"مبلغ جدید به {entry['currency_label']} را بفرستید.",
               "name": "نام جدید را بفرستید.", "note": "شرح را بفرستید (برای خالی کردن: -)."}
    await cb.message.answer(prompts[field] + "\nانصراف: /cancel", parse_mode=None)


@router.message(AccountingFlow.editing, ~F.text.startswith("/"))
async def edit_value(msg: Message, state: FSMContext):
    data = await state.get_data()
    raw = (msg.text or "").strip()
    if not raw or len(raw) > 200:
        return await msg.answer("متن یا عدد معتبر، حداکثر ۲۰۰ کاراکتر بفرستید.")
    try:
        value = "" if raw == "-" else raw
        await ledger.change_entry(data["entry_id"], data["revision"], msg.from_user.id, data["field"], value)
    except ValueError as exc:
        await msg.answer(str(exc), parse_mode=None)
        return
    await state.clear()
    await show_entry(msg, data["entry_id"])


@router.callback_query(F.data.startswith("acc:void:"))
async def ask_void(cb: CallbackQuery):
    suffix = cb.data.removeprefix("acc:void:")
    await cb.answer()
    await cb.message.answer("سند باطل شود؟ سابقه آن حفظ می‌شود و از جمع حساب‌ها خارج خواهد شد.",
                            reply_markup=_kb([[("بله، ابطال", f"acc:do:void:{suffix}")], [("بازگشت", f"acc:view:{suffix.split(':')[0]}")]]))


@router.callback_query(F.data.startswith("acc:do:"))
async def action(cb: CallbackQuery, state: FSMContext):
    _, _, operation, entry_id, revision = cb.data.split(":")
    if operation not in ("posted", "pending", "settle", "void", "direction"):
        return await cb.answer("گزینه نامعتبر")
    value = None
    if operation == "direction":
        entry = await ledger.get_entry(int(entry_id))
        value = "out" if entry and entry["direction"] == "in" else "in"
    try:
        await ledger.change_entry(int(entry_id), int(revision), cb.from_user.id, operation, value)
    except ledger.LedgerError as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await cb.answer("جهت و واحد تغییر کرد؛ مبلغ جدید را وارد کنید." if operation == 'direction' else "ذخیره شد")
    if operation == "direction":
        await show_entry(cb.message, int(entry_id))
    else:
        await show_report(cb.message)


@router.callback_query(F.data == "acc:manual")
@router.callback_query(F.data.startswith("acc:photo:"))
async def old_accounting_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("ثبت حسابداری با نام و مبلغ است؛ ورودی تومان و خروجی روبل.", show_alert=True)
    await show_report(cb.message)

"""Administrator-only receipt review and incoming/outgoing ledger."""
import hashlib
import io
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin import IsAdmin, _b
from core import accounting as ledger
from core.receipts import MAX_IMAGE_BYTES, ReceiptReadError, image_mime, ocr_available, read_receipt

logger = logging.getLogger("pik.accounting")
router = Router()
router.message.filter(IsAdmin(), F.chat.type == "private")
router.callback_query.filter(IsAdmin(), F.message.chat.type == "private")


class AccountingFlow(StatesGroup):
    receipt = State()
    editing = State()


STATUS = {"draft": "پیش‌نویس", "pending": "در انتظار وصول", "posted": "قطعی", "void": "باطل"}
RECEIPT_STATUS = {"success": "انتقال موفق در تصویر", "pending": "در انتظار انتقال", "failed": "ناموفق", "unknown": "نامشخص", "manual": "ورود دستی"}
FIELDS = {"amount_rial": "مبلغ", "reference": "شماره پیگیری", "sender": "فرستنده", "recipient": "گیرنده",
          "date": "تاریخ فیش", "source_bank": "بانک مبدا", "destination_bank": "بانک مقصد", "note": "شرح"}


def _kb(rows):
    kb = InlineKeyboardBuilder()
    for row in rows:
        kb.row(*[_b(label, callback) for label, callback in row])
    return kb.as_markup()


async def show_home(msg):
    sums = await ledger.totals()
    text = ("📒 حسابداری (تومان)\n\n"
            f"ورودی قطعی: {ledger.display_amount(sums['in'])}\n"
            f"خروجی قطعی: {ledger.display_amount(sums['out'])}\n"
            f"مانده: {ledger.display_amount(sums['in'] - sums['out'])}\n"
            f"اسناد در انتظار وصول: {sums['pending']}\n\n"
            "مانده فقط از اسناد قطعی این دفتر محاسبه می‌شود؛ به کیف پول کاربران متصل نیست.\n"
            + ("خواندن عکس فعال است؛ تصویر برای استخراج اطلاعات به OpenAI ارسال می‌شود."
               if ocr_available() else "خواندن خودکار عکس هنوز تنظیم نشده؛ ورود دستی فعال است."))
    await msg.answer(text, parse_mode=None, reply_markup=_kb([
        [("➕ ورودی", "acc:new:in"), ("➖ خروجی", "acc:new:out")],
        [("📋 اسناد و پیش‌نویس‌ها", "acc:list:0")], [("🔙 مدیریت", "admin")],
    ]))


@router.message(Command("accounting"))
async def command_home(msg: Message, state: FSMContext):
    await state.clear()
    await show_home(msg)


@router.callback_query(F.data == "acc:home")
async def home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await show_home(cb.message)


@router.callback_query(F.data.startswith("acc:new:"))
async def new_entry(cb: CallbackQuery, state: FSMContext):
    direction = cb.data.rsplit(":", 1)[1]
    if direction not in ("in", "out"):
        return await cb.answer("گزینه نامعتبر")
    await state.clear()
    await state.set_state(AccountingFlow.receipt)
    await state.update_data(direction=direction)
    await cb.answer()
    await cb.message.answer(
        "عکس فیش را بفرستید (JPG / PNG / WebP، حداکثر ۱۰ مگابایت).\n"
        "برای ثبت دستی دکمه زیر را بزنید. برای انصراف /cancel.\n"
        + ("عکس برای خواندن به OpenAI ارسال می‌شود." if ocr_available() else "خواندن خودکار هنوز تنظیم نشده است؛ عکس قابل پیوست و اطلاعات قابل ورود دستی است."),
        parse_mode=None, reply_markup=_kb([[("✍️ ورود دستی", "acc:manual")], [("بازگشت", "acc:home")]]))


@router.callback_query(AccountingFlow.receipt, F.data == "acc:manual")
async def manual(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    entry_id = await ledger.create_draft(cb.from_user.id, data["direction"], {"status": "manual"})
    await state.set_state(AccountingFlow.editing)
    await state.update_data(entry_id=entry_id, revision=0, field="amount_rial")
    await cb.answer()
    await cb.message.answer("مبلغ و واحد را بفرستید؛ مثال: ۱۰۰٬۰۰۰٬۰۰۰ تومان یا ۱٬۰۰۰٬۰۰۰٬۰۰۰ ریال.", parse_mode=None)


@router.message(AccountingFlow.receipt, ~F.text.startswith("/"))
async def receive_receipt(msg: Message, state: FSMContext):
    data = await state.get_data()
    attachment = msg.photo[-1] if msg.photo else msg.document
    if not attachment:
        await msg.answer("عکس فیش را ارسال کنید یا «ورود دستی» را بزنید.")
        return
    if not attachment.file_size or attachment.file_size > MAX_IMAGE_BYTES:
        await msg.answer("اندازه فایل باید مشخص و حداکثر ۱۰ مگابایت باشد.")
        return
    await msg.answer("در حال دریافت و بررسی فیش…")
    try:
        buffer = io.BytesIO()
        await msg.bot.download(attachment, destination=buffer)
        content = buffer.getvalue()
        if len(content) > MAX_IMAGE_BYTES:
            raise ReceiptReadError("حداکثر اندازه عکس ۱۰ مگابایت است.")
        image_mime(content)
    except Exception:
        await msg.answer("دریافت عکس ممکن نشد؛ یک عکس JPG، PNG یا WebP کوچک‌تر ارسال کنید.")
        return
    receipt = {"status": "unknown"}
    try:
        receipt = await read_receipt(content)
    except ReceiptReadError as exc:
        receipt["warning"] = str(exc)
    entry_id = await ledger.create_draft(msg.from_user.id, data["direction"], receipt,
                                         attachment.file_id, hashlib.sha256(content).hexdigest())
    await state.clear()
    await show_entry(msg, entry_id)


async def show_entry(msg, entry_id):
    entry = await ledger.get_entry(entry_id)
    if not entry:
        await msg.answer("سند یافت نشد.")
        return
    receipt = entry["receipt"]
    label = "ورودی" if entry["direction"] == "in" else "خروجی"
    text = (f"📒 سند #{entry_id} — {label} — {STATUS[entry['status']]}\n"
            f"مبلغ: {ledger.display_amount(entry['amount_rial']) if entry['amount_rial'] else 'نیازمند اصلاح مبلغ و واحد'}\n"
            f"مبلغ خام فیش: {receipt.get('amount') or '—'} { {'rial': 'ریال', 'toman': 'تومان'}.get(receipt.get('currency'), '')}\n"
            f"وضعیت فیش: {RECEIPT_STATUS.get(receipt.get('status', 'manual'), 'نامشخص')}\n"
            f"فرستنده: {receipt.get('sender') or '—'}\nگیرنده: {receipt.get('recipient') or '—'}\n"
            f"بانک مبدا: {receipt.get('source_bank') or '—'}\nبانک مقصد: {receipt.get('destination_bank') or '—'}\n"
            f"تاریخ فیش: {receipt.get('date') or '—'}\nپیگیری: {entry['reference'] or '—'}\n"
            f"شرح: {entry['note'] or '—'}\nثبت دفتر: {entry['created_at']}\n")
    if receipt.get("warning"):
        text += f"\n⚠️ {receipt['warning']}\n"
    suffix = f"{entry_id}:{entry['revision']}"
    rows = []
    if entry["status"] == "draft":
        text += "\nاطلاعات را با اصل فیش تطبیق دهید؛ عکس به‌تنهایی تأیید وصول بانکی نیست."
        for field, title in FIELDS.items():
            rows.append([(f"✏️ {title}", f"acc:edit:{field}:{suffix}")])
        rows.append([("🔄 تغییر ورودی/خروجی", f"acc:do:direction:{suffix}")])
        if receipt.get("status", "manual") in ("success", "manual"):
            rows.append([("✅ تأیید وصول و ثبت قطعی", f"acc:do:posted:{suffix}")])
        rows.append([("⏳ ذخیره در انتظار وصول", f"acc:do:pending:{suffix}")])
    if entry["status"] == "pending":
        rows.append([("✅ وصول را بررسی کردم — ثبت قطعی", f"acc:do:settle:{suffix}")])
    if entry["status"] != "void":
        rows.append([("ابطال سند", f"acc:void:{suffix}")])
    if entry["file_id"]:
        rows.append([("🖼 مشاهده اصل فیش", f"acc:photo:{entry_id}")])
    rows.extend([[("فهرست اسناد", "acc:list:0")], [("حسابداری", "acc:home")]])
    await msg.answer(text, parse_mode=None, reply_markup=_kb(rows))


@router.callback_query(F.data.startswith("acc:list:"))
async def entries(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    offset = max(0, int(cb.data.rsplit(":", 1)[1]))
    items = await ledger.list_entries(12, offset)
    rows = [[(f"#{e['id']} | {'ورودی' if e['direction'] == 'in' else 'خروجی'} | {ledger.display_amount(e['amount_rial'])} | {STATUS[e['status']]}",
              f"acc:view:{e['id']}")] for e in items]
    if len(items) == 12:
        rows.append([("قدیمی‌تر", f"acc:list:{offset + 12}")])
    if offset:
        rows.append([("جدیدتر", f"acc:list:{max(0, offset - 12)}")])
    rows.append([("حسابداری", "acc:home")])
    await cb.answer()
    await cb.message.answer("اسناد حسابداری" if items else "هنوز سندی وجود ندارد.", reply_markup=_kb(rows))


@router.callback_query(F.data.startswith("acc:view:"))
async def view(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await show_entry(cb.message, int(cb.data.rsplit(":", 1)[1]))


@router.callback_query(F.data.startswith("acc:photo:"))
async def photo(cb: CallbackQuery):
    entry = await ledger.get_entry(int(cb.data.rsplit(":", 1)[1]))
    await cb.answer()
    if entry and entry["file_id"]:
        # Telegram file IDs retain the original photo/document type.
        try:
            await cb.message.answer_photo(entry["file_id"])
        except Exception:
            await cb.message.answer_document(entry["file_id"])


@router.callback_query(F.data.startswith("acc:edit:"))
async def edit(cb: CallbackQuery, state: FSMContext):
    _, _, field, entry_id, revision = cb.data.split(":")
    if field not in FIELDS:
        return await cb.answer("گزینه نامعتبر")
    entry = await ledger.get_entry(int(entry_id))
    if not entry or entry["status"] != "draft" or entry["revision"] != int(revision):
        return await cb.answer("سند را دوباره باز کنید.", show_alert=True)
    await state.set_state(AccountingFlow.editing)
    await state.update_data(entry_id=int(entry_id), revision=int(revision), field=field)
    await cb.answer()
    prompt = "مبلغ با واحد: مثلاً ۱۰۰٬۰۰۰ تومان یا ۱٬۰۰۰٬۰۰۰ ریال." if field == "amount_rial" else f"{FIELDS[field]} را بفرستید (برای خالی کردن: -)."
    await cb.message.answer(prompt + "\nانصراف: /cancel", parse_mode=None)


@router.message(AccountingFlow.editing, ~F.text.startswith("/"))
async def edit_value(msg: Message, state: FSMContext):
    data = await state.get_data()
    raw = (msg.text or "").strip()
    if not raw or len(raw) > 200:
        return await msg.answer("متن یا عدد معتبر، حداکثر ۲۰۰ کاراکتر بفرستید.")
    try:
        value = "" if raw == "-" else raw
        if data["field"] == "amount_rial":
            currency = "toman" if raw.endswith("تومان") else "rial" if raw.endswith("ریال") else None
            if not currency:
                raise ValueError("واحد ریال یا تومان را در انتهای مبلغ بنویسید.")
            value = ledger.parse_amount(raw.removesuffix("تومان").removesuffix("ریال").strip(), currency)
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
    await cb.answer("ذخیره شد")
    await show_entry(cb.message, int(entry_id))

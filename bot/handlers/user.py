"""هندلرهای کاربر عادی: منو، مشاهده نرخ، قوانین، دریافت کارت و ارسال فیش."""
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.config import ADMIN_IDS, RATE_VALIDITY_MINUTES, is_admin
from core.db import (
    create_transaction,
    get_or_create_user,
    get_setting,
    get_transaction,
    get_user_transactions,
    update_transaction,
)
from aiogram.filters import Command
from core.cards import get_active_card
from core.products import back_target, get_category, get_product
from core.rates import compute_simple_rate, compute_tier_rate
from core.texts import get_text
from core.config import SUPPORT_USERNAME
from core.utils import fa_digits, money, num, toman
from bot.keyboards import (
    agree_kb,
    back_menu_kb,
    card_kb,
    category_kb,
    admin_review_kb,
    contact_user_kb,
    main_menu,
    payout_kb,
    support_kb,
    suspended,
    suspended_kb,
    tiers_kb,
)
from bot.states import PayoutFlow, ReceiptFlow

logger = logging.getLogger("pik.user")
router = Router()

STATUS_LABELS = {
    "awaiting_receipt": "⏳ در انتظار ارسال فیش",
    "receipt_sent": "🧾 فیش ارسال شد (در حال بررسی)",
    "completed": "✅ تکمیل‌شده",
    "canceled": "❌ رد/لغو شده",
}


async def _send_menu(target: Message, uid: int, greeting: bool = True):
    welcome = await get_text("text_welcome")
    kb = await main_menu(is_admin(uid))
    await target.answer(welcome, reply_markup=kb, parse_mode="Markdown")


@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await get_or_create_user(msg.from_user.id, msg.from_user.username, msg.from_user.full_name)
    await _send_menu(msg, msg.from_user.id)


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = await main_menu(is_admin(cb.from_user.id))
    welcome = await get_text("text_welcome")
    try:
        await cb.message.edit_text(welcome, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        await cb.message.answer(welcome, reply_markup=kb, parse_mode="Markdown")
    await cb.answer()


# لغو باید قبل از هندلرهای حالت (state) ثبت شود تا در هر مرحله‌ای کار کند
@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("❌ عملیات لغو شد.", reply_markup=await main_menu(is_admin(msg.from_user.id)))


# ─────────────────────────── مشاهده محصول / نرخ ───────────────────────────

async def _rate_block(product: dict, rate: int, tier_label: str = "") -> str:
    unit = product.get("unit") or ("روبل" if product.get("base") == "rub" else "دلار")
    currency = product.get("currency", "تومان")
    head = product["title"]
    if tier_label:
        head += f"\n📊 {tier_label}"
    rules = await get_text("text_rules")
    return (
        f"{head}\n\n"
        f"💱 نرخ لحظه‌ای هر {unit}: *{money(rate, currency)}*\n"
        f"⏱ اعتبار نرخ: {fa_digits(RATE_VALIDITY_MINUTES)} دقیقه\n\n"
        f"{rules}"
    )


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(cb: CallbackQuery):
    cid = cb.data.split(":", 1)[1]
    cat = get_category(cid)
    if not cat:
        await cb.answer("این دسته در دسترس نیست.", show_alert=True)
        return
    text = f"{cat['title']}\n\nلطفاً یکی از گزینه‌ها را انتخاب کنید:"
    kb = await category_kb(cid)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("p:"))
async def cb_product(cb: CallbackQuery):
    pid = cb.data.split(":", 1)[1]
    product = await get_product(pid)
    if not product:
        await cb.answer("این گزینه در دسترس نیست.", show_alert=True)
        return

    back = back_target(pid)

    if product["type"] == "rub_tiered":
        text = (
            f"{product['title']}\n\n"
            "لطفاً نوع فیش و حجم معامله‌ی خود را انتخاب کنید تا نرخ لحظه‌ای اعلام شود:"
        )
        kb = tiers_kb(pid, product["tiers"], back_data=back)
        try:
            await cb.message.edit_text(text, reply_markup=kb)
        except Exception:
            await cb.message.answer(text, reply_markup=kb)
        await cb.answer()
        return

    # rub_single یا simple → مستقیم نرخ + قوانین
    await cb.answer("در حال دریافت نرخ لحظه‌ای…")
    if product["type"] == "rub_single":
        rate = await compute_tier_rate(product, {"mult": product.get("mult", 1.0)})
        label = product.get("label", "")
    else:
        rate = await compute_simple_rate(product)
        label = ""
    if not rate:
        await cb.message.answer(
            "⚠️ در حال حاضر امکان دریافت نرخ لحظه‌ای نیست. لطفاً کمی بعد دوباره تلاش کنید یا با پشتیبانی در تماس باشید.",
            reply_markup=back_menu_kb(),
        )
        return
    text = await _rate_block(product, rate, label)
    try:
        await cb.message.edit_text(text, reply_markup=agree_kb(pid, back_data=back), parse_mode="Markdown")
    except Exception:
        await cb.message.answer(text, reply_markup=agree_kb(pid, back_data=back), parse_mode="Markdown")


@router.callback_query(F.data.startswith("tier:"))
async def cb_tier(cb: CallbackQuery):
    _, pid, tier_key = cb.data.split(":", 2)
    product = await get_product(pid)
    if not product or product["type"] != "rub_tiered":
        await cb.answer("این گزینه در دسترس نیست.", show_alert=True)
        return
    tier = next((t for t in product["tiers"] if t["key"] == tier_key), None)
    if not tier:
        await cb.answer("پله انتخاب‌شده معتبر نیست.", show_alert=True)
        return

    await cb.answer("در حال دریافت نرخ لحظه‌ای…")
    rate = await compute_tier_rate(product, tier)
    if not rate:
        await cb.message.answer(
            "⚠️ در حال حاضر امکان دریافت نرخ لحظه‌ای نیست. لطفاً کمی بعد دوباره تلاش کنید.",
            reply_markup=back_menu_kb(),
        )
        return
    text = await _rate_block(product, rate, tier["label"])
    # بازگشت از صفحهٔ نرخِ یک پله → به فهرست پله‌ها (تا کاربر بتواند پلهٔ دیگری بزند)
    kb = agree_kb(pid, tier_key, back_data=f"p:{pid}")
    try:
        await cb.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        await cb.message.answer(text, reply_markup=kb, parse_mode="Markdown")


# ─────────────────────────── موافقت + دریافت کارت ───────────────────────────

async def _confirm_text(product: dict, rate: int, tier_label: str, tx_id: int) -> str:
    """پیام اول: تأیید موافقت + نرخ + راهنما (اطلاعات کارت در پیام بعدی می‌آید)."""
    unit = product.get("unit") or ("روبل" if product.get("base") == "rub" else "دلار")
    currency = product.get("currency", "تومان")
    lines = [
        "✅ *موافقت شما با قوانین ثبت شد.*",
        "",
        f"🧾 شماره معامله: `{fa_digits(tx_id)}`",
        f"🔸 {product['title']}",
    ]
    if tier_label:
        lines.append(f"📊 {tier_label}")
    lines.append(f"💱 نرخ توافقی هر {unit}: *{money(rate, currency)}*")
    lines.append("")
    lines.append("💳 اطلاعات کارت واریز را در *پیام بعدی* برای شما ارسال می‌کنیم.")
    lines.append("پس از واریز، دکمه‌ی «📤 ارسال فیش واریز» را بزنید.")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("agree:"))
async def cb_agree(cb: CallbackQuery):
    _, pid, tier_key = cb.data.split(":", 2)
    product = await get_product(pid)
    if not product:
        await cb.answer("این گزینه در دسترس نیست.", show_alert=True)
        return

    tier_label = ""
    if product["type"] == "rub_tiered":
        tier = next((t for t in product["tiers"] if t["key"] == tier_key), None)
        if not tier:
            await cb.answer("پله انتخاب‌شده معتبر نیست.", show_alert=True)
            return
        rate = await compute_tier_rate(product, tier)
        tier_label = tier["label"]
    elif product["type"] == "rub_single":
        rate = await compute_tier_rate(product, {"mult": product.get("mult", 1.0)})
        tier_label = product.get("label", "")
    else:
        rate = await compute_simple_rate(product)

    if not rate:
        await cb.answer("دریافت نرخ ناموفق بود. دوباره تلاش کنید.", show_alert=True)
        return

    user = await get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.full_name)
    unit = product.get("unit") or ("روبل" if product.get("base") == "rub" else "دلار")
    tx_id = await create_transaction(
        user_id=user["id"],
        telegram_id=cb.from_user.id,
        product_id=pid,
        product_title=product["title"],
        rate=rate,
        tier_key=tier_key if tier_key != "-" else "",
        tier_label=tier_label,
        unit=unit,
        currency=product.get("currency", "تومان"),
    )
    currency = product.get("currency", "تومان")
    unit_txt = product.get("unit") or ("روبل" if product.get("base") == "rub" else "دلار")

    # حالت معلق: به‌جای نمایش کارت، کاربر را به هماهنگی مستقیم با پشتیبانی هدایت کن
    if suspended():
        head = [
            "✅ *درخواست شما ثبت شد.*",
            "",
            f"🧾 شماره معامله: `{fa_digits(tx_id)}`",
            f"🔸 {product['title']}",
        ]
        if tier_label:
            head.append(f"📊 {tier_label}")
        head.append(f"💱 نرخ توافقی هر {unit_txt}: *{money(rate, currency)}*")
        head.append("")
        head.append("⏸ در حال حاضر واریز مستقیم موقتاً غیرفعال است.")
        head.append("لطفاً ابتدا مستقیماً با پشتیبانی هماهنگ کنید؛ سپس اطلاعات واریز به شما داده می‌شود.")
        text = "\n".join(head)
        try:
            await cb.message.edit_text(text, reply_markup=suspended_kb(tx_id), parse_mode="Markdown")
        except Exception:
            await cb.message.answer(text, reply_markup=suspended_kb(tx_id), parse_mode="Markdown")
        await cb.answer()
        return

    # پیام اول: تأیید + نرخ
    confirm = await _confirm_text(product, rate, tier_label, tx_id)
    try:
        await cb.message.edit_text(confirm, parse_mode="Markdown")
    except Exception:
        await cb.message.answer(confirm, parse_mode="Markdown")

    # پیام دوم: کارت فعال (عکس یا متن) + دکمه‌های کپی + ارسال فیش
    card = await get_active_card()
    has_card = card and (card.get("image") or (card.get("text") or "").strip())
    if has_card:
        kb = card_kb(tx_id, card.get("card_number", ""), card.get("sheba", ""))
        if card.get("image"):
            await cb.message.answer_photo(
                card["image"], caption=(card.get("text") or None),
                reply_markup=kb, parse_mode=None,
            )
        else:
            await cb.message.answer(card["text"], reply_markup=kb, parse_mode=None)
    else:
        await cb.message.answer(
            "💳 اطلاعات کارت هنوز ثبت نشده است. لطفاً برای دریافت شماره کارت با پشتیبانی هماهنگ کنید،\n"
            "و پس از واریز از همین‌جا فیش را ارسال نمایید.",
            reply_markup=card_kb(tx_id),
        )
    await cb.answer()


# ─────────────────────────── ارسال فیش ───────────────────────────

@router.callback_query(F.data.startswith("receipt:"))
async def cb_receipt(cb: CallbackQuery, state: FSMContext):
    tx_id = int(cb.data.split(":", 1)[1])
    tx = await get_transaction(tx_id)
    if not tx or tx["telegram_id"] != cb.from_user.id:
        await cb.answer("معامله یافت نشد.", show_alert=True)
        return
    if tx["status"] not in ("awaiting_receipt", "receipt_sent"):
        await cb.answer("این معامله دیگر در انتظار فیش نیست.", show_alert=True)
        return
    await state.set_state(ReceiptFlow.waiting_receipt)
    await state.update_data(tx_id=tx_id)
    await cb.message.answer(
        f"📤 لطفاً تصویر فیش واریز معامله شماره {fa_digits(tx_id)} را ارسال کنید.\n"
        "می‌توانید توضیح (مبلغ، تعداد کارت) را هم به‌همراه فیش بفرستید.\n\n"
        "برای انصراف /cancel را بزنید.",
    )
    await cb.answer()


@router.message(ReceiptFlow.waiting_receipt)
async def receive_receipt(msg: Message, state: FSMContext):
    data = await state.get_data()
    tx_id = data.get("tx_id")
    tx = await get_transaction(tx_id) if tx_id else None
    if not tx:
        await state.clear()
        await msg.answer("معامله یافت نشد.", reply_markup=back_menu_kb())
        return

    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.document:
        file_id = msg.document.file_id

    if not file_id and not (msg.text and msg.text.strip()):
        await msg.answer("لطفاً تصویر فیش (عکس) یا توضیح متنی ارسال کنید.")
        return

    caption = msg.caption or msg.text or ""
    await update_transaction(tx_id, receipt_file_id=file_id or "", status="receipt_sent",
                             admin_note=caption[:500])
    await state.clear()
    await msg.answer(
        "✅ فیش شما دریافت شد و برای بررسی به پشتیبانی ارسال گردید.\n"
        "پس از تأیید، نتیجه از همین‌جا به شما اطلاع داده می‌شود. 🙏",
        reply_markup=back_menu_kb(),
    )
    await _notify_admins_receipt(msg, tx, file_id, caption)


async def _notify_admins_receipt(msg: Message, tx: dict, file_id: str, caption: str):
    # parse_mode=None چون نام/توضیح کاربر ممکن است کاراکترهای مارک‌داون داشته باشد
    uname = f"@{msg.from_user.username}" if msg.from_user.username else "—"
    admin_caption = (
        f"🧾 فیش جدید — معامله {fa_digits(tx['id'])}\n\n"
        f"🔸 {tx['product_title']}\n"
        + (f"📊 {tx['tier_label']}\n" if tx["tier_label"] else "")
        + f"💱 نرخ: {money(tx['rate'], tx.get('currency', 'تومان'))} به ازای هر {tx['unit']}\n"
        f"👤 کاربر: {msg.from_user.full_name} ({uname})\n"
        f"🆔 آیدی: {fa_digits(msg.from_user.id)}\n"
        + (f"📝 توضیح کاربر: {caption[:400]}\n" if caption else "")
    )
    for aid in ADMIN_IDS:
        try:
            if file_id:
                await msg.bot.send_photo(aid, file_id, caption=admin_caption,
                                         reply_markup=admin_review_kb(tx["id"]), parse_mode=None)
            else:
                await msg.bot.send_message(aid, admin_caption,
                                           reply_markup=admin_review_kb(tx["id"]), parse_mode=None)
        except Exception as e:
            logger.warning("notify admin %s failed: %s", aid, e)


# ─────────────────────────── ثبت کارت روسی کاربر (فاکتور واریز روبل) ───────────────────────────

def _build_invoice(payout_text: str, equivalent: int) -> str:
    """فاکتور واریز روبل را از اطلاعات کاربر می‌سازد."""
    lines = [l.strip() for l in (payout_text or "").splitlines() if l.strip()]
    if len(lines) >= 4:
        card, phone, name = lines[0], lines[1], lines[2]
        bank = " ".join(lines[3:])
        parts = [card, "", phone, "", name, "", f"{bank} 💵"]
    else:
        parts = [(payout_text or "").strip()]
    if equivalent and equivalent > 0:
        # عدد روبل با ارقام لاتین (هماهنگ با کارت/تلفن روسی)
        parts += ["", f"{equivalent:,} ₽"]
    return "\n".join(parts)


@router.callback_query(F.data.startswith("payout:"))
async def cb_payout(cb: CallbackQuery, state: FSMContext):
    tx_id = int(cb.data.split(":", 1)[1])
    tx = await get_transaction(tx_id)
    if not tx or tx["telegram_id"] != cb.from_user.id:
        await cb.answer("معامله یافت نشد.", show_alert=True)
        return
    await state.set_state(PayoutFlow.waiting_info)
    await state.update_data(tx_id=tx_id)
    await cb.message.answer(
        "📇 لطفاً اطلاعات حساب روسی خود را که می‌خواهید روبل به آن واریز شود،\n"
        "هر مورد را در *یک خط جدا* و به همین ترتیب بفرستید:\n\n"
        "۱️⃣ شماره کارت ۱۶ رقمی\n"
        "۲️⃣ شماره تلفن متصل به حساب\n"
        "۳️⃣ نام و نام خانوادگی (به روسی)\n"
        "۴️⃣ نام بانک روسی (مثل Сбер، Т-Банк، Озон)\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="Markdown",
    )
    await cb.answer()


@router.message(PayoutFlow.waiting_info)
async def receive_payout(msg: Message, state: FSMContext):
    data = await state.get_data()
    tx_id = data.get("tx_id")
    tx = await get_transaction(tx_id) if tx_id else None
    if not tx:
        await state.clear()
        await msg.answer("معامله یافت نشد.", reply_markup=back_menu_kb())
        return
    text = (msg.text or "").strip()
    if not text:
        await msg.answer("لطفاً اطلاعات حساب را به‌صورت متن (۴ خط) بفرستید.")
        return
    await update_transaction(tx_id, payout_info=text)
    await state.clear()
    invoice = _build_invoice(text, int(tx.get("equivalent") or 0))
    await msg.answer(
        "✅ اطلاعات حساب شما ثبت شد و برای شروع تسویه به پشتیبانی ارسال گردید.\n\n"
        "🧾 فاکتور شما:\n————————\n" + invoice + "\n————————\n"
        "به‌زودی برای واریز روبل با شما هماهنگ می‌شود. 🙏",
        reply_markup=back_menu_kb(), parse_mode=None,
    )
    uname = f"@{msg.from_user.username}" if msg.from_user.username else "—"
    admin_text = (
        f"🧾 فاکتور واریز روبل — معامله {fa_digits(tx_id)}\n"
        f"👤 {msg.from_user.full_name} ({uname}) | 🆔 {fa_digits(msg.from_user.id)}\n"
        f"🔸 {tx['product_title']}\n"
        "————————\n" + invoice
    )
    for aid in ADMIN_IDS:
        try:
            await msg.bot.send_message(
                aid, admin_text,
                reply_markup=contact_user_kb(msg.from_user.id, msg.from_user.username),
                parse_mode=None,
            )
        except Exception as e:
            logger.warning("notify admin invoice %s failed: %s", aid, e)


# ─────────────────────────── کیف پول / معاملات / پشتیبانی ───────────────────────────

@router.callback_query(F.data == "wallet")
async def cb_wallet(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.full_name)
    txs = await get_user_transactions(user["id"], limit=100)
    completed = sum(1 for t in txs if t["status"] == "completed")
    text = (
        "🏦 *کیف پول شما*\n\n"
        f"🇷🇺 روبل: *{num(user.get('wallet_rub', 0))}*\n"
        f"🇮🇷 تومان: *{toman(user['wallet'])}*\n\n"
        f"✅ معاملات موفق: {fa_digits(completed)}\n\n"
        "کیف پول شما به‌صورت خودکار پس از نشستن مبلغ واریزی به حساب مقصد و تأیید فیش واریزی شارژ می‌شود."
    )
    try:
        await cb.message.edit_text(text, reply_markup=back_menu_kb(), parse_mode="Markdown")
    except Exception:
        await cb.message.answer(text, reply_markup=back_menu_kb(), parse_mode="Markdown")
    await cb.answer()


@router.callback_query(F.data == "mytx")
async def cb_mytx(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.full_name)
    txs = await get_user_transactions(user["id"], limit=15)
    if not txs:
        text = "📋 شما هنوز معامله‌ای ثبت نکرده‌اید."
    else:
        lines = ["📋 *آخرین معاملات شما:*\n"]
        for t in txs:
            status = STATUS_LABELS.get(t["status"], t["status"])
            line = f"• #{fa_digits(t['id'])} — {t['product_title']}"
            if t["tier_label"]:
                line += f" ({t['tier_label']})"
            line += f"\n   {status} | نرخ: {money(t['rate'], t.get('currency', 'تومان'))}"
            lines.append(line)
        text = "\n".join(lines)
    try:
        await cb.message.edit_text(text, reply_markup=back_menu_kb(), parse_mode="Markdown")
    except Exception:
        await cb.message.answer(text, reply_markup=back_menu_kb(), parse_mode="Markdown")
    await cb.answer()


@router.callback_query(F.data == "support")
async def cb_support(cb: CallbackQuery):
    # parse_mode=None چون متن پشتیبانی ممکن است یوزرنیم با «_» داشته باشد که مارک‌داون را می‌شکند
    text = (await get_text("text_support") or "").strip()
    if not text:
        text = "📞 پشتیبانی و ارتباط با ما\n\nبرای هماهنگی، از دکمهٔ زیر با پشتیبانی در ارتباط باشید."
    try:
        await cb.message.edit_text(text, reply_markup=support_kb(), parse_mode=None)
    except Exception:
        await cb.message.answer(text, reply_markup=support_kb(), parse_mode=None)
    await cb.answer()

"""پنل مدیریت داخل تلگرام: نرخ‌ها/محصولات، کارت، متن‌ها، تراکنش‌ها، کیف‌پول، پیام همگانی."""
import logging

from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.config import ADMIN_IDS, is_admin
from core.db import (
    adjust_wallet,
    get_all_user_tg_ids,
    get_or_create_user,
    get_recent_transactions,
    get_setting,
    get_transaction,
    get_user_by_tg,
    set_setting,
    transaction_stats,
    update_transaction,
)
from core.cards import add_card, delete_card, get_active_card, get_cards, set_active_card
from core.products import PRODUCT_ORDER, get_product, get_products, save_product
from core.rates import compute_simple_rate, compute_tier_rate, fetch_rates
from core.utils import en_digits, fa_digits, money, num, parse_int, toman
from bot.keyboards import back_menu_kb, color_enabled, contact_user_kb, set_color_enabled, styled_btn
from bot.states import AddCard, AdminEdit, AdminMessageUser, AdminWallet, Broadcast

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger("pik.admin")
router = Router()


class IsAdmin(Filter):
    async def __call__(self, event) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and is_admin(user.id))


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _b(text: str, data: str, style: str | None = None) -> InlineKeyboardButton:
    return styled_btn(text, data, style=style)


def _parse_float(text) -> float:
    return float(en_digits(text).replace(",", "").replace("،", "").strip())


# ─────────────────────────── منوی اصلی مدیریت ───────────────────────────

def admin_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("💱 نرخ‌ها و محصولات", "adm:products", style="success"))
    kb.row(_b("💳 اطلاعات کارت", "adm:card", style="primary"),
           _b("📝 متن‌ها", "adm:texts", style="primary"))
    kb.row(_b("📊 تراکنش‌ها", "adm:tx", style="success"),
           _b("👛 شارژ کیف پول", "adm:wallet", style="primary"))
    kb.row(_b("📈 نرخ لحظه‌ای سایت", "adm:live", style="primary"),
           _b("📢 پیام همگانی", "adm:bcast", style="danger"))
    color_txt = "🎨 دکمه‌های رنگی: روشن ✅" if color_enabled() else "🎨 دکمه‌های رنگی: خاموش ⛔️"
    kb.row(_b(color_txt, "adm:togglecolor", style="primary"))
    kb.row(_b("🔙 بازگشت به منوی کاربری", "menu"))
    return kb.as_markup()


async def _open(cb_or_msg, text: str, kb, edit: bool = True, parse_mode: str | None = "Markdown"):
    if isinstance(cb_or_msg, CallbackQuery):
        if edit:
            try:
                await cb_or_msg.message.edit_text(text, reply_markup=kb, parse_mode=parse_mode)
                return
            except Exception:
                pass
        await cb_or_msg.message.answer(text, reply_markup=kb, parse_mode=parse_mode)
    else:
        await cb_or_msg.answer(text, reply_markup=kb, parse_mode=parse_mode)


@router.message(Command("admin"))
async def cmd_admin(msg: Message, state: FSMContext):
    await state.clear()
    await _open(msg, "🛠 *پنل مدیریت*\n\nیک بخش را انتخاب کنید:", admin_menu_kb(), edit=False)


@router.callback_query(F.data == "admin")
async def cb_admin(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _open(cb, "🛠 *پنل مدیریت*\n\nیک بخش را انتخاب کنید:", admin_menu_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:togglecolor")
async def cb_toggle_color(cb: CallbackQuery):
    new_val = not color_enabled()
    set_color_enabled(new_val)
    await set_setting("buttons_colored", "1" if new_val else "0")
    await _open(cb, "🛠 *پنل مدیریت*\n\nیک بخش را انتخاب کنید:", admin_menu_kb())
    await cb.answer("دکمه‌ها رنگی شد ✅" if new_val else "دکمه‌ها به حالت عادی برگشت ⛔️")


# ─────────────────────────── محصولات / نرخ‌ها ───────────────────────────

@router.callback_query(F.data == "adm:products")
async def cb_products(cb: CallbackQuery):
    products = await get_products()
    kb = InlineKeyboardBuilder()
    for pid in PRODUCT_ORDER:
        p = products.get(pid)
        if p:
            kb.row(_b(p["title"], f"padm:{pid}", style=p.get("style", "primary")))
    kb.row(_b("🔙 بازگشت", "admin"))
    await _open(cb, "💱 *مدیریت محصولات و نرخ‌ها*\n\nمحصول موردنظر را برای ویرایش انتخاب کنید:",
                kb.as_markup())
    await cb.answer()


async def _product_view(pid: str):
    p = await get_product(pid)
    if not p:
        return "محصول یافت نشد.", back_menu_kb()

    col_fa = {"buy": "خرید", "sell": "فروش", "avg": "میانگین"}
    base_fa = {"rub": "روبل (سایت)", "usd": "دلار (سایت)", "usd_rub": "دلار→روبل (بانک روسیه)"}
    currency = p.get("currency", "تومان")
    lines = [f"⚙️ *ویرایش:* {p['title']}", ""]
    if p.get("base") == "usd_rub":
        lines.append(f"منبع نرخ: *{base_fa.get('usd_rub')}*")
    else:
        lines.append(f"منبع نرخ سایت: *{base_fa.get(p.get('base'),'?')}* — ستون *{col_fa.get(p.get('column','buy'))}*")
    lines.append(f"واحد نمایش نتیجه: *{currency}*")

    kb = InlineKeyboardBuilder()

    if p["type"] == "rub_tiered":
        lines.append("")
        lines.append("*پله‌های حجمی:*")
        for t in p["tiers"]:
            mx = "بی‌نهایت" if not t.get("max") else num(t["max"])
            lines.append(
                f"• {t['label']}\n   بازه: {num(t.get('min',0))} تا {mx} تومان | ضریب: {fa_digits(t['mult'])}"
            )
            kb.row(
                _b(f"✏️ برچسب «{t['key']}»", f"pe:{pid}:tlbl:{t['key']}"),
                _b("⚖️ ضریب", f"pe:{pid}:tmult:{t['key']}"),
            )
            kb.row(
                _b("↧ سقف پایین", f"pe:{pid}:tmin:{t['key']}"),
                _b("↥ سقف بالا", f"pe:{pid}:tmax:{t['key']}"),
            )
    elif p["type"] == "rub_single":
        lines.append(f"ضریب: *{fa_digits(p.get('mult',1))}*")
        kb.row(_b("⚖️ ویرایش ضریب", f"pe:{pid}:mult:-"),
               _b("✏️ برچسب", f"pe:{pid}:lbl:-"))
    else:  # simple
        mode = p.get("mode", "formula")
        manual = int(p.get("manual", 0) or 0)
        offset = p.get("offset", 0) or 0
        lines.append(f"حالت: *{'دستی' if mode=='manual' else 'خودکار (مبنا × ضریب + آفست)'}*")
        lines.append(f"نرخ/مبنای دستی: *{money(manual, currency) if manual else 'ثبت نشده'}*")
        lines.append(f"ضریب: *{fa_digits(p.get('mult',1))}*")
        lines.append(f"آفست (± ثابت): *{fa_digits(offset)} {currency}*")
        kb.row(_b(f"🔁 تغییر حالت به {'خودکار' if mode=='manual' else 'دستی'}", f"pe:{pid}:mode:-"))
        kb.row(_b("💵 نرخ/مبنای دستی", f"pe:{pid}:manual:-"), _b("⚖️ ضریب", f"pe:{pid}:mult:-"))
        kb.row(_b("➕➖ آفست", f"pe:{pid}:offset:-"))

    if p.get("base") != "usd_rub":
        kb.row(_b("🔁 تغییر ستون سایت", f"pe:{pid}:col:-"))

    # پیش‌نمایش نرخ فعلی
    try:
        if p["type"] == "rub_tiered":
            preview = await compute_tier_rate(p, p["tiers"][0])
            lines.append(f"\n📈 پیش‌نمایش پله اول: *{money(preview, currency) if preview else 'ناموجود'}*")
        elif p["type"] == "rub_single":
            preview = await compute_tier_rate(p, {"mult": p.get("mult", 1.0)})
            lines.append(f"\n📈 پیش‌نمایش نرخ: *{money(preview, currency) if preview else 'ناموجود'}*")
        else:
            preview = await compute_simple_rate(p)
            lines.append(f"\n📈 پیش‌نمایش نرخ: *{money(preview, currency) if preview else 'ناموجود'}*")
    except Exception:
        pass

    kb.row(_b("🔙 بازگشت به لیست", "adm:products"))
    return "\n".join(lines), kb.as_markup()


@router.callback_query(F.data.startswith("padm:"))
async def cb_product_admin(cb: CallbackQuery):
    pid = cb.data.split(":", 1)[1]
    text, kb = await _product_view(pid)
    await _open(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data.startswith("pe:"))
async def cb_product_edit(cb: CallbackQuery, state: FSMContext):
    _, pid, field, tier = cb.data.split(":", 3)
    p = await get_product(pid)
    if not p:
        await cb.answer("محصول یافت نشد.", show_alert=True)
        return

    # تغییرهای آنی (بدون ورودی):
    if field == "col":
        order = ["buy", "sell", "avg"]
        cur = p.get("column", "buy")
        p["column"] = order[(order.index(cur) + 1) % 3] if cur in order else "buy"
        await save_product(pid, p)
        text, kb = await _product_view(pid)
        await _open(cb, text, kb)
        await cb.answer("ستون نرخ تغییر کرد.")
        return
    if field == "mode":
        p["mode"] = "formula" if p.get("mode") == "manual" else "manual"
        await save_product(pid, p)
        text, kb = await _product_view(pid)
        await _open(cb, text, kb)
        await cb.answer("حالت تغییر کرد.")
        return

    # ورودی‌های عددی/متنی:
    prompts = {
        "tmult": ("ضریب جدید این پله را بفرستید (مثلاً 0.92):", "float"),
        "tmin": ("حداقل مبلغ این پله (تومان) را بفرستید (0 برای بدون حد پایین):", "int"),
        "tmax": ("حداکثر مبلغ این پله (تومان) را بفرستید (0 برای بی‌نهایت):", "int"),
        "tlbl": ("برچسب جدید این پله را بفرستید:", "text"),
        "mult": ("ضریب جدید را بفرستید (مثلاً 0.965 یا 1):", "float"),
        "lbl": ("برچسب جدید را بفرستید:", "text"),
        "manual": ("نرخ/مبنای دستی (به ازای هر واحد) را بفرستید (0 برای استفاده از منبع خودکار):", "int"),
        "offset": ("آفست را بفرستید — عدد ثابتی که به نرخ اضافه/کم می‌شود (مثلاً -300 یا -8 یا 0):", "int"),
    }
    if field not in prompts:
        await cb.answer("گزینه نامعتبر.", show_alert=True)
        return
    prompt, vtype = prompts[field]
    await state.set_state(AdminEdit.waiting_value)
    await state.update_data(save="product", pid=pid, field=field, tier=tier, vtype=vtype)
    await cb.message.answer(f"✏️ {prompt}\n\nبرای انصراف /cancel را بزنید.")
    await cb.answer()


# ─────────────────────────── اطلاعات کارت ───────────────────────────

async def _card_view():
    """نمای مدیریت کارت‌ها (متن ساده، بدون Markdown تا کاراکترهای کارت مشکلی ایجاد نکنند)."""
    cards = await get_cards()
    active = await get_active_card()
    active_id = active["id"] if active else None

    lines = ["💳 مدیریت کارت‌های واریز", ""]
    if not cards:
        lines.append("هیچ کارتی ثبت نشده است.")
        lines.append("با دکمه «➕ افزودن کارت» یک کارت اضافه کنید.")
    else:
        lines.append(f"تعداد کارت‌ها: {fa_digits(len(cards))}")
        lines.append(f"کارت فعال: {active.get('label') if active else '—'}")
        lines.append("")
        lines.append("متنی که هم‌اکنون به کاربر نمایش داده می‌شود:")
        lines.append("────────────")
        lines.append((active or {}).get("text", "—"))
        lines.append("────────────")
        lines.append("برای تغییر کارت فعال، روی «انتخاب» همان کارت بزنید.")

    kb = InlineKeyboardBuilder()
    for c in cards:
        mark = "🟢" if c["id"] == active_id else "⚪️"
        label = c.get("label") or f"کارت {c['id']}"
        kb.row(
            _b(f"{mark} {label}", f"cardsel:{c['id']}", style="success" if c["id"] == active_id else None),
            _b("🗑 حذف", f"carddel:{c['id']}", style="danger"),
        )
    kb.row(_b("➕ افزودن کارت", "cardadd", style="success"))
    kb.row(_b("🔙 بازگشت", "admin"))
    return "\n".join(lines), kb.as_markup()


@router.callback_query(F.data == "adm:card")
async def cb_card(cb: CallbackQuery):
    text, kb = await _card_view()
    await _open(cb, text, kb, parse_mode=None)
    await cb.answer()


@router.callback_query(F.data.startswith("cardsel:"))
async def cb_card_select(cb: CallbackQuery):
    cid = int(cb.data.split(":", 1)[1])
    await set_active_card(cid)
    text, kb = await _card_view()
    await _open(cb, text, kb, parse_mode=None)
    await cb.answer("کارت فعال تغییر کرد ✅")


@router.callback_query(F.data.startswith("carddel:"))
async def cb_card_delete(cb: CallbackQuery):
    cid = int(cb.data.split(":", 1)[1])
    await delete_card(cid)
    text, kb = await _card_view()
    await _open(cb, text, kb, parse_mode=None)
    await cb.answer("کارت حذف شد 🗑")


@router.callback_query(F.data == "cardadd")
async def cb_card_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddCard.waiting_label)
    await cb.message.answer(
        "➕ *افزودن کارت جدید*\n\n"
        "۱) ابتدا یک برچسب کوتاه برای این کارت بفرستید (برای تشخیص در لیست، مثلاً: «بلو - بهرام قاسمی»).\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="Markdown",
    )
    await cb.answer()


@router.message(AddCard.waiting_label)
async def card_add_label(msg: Message, state: FSMContext):
    label = (msg.text or "").strip()
    if not label:
        await msg.answer("برچسب خالی است. یک نام کوتاه بفرستید.")
        return
    await state.update_data(label=label)
    await state.set_state(AddCard.waiting_text)
    await msg.answer(
        "۲) حالا *متن کامل کارت* را همان‌طور که می‌خواهید عیناً به کاربر نمایش داده شود بفرستید.\n\n"
        "مثال:\n"
        "بلو بانک 💵\n\n"
        "IR600560611828005200334501\n\n"
        "6219861946953824\n\n"
        "بهرام قاسمی",
        parse_mode="Markdown",
    )


@router.message(AddCard.waiting_text)
async def card_add_text(msg: Message, state: FSMContext):
    text = msg.text or msg.caption or ""
    if not text.strip():
        await msg.answer("متن کارت خالی است. متن کامل کارت را بفرستید.")
        return
    data = await state.get_data()
    cid = await add_card(data.get("label", "کارت"), text)
    await state.clear()
    await msg.answer(f"✅ کارت ثبت شد (شناسه {fa_digits(cid)}).")
    view, kb = await _card_view()
    await _open(msg, view, kb, parse_mode=None)


# ─────────────────────────── متن‌ها ───────────────────────────

@router.callback_query(F.data == "adm:texts")
async def cb_texts(cb: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.row(_b("📌 متن قوانین معاملات", "set:text_rules"))
    kb.row(_b("👋 متن خوش‌آمد", "set:text_welcome"))
    kb.row(_b("📞 متن پشتیبانی", "set:text_support"))
    kb.row(_b("🔙 بازگشت", "admin"))
    await _open(cb, "📝 *ویرایش متن‌ها*\n\nکدام متن را می‌خواهید تغییر دهید؟", kb.as_markup())
    await cb.answer()


SET_PROMPTS = {
    "text_rules": "متن جدید «قوانین معاملات» را بفرستید:",
    "text_welcome": "متن جدید «خوش‌آمد» را بفرستید:",
    "text_support": "متن جدید «پشتیبانی» را بفرستید:",
}


@router.callback_query(F.data.startswith("set:"))
async def cb_set(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":", 1)[1]
    if key not in SET_PROMPTS:
        await cb.answer("گزینه نامعتبر.", show_alert=True)
        return
    await state.set_state(AdminEdit.waiting_value)
    await state.update_data(save="setting", key=key, vtype="text", ret="texts")
    await cb.message.answer(f"✏️ {SET_PROMPTS[key]}\n\nبرای انصراف /cancel را بزنید.")
    await cb.answer()


# ─────────────────────────── دریافت مقدار (FSM مشترک) ───────────────────────────

@router.message(AdminEdit.waiting_value)
async def on_value(msg: Message, state: FSMContext):
    data = await state.get_data()
    vtype = data.get("vtype", "text")
    raw = msg.text or ""

    # اعتبارسنجی مقدار
    value = raw
    if vtype == "int":
        try:
            value = parse_int(raw)
        except (ValueError, TypeError):
            await msg.answer("لطفاً یک عدد صحیح بفرستید.")
            return
    elif vtype == "float":
        try:
            value = _parse_float(raw)
        except (ValueError, TypeError):
            await msg.answer("لطفاً یک عدد بفرستید (مثلاً 0.92).")
            return

    if data.get("save") == "setting":
        await set_setting(data["key"], value)
        await state.clear()
        await msg.answer("✅ ذخیره شد.\nبرای بازگشت به پنل /admin را بزنید.")
        return

    # save == product
    pid = data["pid"]
    field = data["field"]
    tier = data["tier"]
    p = await get_product(pid)
    if not p:
        await state.clear()
        await msg.answer("محصول یافت نشد.")
        return

    if field in ("tmult", "tmin", "tmax", "tlbl"):
        t = next((x for x in p.get("tiers", []) if x["key"] == tier), None)
        if not t:
            await state.clear()
            await msg.answer("پله یافت نشد.")
            return
        if field == "tmult":
            t["mult"] = value
        elif field == "tmin":
            t["min"] = value
        elif field == "tmax":
            t["max"] = value
        elif field == "tlbl":
            t["label"] = value
    elif field == "mult":
        p["mult"] = value
    elif field == "lbl":
        p["label"] = value
    elif field == "manual":
        p["manual"] = value
    elif field == "offset":
        p["offset"] = value

    await save_product(pid, p)
    await state.clear()
    await msg.answer("✅ ذخیره شد.")
    text, kb = await _product_view(pid)
    await _open(msg, text, kb, edit=False)


# ─────────────────────────── تراکنش‌ها ───────────────────────────

STATUS_LABELS = {
    "awaiting_receipt": "⏳ منتظر فیش",
    "receipt_sent": "🧾 فیش ارسال‌شده",
    "completed": "✅ تکمیل",
    "canceled": "❌ رد",
}


@router.callback_query(F.data == "adm:tx")
async def cb_tx(cb: CallbackQuery):
    stats = await transaction_stats()
    pending = await get_recent_transactions(limit=10, status="receipt_sent")
    recent = await get_recent_transactions(limit=8)

    lines = ["📊 *تراکنش‌ها*", ""]
    lines.append(
        f"کل: {fa_digits(stats.get('total',0))} | "
        f"تکمیل: {fa_digits(stats.get('completed',0))} | "
        f"منتظر بررسی: {fa_digits(stats.get('receipt_sent',0))}"
    )
    if pending:
        lines.append("\n*در انتظار بررسی:*")
        for t in pending:
            lines.append(f"• #{fa_digits(t['id'])} — {t['product_title']} — {money(t['rate'], t.get('currency','تومان'))}")
    lines.append("\n*آخرین معاملات:*")
    for t in recent:
        lines.append(
            f"• #{fa_digits(t['id'])} {STATUS_LABELS.get(t['status'], t['status'])} — "
            f"{t['product_title']} — {money(t['rate'], t.get('currency','تومان'))}"
        )

    kb = InlineKeyboardBuilder()
    for t in pending[:6]:
        kb.row(
            _b(f"✅ تأیید #{t['id']}", f"txok:{t['id']}", style="success"),
            _b(f"❌ رد #{t['id']}", f"txno:{t['id']}", style="danger"),
        )
    kb.row(_b("🔙 بازگشت", "admin"))
    await _open(cb, "\n".join(lines), kb.as_markup())
    await cb.answer()


async def _finalize_tx(cb: CallbackQuery, tx_id: int, approve: bool):
    tx = await get_transaction(tx_id)
    if not tx:
        await cb.answer("معامله یافت نشد.", show_alert=True)
        return
    if tx["status"] in ("completed", "canceled"):
        await cb.answer("این معامله قبلاً بررسی شده است.", show_alert=True)
        return
    new_status = "completed" if approve else "canceled"
    await update_transaction(tx_id, status=new_status)
    # اطلاع به کاربر
    try:
        if approve:
            await cb.bot.send_message(
                tx["telegram_id"],
                f"✅ *تأیید فیش و بسته‌شدن نرخ*\n\n"
                f"🧾 معامله شماره {fa_digits(tx_id)} تأیید شد و نرخ شما قفل گردید.\n"
                f"🔸 {tx['product_title']}\n"
                + (f"📊 {tx['tier_label']}\n" if tx.get("tier_label") else "")
                + f"💱 نرخ نهایی: {money(tx['rate'], tx.get('currency','تومان'))} به ازای هر {tx.get('unit') or ''}\n\n"
                "تسویه‌حساب حداکثر تا ۲۴ ساعت انجام می‌شود. سپاس از اعتماد شما 🙏",
                parse_mode="Markdown",
            )
        else:
            await cb.bot.send_message(
                tx["telegram_id"],
                f"❌ فیش معامله شماره {fa_digits(tx_id)} تأیید نشد.\n"
                "برای پیگیری با پشتیبانی در ارتباط باشید.",
            )
    except Exception as e:
        logger.warning("notify user on finalize failed: %s", e)
    # علامت‌گذاری پیام بررسی برای ادمین
    try:
        suffix = "\n\n✅ تأیید شد." if approve else "\n\n❌ رد شد."
        if cb.message.caption is not None:
            await cb.message.edit_caption(caption=(cb.message.caption or "") + suffix, parse_mode=None)
        else:
            await cb.message.edit_text((cb.message.text or "") + suffix, parse_mode=None)
    except Exception:
        pass
    await cb.answer("انجام شد.")

    # پس از تأیید، ابزار ارتباط با کاربر را به ادمین بده
    if approve:
        u = await get_user_by_tg(tx["telegram_id"])
        uname = (u or {}).get("username")
        name = (u or {}).get("full_name") or "کاربر"
        info = (
            f"🤝 *ارتباط با کاربر معامله {fa_digits(tx_id)}*\n\n"
            f"👤 {name}\n"
            f"🆔 آیدی: `{fa_digits(tx['telegram_id'])}`\n"
            + (f"🔗 یوزرنیم: @{uname}\n" if uname else "🔗 یوزرنیم: ندارد (از دکمهٔ زیر پیام بده)\n")
        )
        try:
            await cb.bot.send_message(
                cb.from_user.id, info,
                reply_markup=contact_user_kb(tx["telegram_id"], uname),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("send contact tools failed: %s", e)


@router.callback_query(F.data.startswith("txok:"))
async def cb_txok(cb: CallbackQuery):
    await _finalize_tx(cb, int(cb.data.split(":", 1)[1]), approve=True)


@router.callback_query(F.data.startswith("txno:"))
async def cb_txno(cb: CallbackQuery):
    await _finalize_tx(cb, int(cb.data.split(":", 1)[1]), approve=False)


# ─────────────────────────── پیام مستقیم ادمین به کاربر (از طریق ربات) ───────────────────────────

@router.callback_query(F.data.startswith("amsg:"))
async def cb_admin_message(cb: CallbackQuery, state: FSMContext):
    tg_id = int(cb.data.split(":", 1)[1])
    await state.set_state(AdminMessageUser.waiting_text)
    await state.update_data(target=tg_id)
    await cb.message.answer(
        f"✉️ پیام خود برای کاربر `{fa_digits(tg_id)}` را بفرستید (متن یا عکس).\n"
        "این پیام از طرف ربات به کاربر ارسال می‌شود.\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="Markdown",
    )
    await cb.answer()


@router.message(AdminMessageUser.waiting_text)
async def admin_message_send(msg: Message, state: FSMContext):
    data = await state.get_data()
    target = data.get("target")
    await state.clear()
    if not target:
        await msg.answer("کاربر مقصد مشخص نیست. دوباره از دکمهٔ ارتباط استفاده کنید.")
        return

    header = "📨 *پیامی از پشتیبانی صرافی پیک:*\n\n"
    try:
        if msg.photo:
            cap = header + (msg.caption or "")
            await msg.bot.send_photo(target, msg.photo[-1].file_id, caption=cap, parse_mode="Markdown")
        else:
            body = msg.text or msg.caption or ""
            if not body.strip():
                await msg.answer("پیام خالی بود؛ ارسال نشد.")
                return
            await msg.bot.send_message(target, header + body, parse_mode="Markdown")
        await msg.answer("✅ پیام برای کاربر ارسال شد.")
    except Exception as e:
        await msg.answer(f"❌ ارسال پیام ناموفق بود (کاربر ممکن است ربات را بلاک کرده باشد).\n{str(e)[:150]}")


# ─────────────────────────── شارژ کیف پول ───────────────────────────

@router.callback_query(F.data == "adm:wallet")
async def cb_wallet_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminWallet.waiting_user)
    await cb.message.answer(
        "👛 آیدی عددی کاربر (telegram id) را بفرستید.\nبرای انصراف /cancel را بزنید."
    )
    await cb.answer()


@router.message(AdminWallet.waiting_user)
async def wallet_user(msg: Message, state: FSMContext):
    try:
        tg_id = parse_int(msg.text)
    except (ValueError, TypeError):
        await msg.answer("آیدی نامعتبر است. یک عدد بفرستید.")
        return
    user = await get_user_by_tg(tg_id)
    if not user:
        await msg.answer("کاربری با این آیدی یافت نشد (کاربر باید حداقل یک‌بار /start زده باشد).")
        return
    await state.update_data(user_id=user["id"], tg_id=tg_id)
    await state.set_state(AdminWallet.waiting_amount)
    await msg.answer(
        f"موجودی فعلی: {toman(user['wallet'])}\n\n"
        "مبلغ تغییر را بفرستید. برای افزایش عدد مثبت و برای کاهش عدد منفی (مثلاً -50000)."
    )


@router.message(AdminWallet.waiting_amount)
async def wallet_amount(msg: Message, state: FSMContext):
    try:
        delta = parse_int(msg.text)
    except (ValueError, TypeError):
        await msg.answer("مبلغ نامعتبر است.")
        return
    data = await state.get_data()
    new_val = await adjust_wallet(data["user_id"], delta)
    await state.clear()
    await msg.answer(f"✅ انجام شد. موجودی جدید: {toman(new_val)}")
    try:
        await msg.bot.send_message(
            data["tg_id"],
            f"👛 موجودی کیف پول شما به‌روزرسانی شد.\nموجودی فعلی: {toman(new_val)}",
        )
    except Exception:
        pass


# ─────────────────────────── نرخ لحظه‌ای سایت ───────────────────────────

@router.callback_query(F.data == "adm:live")
async def cb_live(cb: CallbackQuery):
    await cb.answer("در حال دریافت…")
    from core.rates import fetch_usd_rub
    rates = await fetch_rates(force=True)
    usd_rub = await fetch_usd_rub(force=True)
    lines = ["📈 *نرخ لحظه‌ای سایت (تومان)*", ""]
    if rates.get("rub"):
        lines.append(f"روبل — خرید: {toman(rates['rub']['buy'])} | فروش: {toman(rates['rub']['sell'])}")
    if rates.get("usd"):
        lines.append(f"دلار — خرید: {toman(rates['usd']['buy'])} | فروش: {toman(rates['usd']['sell'])}")
    if usd_rub:
        lines.append(f"دلار به روبل (بانک روسیه): {fa_digits(round(usd_rub, 2))} روبل")
    if not rates:
        lines.append("⚠️ دریافت نرخ از سایت ناموفق بود.")
    lines.append("\n*نرخ محاسبه‌شده محصولات:*")
    products = await get_products()
    for pid in PRODUCT_ORDER:
        p = products.get(pid)
        if not p:
            continue
        try:
            if p["type"] == "rub_tiered":
                r = await compute_tier_rate(p, p["tiers"][0])
                extra = f" (پله اول)"
            elif p["type"] == "rub_single":
                r = await compute_tier_rate(p, {"mult": p.get("mult", 1.0)})
                extra = ""
            else:
                r = await compute_simple_rate(p)
                extra = ""
        except Exception:
            r, extra = None, ""
        cur = p.get("currency", "تومان")
        lines.append(f"• {p['title']}{extra}: {money(r, cur) if r else 'ناموجود'}")

    kb = InlineKeyboardBuilder()
    kb.row(_b("🔄 بروزرسانی", "adm:live", style="success"), _b("🔙 بازگشت", "admin"))
    await _open(cb, "\n".join(lines), kb.as_markup())


# ─────────────────────────── پیام همگانی ───────────────────────────

@router.callback_query(F.data == "adm:bcast")
async def cb_bcast(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.waiting_text)
    await cb.message.answer(
        "📢 متن پیام همگانی را بفرستید (به همه‌ی کاربران ارسال می‌شود).\n"
        "برای انصراف /cancel را بزنید."
    )
    await cb.answer()


@router.message(Broadcast.waiting_text)
async def bcast_send(msg: Message, state: FSMContext):
    await state.clear()
    text = msg.text or msg.caption or ""
    if not text.strip():
        await msg.answer("متن خالی است. لغو شد.")
        return
    ids = await get_all_user_tg_ids()
    sent = failed = 0
    await msg.answer(f"در حال ارسال به {fa_digits(len(ids))} کاربر…")
    import asyncio
    for uid in ids:
        try:
            await msg.bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await msg.answer(f"✅ ارسال شد.\nموفق: {fa_digits(sent)} | ناموفق: {fa_digits(failed)}")


@router.message(Command("cancel"))
async def admin_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("❌ عملیات لغو شد. برای بازگشت به پنل /admin را بزنید.")


# ─────────────────────────── اطلاع‌رسانی آپدیت ───────────────────────────

@router.callback_query(F.data.startswith("updbc:"))
async def cb_update_broadcast(cb: CallbackQuery):
    import asyncio
    from core.updates import DEFAULT_UPDATE_TEXT

    build = cb.data.split(":", 1)[1]
    ids = await get_all_user_tg_ids()
    text = await get_setting("update_text", "") or DEFAULT_UPDATE_TEXT
    await cb.answer("در حال ارسال…")
    sent = 0
    for uid in ids:
        try:
            await cb.bot.send_message(uid, text)
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await set_setting("pending_build", "")
    try:
        await cb.message.edit_text(
            f"✅ اطلاع‌رسانی آپدیت (build {build}) به {fa_digits(sent)} کاربر ارسال شد."
        )
    except Exception:
        await cb.message.answer(f"✅ ارسال شد به {fa_digits(sent)} کاربر.")

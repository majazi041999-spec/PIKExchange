"""Set and reset private web-panel credentials through the trusted admin chat."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin import IsAdmin, _b
from core.db import get_setting
from core import web_auth

router = Router()
router.message.filter(IsAdmin(), F.chat.type == "private")
router.callback_query.filter(IsAdmin(), F.message.chat.type == "private")


class WebAccess(StatesGroup):
    username = State()
    password = State()


async def show_access(msg, admin_id):
    info = await web_auth.credential_info(admin_id)
    url = await get_setting("web_public_url", "https://65.109.182.47:9443")
    text = "🌐 پنل وب PIK Exchange\n\n"
    text += f"نام کاربری شما: {info['username']}" if info else "ابتدا نام کاربری و رمز عبور خودتان را تنظیم کنید."
    text += "\nپنل روی موبایل باز می‌شود و می‌توانید آن را به صفحه اصلی گوشی اضافه کنید."
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🌐 باز کردن پنل", url=url))
    kb.row(_b("🔐 تنظیم / تغییر نام کاربری و رمز", "web:configure"))
    if info:
        kb.row(_b("🚪 خروج از همه مرورگرها", "web:revoke"))
    kb.row(_b("🔙 مدیریت", "admin"))
    await msg.answer(text, parse_mode=None, reply_markup=kb.as_markup())


@router.message(Command("webpanel"))
async def command(msg: Message, state: FSMContext):
    await state.clear()
    await show_access(msg, msg.from_user.id)


@router.callback_query(F.data.startswith("web:"))
async def callbacks(cb: CallbackQuery, state: FSMContext):
    action = cb.data.split(":")[1]
    await state.clear()
    await cb.answer()
    if action == "configure":
        await state.set_state(WebAccess.username)
        return await cb.message.answer("نام کاربری دلخواه را با حروف لاتین بفرستید (۴ تا ۳۲ کاراکتر).\nانصراف: /cancel")
    if action == "revoke":
        await web_auth.revoke(cb.from_user.id)
        await cb.message.answer("همه نشست‌های وب شما بسته شد.")
    await show_access(cb.message, cb.from_user.id)


@router.message(WebAccess.username, ~F.text.startswith("/"))
async def username(msg: Message, state: FSMContext):
    try:
        value = web_auth.valid_username(msg.text or "")
    except ValueError as exc:
        return await msg.answer(str(exc), parse_mode=None)
    await state.update_data(web_username=value)
    await state.set_state(WebAccess.password)
    await msg.answer("رمز عبور جدید را بفرستید (حداقل ۱۰ کاراکتر). پیام رمز پس از دریافت حذف می‌شود.\nانصراف: /cancel")


@router.message(WebAccess.password, ~F.text.startswith("/"))
async def password(msg: Message, state: FSMContext):
    data = await state.get_data()
    try:
        await msg.delete()
    except Exception:
        pass
    try:
        await web_auth.set_credentials(msg.from_user.id, data["web_username"], msg.text or "")
    except ValueError as exc:
        return await msg.answer(str(exc), parse_mode=None)
    await state.clear()
    await msg.answer("✅ دسترسی وب ذخیره شد. نشست‌های قبلی شما بسته شدند.")
    await show_access(msg, msg.from_user.id)

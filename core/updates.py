"""اطلاع‌رسانی آپدیت: وقتی نسخه‌ی جدید (کامیت گیت) نصب شد، به ادمین‌ها خبر بده
و در صورت تأیید، به همه‌ی کاربران اطلاع‌رسانی کن."""
import logging
import subprocess

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.config import ADMIN_IDS
from core.db import get_setting, set_setting

logger = logging.getLogger("pik.updates")

DEFAULT_UPDATE_TEXT = (
    "✨ ربات به‌روزرسانی شد!\n\n"
    "بهبودها و امکانات جدید اعمال شد. برای بارگذاری منوی جدید یک بار /start را بزنید."
)


def current_build() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ""


async def notify_admins_new_build(bot) -> None:
    build = current_build()
    if not build:
        return
    last = await get_setting("last_build", "")
    if build == last:
        return
    # اولین اجرا: فقط نسخه را ثبت کن، اطلاع‌رسانی نده
    if not last:
        await set_setting("last_build", build)
        return

    await set_setting("pending_build", build)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="📢 اطلاع‌رسانی آپدیت به کاربران", callback_data=f"updbc:{build}")
        ]]
    )
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(
                aid,
                f"🔄 نسخه‌ی جدید ربات نصب شد (build `{build}`).\n"
                "در صورت تمایل، پیام آپدیت را برای کاربران ارسال کنید.",
                reply_markup=kb,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("notify admin %s failed: %s", aid, e)
    # نسخه را ثبت کن تا در ری‌استارت‌های بعدی دوباره اطلاع ندهد
    await set_setting("last_build", build)

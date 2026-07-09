"""PIK Exchange Bot — نقطه‌ی ورود.

ربات صرافی روبل/دلار مبتنی بر aiogram با پنل مدیریت داخل تلگرام.
"""
import asyncio
import logging
import os
import sys


def _setup_logging():
    fmt = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    for path in (os.getenv("PIK_LOG_PATH", "").strip(), os.path.join(os.getcwd(), "pik.log"), "/tmp/pik-bot.log"):
        if not path:
            continue
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            handlers.append(logging.FileHandler(path, encoding="utf-8"))
            break
        except OSError:
            continue
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


_setup_logging()
logger = logging.getLogger("pik")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("aiogram").setLevel(logging.WARNING)


async def _rate_refresh_worker():
    """هر چند دقیقه نرخ سایت و نرخ دلار/روبل بانک روسیه را تازه نگه می‌دارد."""
    from core.rates import fetch_rates, fetch_usd_rub

    while True:
        try:
            await fetch_rates(force=True)
            await fetch_usd_rub(force=True)
        except Exception as e:
            logger.warning("rate refresh failed: %s", e)
        await asyncio.sleep(120)


async def main():
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.fsm.storage.memory import MemoryStorage

    from core.config import ADMIN_IDS, BOT_TOKEN
    from core.db import init_db
    from core.updates import notify_admins_new_build
    from bot.handlers import admin, user

    if not BOT_TOKEN or len(BOT_TOKEN) < 20:
        logger.error("❌ BOT_TOKEN در فایل .env تنظیم نشده!")
        return
    if not ADMIN_IDS or ADMIN_IDS == [0]:
        logger.warning("⚠️ ADMIN_IDS تنظیم نشده؛ پنل مدیریت در دسترس نخواهد بود.")

    await init_db()
    logger.info("✅ دیتابیس آماده")

    # بارگذاری وضعیت رنگی‌بودن دکمه‌ها (پیش‌فرض: خاموش)
    from core.db import get_setting
    from bot.keyboards import set_color_enabled
    set_color_enabled(await get_setting("buttons_colored", "0") == "1")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher(storage=MemoryStorage())

    # ترتیب مهم است: admin (فیلترشده برای ادمین) قبل از user
    dp.include_router(admin.router)
    dp.include_router(user.router)

    me = await bot.get_me()
    logger.info(f"🤖 ربات @{me.username} آماده | ادمین‌ها: {ADMIN_IDS}")

    asyncio.create_task(_rate_refresh_worker())
    try:
        await notify_admins_new_build(bot)
    except Exception as e:
        logger.warning("update notify failed: %s", e)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 ربات متوقف شد")

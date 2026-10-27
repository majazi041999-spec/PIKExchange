"""Block banned users before any user handler can read rates or create a trade."""
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from core.config import is_admin
from core.db import get_or_create_user


class SubscriberAccess(BaseMiddleware):
    async def __call__(self, handler, event, data):
        actor = getattr(event, "from_user", None)
        if actor is None or actor.is_bot:
            return await handler(event, data)
        user = await get_or_create_user(actor.id, actor.username, actor.full_name)
        if user["is_blocked"] and not is_admin(actor.id):
            if isinstance(event, CallbackQuery):
                await event.answer("دسترسی شما به ربات مسدود شده است.", show_alert=True)
            else:
                await event.answer("دسترسی شما به ربات مسدود شده است.", parse_mode=None)
            return
        data["subscriber"] = user
        return await handler(event, data)

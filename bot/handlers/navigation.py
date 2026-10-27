"""Global private-chat restart before data-entry handlers can consume its text."""
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.access import SubscriberAccess
from bot.handlers.user import cmd_start
from bot.keyboards import RESTART_TEXT

router = Router()
router.message.filter(F.chat.type == "private")
router.message.outer_middleware(SubscriberAccess())


@router.message(CommandStart())
@router.message(F.text.in_({RESTART_TEXT, "شروع مجدد"}))
async def restart(msg: Message, state: FSMContext):
    await cmd_start(msg, state)

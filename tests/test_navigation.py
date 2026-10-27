import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, Update

from bot.access import SubscriberAccess
from bot.handlers.navigation import restart
from bot.keyboards import RESTART_TEXT
from core import db


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_preempts_data_entry_and_keeps_ban_enforcement(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(db, 'DB_PATH', str(Path(folder) / 'test.db')):
            await db.init_db()
            bot = Bot('123456:TEST_TOKEN')
            dp = Dispatcher()
            navigation, data_entry = Router(), Router()
            navigation.message.filter(F.chat.type == 'private')
            navigation.message.outer_middleware(SubscriberAccess())
            navigation.message.register(restart, F.text == RESTART_TEXT)
            fallback = AsyncMock()
            async def capture(msg):
                await fallback(msg)
            data_entry.message.register(capture)
            dp.include_router(navigation)
            dp.include_router(data_entry)
            result = Message.model_validate({'message_id': 1, 'date': 1700000000, 'chat': {'id': 3, 'type': 'private'}})
            api = AsyncMock(return_value=result)
            try:
                with patch.object(Bot, '__call__', api), patch('bot.access.is_admin', return_value=False):
                    ctx = dp.fsm.get_context(bot=bot, chat_id=3, user_id=3)
                    for index, state in enumerate(('ReceiptFlow:waiting_receipt', 'Broadcast:waiting_text', 'AdminEdit:waiting_value', 'WebAccess:password')):
                        await ctx.set_state(state)
                        await ctx.set_data({'pending_text': 'draft', 'target': 99})
                        api.reset_mock()
                        update = Update.model_validate({'update_id': index, 'message': {'message_id': index + 1, 'date': 1700000000, 'from': {'id': 3, 'is_bot': False, 'first_name': 'Test'}, 'chat': {'id': 3, 'type': 'private'}, 'text': RESTART_TEXT}})
                        await dp.feed_update(bot, update)
                        self.assertIsNone(await ctx.get_state())
                        self.assertEqual(await ctx.get_data(), {})
                        sent = [c.args[0] for c in api.call_args_list if type(c.args[0]).__name__ == 'SendMessage']
                        self.assertEqual(len(sent), 2)
                        self.assertTrue(sent[0].reply_markup.is_persistent)
                        self.assertEqual(sent[0].reply_markup.keyboard[0][0].text, RESTART_TEXT)
                        self.assertTrue(sent[1].reply_markup.inline_keyboard)
                    fallback.assert_not_called()
                    await db.update_user((await db.get_user_by_tg(3))['id'], is_blocked=1)
                    api.reset_mock()
                    await dp.feed_update(bot, update)
                    self.assertIn('مسدود', api.call_args.args[0].text)
            finally:
                await dp.storage.close()
                await bot.session.close()

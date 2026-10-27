import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import db, subscribers


class SubscriberTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        self.db_patch.start()
        await db.init_db()

    async def asyncTearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_directory_pagination_and_persistent_request_counts(self):
        for uid in range(3, 23):
            await db.get_or_create_user(uid, full_name=f"User {uid}")
        user = await db.get_user_by_tg(3)
        args = ("unique-event", user["id"], "rub_c2c", "روبل", "زیر ۵۰ میلیون", 3080, "تومان")
        self.assertIsNotNone(await subscribers.record_quote(*args))
        self.assertIsNone(await subscribers.record_quote(*args))
        self.assertEqual((await subscribers.get_subscriber(3))["quote_count"], 1)
        first = await subscribers.list_subscribers(8, 0)
        second = await subscribers.list_subscribers(8, 8)
        third = await subscribers.list_subscribers(8, 16)
        self.assertEqual([len(first), len(second), len(third)], [8, 8, 4])
        self.assertEqual(len({u["id"] for u in first + second + third}), 20)
        await subscribers.set_blocked(3, True)
        self.assertEqual(await subscribers.stats(), {"total": 20, "active": 19, "blocked": 1})
        await db.init_db()
        self.assertEqual((await subscribers.get_subscriber(3))["is_blocked"], 1)

    async def test_one_unreachable_admin_does_not_skip_other_admins(self):
        from core.activity import track_quote
        async def send(admin_id, *args, **kwargs):
            if admin_id == 1:
                raise ValueError("unreachable")
        sender = AsyncMock(side_effect=send)
        callback = SimpleNamespace(id="quote", from_user=SimpleNamespace(id=3, username="test", full_name="Test"),
                                   bot=SimpleNamespace(send_message=sender))
        with patch("core.activity.ADMIN_IDS", [1, 2]), patch("core.activity.is_admin", return_value=False):
            await track_quote(callback, "rub_c2c", {"title": "روبل"}, 3080)
        self.assertEqual({c.args[0] for c in sender.call_args_list}, {1, 2})
        self.assertEqual((await subscribers.get_subscriber(3))["quote_count"], 1)

    async def test_rate_notifications_all_admins_and_ban_unban_enforcement(self):
        from aiogram import Bot, Dispatcher, F, Router
        from aiogram.filters import CommandStart
        from aiogram.types import Message, Update
        from bot.access import SubscriberAccess
        from bot.handlers import admin, subscribers as handlers, user

        bot = Bot("123456:TEST_TOKEN")
        dp = Dispatcher()
        control = Router()
        control.callback_query.filter(admin.IsAdmin())
        control.callback_query.register(handlers.callbacks, F.data.startswith("subs:"))
        regular = Router()
        regular.message.outer_middleware(SubscriberAccess())
        regular.callback_query.outer_middleware(SubscriberAccess())
        regular.callback_query.register(user.cb_tier, F.data.startswith("tier:"))
        regular.message.register(user.cmd_start, CommandStart())
        dp.include_router(control)
        dp.include_router(regular)
        counter = 0
        message = {"message_id": 1, "date": 1700000000, "chat": {"id": 3, "type": "private"}, "text": "test"}
        api = AsyncMock(return_value=Message.model_validate(message))

        async def feed(uid, callback=None, text=None, event=None):
            nonlocal counter
            counter += 1
            actor = {"id": uid, "is_bot": False, "first_name": "User", "username": f"u{uid}"}
            msg = {**message, "chat": {"id": uid, "type": "private"}, "from": actor}
            body = {"update_id": counter}
            if callback:
                body["callback_query"] = {"id": event or str(counter), "from": actor, "chat_instance": "test", "data": callback, "message": msg}
            else:
                body["message"] = {**msg, "text": text, "entities": [{"type": "bot_command", "offset": 0, "length": len(text)}]}
            await dp.feed_update(bot, Update.model_validate(body))

        is_admin = lambda uid: uid in (1, 2)
        try:
            with patch.object(Bot, "__call__", api), patch.object(admin, "is_admin", is_admin), \
                 patch("core.activity.is_admin", is_admin), patch("core.activity.ADMIN_IDS", [1, 2]), \
                 patch("bot.access.is_admin", is_admin), patch.object(subscribers, "is_admin", is_admin), \
                 patch.object(user, "compute_tier_rate", AsyncMock(return_value=3080)) as compute:
                await feed(3, "tier:rub_c2c:t1", event="quote1")
                sent_to = [c.args[0].chat_id for c in api.call_args_list if type(c.args[0]).__name__ == "SendMessage"]
                self.assertEqual(set(sent_to), {1, 2})
                self.assertEqual((await subscribers.get_subscriber(3))["quote_count"], 1)
                api.reset_mock()
                await feed(3, "tier:rub_c2c:t1", event="quote1")
                self.assertFalse(any(type(c.args[0]).__name__ == "SendMessage" for c in api.call_args_list))
                await feed(1, "subs:ban:3:0")
                self.assertEqual((await subscribers.get_subscriber(3))["is_blocked"], 1)
                compute.reset_mock()
                api.reset_mock()
                await feed(3, "tier:rub_c2c:t1")
                compute.assert_not_called()
                self.assertIn("مسدود", api.call_args.args[0].text)
                await feed(3, text="/start")
                self.assertIn("مسدود", api.call_args.args[0].text)
                await feed(2, "subs:unban:3:0")
                await feed(3, "tier:rub_c2c:t1")
                self.assertEqual((await subscribers.get_subscriber(3))["quote_count"], 2)
                await feed(3, "subs:ban:3:0")  # Non-admin cannot use copied control callbacks.
                self.assertEqual((await subscribers.get_subscriber(3))["is_blocked"], 0)
                with self.assertRaises(ValueError):
                    await subscribers.set_blocked(1, True)
        finally:
            await dp.storage.close()
            await bot.session.close()

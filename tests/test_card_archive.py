import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core import banks, card_archive as archive, db


class CardArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", str(Path(self.temp.name) / "cards.db"))
        self.db_patch.start()
        await db.init_db()

    async def asyncTearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_search_normalizes_persian_letters_and_spaces(self):
        details = {"card_number": "1111111111111111"}
        entry = await archive.save_card("میر حسین كريمي", "melli", details, 1)
        for query in ("میرحسین", "کریمی", "كريمي", "میر حسین"):
            rows, count = await archive.list_cards(query)
            self.assertEqual(count, 1)
            self.assertEqual(rows[0]["id"], entry)
        await archive.save_card("علی%رضا", "mellat", details, 1)
        self.assertEqual((await archive.list_cards("%"))[1], 1)
        self.assertEqual((await archive.list_cards("_"))[1], 0)

    async def test_multiple_cards_pagination_link_edit_delete_and_deduplication(self):
        details = {"card_number": "1111111111111111"}
        first = await archive.save_card("Иван Иванов", "sber", details, 1, 42)
        duplicate = await archive.save_card("Иван Иванов", "sber", details, 1, 42)
        self.assertEqual(first, duplicate)
        for i in range(9):
            await archive.save_card("Иван Иванов", "sber", {"card_number": f"11111111111111{i:02d}"}, 1, 42)
        page1, count = await archive.list_cards("ИВАН", 42)
        page2, _ = await archive.list_cards("иван", 42, page=1)
        self.assertEqual(count, 10)
        self.assertEqual([len(page1), len(page2)], [8, 2])
        await archive.save_card("Ivan Updated", "sber", details, 1, 42, first)
        self.assertEqual((await archive.get_card(first))["owner_name"], "Ivan Updated")
        await archive.delete_card(first)
        self.assertIsNone(await archive.get_card(first))
        self.assertEqual((await archive.list_cards(user_tg_id=42))[1], 9)
        self.assertEqual((await archive.list_cards(user_tg_id=43))[1], 0)

    async def test_payout_import_and_restart_persistence(self):
        payout = "1111111111111111\n+79991112233\nИван Иванов\nСбербанк"
        entry = await archive.archive_payout(42, payout)
        self.assertIsNotNone(entry)
        self.assertEqual(await archive.archive_payout(42, payout), entry)
        await db.init_db()
        self.assertEqual((await archive.get_card(entry))["bank_id"], "sber")
        self.assertEqual((await archive.get_card(entry))["user_tg_id"], 42)
        self.assertIsNone(await archive.archive_payout(42, "unstructured text"))

    async def test_private_admin_access_to_card_details(self):
        from aiogram import Bot, Dispatcher, F, Router
        from aiogram.types import Message, Update
        from bot.handlers import admin, card_archive as handlers
        bot = Bot("123456:TEST_TOKEN")
        dispatcher = Dispatcher()
        router = Router()
        router.callback_query.filter(admin.IsAdmin(), F.message.chat.type == "private")
        router.callback_query.register(handlers.callbacks, F.data.startswith("arc:"))
        dispatcher.include_router(router)
        entry = await archive.save_card("Private User", "melli", {"card_number": "1111111111111111"}, 1)
        message = {"message_id": 1, "date": 1700000000, "chat": {"id": 1, "type": "private"}, "text": "test"}
        api = AsyncMock(return_value=Message.model_validate(message))
        async def feed(uid, chat_type="private"):
            update = Update.model_validate({"update_id": uid, "callback_query": {"id": str(uid), "chat_instance": "test", "from": {"id": uid, "is_bot": False, "first_name": "Test"},
                "message": {**message, "chat": {"id": uid, "type": chat_type}}, "data": f"arc:view:{entry}"}})
            await dispatcher.feed_update(bot, update)
        try:
            with patch.object(admin, "is_admin", lambda uid: uid == 1), patch.object(Bot, "__call__", api):
                await feed(2)
                await feed(1, "group")
                api.assert_not_called()
                await feed(1)
                self.assertIn("1111111111111111", api.call_args.args[0].text)
                self.assertIn("tg-emoji", api.call_args.args[0].text)
        finally:
            await dispatcher.storage.close()
            await bot.session.close()


class BankTests(unittest.TestCase):
    def test_bank_aliases_and_current_custom_emoji_pack(self):
        for name, bank_id in (("Т-Банк", "tbank"), ("тинькофф", "tbank"), ("Сбербанк", "sber"), ("ВТБ", "vtb"), ("Альфа-Банк", "alfa"), ("ملی", "melli")):
            self.assertEqual(banks.find_bank(name), bank_id)
            self.assertTrue(banks.emoji_id(bank_id))
            self.assertEqual(banks.bank_button(bank_id, "test").icon_custom_emoji_id, banks.emoji_id(bank_id))
        self.assertIn("Bahram_ru", banks.emoji_pack_url())

    def test_validation_escapes_invoice_and_preserves_identifiers(self):
        from bot.handlers.user import _build_invoice
        details = archive.parse_details("کارت: ۱۱۱۱ ۱۱۱۱ ۱۱۱۱ ۱۱۱۱\nتلفن: +7 999 111 22 33", "sber")
        self.assertEqual(details["card_number"], "1111111111111111")
        self.assertEqual(details["phone"], "+79991112233")
        with self.assertRaises(ValueError):
            archive.parse_details("123", "melli")
        html = _build_invoice("1111111111111111\n+79991112233\nИван <script>\nСбер", 500, as_html=True)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("tg-emoji", html)

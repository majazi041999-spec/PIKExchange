import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core import accounting, db, migrations
from core.products import get_product
from core.rates import compute_tier_rate


class LedgerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        self.db_patch.start()
        await db.init_db()

    async def asyncTearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    async def draft(self, direction="in", status="success", reference="", file_hash=""):
        return await accounting.create_draft(1, direction, {
            "amount": "۱٬۰۰۰٬۰۰۰٬۰۰۰", "currency": "rial", "status": status, "reference": reference,
        }, file_hash=file_hash)

    async def test_drafts_pending_and_void_do_not_contribute(self):
        incoming = await self.draft()
        outgoing = await self.draft("out")
        pending = await self.draft(status="pending")
        await accounting.change_entry(incoming, 0, 1, "posted")
        await accounting.change_entry(pending, 0, 1, "pending")
        self.assertEqual((await accounting.totals())["IRR"], {"in": 1000000000, "out": 0, "balance": 1000000000})
        self.assertEqual((await accounting.totals())["pending"], 1)
        await accounting.change_entry(outgoing, 0, 1, "posted")
        await accounting.change_entry(incoming, 1, 1, "void")
        self.assertEqual((await accounting.totals())["IRR"], {"in": 0, "out": 1000000000, "balance": -1000000000})
        self.assertEqual((await accounting.totals())["pending"], 1)
        await accounting.change_entry(pending, 1, 2, "settle")
        self.assertEqual((await accounting.totals())["IRR"]["in"], 1000000000)

    async def test_pending_receipt_requires_settlement_step(self):
        entry = await self.draft(status="pending")
        with self.assertRaises(accounting.LedgerError):
            await accounting.change_entry(entry, 0, 1, "posted")
        await accounting.change_entry(entry, 0, 1, "pending")
        self.assertEqual((await accounting.get_entry(entry))["status"], "pending")

    async def test_concurrent_double_confirm(self):
        entry = await self.draft()
        results = await asyncio.gather(*[
            accounting.change_entry(entry, 0, 1, "posted") for _ in range(2)
        ], return_exceptions=True)
        self.assertEqual(sum(isinstance(r, accounting.LedgerError) for r in results), 1)
        self.assertEqual((await accounting.totals())["IRR"]["in"], 1000000000)

    async def test_duplicate_reference_and_image_are_rejected(self):
        for kwargs in ({"reference": "۱۲۳-۴۵۶"}, {"file_hash": "same-photo-hash"}):
            first = await self.draft(**kwargs)
            second = await self.draft(**kwargs)
            await accounting.change_entry(first, 0, 1, "pending")
            with self.assertRaises(accounting.LedgerError):
                await accounting.change_entry(second, 0, 1, "posted")
            self.assertEqual((await accounting.get_entry(second))["status"], "draft")

    async def test_stale_edit_and_posted_edit_are_rejected(self):
        entry = await self.draft()
        await accounting.change_entry(entry, 0, 1, "note", "test")
        with self.assertRaises(accounting.LedgerError):
            await accounting.change_entry(entry, 0, 1, "posted")
        await accounting.change_entry(entry, 1, 1, "posted")
        with self.assertRaises(accounting.LedgerError):
            await accounting.change_entry(entry, 2, 1, "amount_rial", 10)

    async def test_unknown_currency_requires_manual_correction(self):
        entry = await accounting.create_draft(1, "in", {"amount": "123", "currency": "unknown"})
        with self.assertRaises(accounting.LedgerError):
            await accounting.change_entry(entry, 0, 1, "pending")
        await accounting.change_entry(entry, 0, 1, "amount_rial", 1230)
        self.assertEqual((await accounting.get_entry(entry))["amount_rial"], 1230)

    async def test_drafts_survive_database_reinitialization(self):
        entry = await self.draft()
        await db.init_db()
        self.assertEqual((await accounting.get_entry(entry))["status"], "draft")

    async def test_migration_preserves_high_tier_settings_and_single_receipt(self):
        await db.set_setting("settings_schema_version", "2")
        tiers = [
            {"key": "t1", "mult": 0.5}, {"key": "t2", "mult": 0.6},
            {"key": "t3", "mult": 0.89, "offset": -123, "min": 200000000, "max": 0},
            {"key": "s1", "mult": 0.96, "min": 0, "max": 50000000},
            {"key": "s2", "mult": 0.95, "min": 50000000, "max": 0},
        ]
        await db.set_json("products", {"rub_c2c": {"tiers": tiers}})
        await migrations.run_migrations()
        product = await get_product("rub_c2c")
        self.assertEqual([t["key"] for t in product["tiers"]], ["t1", "t2", "t3", "s1", "s2"])
        self.assertEqual([t["mult"] for t in product["tiers"]], [0.5, 0.6, 0.89, 0.96, 0.95])
        self.assertEqual(product["tiers"][2]["offset"], -123)
        self.assertEqual([(t["min"], t["max"]) for t in product["tiers"]],
                         [(0, 50000000), (50000000, 200000000), (200000000, 0), (0, 30000000), (30000000, 0)])
        await migrations.run_migrations()
        self.assertEqual(await get_product("rub_c2c"), product)
        with patch("core.rates.site_base_toman", AsyncMock(return_value=2000)):
            self.assertEqual(await compute_tier_rate(product, product["tiers"][2]), 1877)

    async def test_flat_rate_install_restores_five_choices_without_losing_rate(self):
        await db.set_setting("settings_schema_version", "3")
        await db.set_json("products", {"rub_c2c": {"tiers": [
            {"key": "t3", "mult": 0.9108, "offset": -50},
            {"key": "s1", "mult": 0.9806}, {"key": "s2", "mult": 0.9787},
        ]}})
        await migrations.run_migrations()
        product = await get_product("rub_c2c")
        self.assertEqual(len(product["tiers"]), 5)
        self.assertEqual([t["mult"] for t in product["tiers"][:3]], [0.9108] * 3)
        self.assertEqual([t["offset"] for t in product["tiers"][:3]], [-50] * 3)
        from bot.keyboards import tiers_kb
        kb = tiers_kb("rub_c2c", product["tiers"])
        labels = [b.text for row in kb.inline_keyboard for b in row if b.callback_data.startswith("tier:")]
        self.assertEqual(len(labels), 5)
        self.assertIn("زیر ۳۰ میلیون", labels[3])
        self.assertIn("بالای ۳۰ میلیون", labels[4])

    async def test_fresh_install_and_admin_controls(self):
        await migrations.run_migrations()
        from bot.handlers.admin import _product_view
        with patch("bot.handlers.admin.compute_tier_rate", AsyncMock(return_value=1800)):
            _, kb = await _product_view("rub_c2c")
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn("pe:rub_c2c:toffset:t3", callbacks)
        self.assertIn("pe:rub_c2c:tmult:t3", callbacks)
        self.assertNotIn("pe:rub_c2c:tmin:t3", callbacks)
        self.assertNotIn("pe:rub_c2c:tmax:t3", callbacks)
        for key in ("t1", "t2", "t3", "s1", "s2"):
            self.assertIn(f"pe:rub_c2c:toffset:{key}", callbacks)
            self.assertIn(f"pe:rub_c2c:tmult:{key}", callbacks)

    async def test_migration_selects_offset_only_when_configured(self):
        await db.set_setting("settings_schema_version", "4")
        await db.set_json("products", {"rub_c2c": {"tiers": [
            {"key": "t1", "mult": 0.9197, "offset": -270},
            {"key": "t2", "mult": 0.9153, "offset": 0},
            {"key": "t3", "mult": 0.9108, "offset": -200, "pricing_mode": "multiplier"},
        ]}})
        await migrations.run_migrations()
        product = await get_product("rub_c2c")
        self.assertEqual([t["pricing_mode"] for t in product["tiers"]], ["offset", "multiplier", "multiplier"])
        await migrations.run_migrations()
        self.assertEqual(await get_product("rub_c2c"), product)

    async def test_manual_name_edit_and_report_pagination(self):
        from bot.handlers.accounting import show_report
        entry_id = await accounting.create_manual_draft(1, "in", "میرحسین - ۱۰۰٬۰۰۰٬۰۰۰")
        await accounting.change_entry(entry_id, 0, 1, "name", "میرحسین <b>&</b>")
        await accounting.change_entry(entry_id, 1, 1, "posted")
        msg = AsyncMock()
        await show_report(msg)
        report = msg.answer.call_args.args[0]
        self.assertIn("میرحسین &lt;b&gt;&amp;&lt;/b&gt;", report)
        self.assertIn("۱۰۰,۰۰۰,۰۰۰ تومان", report)
        for i in range(8):
            await accounting.create_manual_draft(1, "out", f"شخص {i} - ۱۰۰")
        await show_report(msg)
        first = msg.answer.call_args
        self.assertLess(len(first.args[0]), 4096)
        self.assertIn("acc:list:8", [b.callback_data for row in first.kwargs["reply_markup"].inline_keyboard for b in row])
        await show_report(msg, 8)
        self.assertIn("میرحسین", msg.answer.call_args.args[0])
        self.assertNotIn("acc:list:16", [b.callback_data for row in msg.answer.call_args.kwargs["reply_markup"].inline_keyboard for b in row])

    async def test_wallet_removed_but_existing_balances_not_destroyed(self):
        from bot.keyboards import main_menu
        from bot.handlers.admin import admin_menu_kb
        user_buttons = [b.callback_data for row in (await main_menu(True)).inline_keyboard for b in row]
        admin_buttons = [b.callback_data for row in admin_menu_kb().inline_keyboard for b in row]
        self.assertNotIn("wallet", user_buttons)
        self.assertNotIn("adm:wallet", admin_buttons)
        user = await db.get_or_create_user(42)
        conn = await db._connect()
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN wallet INTEGER DEFAULT 12345")
            await conn.execute("ALTER TABLE users ADD COLUMN wallet_rub INTEGER DEFAULT 567")
            await conn.commit()
        finally:
            await conn.close()
        await db.init_db()
        await db.update_user(user["id"], wallet=0)
        saved = await db.get_user_by_tg(42)
        self.assertEqual(saved["wallet"], 12345)
        self.assertEqual(saved["wallet_rub"], 567)

    async def test_telegram_admin_flow_and_access_filters(self):
        from aiogram import Bot, Dispatcher
        from aiogram.types import Message, Update
        from bot.handlers import accounting as handlers, admin, user

        bot = Bot("123456:TEST_TOKEN")
        dp = Dispatcher()
        dp.include_router(admin.router)
        dp.include_router(handlers.router)
        dp.include_router(user.router)
        message = {"message_id": 1, "date": 1700000000, "chat": {"id": 42, "type": "private"}}
        counter = 0

        async def feed(text=None, callback=None, uid=42, private=True, photo=False):
            nonlocal counter
            counter += 1
            actor = {"id": uid, "is_bot": False, "first_name": "Test"}
            msg = {**message, "chat": {"id": uid if private else -123, "type": "private" if private else "group"}}
            payload = {"update_id": counter}
            if callback:
                payload["callback_query"] = {"id": str(counter), "from": actor, "chat_instance": "test", "data": callback,
                                              "message": {**msg, "from": {"id": 123456, "is_bot": True, "first_name": "Bot"}, "text": "test"}}
            else:
                payload["message"] = {**msg, "from": actor}
                if photo:
                    payload["message"]["photo"] = [{"file_id": "test-photo", "file_unique_id": "photo-unique", "width": 800, "height": 1200, "file_size": 6}]
                else:
                    payload["message"]["text"] = text
                if text and text.startswith("/"):
                    payload["message"]["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text)}]
            return await dp.feed_update(bot, Update.model_validate(payload))

        api = AsyncMock(return_value=Message.model_validate({**message, "text": "ok"}))
        try:
            with patch.object(admin, "is_admin", lambda uid: uid == 42), patch.object(Bot, "__call__", api):
                await feed(callback="acc:home", uid=43)
                await feed(callback="acc:home", private=False)
                api.assert_not_called()
                await feed(text="/accounting")
                self.assertTrue(api.called)
                await feed(callback="acc:new:in")
                await feed(text="میرحسین - ۱۰۰٬۰۰۰٬۰۰۰ تومان")
                entry = (await accounting.list_entries())[0]
                self.assertEqual(entry["amount_rial"], 1000000000)
                await feed(callback=f"acc:do:posted:{entry['id']}:{entry['revision']}", uid=43)
                self.assertEqual((await accounting.totals())["IRR"]["in"], 0)
                await feed(callback=f"acc:do:posted:{entry['id']}:{entry['revision']}")
                self.assertEqual((await accounting.totals())["IRR"]["in"], 1000000000)
                with patch("core.rates.site_base_toman", AsyncMock(return_value=3350)):
                    await feed(callback="pe:rub_c2c:toffset:t1")
                    await feed(text="-۲۷۰")
                    product = await get_product("rub_c2c")
                    self.assertEqual(product["tiers"][0]["pricing_mode"], "offset")
                    self.assertEqual(await compute_tier_rate(product, product["tiers"][0]), 3080)
                    await feed(callback="pe:rub_c2c:tmult:t1")
                    await feed(text="۰.۹")
                    product = await get_product("rub_c2c")
                    self.assertEqual(product["tiers"][0]["pricing_mode"], "multiplier")
                    self.assertEqual(await compute_tier_rate(product, product["tiers"][0]), 3015)
                    await feed(callback="pe:rub_c2c:tmode:t1")
                    product = await get_product("rub_c2c")
                    self.assertEqual(await compute_tier_rate(product, product["tiers"][0]), 3080)
                    await feed(callback="tier:rub_c2c:t1")
                    self.assertIn("۳,۰۸۰", api.call_args.args[0].text)
                await feed(callback="acc:new:out")

                before = len(await accounting.list_entries())
                with patch.object(Bot, "download", AsyncMock()) as download:
                    await feed(photo=True)
                    download.assert_not_called()
                self.assertEqual(len(await accounting.list_entries()), before)
                await feed(text="حسین - ۱۵٬۰۰۰٬۰۰۰")
                outgoing = (await accounting.list_entries())[0]
                self.assertEqual(outgoing["currency"], "RUB")
                self.assertEqual(outgoing["amount_minor"], 1500000000)
                self.assertEqual(accounting.entry_name(outgoing), "حسین")
                await feed(callback=f"acc:do:posted:{outgoing['id']}:{outgoing['revision']}")
                self.assertEqual((await accounting.totals())["RUB"]["out"], 1500000000)
                await feed(callback="acc:home")
                report = api.call_args.args[0].text
                self.assertIn("میرحسین", report)
                self.assertIn("۱۰۰,۰۰۰,۰۰۰ تومان", report)
                self.assertIn("۱۵,۰۰۰,۰۰۰ روبل", report)
                self.assertNotIn("۸۵,۰۰۰,۰۰۰ تومان", report)
                # A second click must not add another posting.
                await feed(callback=f"acc:do:posted:{entry['id']}:{entry['revision']}")
                self.assertEqual((await accounting.totals())["IRR"]["in"], 1000000000)
                # Old update buttons must never broadcast to users again.
                api.reset_mock()
                with patch.object(admin, "get_all_user_tg_ids", AsyncMock()) as recipients:
                    await feed(callback="updbc:old-build")
                    recipients.assert_not_called()
                self.assertEqual(api.call_count, 1)
                self.assertEqual(type(api.call_args.args[0]).__name__, "AnswerCallbackQuery")
        finally:
            await dp.storage.close()
            await bot.session.close()


class AmountTests(unittest.TestCase):
    def test_name_and_toman_line(self):
        for text in ("میرحسین - ۱۰۰٬۰۰۰٬۰۰۰", "میرحسین-100,000,000 تومان", "میرحسین — ١٠٠٠٠٠٠٠٠"):
            self.assertEqual(accounting.parse_manual_entry(text), ("میرحسین", 1000000000))
        for text in ("میرحسین", " - 100", "میرحسین - -100", "میرحسین - 100 ریال", "میرحسین - 0", "123 - 100", "میرحسین - 10\nعلی - 20"):
            with self.assertRaises(ValueError):
                accounting.parse_manual_entry(text)

    def test_exact_amounts_and_sample_units(self):
        for value, expected in [("۱٬۰۰۰٬۰۰۰٬۰۰۰", 1000000000), ("۲,۱۰۰,۰۰۰,۰۰۰", 2100000000),
                                ("۸۸۰٬۰۰۰٬۰۰۰", 880000000), ("۱۵۰٬۰۰۰٬۰۰۰", 150000000)]:
            self.assertEqual(accounting.parse_amount(value), expected)
        self.assertEqual(accounting.parse_amount("۱۰۰٬۰۰۰٬۰۰۰", "toman"), 1000000000)
        self.assertEqual(accounting.parse_amount("1.1", "toman"), 11)
        self.assertEqual(accounting.display_amount(11), "۱.۱ تومان")
        for value in ("nan", "inf", "-1", "0", "1.5", "1e9", str(10**16)):
            with self.assertRaises(ValueError):
                accounting.parse_amount(value)


if __name__ == "__main__":
    unittest.main()

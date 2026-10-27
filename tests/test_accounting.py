import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from core import accounting, db, migrations, receipts
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
        self.assertEqual(await accounting.totals(), {"in": 1000000000, "out": 0, "pending": 1})
        await accounting.change_entry(outgoing, 0, 1, "posted")
        await accounting.change_entry(incoming, 1, 1, "void")
        self.assertEqual(await accounting.totals(), {"in": 0, "out": 1000000000, "pending": 1})
        await accounting.change_entry(pending, 1, 2, "settle")
        self.assertEqual((await accounting.totals())["in"], 1000000000)

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
        self.assertEqual((await accounting.totals())["in"], 1000000000)

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
        self.assertEqual([t["key"] for t in product["tiers"]], ["t3", "s1", "s2"])
        self.assertEqual(product["tiers"][0]["mult"], 0.89)
        self.assertEqual(product["tiers"][0]["offset"], -123)
        self.assertNotIn("min", product["tiers"][0])
        self.assertNotIn("max", product["tiers"][0])
        self.assertEqual(product["tiers"][1:], tiers[3:])
        await migrations.run_migrations()
        self.assertEqual(await get_product("rub_c2c"), product)
        with patch("core.rates.site_base_toman", AsyncMock(return_value=2000)):
            self.assertEqual(await compute_tier_rate(product, product["tiers"][0]), 1657)

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
                await feed(callback="acc:manual")
                await feed(text="۱۰۰٬۰۰۰٬۰۰۰ تومان")
                entry = (await accounting.list_entries())[0]
                self.assertEqual(entry["amount_rial"], 1000000000)
                await feed(callback=f"acc:do:posted:{entry['id']}:{entry['revision']}", uid=43)
                self.assertEqual((await accounting.totals())["in"], 0)
                await feed(callback=f"acc:do:posted:{entry['id']}:{entry['revision']}")
                self.assertEqual((await accounting.totals())["in"], 1000000000)
                await feed(callback="acc:new:out")

                async def download(file, destination):
                    destination.write(b"\xff\xd8\xffimg")

                with patch.object(Bot, "download", AsyncMock(side_effect=download)), patch.object(handlers, "read_receipt", AsyncMock(return_value={
                    "amount": "150000000", "currency": "rial", "status": "pending", "warning": "در انتظار چرخه پل",
                })):
                    await feed(photo=True)
                photo_entry = (await accounting.list_entries())[0]
                self.assertEqual(photo_entry["file_id"], "test-photo")
                self.assertEqual(photo_entry["amount_rial"], 150000000)
                self.assertEqual(photo_entry["status"], "draft")
                self.assertEqual((await accounting.totals())["out"], 0)
                # A second click must not add another posting.
                await feed(callback=f"acc:do:posted:{entry['id']}:{entry['revision']}")
                self.assertEqual((await accounting.totals())["in"], 1000000000)
        finally:
            await dp.storage.close()
            await bot.session.close()


class AmountTests(unittest.TestCase):
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


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_key_has_actionable_error(self):
        with patch.object(receipts, "OPENAI_API_KEY", ""):
            with self.assertRaisesRegex(receipts.ReceiptReadError, "OPENAI_API_KEY"):
                await receipts.read_receipt(b"\xff\xd8\xfftest")

    async def test_provider_json_is_validated_and_store_disabled(self):
        value = {key: None for key in receipts.TEXT_FIELDS}
        value.update(amount="1000000000", currency="rial", status="pending")
        payload = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(value)}]}]}
        response = httpx.Response(200, json=payload, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        post = AsyncMock(return_value=response)
        with patch.object(receipts, "OPENAI_API_KEY", "test"), patch.object(httpx.AsyncClient, "post", post):
            result = await receipts.read_receipt(b"\xff\xd8\xfftest")
        self.assertEqual(result["status"], "pending")
        self.assertFalse(post.call_args.kwargs["json"]["store"])

    async def test_provider_failure_does_not_expose_credentials(self):
        response = httpx.Response(401, text="secret", request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        with patch.object(receipts, "OPENAI_API_KEY", "secret"), patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
            with self.assertRaises(receipts.ReceiptReadError) as raised:
                await receipts.read_receipt(b"\xff\xd8\xfftest")
        self.assertNotIn("secret", str(raised.exception))

    async def test_non_image_and_oversized_files_rejected(self):
        for data in (b"%PDF-test", b"\xff\xd8\xff" + b"0" * receipts.MAX_IMAGE_BYTES):
            with self.assertRaises(receipts.ReceiptReadError):
                await receipts.read_receipt(data)


if __name__ == "__main__":
    unittest.main()

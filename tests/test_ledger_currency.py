import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import accounting as ledger, db


class CurrencyLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(db, 'DB_PATH', str(Path(self.temp.name) / 'ledger.db'))
        self.patch.start()
        await db.init_db()

    async def asyncTearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    async def test_income_toman_outgoing_roubles_never_share_a_total(self):
        incoming = await ledger.create_manual_draft(1, 'in', 'میرحسین - ۱۰۰٬۰۰۰٬۰۰۰ تومان')
        outgoing = await ledger.create_manual_draft(1, 'out', 'حسین - ۱۲٬۳۴۵٫۶۷ روبل')
        waiting = await ledger.create_manual_draft(1, 'out', 'علی - ۵۰۰ روبل')
        await ledger.change_entry(incoming, 0, 1, 'posted')
        await ledger.change_entry(outgoing, 0, 1, 'posted')
        await ledger.change_entry(waiting, 0, 1, 'pending')
        entry = await ledger.get_entry(outgoing)
        self.assertEqual(entry['currency'], 'RUB')
        self.assertEqual(entry['amount_minor'], 1234567)
        self.assertEqual(entry['amount_rial'], 0)
        self.assertEqual(ledger.display_entry(entry), '۱۲,۳۴۵.۶۷ روبل')
        sums = await ledger.totals()
        self.assertEqual(sums['IRR'], {'in': 1000000000, 'out': 0, 'balance': 1000000000})
        self.assertEqual(sums['RUB'], {'in': 0, 'out': 1234567, 'balance': -1234567})
        self.assertNotIn('balance', sums)
        self.assertEqual(sums['pending'], 1)
        from bot.handlers.accounting import report_text
        report = report_text(await ledger.list_entries(), sums)
        self.assertIn('۱۲,۳۴۵.۶۷ روبل', report)
        self.assertIn('۱۰۰,۰۰۰,۰۰۰ تومان', report)
        self.assertNotIn('💰 مانده', report)
        with self.assertRaises(ValueError):
            await ledger.create_manual_draft(1, 'out', 'حسین - ۱۰۰ تومان')

    async def test_direction_change_clears_amount_until_new_unit_is_entered(self):
        entry_id = await ledger.create_manual_draft(1, 'in', 'میرحسین - ۱۰۰۰')
        await ledger.change_entry(entry_id, 0, 1, 'direction', 'out')
        entry = await ledger.get_entry(entry_id)
        self.assertEqual((entry['currency'], entry['amount_minor']), ('RUB', 0))
        with self.assertRaises(ledger.LedgerError):
            await ledger.change_entry(entry_id, 1, 1, 'posted')
        with self.assertRaises(ledger.LedgerError):
            await ledger.change_entry(entry_id, 1, 1, 'amount_rial', 1234)
        await ledger.change_entry(entry_id, 1, 1, 'amount', '۱۲۳,۴۵ روبل')
        await ledger.change_entry(entry_id, 2, 1, 'posted')
        self.assertEqual((await ledger.totals())['RUB']['out'], 12345)

    async def test_atomic_edit_changes_currency_and_amount_once(self):
        entry_id = await ledger.create_manual_draft(1, 'in', 'میرحسین - ۱۰۰۰')
        values = {'name': 'حسین', 'direction': 'out', 'amount': '۶۰٫۲۵', 'currency': 'RUB'}
        await ledger.change_entry(entry_id, 0, 1, 'edit', values)
        entry = await ledger.get_entry(entry_id)
        self.assertEqual((entry['currency'], entry['amount_minor'], entry['revision']), ('RUB', 6025, 1))
        with self.assertRaises(ledger.LedgerError):
            await ledger.change_entry(entry_id, 0, 1, 'posted')
        await ledger.change_entry(entry_id, 1, 1, 'posted')
        self.assertEqual((await ledger.totals())['IRR']['in'], 0)
        self.assertEqual((await ledger.totals())['RUB']['out'], 6025)

    async def test_legacy_rial_records_are_preserved_on_upgrade(self):
        entry_id = await ledger.create_draft(1, 'out', {'name': 'قدیمی', 'amount': '۱۰۰۰', 'currency': 'toman'})
        await ledger.change_entry(entry_id, 0, 1, 'posted')
        before = await ledger.get_entry(entry_id)
        conn = await db._connect()
        try:
            await conn.execute('ALTER TABLE ledger DROP COLUMN amount_minor')
            await conn.execute('ALTER TABLE ledger DROP COLUMN currency')
            await conn.commit()
        finally:
            await conn.close()
        await db.init_db()
        await db.init_db()
        after = await ledger.get_entry(entry_id)
        self.assertEqual(after['currency'], 'IRR')
        self.assertEqual(after['amount_minor'], 10000)
        for key in ('amount_rial', 'status', 'revision', 'created_at', 'updated_at'):
            self.assertEqual(before[key], after[key])
        self.assertEqual((await ledger.totals())['IRR']['out'], 10000)
        self.assertEqual((await ledger.totals())['RUB']['out'], 0)


class RoubleAmountTests(unittest.TestCase):
    def test_kopecks_persian_and_russian_decimal_notation(self):
        for value, expected in [('۰٫۰۱', 1), ('۱۲۳,۴۵', 12345), ('1,234.56', 123456), ('۱٬۲۳۴٫۵۶', 123456), ('۱۵۰۰۰', 1500000)]:
            self.assertEqual(ledger.parse_amount(value, 'rub'), expected)
        for invalid in ('1.001', '1,234,56', '-1', '0', 'NaN', '1e3'):
            with self.assertRaises(ValueError):
                ledger.parse_amount(invalid, 'rub')

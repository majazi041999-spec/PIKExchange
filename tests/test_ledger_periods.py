import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core import accounting as ledger, db
from core.jalali import TEHRAN_TZ
from core.ledger_periods import period_bounds


class CalendarPeriodTests(unittest.TestCase):
    def test_jalali_calendar_boundaries_and_leap_year(self):
        cases = [
            ('day', '2025-03-20', '2025-03-20', '2025-03-21'),
            ('month', '2025-03-20', '2025-02-19', '2025-03-21'),
            ('quarter', '2024-08-10', '2024-06-21', '2024-09-22'),
            ('half', '2024-08-10', '2024-03-20', '2024-09-22'),
            ('year', '2025-03-20', '2024-03-20', '2025-03-21'),
            ('year', '2025-03-21', '2025-03-21', '2026-03-21'),
        ]
        for period, anchor, start, end in cases:
            with self.subTest(period=period, anchor=anchor):
                result = period_bounds(period, anchor)
                self.assertEqual(result['start'], start + ' 00:00:00')
                self.assertEqual(result['end'], end + ' 00:00:00')
                self.assertEqual(period_bounds(period, result['next'])['previous'], start)
                self.assertEqual(period_bounds(period, result['previous'])['next'], start)

    def test_tehran_today_and_invalid_input(self):
        with patch('core.ledger_periods.tehran_now', return_value=datetime(2026, 10, 30, 0, 1, tzinfo=TEHRAN_TZ)):
            self.assertEqual(period_bounds('day')['start'], '2026-10-30 00:00:00')
        for period, anchor in [('weekly', None), ('month', 'bad'), ('day', '2025-02-30')]:
            with self.assertRaises(ValueError):
                period_bounds(period, anchor)


class PeriodReportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(db, 'DB_PATH', str(Path(self.temp.name) / 'ledger.db'))
        self.patch.start()
        await db.init_db()

    async def asyncTearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    async def add(self, direction, amount, status, stamp):
        key = await ledger.create_manual_draft(1, direction, 'مشتری - ' + amount)
        if status != 'draft':
            await ledger.change_entry(key, 0, 1, status)
        conn = await db._connect()
        try:
            await conn.execute('UPDATE ledger SET created_at=? WHERE id=?', (stamp, key))
            await conn.commit()
        finally:
            await conn.close()
        return key

    async def test_range_totals_statuses_and_paging_use_same_filter(self):
        await self.add('in', '999', 'posted', '2024-03-19 23:59:59')
        first = await self.add('in', '1000', 'posted', '2024-03-20 00:00:00')
        await self.add('out', '12.34', 'posted', '2024-03-20 23:59:59')
        await self.add('out', '30', 'pending', '2024-03-20 12:00:00')
        await self.add('in', '800', 'void', '2024-03-20 13:00:00')
        await self.add('in', '999', 'posted', '2024-03-21 00:00:00')
        report = await ledger.period_report('day', '2024-03-20', 2)
        self.assertEqual(report['total'], 4)
        self.assertEqual(len(report['rows']), 2)
        self.assertEqual(report['totals']['IRR']['in'], 10000)
        self.assertEqual(report['totals']['RUB']['out'], 1234)
        self.assertEqual(report['totals']['posted'], 2)
        self.assertEqual(report['totals']['pending'], 1)
        second = await ledger.period_report('day', '2024-03-20', 2, 2)
        self.assertEqual(second['rows'][-1]['id'], first)
        self.assertEqual(second['totals'], report['totals'])
        empty = await ledger.period_report('day', '2024-03-22')
        self.assertEqual((empty['total'], empty['totals']['posted']), (0, 0))
        self.assertEqual((await ledger.period_report())['totals'], await ledger.totals())

    async def test_bot_controls_preserve_period_and_anchor(self):
        from bot.handlers.accounting import show_report
        for i in range(9):
            await self.add('in', '100', 'posted', '2024-03-20 12:00:00')
        msg = AsyncMock()
        await show_report(msg, period='month', anchor='2024-03-20')
        call = msg.answer.call_args
        buttons = [b.callback_data for row in call.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertIn('acc:period:month:2024-03-20:8', buttons)
        self.assertIn('acc:period:month:2024-04-20:0', buttons)
        self.assertIn('۱۴۰۳/۰۱/۰۱', call.args[0])
        self.assertTrue(all(len(b.encode()) <= 64 for b in buttons))

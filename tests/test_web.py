import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from core import config, db, web_auth, accounting
from core.products import get_product
from webapp.server import create_app


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(db, 'DB_PATH', str(Path(self.temp.name) / 'test.db')), patch.object(config, 'ADMIN_IDS', [1, 2])]
        for p in self.patches:
            p.start()
        self.bot = SimpleNamespace(session=SimpleNamespace(close=AsyncMock()), send_message=AsyncMock(), download=AsyncMock())
        self.client = TestClient(TestServer(create_app(False, self.bot)), cookie_jar=CookieJar(unsafe=True), headers={"X-PIK-UI-Version": "2"})
        await self.client.start_server()
        await web_auth.set_credentials(1, 'adminone', 'Test password.2026')

    async def asyncTearDown(self):
        await self.client.close()
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    async def login(self):
        r = await self.client.post('/api/login', json={'username': 'adminone', 'password': 'Test password.2026'})
        self.assertEqual(r.status, 200)
        self.assertIn('HttpOnly', r.headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', r.headers['Set-Cookie'])
        return (await r.json())['csrf']

    async def post(self, route, data, csrf):
        return await self.client.post('/api/' + route, json=data, headers={'X-CSRF-Token': csrf})

    async def test_access_csrf_origin_and_reset_revocation(self):
        self.assertEqual((await self.client.get('/api/subscribers')).status, 401)
        self.assertEqual((await self.client.get('/api/media/tx/1')).status, 401)
        csrf = await self.login()
        self.assertEqual((await self.client.post('/api/settings', json={'suspended': '1'})).status, 403)
        self.assertEqual((await self.client.post('/api/settings', json={'suspended': '1'}, headers={'X-CSRF-Token': csrf, 'Origin': 'https://attacker.invalid'})).status, 403)
        self.assertEqual(await db.get_setting('suspended'), '')
        await web_auth.set_credentials(1, 'adminone', 'A different password.2026')
        self.assertEqual((await self.client.get('/api/me')).status, 401)
        with self.assertRaises(ValueError):
            await web_auth.set_credentials(99, 'intruder', 'Not an admin password')

    async def test_ledger_period_filter_and_invalid_period(self):
        await self.login()
        key = await accounting.create_manual_draft(1, 'out', 'حسین - ۱۲٫۵۰')
        await accounting.change_entry(key, 0, 1, 'posted')
        conn = await db._connect()
        try:
            await conn.execute("UPDATE ledger SET created_at='2024-03-20 00:00:00' WHERE id=?", (key,))
            await conn.commit()
        finally:
            await conn.close()
        response = await self.client.get('/api/ledger?period=day&anchor=2024-03-20')
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual((data['total'], data['totals']['RUB']['out']), (1, 1250))
        self.assertEqual(data['period']['range_label'], '۱۴۰۳/۰۱/۰۱ تا ۱۴۰۳/۰۱/۰۱')
        data = await (await self.client.get('/api/ledger?period=day&anchor=2024-03-21')).json()
        self.assertEqual((data['total'], data['totals']['RUB']['out']), (0, 0))
        self.assertEqual((await self.client.get('/api/ledger?period=unknown')).status, 400)
        self.assertEqual((await self.client.get('/api/ledger?period=month&anchor=wrong')).status, 400)

    async def test_ledger_confirmation_balance_and_stale_revision(self):
        csrf = await self.login()
        r = await self.post('ledger', {'action': 'create', 'direction': 'in', 'name': 'میرحسین', 'amount': '۱۰۰٬۰۰۰٬۰۰۰', 'currency': 'IRR'}, csrf)
        entry_id = (await r.json())['id']
        self.assertEqual((await accounting.totals())['IRR']['in'], 0)
        data = {'id': entry_id, 'revision': 0, 'action': 'posted'}
        results = await asyncio.gather(self.post('ledger', data, csrf), self.post('ledger', data, csrf))
        self.assertEqual(sorted(r.status for r in results), [200, 400])
        self.assertEqual((await accounting.totals())['IRR']['in'], 1_000_000_000)
        await self.post('ledger', {'id': entry_id, 'revision': 1, 'action': 'void'}, csrf)
        self.assertEqual((await accounting.totals())['IRR']['in'], 0)

    async def test_offset_mode_fixed_tiers_and_bans(self):
        csrf = await self.login()
        before = await get_product('rub_c2c')
        r = await self.post('products', {'id': 'rub_c2c', 'tiers': [{'key': 't1', 'offset': '-۲۷۰', 'pricing_mode': 'offset', 'min': 999, 'max': 999}]}, csrf)
        self.assertEqual(r.status, 200)
        after = await get_product('rub_c2c')
        self.assertEqual(after['tiers'][0]['offset'], -270)
        self.assertEqual(after['tiers'][0]['max'], before['tiers'][0]['max'])
        self.assertEqual(after['tiers'][0]['mult'], before['tiers'][0]['mult'])
        from core.rates import compute_tier_rate
        with patch('core.rates.site_base_toman', AsyncMock(return_value=1000)):
            self.assertEqual(await compute_tier_rate(after, after['tiers'][0]), 730)
        await db.get_or_create_user(9, 'sample', 'نمونه')
        self.assertEqual((await self.post('block', {'id': 9, 'blocked': True}, csrf)).status, 200)
        self.assertEqual((await db.get_user_by_tg(9))['is_blocked'], 1)
        self.assertEqual((await self.post('block', {'id': 1, 'blocked': True}, csrf)).status, 400)

    async def test_transaction_review_has_one_atomic_winner(self):
        csrf = await self.login()
        u = await db.get_or_create_user(9, 'sample', 'نمونه')
        tx = await db.create_transaction(u['id'], 9, 'rub_c2c', 'خرید روبل', 2000, unit='روبل')
        a, b = await asyncio.gather(self.post('transaction', {'id': tx, 'action': 'approve', 'amount': 10000000}, csrf), self.post('transaction', {'id': tx, 'action': 'reject'}, csrf))
        self.assertEqual(sorted([a.status, b.status]), [200, 400])
        self.assertIn((await db.get_transaction(tx))['status'], ('completed', 'canceled'))

    async def test_outgoing_roubles_explicit_unit_and_atomic_web_edit(self):
        csrf = await self.login()
        data = {'action': 'create', 'direction': 'out', 'name': 'حسین', 'amount': '۱۲۳٫۴۵', 'currency': 'RUB'}
        r = await self.post('ledger', data, csrf)
        self.assertEqual(r.status, 200)
        entry_id = (await r.json())['id']
        self.assertEqual((await accounting.get_entry(entry_id))['amount_minor'], 12345)
        self.assertEqual((await self.post('ledger', {**data, 'currency': 'IRR'}, csrf)).status, 400)
        self.assertEqual((await self.client.get('/api/ledger', headers={'X-PIK-UI-Version': '1'})).status, 409)
        values = {'name': 'حسین', 'direction': 'out', 'currency': 'RUB', 'amount': '۹۹۹٫۹۵'}
        r = await self.post('ledger', {'id': entry_id, 'revision': 0, 'action': 'edit', 'value': values}, csrf)
        self.assertEqual(r.status, 200)
        entry = await accounting.get_entry(entry_id)
        self.assertEqual((entry['amount_minor'], entry['currency'], entry['revision']), (99995, 'RUB', 1))
        await self.post('ledger', {'id': entry_id, 'revision': 1, 'action': 'posted'}, csrf)
        self.assertEqual((await accounting.totals())['RUB']['out'], 99995)
        self.assertEqual((await accounting.totals())['IRR']['out'], 0)

    async def test_throttle_and_revoked_admin(self):
        for i in range(12):
            with self.assertRaises(ValueError):
                await web_auth.login('unknown', 'Invalid password.2026', '1.2.3.4')
        with self.assertRaisesRegex(ValueError, '۱۵ دقیقه'):
            await web_auth.login('adminone', 'Test password.2026', '1.2.3.4')
        await self.login()
        with patch.object(config, 'ADMIN_IDS', [2]):
            self.assertEqual((await self.client.get('/api/me')).status, 401)


if __name__ == '__main__':
    unittest.main()

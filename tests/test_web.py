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
        self.client = TestClient(TestServer(create_app(False, self.bot)), cookie_jar=CookieJar(unsafe=True))
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

    async def test_ledger_confirmation_balance_and_stale_revision(self):
        csrf = await self.login()
        r = await self.post('ledger', {'action': 'create', 'direction': 'in', 'name': 'میرحسین', 'amount': '۱۰۰٬۰۰۰٬۰۰۰'}, csrf)
        entry_id = (await r.json())['id']
        self.assertEqual((await accounting.totals())['in'], 0)
        data = {'id': entry_id, 'revision': 0, 'action': 'posted'}
        results = await asyncio.gather(self.post('ledger', data, csrf), self.post('ledger', data, csrf))
        self.assertEqual(sorted(r.status for r in results), [200, 400])
        self.assertEqual((await accounting.totals())['in'], 1_000_000_000)
        await self.post('ledger', {'id': entry_id, 'revision': 1, 'action': 'void'}, csrf)
        self.assertEqual((await accounting.totals())['in'], 0)

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

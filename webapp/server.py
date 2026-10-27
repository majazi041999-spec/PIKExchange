"""Run with python -m webapp.server behind the deployment's HTTPS proxy."""
import asyncio
import base64
import hmac
import io
import json
import logging
import math
import os
from pathlib import Path

from aiohttp import web
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BufferedInputFile

from core import accounting, card_archive, cards, subscribers, web_auth
from core.banks import BANKS
from core.config import BOT_TOKEN
from core.db import _connect, _now, get_json, get_setting, set_json, set_setting, get_transaction, transaction_stats, resolve_transaction
from core.market import SOURCES, fetch_market
from core.products import get_products, get_product, save_product
from core.rates import compute_tier_rate, compute_simple_rate
from core.utils import en_digits

ROOT = Path(__file__).resolve().parent
PUBLIC_URL = os.getenv('PIK_PUBLIC_URL', 'https://65.109.182.47:9443')
COOKIE = 'pik_session'
SECURE = web.AppKey('secure', bool)
BOT = web.AppKey('bot', object)
JOBS = web.AppKey('jobs', set)
logger = logging.getLogger('pik.web')


async def query(sql, params=(), one=False):
    conn = await _connect()
    try:
        cur = await conn.execute(sql, params)
        return dict(row) if one and (row := await cur.fetchone()) else None if one else [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()


async def execute(sql, params=()):
    conn = await _connect()
    try:
        cur = await conn.execute(sql, params)
        await conn.commit()
        return cur.lastrowid
    finally:
        await conn.close()


def text_field(data, key, limit=4000, required=False):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError('مقدار «' + key + '» معتبر نیست.')
    return value.strip()


def number(value, minimum=-10**12, maximum=10**12):
    try:
        value = float(en_digits(value).replace(',', '').replace('٬', '').replace('٫', '.').replace('−', '-'))
    except (TypeError, ValueError):
        raise ValueError('عدد معتبر وارد کنید.')
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('عدد خارج از محدوده مجاز است.')
    return value


@web.middleware
async def security(request, handler):
    try:
        if request.path.startswith('/api/'):
            if request.method not in ('GET', 'HEAD'):
                origin = request.headers.get('Origin')
                allowed = PUBLIC_URL if request.app[SECURE] else f'http://{request.host}'
                if origin and origin != allowed:
                    raise web.HTTPForbidden(text='درخواست از مبدأ نامعتبر است.')
            if request.path != '/api/login':
                auth = await web_auth.session(request.cookies.get(COOKIE))
                if not auth:
                    return web.json_response({'error': 'برای ادامه وارد شوید.'}, status=401)
                request['auth'] = auth
                if request.path in ('/api/ledger', '/api/dashboard') and request.headers.get('X-PIK-UI-Version') != '2':
                    return web.json_response({'error': 'واحدهای حسابداری به‌روز شده‌اند؛ صفحهٔ مرورگر را یک‌بار بارگذاری مجدد کنید.'}, status=409, headers={'Cache-Control': 'no-store'})
                if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), auth['csrf']):
                    raise web.HTTPForbidden(text='نشست تغییر کرده؛ صفحه را تازه کنید.')
        response = await handler(request)
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response
    except (ValueError, KeyError) as exc:
        return web.json_response({'error': str(exc)}, status=400, headers={'Cache-Control': 'no-store'})
    except web.HTTPException:
        raise
    except Exception as exc:
        logger.error('request %s failed (%s)', request.path, type(exc).__name__)
        return web.json_response({'error': 'انجام عملیات ممکن نشد؛ دوباره امتحان کنید.'}, status=500, headers={'Cache-Control': 'no-store'})


async def login(request):
    data = await request.json()
    ip = request.headers.get('X-Real-IP', request.remote) if request.app[SECURE] else request.remote
    password = data.get('password')
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise ValueError('نام کاربری یا رمز عبور نادرست است.')
    auth = await web_auth.login(text_field(data, 'username', 32, True), password, ip)
    response = web.json_response({k: v for k, v in auth.items() if k != 'token'})
    response.set_cookie(COOKIE, auth['token'], max_age=web_auth.SESSION_AGE, httponly=True,
                        secure=request.app[SECURE], samesite='Strict', path='/')
    return response


async def logout(request):
    await web_auth.logout(request.cookies.get(COOKIE))
    response = web.json_response({'ok': True})
    response.del_cookie(COOKIE, path='/')
    return response


async def dashboard(request):
    return web.json_response({'users': await subscribers.stats(), 'transactions': await transaction_stats(),
                             'ledger': await accounting.totals(), 'recent': await query('SELECT t.*,u.full_name,u.username FROM transactions t JOIN users u ON u.id=t.user_id ORDER BY t.id DESC LIMIT 6'),
                             'quotes': await query('SELECT r.*,u.full_name,u.username,u.telegram_id FROM rate_requests r JOIN users u ON u.id=r.user_id ORDER BY r.id DESC LIMIT 8')})


async def read(request):
    section = request.match_info['section']
    page = max(0, int(request.query.get('page', '0')))
    if section == 'me':
        return web.json_response({**request['auth'], 'banks': BANKS, 'pack_url': 'https://t.me/addemoji/PIK_Banks_Bahram_ru_by_PIKexchange_bot'})
    if section == 'dashboard':
        return await dashboard(request)
    if section == 'market':
        return web.json_response({'sources': SOURCES, 'snapshots': await fetch_market(force=request.query.get('refresh') == '1')})
    if section == 'products':
        return web.json_response(await get_products())
    if section == 'product-previews':
        products = await get_products()
        async def preview(pid, product, tier=None):
            try:
                rate = await asyncio.wait_for(compute_tier_rate(product, tier) if tier else compute_simple_rate(product), 5)
            except Exception:
                rate = None
            return pid + ':' + (tier['key'] if tier else ''), rate
        values = await asyncio.gather(*(preview(pid, p, t) for pid, p in products.items() for t in (p.get('tiers') or [None])))
        return web.json_response(dict(values))
    if section == 'subscribers':
        q = '%' + request.query.get('q', '')[:100].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where = " WHERE u.full_name LIKE ? ESCAPE '\\' OR u.username LIKE ? ESCAPE '\\' OR CAST(u.telegram_id AS TEXT) LIKE ? ESCAPE '\\'"
        params = (q, q, q)
        rows = await query(subscribers._SELECT + where + ' ORDER BY u.id DESC LIMIT 15 OFFSET ?', (*params, page * 15))
        count = await query('SELECT COUNT(*) n FROM users u' + where, params, True)
        return web.json_response({'rows': rows, 'total': count['n'], 'stats': await subscribers.stats()})
    if section == 'subscriber':
        user = await subscribers.get_subscriber(int(request.query['id']))
        if not user:
            raise web.HTTPNotFound()
        return web.json_response({'user': user, 'quotes': await subscribers.recent_quotes(user['id'], 30),
                                  'cards': (await card_archive.list_cards(user_tg_id=user['telegram_id'], page_size=100))[0],
                                  'transactions': await query('SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 20', (user['id'],))})
    if section == 'transactions':
        status = request.query.get('status', '')
        where, args = (' WHERE t.status=?', (status,)) if status else ('', ())
        total = await query('SELECT COUNT(*) n FROM transactions t' + where, args, True)
        rows = await query('SELECT t.*,u.full_name,u.username FROM transactions t JOIN users u ON u.id=t.user_id' + where + ' ORDER BY t.id DESC LIMIT 15 OFFSET ?', (*args, page * 15))
        return web.json_response({'rows': rows, 'total': total['n']})
    if section == 'transaction':
        tx = await get_transaction(int(request.query['id']))
        if not tx:
            raise web.HTTPNotFound()
        return web.json_response(tx)
    if section == 'archive':
        rows, total = await card_archive.list_cards(request.query.get('q', '')[:100], page=page, page_size=15)
        return web.json_response({'rows': rows, 'total': total})
    if section == 'ledger':
        total = await query('SELECT COUNT(*) n FROM ledger', one=True)
        return web.json_response({'rows': await accounting.list_entries(15, page * 15), 'total': total['n'], 'totals': await accounting.totals()})
    if section == 'cards':
        active = await cards.get_active_card()
        return web.json_response({'rows': await cards.get_cards(), 'active_id': active['id'] if active else None})
    if section == 'settings':
        return web.json_response({k: await get_setting(k, '') for k in ('text_welcome', 'text_rules', 'text_support', 'buttons_colored', 'suspended')})
    if section == 'broadcasts':
        return web.json_response(await query('SELECT * FROM web_broadcasts ORDER BY id DESC LIMIT 10'))
    raise web.HTTPNotFound()


async def receipt(request):
    kind, item_id = request.match_info['kind'], int(request.match_info['id'])
    if kind == 'tx':
        item = await get_transaction(item_id)
        file_id = item.get('receipt_file_id') if item else None
    elif kind == 'card':
        item = next((c for c in await cards.get_cards() if c['id'] == item_id), None)
        file_id = item.get('image') if item else None
    else:
        item = await accounting.get_entry(item_id)
        file_id = item.get('file_id') if item else None
    if not file_id:
        raise web.HTTPNotFound()
    buf = io.BytesIO()
    await request.app[BOT].download(file_id, destination=buf)
    content = buf.getvalue()
    content_type = 'image/png' if content.startswith(b'\x89PNG') else 'image/webp' if content.startswith(b'RIFF') else 'image/jpeg' if content.startswith(b'\xff\xd8') else 'application/octet-stream'
    return web.Response(body=content, content_type=content_type, headers={'Cache-Control': 'no-store'})


async def broadcast_worker(app, job_id, text, targets):
    try:
        for target in targets:
            job = await query('SELECT state FROM web_broadcasts WHERE id=?', (job_id,), True)
            if job['state'] != 'sending':
                break
            success = False
            for attempt in range(2):
                try:
                    await app[BOT].send_message(target, text, parse_mode=None)
                    success = True
                    break
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(min(exc.retry_after, 60))
                except Exception:
                    break
            field = 'sent' if success else 'failed'
            await execute(f'UPDATE web_broadcasts SET {field}={field}+1 WHERE id=?', (job_id,))
            await asyncio.sleep(.07)
        await execute("UPDATE web_broadcasts SET state='completed' WHERE id=? AND state='sending'", (job_id,))
    except asyncio.CancelledError:
        await execute("UPDATE web_broadcasts SET state='interrupted' WHERE id=? AND state='sending'", (job_id,))
        raise
    except Exception as exc:
        logger.error('broadcast %s failed (%s)', job_id, type(exc).__name__)
        await execute("UPDATE web_broadcasts SET state='interrupted' WHERE id=? AND state='sending'", (job_id,))


async def mutate(request):
    section = request.match_info['section']
    data = await request.json()
    admin_id, bot = request['auth']['admin_id'], request.app[BOT]
    result = {'ok': True}
    if section == 'products':
        pid = text_field(data, 'id', 60, True)
        product = await get_product(pid)
        if not product:
            raise ValueError('محصول یافت نشد.')
        for key in ('column', 'mode'):
            if key in data:
                allowed = ('buy', 'sell', 'avg') if key == 'column' else ('formula', 'manual')
                if data[key] not in allowed:
                    raise ValueError('گزینه نامعتبر است.')
                product[key] = data[key]
        values = data.get('tiers') if product.get('tiers') else [data]
        if not isinstance(values, list) or len(values) > 10:
            raise ValueError('پله‌ها معتبر نیستند.')
        for update in values:
            target = next((t for t in product['tiers'] if t['key'] == update.get('key')), None) if product.get('tiers') else product
            if target is None:
                raise ValueError('پله یافت نشد.')
            for key in ('mult', 'offset', 'manual', 'min', 'max'):
                if key in update and not (pid == 'rub_c2c' and key in ('min', 'max')):
                    target[key] = number(update[key], 0 if key != 'offset' else -10**12, 10 if key == 'mult' else 10**12)
            if 'label' in update:
                target['label'] = text_field(update, 'label', 100, True)
            if pid == 'rub_c2c':
                if update.get('pricing_mode') not in ('multiplier', 'offset'):
                    raise ValueError('روش قیمت‌گذاری را انتخاب کنید.')
                target['pricing_mode'] = update['pricing_mode']
        await save_product(pid, product)
    elif section == 'settings':
        for key in ('text_welcome', 'text_rules', 'text_support'):
            if key in data:
                await set_setting(key, text_field(data, key))
        for key in ('buttons_colored', 'suspended'):
            if key in data:
                if data[key] not in ('0', '1'):
                    raise ValueError('گزینه نامعتبر است.')
                await set_setting(key, data[key])
    elif section == 'block':
        if not isinstance(data.get('blocked'), bool):
            raise ValueError('وضعیت معتبر نیست.')
        if not await subscribers.set_blocked(int(data['id']), data['blocked']):
            raise ValueError('کاربر یافت نشد.')
    elif section == 'archive':
        if data.get('action') == 'delete':
            await card_archive.delete_card(int(data['id']))
        else:
            result['id'] = await card_archive.save_card(text_field(data, 'owner_name', 100, True), text_field(data, 'bank_id', 40, True), data, admin_id,
                                                      int(data['user_tg_id']) if data.get('user_tg_id') else None, int(data['id']) if data.get('id') else None)
    elif section == 'ledger':
        if data.get('action') == 'create':
            if data.get('currency') != accounting.currency_for_direction(data.get('direction')):
                raise ValueError('واحد سند تغییر کرده؛ صفحه را بارگذاری مجدد کنید. ورودی تومان و خروجی روبل است.')
            entry_id = await accounting.create_manual_draft(admin_id, data.get('direction'), text_field(data, 'name', 80, True) + ' - ' + text_field(data, 'amount', 40, True))
            result['id'] = entry_id
        else:
            if data.get('action') == 'edit':
                entry = await accounting.get_entry(int(data['id']))
                values = data.get('value', {})
                if not entry or not isinstance(values, dict):
                    raise ValueError('سند معتبر نیست.')
                direction = values.get('direction', entry['direction'])
                currency = accounting.currency_for_direction(direction) if direction != entry['direction'] else entry['currency']
                if values.get('currency') != currency:
                    raise ValueError('واحد فرم با سند یکسان نیست؛ سند را دوباره باز کنید.')
            await accounting.change_entry(int(data['id']), int(data['revision']), admin_id, data['action'], data.get('value'))
    elif section == 'transaction':
        tx = await get_transaction(int(data['id']))
        if not tx:
            raise ValueError('معامله یافت نشد.')
        if data.get('action') == 'approve':
            from bot.handlers.admin import _approve_tx
            if not await _approve_tx(None, tx, int(number(data.get('amount', 0), 0, 10**15)), admin_id, bot):
                raise ValueError('این معامله قبلاً بررسی شده است.')
        elif data.get('action') == 'reject':
            if not await resolve_transaction(tx['id'], 'canceled', note=text_field(data, 'note', 500)):
                raise ValueError('این معامله قبلاً بررسی شده است.')
            try:
                await bot.send_message(tx['telegram_id'], f"❌ فیش معامله شماره {tx['id']} تأیید نشد.\nبرای پیگیری با پشتیبانی در ارتباط باشید.", parse_mode=None)
            except Exception:
                result['warning'] = 'معامله رد شد؛ پیام تلگرام به کاربر نرسید.'
        else:
            raise ValueError('عملیات معتبر نیست.')
    elif section == 'cards':
        action = data.get('action')
        card_id = int(data.get('id') or 0)
        if action == 'active':
            if not any(c['id'] == card_id for c in await cards.get_cards()):
                raise ValueError('کارت یافت نشد.')
            await cards.set_active_card(card_id)
        elif action == 'delete':
            await cards.delete_card(card_id)
        else:
            fields = {k: text_field(data, k, 4000 if k == 'text' else 300, k == 'label') for k in ('label', 'text', 'image', 'card_number', 'sheba', 'account')}
            if not fields['text'] and not fields['image']:
                raise ValueError('متن یا عکس کارت را وارد کنید.')
            if card_id:
                all_cards = await cards.get_cards()
                card = next((c for c in all_cards if c['id'] == card_id), None)
                if not card:
                    raise ValueError('کارت یافت نشد.')
                card.update(fields)
                await set_json('cards', all_cards)
            else:
                result['id'] = await cards.add_card(**fields)
    elif section == 'upload':
        encoded = text_field(data, 'image', 11_000_000, True)
        if not encoded.startswith(('data:image/jpeg;base64,', 'data:image/png;base64,', 'data:image/webp;base64,')):
            raise ValueError('عکس JPG، PNG یا WEBP انتخاب کنید.')
        content = base64.b64decode(encoded.split(',', 1)[1], validate=True)
        if len(content) > 8_000_000 or not content.startswith((b'\xff\xd8', b'\x89PNG', b'RIFF')):
            raise ValueError('عکس باید کمتر از ۸ مگابایت باشد.')
        message = await bot.send_photo(admin_id, BufferedInputFile(content, 'panel-image.jpg'), caption='عکس بارگذاری‌شده از پنل وب')
        result['file_id'] = message.photo[-1].file_id
    elif section == 'message':
        user = await subscribers.get_subscriber(int(data['id']))
        if not user:
            raise ValueError('کاربر یافت نشد.')
        text = '📨 پیامی از پشتیبانی صرافی پیک:\n\n' + text_field(data, 'text', 3900, True)
        image = text_field(data, 'image', 300)
        if image:
            if len(text) > 1024:
                raise ValueError('متن همراه عکس باید کمتر از ۱۰۰۰ کاراکتر باشد.')
            await bot.send_photo(user['telegram_id'], image, caption=text, parse_mode=None)
        else:
            await bot.send_message(user['telegram_id'], text, parse_mode=None)
    elif section == 'broadcasts':
        if data.get('action') == 'cancel':
            await execute("UPDATE web_broadcasts SET state='canceled' WHERE id=? AND state='sending'", (int(data['id']),))
        else:
            text = text_field(data, 'text', 4000, True)
            targets = [r['telegram_id'] for r in await query('SELECT telegram_id FROM users WHERE is_blocked=0')]
            active = await query("SELECT id FROM web_broadcasts WHERE state='sending'", one=True)
            if active:
                raise ValueError('یک ارسال همگانی در حال اجراست.')
            job_id = await execute('INSERT INTO web_broadcasts(admin_id,text,total,created_at) VALUES(?,?,?,?)', (admin_id, text, len(targets), _now()))
            task = asyncio.create_task(broadcast_worker(request.app, job_id, text, targets))
            request.app[JOBS].add(task)
            task.add_done_callback(request.app[JOBS].discard)
            result['id'] = job_id
    else:
        raise web.HTTPNotFound()
    await execute('INSERT INTO web_audit(admin_id,action,created_at) VALUES(?,?,?)', (admin_id, section + ':' + str(data.get('action', data.get('id', 'save')))[:100], _now()))
    return web.json_response(result)


async def lifecycle(app):
    from core.db import init_db
    await init_db()
    await web_auth.init_web()
    await execute("UPDATE web_broadcasts SET state='interrupted' WHERE state='sending'")
    if not app.get(BOT):
        app[BOT] = Bot(BOT_TOKEN)
    app[JOBS] = set()
    yield
    for task in list(app[JOBS]):
        task.cancel()
    await asyncio.gather(*app[JOBS], return_exceptions=True)
    await app[BOT].session.close()


def create_app(secure=True, bot=None):
    app = web.Application(middlewares=[security], client_max_size=12_000_000)
    app[SECURE] = secure
    if bot:
        app[BOT] = bot
    app.cleanup_ctx.append(lifecycle)
    app.router.add_post('/api/login', login)
    app.router.add_post('/api/logout', logout)
    app.router.add_get('/api/media/{kind:tx|card|ledger}/{id:\\d+}', receipt)
    app.router.add_get('/api/{section}', read)
    app.router.add_post('/api/{section}', mutate)
    async def index(request):
        return web.FileResponse(ROOT / 'static/index.html', headers={'Cache-Control': 'no-cache'})
    app.router.add_get('/', index)
    app.router.add_static('/assets/banks/', ROOT.parent / 'assets/banks/emoji', show_index=False)
    app.router.add_static('/', ROOT / 'static', show_index=False)
    return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(secure=os.getenv('PIK_WEB_DEV') != '1'), host='127.0.0.1', port=int(os.getenv('PIK_WEB_PORT', '8789')), access_log=None)

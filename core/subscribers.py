"""Subscriber directory, access control and persistent quote activity."""
from core.config import is_admin
from core.db import _connect, _now


_SELECT = """SELECT u.*, COALESCE(q.quote_count, 0) AS quote_count, q.last_quote_at
    FROM users u LEFT JOIN (
        SELECT user_id, COUNT(*) AS quote_count, MAX(created_at) AS last_quote_at
        FROM rate_requests GROUP BY user_id
    ) q ON q.user_id=u.id"""


async def stats():
    conn = await _connect()
    try:
        row = await (await conn.execute("SELECT COUNT(*) total, COALESCE(SUM(is_blocked),0) blocked FROM users")).fetchone()
        result = dict(row)
        result["active"] = result["total"] - result["blocked"]
        return result
    finally:
        await conn.close()


async def list_subscribers(limit=8, offset=0):
    conn = await _connect()
    try:
        rows = await (await conn.execute(_SELECT + " ORDER BY u.id DESC LIMIT ? OFFSET ?", (limit, offset))).fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def get_subscriber(telegram_id):
    conn = await _connect()
    try:
        row = await (await conn.execute(_SELECT + " WHERE u.telegram_id=?", (telegram_id,))).fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def set_blocked(telegram_id, blocked):
    if is_admin(telegram_id):
        raise ValueError("ادمین‌های ربات قابل بن‌کردن نیستند.")
    conn = await _connect()
    try:
        cur = await conn.execute("UPDATE users SET is_blocked=? WHERE telegram_id=?", (int(blocked), telegram_id))
        await conn.commit()
        return cur.rowcount > 0
    finally:
        await conn.close()


async def record_quote(event_key, user_id, product_id, product_title, tier_label, rate, currency):
    conn = await _connect()
    try:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO rate_requests(event_key,user_id,product_id,product_title,tier_label,rate,currency,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (event_key, user_id, product_id, product_title, tier_label, rate, currency, _now()),
        )
        await conn.commit()
        return cur.lastrowid if cur.rowcount else None
    finally:
        await conn.close()


async def recent_quotes(user_id, limit=5):
    conn = await _connect()
    try:
        rows = await (await conn.execute("SELECT * FROM rate_requests WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))).fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()

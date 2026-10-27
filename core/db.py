"""لایه‌ی دیتابیس (SQLite با aiosqlite).

سبک و ساده: کاربران، تراکنش‌ها، حسابداری و تنظیمات کلید-مقدار.
"""
import json
import logging
from typing import Any, Dict, List, Optional

import aiosqlite

from core.config import DB_PATH
from core.jalali import tehran_now

logger = logging.getLogger("pik.db")


def _now() -> str:
    return tehran_now().strftime("%Y-%m-%d %H:%M:%S")


async def _connect() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def init_db() -> None:
    conn = await _connect()
    try:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   INTEGER UNIQUE NOT NULL,
                username      TEXT,
                full_name     TEXT,
                is_blocked    INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                telegram_id    INTEGER NOT NULL,
                product_id     TEXT NOT NULL,
                product_title  TEXT NOT NULL,
                tier_key       TEXT,
                tier_label     TEXT,
                unit           TEXT,
                rate           INTEGER NOT NULL DEFAULT 0,
                status         TEXT NOT NULL DEFAULT 'awaiting_receipt',
                receipt_file_id TEXT,
                admin_note     TEXT,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id);
            CREATE INDEX IF NOT EXISTS idx_tx_status ON transactions(status);

            CREATE TABLE IF NOT EXISTS rate_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL REFERENCES users(id),
                product_id TEXT NOT NULL,
                product_title TEXT NOT NULL,
                tier_label TEXT NOT NULL DEFAULT '',
                rate INTEGER NOT NULL,
                currency TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rate_requests_user ON rate_requests(user_id, id DESC);

            CREATE TABLE IF NOT EXISTS customer_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_name TEXT NOT NULL,
                name_key TEXT NOT NULL,
                bank_id TEXT NOT NULL,
                card_number TEXT NOT NULL DEFAULT '',
                iban TEXT NOT NULL DEFAULT '',
                account TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                user_tg_id INTEGER,
                created_by INTEGER NOT NULL,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_customer_cards_name ON customer_cards(name_key);
            CREATE INDEX IF NOT EXISTS idx_customer_cards_user ON customer_cards(user_tg_id, is_deleted);

            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('in', 'out')),
                amount_rial INTEGER NOT NULL DEFAULT 0 CHECK(amount_rial >= 0),
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK(status IN ('draft', 'pending', 'posted', 'void')),
                receipt_json TEXT NOT NULL DEFAULT '{}',
                file_id TEXT NOT NULL DEFAULT '',
                file_hash TEXT NOT NULL DEFAULT '',
                reference TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ledger_hash_unique ON ledger(file_hash)
                WHERE file_hash != '' AND status IN ('pending', 'posted');
            CREATE UNIQUE INDEX IF NOT EXISTS ledger_ref_unique ON ledger(reference)
                WHERE reference != '' AND status IN ('pending', 'posted');
            CREATE TABLE IF NOT EXISTS ledger_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ledger_id INTEGER NOT NULL REFERENCES ledger(id),
                admin_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        await conn.commit()
        # migrations (افزودن ستون‌های جدید در صورت نبودن)
        for stmt in (
            "ALTER TABLE transactions ADD COLUMN currency TEXT NOT NULL DEFAULT 'تومان'",
            "ALTER TABLE transactions ADD COLUMN equivalent INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE transactions ADD COLUMN payout_info TEXT DEFAULT ''",
        ):
            try:
                await conn.execute(stmt)
                await conn.commit()
            except Exception:
                pass
    finally:
        await conn.close()


# ─────────────────────────── settings ───────────────────────────

async def get_setting(key: str, default: str = "") -> str:
    conn = await _connect()
    try:
        cur = await conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else default
    finally:
        await conn.close()


async def set_setting(key: str, value: str) -> None:
    conn = await _connect()
    try:
        await conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_json(key: str, default: Any) -> Any:
    raw = await get_setting(key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


async def set_json(key: str, value: Any) -> None:
    await set_setting(key, json.dumps(value, ensure_ascii=False))


# ─────────────────────────── users ───────────────────────────

async def get_or_create_user(
    telegram_id: int, username: Optional[str] = None, full_name: Optional[str] = None
) -> Dict:
    conn = await _connect()
    try:
        cur = await conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        row = await cur.fetchone()
        if row:
            # به‌روزرسانی نام/یوزرنیم در صورت تغییر
            if username is not None or full_name is not None:
                await conn.execute(
                    "UPDATE users SET username=COALESCE(?,username), full_name=COALESCE(?,full_name) WHERE id=?",
                    (username, full_name, row["id"]),
                )
                await conn.commit()
                cur = await conn.execute("SELECT * FROM users WHERE id=?", (row["id"],))
                row = await cur.fetchone()
            return dict(row)

        await conn.execute(
            "INSERT OR IGNORE INTO users(telegram_id, username, full_name, created_at) VALUES(?,?,?,?)",
            (telegram_id, username, full_name, _now()),
        )
        await conn.commit()
        cur = await conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        return dict(await cur.fetchone())
    finally:
        await conn.close()


async def get_user_by_tg(telegram_id: int) -> Optional[Dict]:
    conn = await _connect()
    try:
        cur = await conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def update_user(user_id: int, **fields) -> None:
    if not fields:
        return
    allowed = {"username", "full_name", "is_blocked"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=?" for k in sets)
    conn = await _connect()
    try:
        await conn.execute(f"UPDATE users SET {cols} WHERE id=?", (*sets.values(), user_id))
        await conn.commit()
    finally:
        await conn.close()


async def count_users() -> int:
    conn = await _connect()
    try:
        cur = await conn.execute("SELECT COUNT(*) AS c FROM users")
        return (await cur.fetchone())["c"]
    finally:
        await conn.close()


async def get_all_user_tg_ids() -> List[int]:
    conn = await _connect()
    try:
        cur = await conn.execute("SELECT telegram_id FROM users WHERE is_blocked=0")
        return [r["telegram_id"] for r in await cur.fetchall()]
    finally:
        await conn.close()


# ─────────────────────────── transactions ───────────────────────────

async def create_transaction(
    user_id: int,
    telegram_id: int,
    product_id: str,
    product_title: str,
    rate: int,
    tier_key: str = "",
    tier_label: str = "",
    unit: str = "",
    currency: str = "تومان",
) -> int:
    conn = await _connect()
    try:
        cur = await conn.execute(
            """INSERT INTO transactions
               (user_id, telegram_id, product_id, product_title, tier_key, tier_label,
                unit, rate, currency, status, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?, 'awaiting_receipt', ?, ?)""",
            (user_id, telegram_id, product_id, product_title, tier_key, tier_label,
             unit, rate, currency, _now(), _now()),
        )
        await conn.commit()
        return cur.lastrowid
    finally:
        await conn.close()


async def get_transaction(tx_id: int) -> Optional[Dict]:
    conn = await _connect()
    try:
        cur = await conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,))
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def update_transaction(tx_id: int, **fields) -> None:
    allowed = {"status", "receipt_file_id", "admin_note", "equivalent", "payout_info"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    sets_sql = ", ".join(f"{k}=?" for k in sets)
    conn = await _connect()
    try:
        await conn.execute(
            f"UPDATE transactions SET {sets_sql}, updated_at=? WHERE id=?",
            (*sets.values(), _now(), tx_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def resolve_transaction(tx_id: int, status: str, equivalent: int = 0, note: str = "") -> bool:
    """One atomic winner when Telegram and the web panel review the same transfer."""
    if status not in ("completed", "canceled"):
        raise ValueError("وضعیت نهایی نامعتبر است.")
    conn = await _connect()
    try:
        cur = await conn.execute("UPDATE transactions SET status=?,equivalent=?,admin_note=?,updated_at=? WHERE id=? AND status IN ('awaiting_receipt','receipt_sent')",
                                 (status, equivalent, note, _now(), tx_id))
        await conn.commit()
        return cur.rowcount > 0
    finally:
        await conn.close()


async def get_user_transactions(user_id: int, limit: int = 15) -> List[Dict]:
    conn = await _connect()
    try:
        cur = await conn.execute(
            "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()


async def get_recent_transactions(limit: int = 20, status: str = "") -> List[Dict]:
    conn = await _connect()
    try:
        if status:
            cur = await conn.execute(
                "SELECT * FROM transactions WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = await conn.execute(
                "SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()


async def transaction_stats() -> Dict[str, int]:
    conn = await _connect()
    try:
        cur = await conn.execute(
            "SELECT status, COUNT(*) AS c FROM transactions GROUP BY status"
        )
        rows = await cur.fetchall()
        stats = {r["status"]: r["c"] for r in rows}
        cur = await conn.execute("SELECT COUNT(*) AS c FROM transactions")
        stats["total"] = (await cur.fetchone())["c"]
        return stats
    finally:
        await conn.close()

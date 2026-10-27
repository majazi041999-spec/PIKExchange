"""Administrator credentials set in Telegram, opaque sessions, and login throttling."""
import asyncio
import hashlib
import hmac
import re
import secrets
import sqlite3
import time

from core.config import is_admin
from core.db import _connect, _now

SESSION_AGE = 7 * 24 * 3600


async def init_web():
    conn = await _connect()
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS web_credentials (
                admin_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_sessions (
                token_hash TEXT PRIMARY KEY,
                admin_id INTEGER NOT NULL REFERENCES web_credentials(admin_id) ON DELETE CASCADE,
                csrf TEXT NOT NULL,
                expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_login_attempts (ip TEXT NOT NULL, attempted_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_web_login_ip ON web_login_attempts(ip, attempted_at);
            CREATE TABLE IF NOT EXISTS web_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
                action TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL, text TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'sending', sent INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
        """)
        await conn.commit()
    finally:
        await conn.close()


def valid_username(username):
    username = username.strip().casefold()
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{3,31}", username):
        raise ValueError("نام کاربری باید ۴ تا ۳۲ حرف لاتین/عدد باشد و با حرف شروع شود.")
    return username


def hash_password(password, salt=None):
    if not 10 <= len(password) <= 128:
        raise ValueError("رمز عبور باید ۱۰ تا ۱۲۸ کاراکتر باشد.")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return "scrypt$" + salt + "$" + digest


def verify_password(password, saved):
    try:
        scheme, salt, digest = saved.split("$")
        return scheme == "scrypt" and hmac.compare_digest(hash_password(password, salt).rsplit("$", 1)[1], digest)
    except (ValueError, TypeError):
        return False


async def set_credentials(admin_id, username, password):
    if not is_admin(admin_id):
        raise ValueError("فقط ادمین ربات می‌تواند دسترسی وب داشته باشد.")
    username = valid_username(username)
    hashed = await asyncio.to_thread(hash_password, password)
    conn = await _connect()
    try:
        await conn.execute("INSERT INTO web_credentials VALUES(?,?,?,?) ON CONFLICT(admin_id) DO UPDATE SET username=excluded.username,password_hash=excluded.password_hash,updated_at=excluded.updated_at",
                           (admin_id, username, hashed, _now()))
        await conn.execute("DELETE FROM web_sessions WHERE admin_id=?", (admin_id,))
        await conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("این نام کاربری قبلاً برای ادمین دیگری ثبت شده است.") from exc
    finally:
        await conn.close()


async def credential_info(admin_id):
    conn = await _connect()
    try:
        row = await (await conn.execute("SELECT username,updated_at FROM web_credentials WHERE admin_id=?", (admin_id,))).fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def login(username, password, ip):
    now = time.time()
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        await conn.execute("DELETE FROM web_login_attempts WHERE attempted_at<?", (now - 900,))
        attempts = (await (await conn.execute("SELECT COUNT(*) n FROM web_login_attempts WHERE ip=?", (ip,))).fetchone())["n"]
        if attempts >= 12:
            raise ValueError("تلاش ورود زیاد بوده؛ ۱۵ دقیقه بعد دوباره امتحان کنید.")
        # Count before verification so concurrent requests cannot bypass the limit.
        await conn.execute("INSERT INTO web_login_attempts VALUES(?,?)", (ip, now))
        await conn.commit()
        row = await (await conn.execute("SELECT * FROM web_credentials WHERE username=? COLLATE NOCASE", (username.strip(),))).fetchone()
        saved = row["password_hash"] if row else "scrypt$" + "0" * 32 + "$" + "0" * 128
        valid = await asyncio.to_thread(verify_password, password, saved)
        if not row or not valid or not is_admin(row["admin_id"]):
            raise ValueError("نام کاربری یا رمز عبور نادرست است.")
        await conn.execute("BEGIN IMMEDIATE")
        latest = await (await conn.execute("SELECT password_hash FROM web_credentials WHERE admin_id=?", (row['admin_id'],))).fetchone()
        if not latest or not hmac.compare_digest(latest['password_hash'], saved):
            raise ValueError("دسترسی تغییر کرده؛ دوباره وارد شوید.")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        await conn.execute("DELETE FROM web_sessions WHERE expires<?", (now,))
        await conn.execute("INSERT INTO web_sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), row["admin_id"], csrf, now + SESSION_AGE))
        await conn.execute("DELETE FROM web_login_attempts WHERE ip=?", (ip,))
        await conn.commit()
        return {"token": token, "csrf": csrf, "admin_id": row["admin_id"], "username": row["username"]}
    finally:
        await conn.close()


async def session(token):
    if not token:
        return None
    conn = await _connect()
    try:
        row = await (await conn.execute("SELECT s.admin_id,s.csrf,c.username FROM web_sessions s JOIN web_credentials c ON c.admin_id=s.admin_id WHERE token_hash=? AND expires>?",
                                        (hashlib.sha256(token.encode()).hexdigest(), time.time()))).fetchone()
        return dict(row) if row and is_admin(row["admin_id"]) else None
    finally:
        await conn.close()


async def logout(token):
    conn = await _connect()
    try:
        await conn.execute("DELETE FROM web_sessions WHERE token_hash=?", (hashlib.sha256((token or "").encode()).hexdigest(),))
        await conn.commit()
    finally:
        await conn.close()


async def revoke(admin_id):
    conn = await _connect()
    try:
        await conn.execute("DELETE FROM web_sessions WHERE admin_id=?", (admin_id,))
        await conn.commit()
    finally:
        await conn.close()

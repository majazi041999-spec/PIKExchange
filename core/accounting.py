"""Small cash ledger. Drafts/pending transfers never contribute to the balance."""
import json
import re
import sqlite3
from decimal import Decimal, InvalidOperation

from core.db import _connect, _now
from core.utils import en_digits, fa_digits

MAX_AMOUNT_RIAL = 10**15


class LedgerError(ValueError):
    pass


def parse_amount(value, currency="rial") -> int:
    if currency not in ("rial", "toman"):
        raise ValueError("واحد مبلغ باید ریال یا تومان باشد.")
    raw = en_digits(value).replace("٬", "").replace(",", "").replace("،", "").replace(" ", "").replace("٫", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
        raise ValueError("مبلغ معتبر نیست.")
    try:
        amount = Decimal(raw) * (10 if currency == "toman" else 1)
        if not amount.is_finite() or amount != amount.to_integral_value() or not 0 < amount <= MAX_AMOUNT_RIAL:
            raise ValueError("مبلغ باید مثبت و به ریال عدد صحیح باشد.")
        return int(amount)
    except InvalidOperation as exc:
        raise ValueError("مبلغ معتبر نیست.") from exc


def display_amount(rial: int) -> str:
    return fa_digits(f"{Decimal(rial) / 10:,.1f}".removesuffix(".0")) + " تومان"


def normalize_reference(value: str) -> str:
    return re.sub(r"[\s\-\u200c\u200e\u200f]+", "", en_digits(value)).upper()


def _decode(row):
    if row is None:
        return None
    result = dict(row)
    result["receipt"] = json.loads(result.pop("receipt_json"))
    return result


async def create_draft(admin_id: int, direction: str, receipt=None, file_id="", file_hash="") -> int:
    if direction not in ("in", "out"):
        raise LedgerError("نوع ورودی/خروجی معتبر نیست.")
    receipt = receipt or {}
    amount = 0
    if receipt.get("amount") and receipt.get("currency") in ("rial", "toman"):
        try:
            amount = parse_amount(receipt["amount"], receipt["currency"])
        except ValueError:
            pass  # Remain a draft until the administrator fixes the amount/unit.
    conn = await _connect()
    try:
        cur = await conn.execute(
            "INSERT INTO ledger(admin_id,direction,amount_rial,receipt_json,file_id,file_hash,reference,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (admin_id, direction, amount, json.dumps(receipt, ensure_ascii=False), file_id, file_hash,
             normalize_reference(receipt.get("reference") or ""), _now(), _now()),
        )
        await conn.commit()
        return cur.lastrowid
    finally:
        await conn.close()


async def get_entry(entry_id):
    conn = await _connect()
    try:
        cur = await conn.execute("SELECT * FROM ledger WHERE id=?", (entry_id,))
        return _decode(await cur.fetchone())
    finally:
        await conn.close()


async def list_entries(limit=12, offset=0):
    conn = await _connect()
    try:
        cur = await conn.execute("SELECT * FROM ledger ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
        return [_decode(row) for row in await cur.fetchall()]
    finally:
        await conn.close()


async def totals():
    conn = await _connect()
    try:
        cur = await conn.execute("SELECT direction, SUM(amount_rial) amount FROM ledger WHERE status='posted' GROUP BY direction")
        sums = {row["direction"]: row["amount"] for row in await cur.fetchall()}
        cur = await conn.execute("SELECT COUNT(*) n FROM ledger WHERE status='pending'")
        return {"in": sums.get("in", 0), "out": sums.get("out", 0), "pending": (await cur.fetchone())["n"]}
    finally:
        await conn.close()


async def change_entry(entry_id, revision, admin_id, action, value=None):
    """Serialize edits and confirmation; stale/double clicks cannot post twice."""
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        cur = await conn.execute("SELECT * FROM ledger WHERE id=?", (entry_id,))
        entry = _decode(await cur.fetchone())
        if not entry or entry["revision"] != revision:
            raise LedgerError("این پیام قدیمی است؛ سند را دوباره از فهرست باز کنید.")
        status = entry["status"]
        fields = {}
        if action in ("amount_rial", "reference", "note", "direction", "sender", "recipient", "date", "source_bank", "destination_bank"):
            if status != "draft":
                raise LedgerError("فقط پیش‌نویس قابل ویرایش است؛ سند ثبت‌شده را ابطال و دوباره ثبت کنید.")
            if action == "amount_rial":
                value = parse_amount(value)
            elif action == "direction" and value not in ("in", "out"):
                raise LedgerError("جهت معتبر نیست.")
            elif action == "reference":
                value = normalize_reference(value)
            if action in ("sender", "recipient", "date", "source_bank", "destination_bank"):
                entry["receipt"][action] = str(value)[:200]
                fields["receipt_json"] = json.dumps(entry["receipt"], ensure_ascii=False)
            else:
                fields[action] = value
        elif action in ("pending", "posted", "settle"):
            expected = "pending" if action == "settle" else "draft"
            if status != expected or not entry["amount_rial"]:
                raise LedgerError("ابتدا مبلغ را اصلاح کنید و وضعیت سند را بررسی کنید.")
            if action == "posted" and entry["receipt"].get("status", "manual") not in ("success", "manual"):
                raise LedgerError("وصول این فیش مشخص نیست؛ ابتدا در انتظار وصول ذخیره کنید.")
            fields["status"] = "posted" if action == "settle" else action
        elif action == "void" and status != "void":
            fields["status"] = "void"
        else:
            raise LedgerError("این عملیات برای وضعیت فعلی سند ممکن نیست.")
        fields.update(revision=revision + 1, updated_at=_now())
        await conn.execute(
            "UPDATE ledger SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?",
            (*fields.values(), entry_id),
        )
        await conn.execute("INSERT INTO ledger_events(ledger_id,admin_id,action,created_at) VALUES(?,?,?,?)",
                           (entry_id, admin_id, action, _now()))
        await conn.commit()
    except sqlite3.IntegrityError as exc:
        await conn.rollback()
        raise LedgerError("این تصویر یا شماره پیگیری قبلاً ثبت شده است؛ فهرست اسناد را بررسی کنید.") from exc
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()

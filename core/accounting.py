"""Currency-tagged ledger: manual income in toman, outgoing payments in roubles."""
import json
import re
import sqlite3
from decimal import Decimal, InvalidOperation

from core.db import _connect, _now
from core.utils import en_digits, fa_digits

MAX_AMOUNT_MINOR = 10**15
CURRENCIES = {'IRR': {'label': 'تومان', 'factor': 10}, 'RUB': {'label': 'روبل', 'factor': 100}}


class LedgerError(ValueError):
    pass


def parse_amount(value, currency="rial") -> int:
    if currency not in ("rial", "toman", "rub"):
        raise ValueError("واحد مبلغ معتبر نیست.")
    raw = en_digits(value).replace(' ', '').replace('٫', '.').replace('،', ',')
    if currency == 'rub' and ',' in raw:
        if '.' not in raw and re.fullmatch(r'\d+,\d{1,2}', raw):
            raw = raw.replace(',', '.')  # Russian decimal notation, e.g. 123,45.
        elif not re.fullmatch(r'\d+(?:,\d{3})+(?:\.\d{1,2})?', raw):
            raise ValueError('مبلغ روبل معتبر نیست؛ مثال: ۱۲۳۴٫۵۶')
    raw = raw.replace('٬', '').replace(',', '')
    if len(raw) > 80:
        raise ValueError('مبلغ معتبر نیست.')
    if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
        raise ValueError("مبلغ معتبر نیست.")
    try:
        amount = Decimal(raw) * {'rial': 1, 'toman': 10, 'rub': 100}[currency]
        if not amount.is_finite() or amount != amount.to_integral_value() or not 0 < amount <= MAX_AMOUNT_MINOR:
            raise ValueError("مبلغ روبل باید مثبت و حداکثر دو رقم اعشار داشته باشد." if currency == 'rub' else "مبلغ باید مثبت و به ریال عدد صحیح باشد.")
        return int(amount)
    except InvalidOperation as exc:
        raise ValueError("مبلغ معتبر نیست.") from exc


def display_amount(minor: int, currency='IRR') -> str:
    info = CURRENCIES[currency]
    amount = f"{Decimal(minor) / info['factor']:,.2f}".rstrip('0').rstrip('.')
    return fa_digits(amount) + ' ' + info['label']


def display_entry(entry):
    return display_amount(entry['amount_minor'], entry['currency'])


def currency_for_direction(direction):
    if direction not in ('in', 'out'):
        raise LedgerError('نوع ورودی/خروجی معتبر نیست.')
    return 'RUB' if direction == 'out' else 'IRR'


def validate_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 80 or "\n" in name or "\r" in name or not any(c.isalpha() for c in name):
        raise ValueError("نام معتبر، حداکثر ۸۰ کاراکتر، وارد کنید.")
    return name


def parse_toman(value: str) -> int:
    """Parse a displayed toman amount into integer rials."""
    return parse_amount(value.strip().removesuffix("تومان").strip(), "toman")


def parse_display_amount(value, currency):
    value = str(value).strip()
    if currency == 'IRR':
        return parse_toman(value)
    if currency != 'RUB':
        raise LedgerError('واحد معتبر نیست.')
    for suffix in ('روبل', 'RUB', 'rub', 'руб.', 'руб'):
        value = value.removesuffix(suffix).strip()
    return parse_amount(value, 'rub')


def parse_manual_entry(text: str, currency='IRR') -> tuple[str, int]:
    parts = re.split(r"[-–—]", text.strip(), maxsplit=1)
    if len(parts) != 2 or "\n" in text or "\r" in text:
        raise ValueError("نام و مبلغ را در یک خط بفرستید؛ مثال: میرحسین - ۱۰۰٬۰۰۰٬۰۰۰")
    return validate_name(parts[0]), parse_display_amount(parts[1], currency)


def entry_name(entry: dict) -> str:
    details = entry["receipt"]
    return details.get("name") or details.get("sender" if entry["direction"] == "in" else "recipient") or "بدون نام"


async def create_manual_draft(admin_id: int, direction: str, text: str) -> int:
    currency = currency_for_direction(direction)
    name, minor = parse_manual_entry(text, currency)
    return await create_draft(admin_id, direction, {
        "name": name, "amount": str(Decimal(minor) / 100) if currency == 'RUB' else str(minor),
        "currency": "rub" if currency == 'RUB' else "rial", "status": "manual",
    })


def normalize_reference(value: str) -> str:
    return re.sub(r"[\s\-\u200c\u200e\u200f]+", "", en_digits(value)).upper()


def _decode(row):
    if row is None:
        return None
    result = dict(row)
    result["receipt"] = json.loads(result.pop("receipt_json"))
    info = CURRENCIES[result['currency']]
    result['amount'] = str(Decimal(result['amount_minor']) / info['factor'])
    result['currency_label'] = info['label']
    return result


async def create_draft(admin_id: int, direction: str, receipt=None, file_id="", file_hash="") -> int:
    if direction not in ("in", "out"):
        raise LedgerError("نوع ورودی/خروجی معتبر نیست.")
    receipt = receipt or {}
    currency = 'RUB' if receipt.get('currency') == 'rub' else 'IRR'
    amount = 0
    if receipt.get("amount") and receipt.get("currency") in ("rial", "toman", "rub"):
        try:
            amount = parse_amount(receipt["amount"], receipt["currency"])
        except ValueError:
            pass  # Remain a draft until the administrator fixes the amount/unit.
    conn = await _connect()
    try:
        cur = await conn.execute(
            "INSERT INTO ledger(admin_id,direction,amount_rial,currency,amount_minor,receipt_json,file_id,file_hash,reference,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (admin_id, direction, amount if currency == 'IRR' else 0, currency, amount, json.dumps(receipt, ensure_ascii=False), file_id, file_hash,
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
        cur = await conn.execute("SELECT currency,direction,SUM(amount_minor) amount,COUNT(*) n FROM ledger WHERE status='posted' GROUP BY currency,direction")
        sums = {currency: {'in': 0, 'out': 0, 'balance': 0} for currency in CURRENCIES}
        posted = 0
        for row in await cur.fetchall():
            sums[row['currency']][row['direction']] = row['amount']
            posted += row['n']
        for values in sums.values():
            values['balance'] = values['in'] - values['out']
        cur = await conn.execute("SELECT COUNT(*) n FROM ledger WHERE status='pending'")
        return {**sums, 'posted': posted, 'pending': (await cur.fetchone())["n"]}
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
        def set_amount(minor, currency):
            fields.update(amount_minor=minor, currency=currency, amount_rial=minor if currency == 'IRR' else 0)
            entry['receipt'].update(currency='rub' if currency == 'RUB' else 'rial', amount=str(Decimal(minor) / 100) if currency == 'RUB' else str(minor))

        if action == 'edit':
            if status != 'draft' or not isinstance(value, dict):
                raise LedgerError('فقط پیش‌نویس قابل ویرایش است.')
            direction = value.get('direction', entry['direction'])
            expected_currency = currency_for_direction(direction)
            currency = expected_currency if direction != entry['direction'] else entry['currency']
            if value.get('currency', currency) != currency:
                raise LedgerError('واحد مبلغ با سند یکسان نیست.')
            set_amount(parse_display_amount(value.get('amount', ''), currency), currency)
            fields.update(direction=direction, reference=normalize_reference(str(value.get('reference', ''))), note=str(value.get('note', ''))[:1000])
            entry['receipt']['name'] = validate_name(str(value.get('name', '')))
        elif action in ("amount", "amount_rial", "reference", "note", "direction", "name", "sender", "recipient", "date", "source_bank", "destination_bank"):
            if status != "draft":
                raise LedgerError("فقط پیش‌نویس قابل ویرایش است؛ سند ثبت‌شده را ابطال و دوباره ثبت کنید.")
            if action == 'amount':
                set_amount(parse_display_amount(value, entry['currency']), entry['currency'])
            elif action == "amount_rial":
                if entry['currency'] != 'IRR':
                    raise LedgerError('این سند روبلی است؛ آن را دوباره باز کنید.')
                set_amount(parse_amount(value), 'IRR')
            elif action == "direction":
                currency = currency_for_direction(value)
                fields['direction'] = value
                if value != entry['direction']:
                    # Changing direction is not an exchange-rate conversion.
                    set_amount(0, currency)
            elif action == "reference":
                value = normalize_reference(value)
            elif action == "name":
                value = validate_name(value)
            if action in ("name", "sender", "recipient", "date", "source_bank", "destination_bank"):
                entry["receipt"][action] = str(value)[:200]
                fields["receipt_json"] = json.dumps(entry["receipt"], ensure_ascii=False)
            elif action not in ('amount', 'amount_rial', 'direction'):
                fields[action] = value
        elif action in ("pending", "posted", "settle"):
            expected = "pending" if action == "settle" else "draft"
            if status != expected or not entry["amount_minor"]:
                raise LedgerError("ابتدا مبلغ را اصلاح کنید و وضعیت سند را بررسی کنید.")
            if action == "posted" and entry["receipt"].get("status", "manual") not in ("success", "manual"):
                raise LedgerError("وصول این فیش مشخص نیست؛ ابتدا در انتظار وصول ذخیره کنید.")
            fields["status"] = "posted" if action == "settle" else action
        elif action == "void" and status != "void":
            fields["status"] = "void"
        else:
            raise LedgerError("این عملیات برای وضعیت فعلی سند ممکن نیست.")
        fields.update(receipt_json=json.dumps(entry['receipt'], ensure_ascii=False), revision=revision + 1, updated_at=_now())
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

"""Administrator-only customer bank accounts, searchable by normalized owner name."""
import re

from core.banks import BANKS, find_bank, normalize_name
from core.db import _connect, _now
from core.utils import en_digits


def parse_details(text, bank_id):
    if bank_id not in BANKS:
        raise ValueError("بانک را از فهرست انتخاب کنید.")
    fields = {"card_number": "", "iban": "", "account": "", "phone": "", "note": ""}
    labels = {"کارت": "card_number", "card": "card_number", "شبا": "iban", "iban": "iban", "حساب": "account", "account": "account", "تلفن": "phone", "phone": "phone", "توضیح": "note", "شرح": "note"}
    if not text or len(text) > 1200:
        raise ValueError("اطلاعات معتبر، حداکثر ۱۲۰۰ کاراکتر، بفرستید.")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            if len(text.splitlines()) == 1:
                fields["card_number"] = line
                break
            raise ValueError("هر خط باید برچسب داشته باشد: کارت: ... یا شبا: ...")
        label, value = line.split(":", 1)
        field = labels.get(normalize_name(label))
        if not field:
            raise ValueError("برچسب مجاز: کارت، شبا، حساب، تلفن، توضیح.")
        fields[field] = value.strip()
    for field in ("card_number", "iban", "account", "phone"):
        fields[field] = re.sub(r"[\s\-\u200c\u200e\u200f]", "", en_digits(fields[field])).upper()
    card = fields["card_number"]
    if card and not re.fullmatch(r"\d{16}" if BANKS[bank_id]["country"] == "ir" else r"\d{16,19}", card):
        raise ValueError("شماره کارت ایران ۱۶ رقم و کارت روسیه ۱۶ تا ۱۹ رقم است.")
    if fields["iban"] and not re.fullmatch(r"IR\d{24}", fields["iban"]):
        raise ValueError("شبا باید IR و ۲۴ رقم باشد.")
    if fields["account"] and not re.fullmatch(r"\d{3,34}", fields["account"]):
        raise ValueError("شماره حساب باید ۳ تا ۳۴ رقم باشد.")
    if fields["phone"] and not re.fullmatch(r"\+?\d{8,15}", fields["phone"]):
        raise ValueError("شماره تلفن معتبر همراه کد کشور وارد کنید.")
    if not any(fields[k] for k in ("card_number", "iban", "account", "phone")):
        raise ValueError("حداقل کارت، شبا، حساب یا تلفن بانکی را وارد کنید.")
    fields["note"] = fields["note"][:300]
    return fields


async def save_card(owner_name, bank_id, details, admin_id, user_tg_id=None, card_id=None):
    owner_name = owner_name.strip()
    if not owner_name or len(owner_name) > 100 or "\n" in owner_name:
        raise ValueError("نام صاحب حساب باید ۱ تا ۱۰۰ کاراکتر و در یک خط باشد.")
    # Validate structured callers as strictly as typed input.
    raw = "\n".join(f"{label}: {details.get(field, '')}" for field, label in (
        ("card_number", "کارت"), ("iban", "شبا"), ("account", "حساب"), ("phone", "تلفن"), ("note", "توضیح")))
    details = parse_details(raw, bank_id)
    key = normalize_name(owner_name).replace(" ", "")
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        if not card_id:
            existing = await (await conn.execute(
                "SELECT id FROM customer_cards WHERE is_deleted=0 AND bank_id=? AND "
                "(name_key=? OR (user_tg_id IS NOT NULL AND user_tg_id=?)) AND card_number=? AND iban=? AND account=? AND phone=?",
                (bank_id, key, user_tg_id, details["card_number"], details["iban"], details["account"], details["phone"]),
            )).fetchone()
            if existing:
                return existing["id"]
        values = (owner_name, key, bank_id, details["card_number"], details["iban"], details["account"], details["phone"], details["note"], user_tg_id)
        if card_id:
            cur = await conn.execute("UPDATE customer_cards SET owner_name=?,name_key=?,bank_id=?,card_number=?,iban=?,account=?,phone=?,note=?,user_tg_id=?,updated_at=? WHERE id=? AND is_deleted=0",
                                     (*values, _now(), card_id))
            if not cur.rowcount:
                raise ValueError("کارت یافت نشد.")
        else:
            cur = await conn.execute("INSERT INTO customer_cards(owner_name,name_key,bank_id,card_number,iban,account,phone,note,user_tg_id,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                     (*values, admin_id, _now(), _now()))
            card_id = cur.lastrowid
        await conn.commit()
        return card_id
    finally:
        await conn.close()


async def list_cards(query="", user_tg_id=None, page=0, page_size=8):
    clauses, params = ["is_deleted=0"], []
    if query:
        key = normalize_name(query).replace(" ", "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("name_key LIKE ? ESCAPE '\\'")
        params.append("%" + key + "%")
    if user_tg_id:
        clauses.append("user_tg_id=?")
        params.append(user_tg_id)
    where = " AND ".join(clauses)
    conn = await _connect()
    try:
        count = (await (await conn.execute("SELECT COUNT(*) n FROM customer_cards WHERE " + where, params)).fetchone())["n"]
        rows = await (await conn.execute("SELECT * FROM customer_cards WHERE " + where + " ORDER BY name_key,id DESC LIMIT ? OFFSET ?", (*params, page_size, max(0, page) * page_size))).fetchall()
        return [dict(r) for r in rows], count
    finally:
        await conn.close()


async def get_card(card_id):
    conn = await _connect()
    try:
        row = await (await conn.execute("SELECT * FROM customer_cards WHERE id=? AND is_deleted=0", (card_id,))).fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def delete_card(card_id):
    conn = await _connect()
    try:
        await conn.execute("UPDATE customer_cards SET is_deleted=1,updated_at=? WHERE id=?", (_now(), card_id))
        await conn.commit()
    finally:
        await conn.close()


async def archive_payout(user_tg_id, text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 4:
        return None
    bank_id = find_bank(" ".join(lines[3:]))
    if not bank_id:
        return None
    try:
        return await save_card(lines[2], bank_id, {"card_number": lines[0], "phone": lines[1]}, 0, user_tg_id)
    except ValueError:
        return None


async def backfill_payouts():
    """One-time import of already-saved payout instructions; no user messages."""
    from core.db import get_setting, set_setting
    if await get_setting("card_archive_backfill", "") == "1":
        return
    conn = await _connect()
    try:
        rows = await (await conn.execute("SELECT telegram_id,payout_info FROM transactions WHERE payout_info IS NOT NULL AND payout_info!='' ORDER BY id")).fetchall()
    finally:
        await conn.close()
    for row in rows:
        await archive_payout(row["telegram_id"], row["payout_info"])
    await set_setting("card_archive_backfill", "1")

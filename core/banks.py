"""Bank identities and optional Telegram custom emoji identifiers."""
import json
import re
import unicodedata
from html import escape
from pathlib import Path

from aiogram.types import InlineKeyboardButton


def normalize_name(value):
    value = unicodedata.normalize("NFKC", value or "").translate(str.maketrans("كيىأإآ", "کییااا"))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", value.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")).strip().casefold()


BANKS = {
    "melli": {"name": "ملی", "country": "ir", "aliases": ["ملی", "melli"]},
    "mellat": {"name": "ملت", "country": "ir", "aliases": ["ملت", "mellat"]},
    "saderat": {"name": "صادرات", "country": "ir", "aliases": ["صادرات", "saderat"]},
    "tejarat": {"name": "تجارت", "country": "ir", "aliases": ["تجارت", "tejarat"]},
    "sepah": {"name": "سپه", "country": "ir", "aliases": ["سپه", "sepah"]},
    "saman": {"name": "سامان", "country": "ir", "aliases": ["سامان", "saman"]},
    "blu": {"name": "بلوبانک", "country": "ir", "aliases": ["بلوبانک", "بلو", "blubank", "blu"]},
    "mehr": {"name": "مهر ایران", "country": "ir", "aliases": ["مهر ایران", "مهرایران", "mehriran", "کیوبانک"]},
    "resalat": {"name": "رسالت", "country": "ir", "aliases": ["رسالت", "resalat"]},
    "pasargad": {"name": "پاسارگاد", "country": "ir", "aliases": ["پاسارگاد", "pasargad"]},
    "parsian": {"name": "پارسیان", "country": "ir", "aliases": ["پارسیان", "parsian"]},
    "refah": {"name": "رفاه", "country": "ir", "aliases": ["رفاه", "refah"]},
    "keshavarzi": {"name": "کشاورزی", "country": "ir", "aliases": ["کشاورزی", "keshavarzi"]},
    "maskan": {"name": "مسکن", "country": "ir", "aliases": ["مسکن", "maskan"]},
    "shahr": {"name": "شهر", "country": "ir", "aliases": ["شهر", "shahr"]},
    "novin": {"name": "اقتصاد نوین", "country": "ir", "aliases": ["اقتصاد نوین", "eghtesadnovin"]},
    "karafarin": {"name": "کارآفرین", "country": "ir", "aliases": ["کارآفرین", "کارافرین", "karafarin"]},
    "sina": {"name": "سینا", "country": "ir", "aliases": ["سینا", "sina"]},
    "ayandeh": {"name": "آینده", "country": "ir", "aliases": ["آینده", "ayandeh"]},
    "iranzamin": {"name": "ایران زمین", "country": "ir", "aliases": ["ایران زمین", "iranzamin"]},
    "tbank": {"name": "تینکوف · Т-Банк", "country": "ru", "aliases": ["تینکوف", "تینکفی", "تینک", "tinkoff", "tbank", "t-bank", "т-банк", "т банк", "тинькофф", "тинькоф"]},
    "sber": {"name": "اسبر · Сбер", "country": "ru", "aliases": ["اسبر", "sber", "сбер"]},
    "vtb": {"name": "VTB · ВТБ", "country": "ru", "aliases": ["vtb", "втб", "وی تی بی"]},
    "alfa": {"name": "آلفا · Альфа", "country": "ru", "aliases": ["الفا", "آلفا", "alfa", "альфа"]},
    "ozon": {"name": "اوزون · Ozon", "country": "ru", "aliases": ["ozon", "озон", "اوزون", "ازون"]},
    "gazprom": {"name": "گازپروم‌بانک · Газпромбанк", "country": "ru", "aliases": ["گازپروم", "gazprom", "газпром"]},
    "raiffeisen": {"name": "رایفایزن · Райффайзен", "country": "ru", "aliases": ["رایفایزن", "رایفایسن", "raiffeisen", "райффайзен"]},
    "rshb": {"name": "روس‌سلخوزبانک · Россельхозбанк", "country": "ru", "aliases": ["روس سلخوز", "روسسلخوز", "rshb", "россельхоз", "рсхб"]},
    "mkb": {"name": "MKB · МКБ", "country": "ru", "aliases": ["mkb", "мкб", "ام کی بی"]},
    "sovcom": {"name": "سووکوم‌بانک · Совкомбанк", "country": "ru", "aliases": ["سووکوم", "سوکم", "sovcom", "совком"]},
}

try:
    _PACK = json.loads((Path(__file__).resolve().parent.parent / "assets/banks/emoji_ids.json").read_text(encoding="utf-8"))
except (FileNotFoundError, ValueError):
    _PACK = {}


def find_bank(value):
    value = normalize_name(value)
    for bank_id, bank in BANKS.items():
        for alias in [bank_id, *bank["aliases"]]:
            if normalize_name(alias) == value:
                return bank_id
    for bank_id, bank in BANKS.items():
        if any(normalize_name(alias) in value for alias in bank["aliases"] if len(alias) >= 3):
            return bank_id
    return None


def emoji_id(bank_id):
    return _PACK.get("banks", {}).get(bank_id, {}).get("custom_emoji_id")


def bank_html(bank_id):
    bank = BANKS[bank_id]
    icon = f'<tg-emoji emoji-id="{emoji_id(bank_id)}">🏦</tg-emoji>' if emoji_id(bank_id) else "🏦"
    return icon + " " + escape(bank["name"])


def bank_button(bank_id, data):
    options = {"text": BANKS[bank_id]["name"], "callback_data": data}
    if emoji_id(bank_id):
        options["icon_custom_emoji_id"] = emoji_id(bank_id)
    return InlineKeyboardButton(**options)


def emoji_pack_url():
    return _PACK.get("url")

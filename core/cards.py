"""مدیریت چند «کارت واریز».

هر کارت یک برچسب (نام صاحب/بانک برای تشخیص) و یک «متن کامل» دارد که عیناً
به‌صورت یک پیام مستقل به کاربر نشان داده می‌شود (تمیز و قابل‌کپی).
ادمین می‌تواند چند کارت ثبت کند و انتخاب کند ربات کدام را نمایش دهد.

ساختار ذخیره‌سازی در جدول settings:
    cards           → JSON list of {"id": int, "label": str, "text": str}
    active_card_id  → id کارت فعال
"""
from typing import List, Optional

from core.db import get_json, get_setting, set_json, set_setting


async def get_cards() -> List[dict]:
    cards = await get_json("cards", [])
    return cards if isinstance(cards, list) else []


async def get_active_card() -> Optional[dict]:
    cards = await get_cards()
    if not cards:
        return None
    active_id = await get_setting("active_card_id", "")
    if active_id:
        for c in cards:
            if str(c.get("id")) == str(active_id):
                return c
    return cards[0]  # اگر انتخابی نبود، اولین کارت


async def add_card(label: str, text: str) -> int:
    cards = await get_cards()
    new_id = (max((int(c.get("id", 0)) for c in cards), default=0) + 1)
    cards.append({"id": new_id, "label": label.strip(), "text": text})
    await set_json("cards", cards)
    # اگر اولین کارت است، همان را فعال کن
    if len(cards) == 1:
        await set_setting("active_card_id", str(new_id))
    return new_id


async def delete_card(card_id: int) -> None:
    cards = await get_cards()
    cards = [c for c in cards if int(c.get("id", 0)) != int(card_id)]
    await set_json("cards", cards)
    active_id = await get_setting("active_card_id", "")
    if str(active_id) == str(card_id):
        await set_setting("active_card_id", str(cards[0]["id"]) if cards else "")


async def set_active_card(card_id: int) -> None:
    await set_setting("active_card_id", str(card_id))

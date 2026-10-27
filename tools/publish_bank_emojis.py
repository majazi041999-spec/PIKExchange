"""Publish the approved animated custom emoji pack using the existing bot identity.

Run on the bot server so BOT_TOKEN stays in its .env. Upload checkpoints allow a
retry without uploading the same files again. No credentials are printed.
"""
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.getenv("PIK_BOT_ROOT", str(ROOT)))

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputSticker

from core.config import BOT_TOKEN

OWNER = 6787962422
FOLDER = ROOT / "assets/banks"
BANKS = json.loads((FOLDER / "catalog.json").read_text(encoding="utf-8"))
SPECIALS = json.loads((FOLDER / "specials.json").read_text(encoding="utf-8"))


async def main():
    async with Bot(BOT_TOKEN) as bot:
        me = await bot.get_me()
        owner = await bot.get_chat(OWNER)
        handle = owner.username or str(OWNER)
        name = f"PIK_Banks_{handle}_by_{me.username}"
        title = f"PIK Banks • @{handle}"
        checkpoint = FOLDER / ".upload-cache.json"
        uploads = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
        order = ["pik", *BANKS, *SPECIALS]
        manifest_path = FOLDER / "emoji_ids.json"
        previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        stickers = []
        for key in order:
            static = SPECIALS.get(key, {}).get('static', False)
            fmt = 'static' if static else 'video'
            path = FOLDER / f"emoji/{key}.{'webp' if static else 'webm'}"
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            cached = uploads.get(key, {})
            if cached.get("sha256") != digest:
                uploaded = await bot.upload_sticker_file(OWNER, FSInputFile(path), fmt)
                cached = {"sha256": digest, "file_id": uploaded.file_id}
                uploads[key] = cached
                checkpoint.write_text(json.dumps(uploads, indent=2), encoding="utf-8")
            keywords = ["PIK", handle] if key == "pik" else [BANKS[key]["name"].split(" · ")[0], key] if key in BANKS else [SPECIALS[key]['name'], key]
            stickers.append(InputSticker(sticker=cached["file_id"], format=fmt, emoji_list=["💱" if key == "pik" else "🏦" if key in BANKS else SPECIALS[key]['emoji']], keywords=keywords))
            print("uploaded:", key, flush=True)
        try:
            pack = await bot.get_sticker_set(name)
            if not previous.get("banks"):
                raise RuntimeError("Existing pack needs its saved manifest to update safely")
            for key, sticker in zip(order, stickers):
                old = previous["banks"].get(key)
                if old is None:
                    await bot.add_sticker_to_set(OWNER, name, sticker)
                elif old.get("sha256") != uploads[key]["sha256"]:
                    await bot.replace_sticker_in_set(OWNER, name, old["file_id"], sticker)
            pack = await bot.get_sticker_set(name)
        except TelegramBadRequest as exc:
            if "STICKERSET_INVALID" not in str(exc):
                raise
            await bot.create_new_sticker_set(OWNER, name, title, stickers, sticker_type="custom_emoji", needs_repainting=False)
            pack = await bot.get_sticker_set(name)
        if pack.sticker_type != "custom_emoji" or len(pack.stickers) != len(order):
            raise RuntimeError("Telegram did not return the expected custom emoji set")
        result = {"name": name, "title": title, "url": f"https://t.me/addemoji/{name}", "owner_id": OWNER,
                  "owner_username": handle, "animated": True, "banks": {}}
        for key, sticker in zip(order, pack.stickers):
            if not sticker.custom_emoji_id or sticker.is_video == SPECIALS.get(key, {}).get('static', False):
                raise RuntimeError("A sticker was not accepted in its expected custom emoji format")
            result["banks"][key] = {"custom_emoji_id": sticker.custom_emoji_id, "file_id": sticker.file_id,
                                     "sha256": uploads[key]["sha256"], "is_video": sticker.is_video}
        await bot.set_custom_emoji_sticker_set_thumbnail(name, custom_emoji_id=result["banks"]["pik"]["custom_emoji_id"])
        # The owner explicitly asked to receive the finished pack.
        text = f"🎨 پک ایموجی متحرک PIK Banks\nبه نام @{handle}\n\n{result['url']}\n\n"
        for key in ["pik", "tbank", "sber", "vtb", "alfa"]:
            alternate = "💱" if key == "pik" else "🏦"
            label = "PIK Exchange" if key == "pik" else BANKS[key]["name"]
            text += f'<tg-emoji emoji-id="{result["banks"][key]["custom_emoji_id"]}">{alternate}</tg-emoji> {label}\n'
        message = await bot.send_message(OWNER, text, parse_mode="HTML")
        result["bot_custom_emoji_enabled"] = any(entity.type == "custom_emoji" for entity in (message.entities or []))
        (FOLDER / "emoji_ids.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PACK_URL:", result["url"])
        print("EMOJI_COUNT:", len(pack.stickers))
        print("BOT_CUSTOM_EMOJI_ENABLED:", result["bot_custom_emoji_enabled"])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(type(exc).__name__, str(exc).replace(BOT_TOKEN, "<REDACTED>"), file=sys.stderr)
        sys.exit(1)

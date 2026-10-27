"""Read-only retrieval of a user-supplied sticker reference, using the bot server."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, '/opt/PIKExchangeBot')
from aiogram import Bot
from core.config import BOT_TOKEN


async def main():
    folder = Path('/root/pik-sticker-reference')
    folder.mkdir(exist_ok=True)
    async with Bot(BOT_TOKEN) as bot:
        pack = await bot.get_sticker_set('PIKEXCHANGEBahramRu')
        for i, sticker in enumerate(pack.stickers):
            ext = 'webm' if sticker.is_video else 'tgs' if sticker.is_animated else 'webp'
            await bot.download(sticker.file_id, destination=folder / f'{i:03}.{ext}')
        (folder / 'metadata.json').write_text(pack.model_dump_json(exclude_none=True))
        me = await bot.get_me()
        photos = await bot.get_user_profile_photos(me.id, limit=1)
        if photos.photos:
            await bot.download(photos.photos[0][-1].file_id, destination=folder / 'bot-profile.jpg')
        print(json.dumps({'title': pack.title, 'count': len(pack.stickers),
                          'static': sum(not s.is_video and not s.is_animated for s in pack.stickers)}, ensure_ascii=False))


try:
    asyncio.run(main())
except Exception as exc:
    print(type(exc).__name__, str(exc).replace(BOT_TOKEN, '<REDACTED>'), file=sys.stderr)
    sys.exit(1)

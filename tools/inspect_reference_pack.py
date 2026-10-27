"""Read-only comparison assets for a user-supplied Telegram emoji pack."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/PIKExchangeBot")
from aiogram import Bot
from core.config import BOT_TOKEN


async def main():
    folder = Path('/root/pik-reference')
    folder.mkdir(exist_ok=True)
    async with Bot(BOT_TOKEN) as bot:
        pack = await bot.get_sticker_set('PIKBahramRuMadeByT0')
        for i, sticker in enumerate(pack.stickers):
            await bot.download(sticker.thumbnail.file_id if sticker.thumbnail else sticker.file_id,
                               destination=folder / f'{i:03}.webp')
        (folder / 'metadata.json').write_text(pack.model_dump_json(exclude_none=True))
        print(json.dumps({'title': pack.title, 'count': len(pack.stickers)}, ensure_ascii=False))


asyncio.run(main())

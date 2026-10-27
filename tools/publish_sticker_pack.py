"""Create the new PIK sticker pack; the supplied old pack is never modified."""
import asyncio
import hashlib
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.getenv('PIK_BOT_ROOT', str(ROOT)))
sys.path.insert(0, str(ROOT / 'vendor'))

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputSticker
from core.config import BOT_TOKEN
from PIL import Image, ImageChops, ImageStat

FOLDER = ROOT / 'assets/stickers/redesign'
CATALOG = json.loads((FOLDER / 'catalog.json').read_text(encoding='utf-8'))


async def main():
    async with Bot(BOT_TOKEN) as bot:
        me = await bot.get_me()
        owner_id = CATALOG['owner_id']
        owner = await bot.get_chat(owner_id)
        handle = owner.username or str(owner_id)
        name = f'PIK_Exchange_New_{handle}_by_{me.username}'
        title = f'PIK Exchange · @{handle}'
        if len(name) > 64 or 'PIKEXCHANGEBahramRu'.casefold() == name.casefold():
            raise RuntimeError('Invalid new pack identity')
        cache_path = FOLDER / '.upload-cache.json'
        cache = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
        inputs = []
        for item in CATALOG['stickers']:
            key = item['key']
            path = FOLDER / 'stickers' / f'{key}.webp'
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if cache.get(key, {}).get('sha256') != digest:
                uploaded = await bot.upload_sticker_file(owner_id, FSInputFile(path), 'static')
                cache[key] = {'sha256': digest, 'file_id': uploaded.file_id, 'file_unique_id': uploaded.file_unique_id}
                cache_path.write_text(json.dumps(cache, indent=2), encoding='utf-8')
            inputs.append(InputSticker(sticker=cache[key]['file_id'], format='static', emoji_list=[item['emoji']]))
            print('uploaded:', key, flush=True)
        try:
            pack = await bot.get_sticker_set(name)
        except TelegramBadRequest as exc:
            if 'STICKERSET_INVALID' not in str(exc):
                raise
            await bot.create_new_sticker_set(owner_id, name, title, inputs, sticker_type='regular')
            pack = await bot.get_sticker_set(name)
        if pack.sticker_type != 'regular' or len(pack.stickers) != len(inputs):
            raise RuntimeError('Telegram returned an unexpected sticker pack')
        result = {'name': pack.name, 'title': pack.title, 'url': f'https://t.me/addstickers/{pack.name}',
                  'owner_id': owner_id, 'owner_username': handle, 'count': len(pack.stickers),
                  'reference': CATALOG['reference'], 'stickers': {}}
        for item, sticker in zip(CATALOG['stickers'], pack.stickers):
            key = item['key']
            if sticker.is_animated or sticker.is_video or max(sticker.width, sticker.height) != 512:
                raise RuntimeError('Sticker dimensions or format differ from the approved asset')
            # Telegram re-encodes static stickers and changes their File identity.
            # Compare decoded visible pixels on both chat backgrounds instead.
            downloaded = io.BytesIO()
            await bot.download(sticker.file_id, destination=downloaded)
            actual = Image.open(downloaded).convert('RGBA')
            expected = Image.open(FOLDER / 'stickers' / f'{key}.webp').convert('RGBA')
            if actual.size != expected.size:
                raise RuntimeError('Published sticker dimensions changed: ' + key)
            deltas = []
            for color in ('black', 'white'):
                a = Image.alpha_composite(Image.new('RGBA', actual.size, color), actual).convert('RGB')
                b = Image.alpha_composite(Image.new('RGBA', expected.size, color), expected).convert('RGB')
                deltas.append(max(ImageStat.Stat(ImageChops.difference(a, b)).mean))
            alpha_delta = ImageStat.Stat(ImageChops.difference(actual.getchannel('A'), expected.getchannel('A'))).mean[0]
            if max(deltas) > 3 or alpha_delta > .5:
                raise RuntimeError(f'Published artwork differs from approved image: {key}, color={max(deltas):.3f}, alpha={alpha_delta:.3f}')
            result['stickers'][key] = {'file_id': sticker.file_id, 'file_unique_id': sticker.file_unique_id,
                                       'sha256': cache[key]['sha256'], 'lines': item['lines'],
                                       'telegram_sha256': hashlib.sha256(downloaded.getvalue()).hexdigest(),
                                       'verification_color_mae': max(deltas), 'verification_alpha_mae': alpha_delta}
            print(f'verified: {key}, color={max(deltas):.3f}, alpha={alpha_delta:.3f}', flush=True)
        await bot.set_sticker_set_thumbnail(name=name, user_id=owner_id, format='static', thumbnail=FSInputFile(FOLDER / 'thumbnail.png'))
        (FOLDER / 'published.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('PACK_URL:', result['url'])
        print('STICKER_COUNT:', result['count'])


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as exc:
        print(type(exc).__name__, str(exc).replace(BOT_TOKEN, '<REDACTED>'), file=sys.stderr)
        sys.exit(1)

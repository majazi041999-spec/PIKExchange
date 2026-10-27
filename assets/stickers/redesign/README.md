# PIK Exchange · @Bahram_ru

New regular Telegram sticker pack, redesigned from the owner's supplied reference.
It contains 13 static 512×512 stickers with a charcoal/mint PIK identity and
transparent outer corners. The contact number and remaining reference messages
are preserved. The original pack is not edited.

Pack: https://t.me/addstickers/PIK_Exchange_New_Bahram_ru_by_PIKexchange_bot

The original 14-sticker reference is https://t.me/addstickers/PIKEXCHANGEBahramRu.
Reference stickers 001 (coordinate before depositing) and 002 (instant transfers)
are excluded. The added screenshot-again sticker has these exact line breaks:

```text
اسکرین شات
مجدد لطفا
```

`catalog.json` records exact copy, emoji associations, and reference mapping.
`source/` retains the high-resolution artwork. `stickers/` contains the final PNG
and Telegram WEBP exports. `preview.png` is the pack contact sheet.
`published.json` contains the actual published Telegram identifiers.

The main artwork was produced with built-in ImageGen, without a CLI/API key.
The prompt set is in `prompts.json`. The dense bank-information request uses
native Vazirmatn ExtraBold typography and editable layout code in
`tools/typeset_payout_sticker.cjs` to preserve every field label. The font is
distributed under the included SIL Open Font License.

For mechanical packaging, run `tools/package_stickers.cjs` with sharp available
through SHARP_MODULE_PATH. Packaging resizes/encodes approved art without changing
its text or illustration. Each WEBP must remain below 512 KiB with transparent
outer corners. Publishing is run on the Finland bot server with
`tools/publish_sticker_pack.py`; BOT_TOKEN remains in the server environment.
Publication verifies every downloaded Telegram sticker against the approved
image on light and dark backgrounds, allowing only small compression differences.
Source and published hashes and image differences are retained in the manifest.
The verification tool uses Pillow, installed separately from the bot runtime.
It does not send any broadcast or modify the old pack.

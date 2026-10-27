# PIK Banks · @Bahram_ru

Animated custom emoji for identifying the banks in PIK Exchange. The pack contains
30 bank identifiers, the PIK mark, four unique reference graphics, and six static
action/currency icons (41 total). Static icons are 100×100 transparent WEBP. Each WEBM is 100×100, VP9
with alpha, 20 fps, 2.4 seconds, and below 256 KB. Motion is a subtle seamless
breathing scale; the banks retain their colors and aspect ratios. PIK has true
transparency and no Exchange subtitle. Bank artwork has transparent outer backgrounds.

Pack: https://t.me/addemoji/PIK_Banks_Bahram_ru_by_PIKexchange_bot

`emoji_ids.json` contains the published Telegram identifiers. `source/` contains
the editable original artwork and `sources.json` lists origins. The Iranian SVG
library is MIT licensed (license included); bank names and marks belong to their
respective banks. Logos are used to identify bank accounts, with no claim of bank
affiliation or endorsement.

The Russian inverted square tiles were replaced by standalone colored marks.
Sber uses its multicolor ring/check mark, with artwork by Sberbank distributed at
https://commons.wikimedia.org/wiki/File:Logo_Sberbank.svg (source: sberbank.ru).
T-Bank uses its current yellow T shield, replacing the former Tinkoff crest:
https://www.tbank.ru/about/brand/ and https://www.tbank.ru/finance/blog/goes-tbank/.
VTB uses native blue stripes and Cyrillic letter outlines stacked for readability
at chat size. Alfa retains its red A. Unique reference graphics were compared to
https://t.me/addemoji/PIKBahramRuMadeByT0; duplicate bank and PIK variants were excluded.
The purple and gold reference graphics retain their original identities without
assigning an unverified bank name. Assets and
visible shape/color should be rechecked against official brand material before
replacing a published logo. Historical bank marks can still identify archived cards.

To rebuild locally, use `tools/render_bank_vectors.cjs` with sharp, then
`tools/build_bank_emojis.py` with pillow and imageio-ffmpeg. Development rendering
dependencies are not required to run the bot. `tools/publish_bank_emojis.py` runs
on the bot server and never prints its token; the pack is owned by admin 6787962422.

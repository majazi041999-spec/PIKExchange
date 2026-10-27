"""مهاجرت‌های سبک و نسخه‌دار روی دیتابیس (تنظیمات کلید-مقدار).

از آنجا که «محصولات» و «متن‌ها» در دیتابیس بازنویسی می‌شوند و ادغام دیکشنری‌ها
لیست پله‌ها را کامل جایگزین می‌کند، تغییر ساختار پیش‌فرض در کد به‌تنهایی روی نصب
تولیدی اثر ندارد. این ماژول یک‌بار (بر اساس شمارهٔ نسخه) بازنویسی‌های ذخیره‌شده را
با ساختار جدید هماهنگ می‌کند و ضریب‌های تنظیم‌شدهٔ ادمین را حفظ می‌کند.

هر مهاجرت باید idempotent باشد؛ اجرای دوباره نباید داده را خراب کند.
"""
import logging
import re
from copy import deepcopy

from core.db import get_json, get_setting, set_json, set_setting
from core.products import DEFAULT_PRODUCTS

logger = logging.getLogger("pik.migrations")

# نسخهٔ فعلی اسکیمای تنظیمات. هر بار که مهاجرت جدیدی افزوده شد، این عدد را زیاد کنید.
SCHEMA_VERSION = 4


async def _migrate_rub_five_tiers() -> None:
    """Restore five choices, preserving configured rates and updating volume labels."""
    overrides = await get_json("products", {})
    rub = overrides.get("rub_c2c")
    if not isinstance(rub, dict) or not isinstance(rub.get("tiers"), list):
        return
    old = {t["key"]: t for t in rub["tiers"]}
    tiers = deepcopy(DEFAULT_PRODUCTS["rub_c2c"]["tiers"])
    for tier in tiers:
        source = old.get(tier["key"])
        if source is None and tier["key"] in ("t1", "t2"):
            source = old.get("t3")  # Existing flat-rate installs retain their current price.
        if source:
            for field in ("mult", "offset"):
                if field in source:
                    tier[field] = source[field]
    rub["tiers"] = tiers
    await set_json("products", overrides)


async def _migrate_products() -> None:
    """پله‌های «خرید روبل» را به ساختار جدید (۳ تینکوف + ۲ تک‌فیش) می‌برد و
    ضریب‌های قبلی را حفظ می‌کند؛ «خرید تومان» را به حالت دوپله‌ای تبدیل می‌کند."""
    overrides = await get_json("products", {})
    if not isinstance(overrides, dict):
        return
    changed = False

    # ── خرید روبل: بازسازی پله‌ها با حفظ ضریب‌های ذخیره‌شده ──
    rub = overrides.get("rub_c2c")
    if isinstance(rub, dict) and isinstance(rub.get("tiers"), list):
        old_by_key = {t.get("key"): t for t in rub["tiers"] if isinstance(t, dict)}
        # نگاشت پلهٔ جدید → پلهٔ قدیمی که ضریبش را به ارث می‌برد
        mult_from = {"t1": "t1", "t2": "t2", "t3": "t3", "s1": "single", "s2": "single"}
        new_tiers = deepcopy(DEFAULT_PRODUCTS["rub_c2c"]["tiers"])
        # فقط وقتی ساختار قدیمی است (کلید s1/s2 ندارد) مهاجرت لازم است
        if "s1" not in old_by_key or "s2" not in old_by_key:
            for nt in new_tiers:
                src = old_by_key.get(mult_from.get(nt["key"], ""))
                if src and "mult" in src:
                    nt["mult"] = src["mult"]
                if src and "offset" in src:
                    nt["offset"] = src["offset"]
            rub["tiers"] = new_tiers
            changed = True
            logger.info("migrated rub_c2c tiers (preserved multipliers)")

    # ── خرید تومان: تبدیل به دوپله‌ای (۳۰۰/۴۰۰ زیر نرخ سایت) + ارجاع به پشتیبانی ──
    toman = overrides.get("toman_with_rub")
    if isinstance(toman, dict) and toman.get("type") != "rub_tiered":
        overrides["toman_with_rub"] = deepcopy(DEFAULT_PRODUCTS["toman_with_rub"])
        changed = True
        logger.info("migrated toman_with_rub to tiered structure")

    if changed:
        await set_json("products", overrides)


async def _migrate_product_titles() -> None:
    """عنوان محصولات از پنل قابل‌ویرایش نیست و باید از کد بیاید. مهاجرت v1 کل
    دیکشنری پیش‌فرض «خرید تومان» (شامل عنوان قدیمی) را در بازنویسی نوشت؛ اینجا
    کلید ``title`` را از بازنویسی حذف می‌کنیم تا عنوانِ به‌روزِ کد (با پرچم ایران)
    نمایش داده شود. برای بقیهٔ محصولات هم اگر عنوان در بازنویسی مانده باشد پاک می‌شود."""
    overrides = await get_json("products", {})
    if not isinstance(overrides, dict):
        return
    changed = False
    for pid, ov in overrides.items():
        if isinstance(ov, dict) and "title" in ov:
            ov.pop("title", None)
            changed = True
    if changed:
        await set_json("products", overrides)
        logger.info("removed stored product title overrides (code titles now apply)")


async def _migrate_rules_text() -> None:
    """در «متن قوانین» ذخیره‌شده: بخش واریز آنی/پل را بولد می‌کند و قانون کارمزد
    فسخ (۴٪) را اضافه می‌کند. اگر متنی ذخیره نشده باشد، پیش‌فرضِ کد (که از قبل
    شامل این موارد است) استفاده می‌شود و نیازی به کاری نیست."""
    rules = await get_setting("text_rules", "")
    if not rules:
        return
    new = rules

    # ۱) بولد کردن پرانتز راهنمای «واریز آنی» در قوانین (idempotent)
    m = re.search(r"\([^)]*واریز آنی[^)]*\)", new)
    if m and "*" not in m.group(0):
        matched = m.group(0)
        new = new.replace(matched, "(*" + matched[1:-1] + "*)", 1)

    # ۲) افزودن قانون کارمزد فسخ ۴٪ (idempotent)
    if "۴٪ کارمزد" not in new and "۴ ٪ کارمزد" not in new:
        cancel_rule = "7️⃣ در صورت فسخ معامله، طرفِ فسخ‌کننده ملزم به پرداخت *۴٪ کارمزد* می‌باشد."
        idx = new.find("‼️ واریز وجه")
        if idx != -1:
            new = new[:idx] + cancel_rule + "\n\n" + new[idx:]
        else:
            new = new.rstrip() + "\n\n" + cancel_rule

    if new != rules:
        await set_setting("text_rules", new)
        logger.info("migrated stored text_rules (bold instant-payment + 4%% cancellation rule)")


async def run_migrations() -> None:
    """مهاجرت‌های لازم را بر اساس نسخهٔ ذخیره‌شده اجرا می‌کند (یک‌بار)."""
    try:
        current = int(await get_setting("settings_schema_version", "0") or "0")
    except (TypeError, ValueError):
        current = 0
    if current >= SCHEMA_VERSION:
        return

    logger.info("running settings migrations: %s → %s", current, SCHEMA_VERSION)
    try:
        if current < 2:
            await _migrate_products()
            await _migrate_product_titles()
            await _migrate_rules_text()
        if current < 4:
            await _migrate_rub_five_tiers()
    except Exception as e:
        # مهاجرت نباید مانع بالا آمدن ربات شود؛ فقط لاگ می‌کنیم.
        logger.exception("migration failed: %s", e)
        return

    await set_setting("settings_schema_version", str(SCHEMA_VERSION))
    logger.info("settings migrations complete (version=%s)", SCHEMA_VERSION)

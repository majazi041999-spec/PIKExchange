"""کمکی‌های نمایش: تبدیل ارقام فارسی و قالب‌بندی مبلغ."""

_EN_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_FA_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def fa_digits(text) -> str:
    """ارقام لاتین را به فارسی تبدیل می‌کند."""
    return str(text).translate(_EN_TO_FA)


def en_digits(text) -> str:
    """ارقام فارسی/عربی را به لاتین تبدیل می‌کند (برای پارس ورودی کاربر)."""
    return str(text).translate(_FA_TO_EN)


def parse_int(text) -> int:
    """رشته‌ی ورودی کاربر (با ارقام فارسی، کاما، فاصله) را به عدد صحیح تبدیل می‌کند."""
    s = en_digits(text or "")
    s = s.replace(",", "").replace("،", "").replace(" ", "").replace("_", "").strip()
    if not s:
        raise ValueError("empty")
    return int(float(s))


def toman(amount) -> str:
    """مبلغ را با جداکننده هزارگان و ارقام فارسی، به‌همراه واحد «تومان»."""
    try:
        n = int(round(float(amount)))
    except (TypeError, ValueError):
        n = 0
    return f"{fa_digits(f'{n:,}')} تومان"


def num(amount) -> str:
    """فقط عدد با جداکننده هزارگان و ارقام فارسی (بدون واحد)."""
    try:
        n = int(round(float(amount)))
    except (TypeError, ValueError):
        n = 0
    return fa_digits(f"{n:,}")


def money(amount, currency: str = "تومان") -> str:
    """عدد + واحد دلخواه (تومان/روبل/…)."""
    return f"{num(amount)} {currency or 'تومان'}"

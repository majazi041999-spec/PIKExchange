import os
from typing import List

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS: List[int] = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()
]

SUPPORT_USERNAME: str = os.getenv("SUPPORT_USERNAME", "").strip()
RATE_URL: str = os.getenv("RATE_URL", "https://alanchand.com/en").strip()
DB_PATH: str = os.getenv("DB_PATH", "pik.db").strip() or "pik.db"

# مدت اعتبار نرخ اعلامی به کاربر (دقیقه)
RATE_VALIDITY_MINUTES: int = int(os.getenv("RATE_VALIDITY_MINUTES", "30"))


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

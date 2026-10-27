"""Back up the Finland ledger and upgrade currency storage without changing old money."""
import asyncio
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import DB_PATH
from core.db import init_db
from core.accounting import totals


async def main():
    if ROOT != Path('/opt/PIKExchangeBot') or os.geteuid() != 0:
        raise RuntimeError('Run only on the configured Finland server as root')
    folder = Path('/root/pik-backups') / (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-before-rouble-ledger')
    folder.mkdir(parents=True, mode=0o700)
    subprocess.run(['systemctl', 'stop', 'pik-bot', 'pik-web'], check=True)
    try:
        with sqlite3.connect(DB_PATH) as conn, sqlite3.connect(str(folder / 'pik.db')) as backup:
            conn.backup(backup)
            before = conn.execute('SELECT id,direction,amount_rial,status,revision,created_at,updated_at,receipt_json FROM ledger ORDER BY id').fetchall()
        os.chmod(folder / 'pik.db', 0o600)
        await init_db()
        with sqlite3.connect(DB_PATH) as conn:
            after = conn.execute('SELECT id,direction,amount_rial,status,revision,created_at,updated_at,receipt_json FROM ledger ORDER BY id').fetchall()
        if before != after:
            raise RuntimeError('Original ledger fields changed unexpectedly; inspect the saved backup')
        print('Original ledger records preserved:', len(before))
        print('Currency totals:', await totals())
    finally:
        subprocess.run(['systemctl', 'start', 'pik-web', 'pik-bot'], check=True)


if __name__ == '__main__':
    asyncio.run(main())

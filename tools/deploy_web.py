"""Finland deployment: back up the shared database, then activate reviewed services."""
import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import DB_PATH
from core.db import set_setting
from core.web_auth import init_web


async def main():
    if ROOT != Path('/opt/PIKExchangeBot') or os.geteuid() != 0:
        raise RuntimeError('Run on the configured Finland bot server as root')
    folder = Path('/root/pik-backups') / (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-before-web-panel')
    folder.mkdir(mode=0o700, parents=True)
    source = sqlite3.connect(DB_PATH)
    target = sqlite3.connect(str(folder / 'pik.db'))
    source.backup(target)
    target.close()
    source.close()
    os.chmod(folder / 'pik.db', 0o600)
    await init_web()
    await set_setting('web_public_url', 'https://65.109.182.47:9443')
    shutil.copyfile(ROOT / 'deploy/pik-web.service', '/etc/systemd/system/pik-web.service')
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'pik-web'], check=True)
    subprocess.run(['systemctl', 'restart', 'pik-web', 'pik-bot'], check=True)
    print('Database backup created; web panel and bot started.')


if __name__ == '__main__':
    asyncio.run(main())

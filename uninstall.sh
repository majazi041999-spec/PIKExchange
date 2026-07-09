#!/usr/bin/env bash
set -euo pipefail

# PIK Exchange Bot — uninstall
# سرویس را متوقف/حذف و دستور pik را پاک می‌کند. دیتابیس و .env به‌صورت پیش‌فرض نگه داشته می‌شوند.
#   bash uninstall.sh            # با تأیید
#   bash uninstall.sh --force    # بدون سؤال
#   bash uninstall.sh --purge    # حذف کامل شامل دیتابیس، .env و کل پوشه

NAME="pik-bot"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORCE=0; PURGE=0
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    --purge) PURGE=1; FORCE=1 ;;
  esac
done

run_root(){
  if [[ "$(id -u)" -eq 0 ]]; then bash -c "$*"
  elif command -v sudo >/dev/null 2>&1; then sudo bash -c "$*"
  else echo "needs root"; exit 1; fi
}

if [[ "$FORCE" -ne 1 ]]; then
  read -r -p "آیا مطمئن هستید که می‌خواهید سرویس $NAME حذف شود؟ [y/N]: " ans
  [[ "$ans" =~ ^[Yy]$ ]] || { echo "لغو شد."; exit 0; }
fi

echo "ℹ️ Stopping & disabling $NAME ..."
run_root "systemctl stop '$NAME' 2>/dev/null || true"
run_root "systemctl disable '$NAME' 2>/dev/null || true"
run_root "rm -f '/etc/systemd/system/${NAME}.service'"
run_root "systemctl daemon-reload || true"
run_root "rm -f /usr/local/bin/pik"
echo "✅ Service and command removed."

if [[ "$PURGE" -eq 1 ]]; then
  echo "⚠️ Purging install directory: $DIR"
  cd /
  run_root "rm -rf '$DIR'"
  echo "✅ Purged."
else
  echo "ℹ️ دیتابیس و .env حفظ شدند. برای حذف کامل: bash uninstall.sh --purge"
fi

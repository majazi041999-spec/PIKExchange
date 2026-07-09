#!/usr/bin/env bash
set -euo pipefail

# دریافت آخرین نسخه از گیت‌هاب و ری‌استارت سرویس
# Usage:
#   sudo ./update.sh                # pull سریع (با auto-stash تغییرات محلی)
#   sudo ./update.sh hard           # reset --hard به origin (حذف تغییرات محلی)
#   sudo ./update.sh pull-no-stash  # اگر درخت کاری کثیف بود، خطا بده

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRANCH="${BRANCH:-main}"
SERVICE="${SERVICE:-pik-bot}"
MODE="${1:-pull}"

if [ -z "${HOME:-}" ]; then
  export HOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"
  [ -z "$HOME" ] && export HOME="/root"
fi

need_cmd(){ command -v "$1" >/dev/null 2>&1 || { echo "❌ '$1' is not installed"; exit 1; }; }
need_cmd git; need_cmd systemctl; need_cmd python3

git config --system --add safe.directory "$REPO_DIR" 2>/dev/null \
  || git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true

cd "$REPO_DIR"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "❌ Current directory is not a git repository."; exit 1
fi

SERVICE_STOPPED=0; STASHED=0; STASH_REF=""
STAMP="$(date +%Y%m%d-%H%M%S)"
PATCH_FILE="$REPO_DIR/update-local-changes-$STAMP.patch"
STOP_TIMEOUT="${STOP_TIMEOUT:-20}"

stop_service_fast(){
  local service="$1" timeout="$2"
  systemctl is-active --quiet "$service" || return 0
  systemctl stop "$service" --no-block || return 0
  for ((i=0;i<timeout;i++)); do
    systemctl is-active --quiet "$service" || return 0
    sleep 1
  done
  echo "⚠️ forcing $service down ..."
  systemctl kill --kill-who=all --signal=SIGKILL "$service" || true
  return 0
}

cleanup_on_error(){
  local code=$?
  if [[ "$code" -ne 0 ]]; then
    echo "❌ Update failed (code=$code)."
    if [[ "$STASHED" -eq 1 ]]; then
      git stash pop --index -q "$STASH_REF" && echo "✅ Local changes restored." \
        || echo "⚠️ stash restore conflict; check: git stash list"
      STASHED=0
    fi
    if [[ "$SERVICE_STOPPED" -eq 1 ]]; then
      systemctl start "$SERVICE" || true
    fi
  fi
}
trap cleanup_on_error EXIT

echo "ℹ️ Fetching origin/$BRANCH ..."
git fetch origin "$BRANCH" --prune

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"

if [[ "$LOCAL" == "$REMOTE" ]]; then
  echo "✅ Already up to date"
  systemctl restart "$SERVICE"
  trap - EXIT; exit 0
fi

if [[ "$MODE" == "pull" || "$MODE" == "pull-no-stash" ]]; then
  if [[ -n "$(git status --porcelain)" ]]; then
    if [[ "$MODE" == "pull-no-stash" ]]; then
      echo "❌ Working tree is dirty and pull-no-stash was selected."; exit 3
    fi
    echo "⚠️ Local changes detected; stashing before update."
    git diff > "$PATCH_FILE" || true
    echo "ℹ️ Diff backup: $PATCH_FILE"
    git stash push -m "pik-auto-stash-$STAMP" >/dev/null
    STASH_REF="stash@{0}"; STASHED=1
  fi
fi

echo "ℹ️ Stopping service $SERVICE ..."
stop_service_fast "$SERVICE" "$STOP_TIMEOUT"; SERVICE_STOPPED=1

case "$MODE" in
  pull|pull-no-stash) echo "ℹ️ git pull --ff-only ..."; git pull --ff-only origin "$BRANCH" ;;
  hard) echo "⚠️ git reset --hard origin/$BRANCH ..."; git reset --hard "origin/$BRANCH" ;;
  *) echo "Usage: $0 [pull|hard|pull-no-stash]"; exit 2 ;;
esac

# venv + deps
if [[ ! -x "$REPO_DIR/.venv/bin/python" ]]; then
  echo "ℹ️ creating .venv ..."; python3 -m venv "$REPO_DIR/.venv"
fi
"$REPO_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null
"$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt" --upgrade

# reload unit (if changed) and restart
if [[ -f "$REPO_DIR/setup_service.sh" ]]; then
  bash "$REPO_DIR/setup_service.sh"
else
  systemctl start "$SERVICE"
fi
SERVICE_STOPPED=0

if [[ "$STASHED" -eq 1 ]]; then
  echo "ℹ️ Restoring local changes ..."
  git stash pop --index -q "$STASH_REF" && echo "✅ Restored." \
    || echo "⚠️ stash conflict; check: git stash list"
  STASHED=0
fi

echo "✅ Done"
trap - EXIT

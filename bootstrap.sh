#!/usr/bin/env bash
set -euo pipefail

# PIK Exchange Bot — bootstrap/manager (نصب و مدیریت با یک خط)
#
# مثال نصب:
#   bash <(curl -Ls https://raw.githubusercontent.com/majazi041999-spec/PIKExchange/main/bootstrap.sh)
# مثال آپدیت:
#   bash <(curl -Ls https://raw.githubusercontent.com/majazi041999-spec/PIKExchange/main/bootstrap.sh) update

REPO_URL="${REPO_URL:-https://github.com/majazi041999-spec/PIKExchange.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/PIKExchangeBot}"
SERVICE="${SERVICE:-pik-bot}"
CMD="${1:-install}"

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info(){ echo -e "${BLUE}ℹ${NC} $1"; }
ok(){ echo -e "${GREEN}✓${NC} $1"; }
warn(){ echo -e "${YELLOW}⚠${NC} $1"; }
err(){ echo -e "${RED}✗${NC} $1"; }
need_cmd(){ command -v "$1" >/dev/null 2>&1 || { err "'$1' is required"; exit 1; }; }

run_root(){
  if [[ "$(id -u)" -eq 0 ]]; then bash -c "$*"
  elif command -v sudo >/dev/null 2>&1; then sudo bash -c "$*"
  else err "This action needs root privileges (sudo not found)."; exit 1; fi
}

install_deps_if_missing(){
  command -v git >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 && return 0
  if command -v apt-get >/dev/null 2>&1; then
    info "Installing git, curl..."
    run_root "apt-get update -q"
    run_root "apt-get install -y git curl -q"
    return 0
  fi
  err "git/curl missing and auto-install is only supported on apt-based systems."; exit 1
}

ensure_repo(){
  install_deps_if_missing; need_cmd git
  if [[ "$REPO_URL" == *"YOUR_USERNAME"* ]]; then
    err "REPO_URL هنوز تنظیم نشده. داخل bootstrap.sh آدرس ریپو خود را بگذارید یا REPO_URL=... را هنگام اجرا بدهید."
    exit 1
  fi
  if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    info "Cloning into $INSTALL_DIR ..."
    run_root "mkdir -p \"$(dirname "$INSTALL_DIR")\""
    run_root "git clone --branch \"$BRANCH\" \"$REPO_URL\" \"$INSTALL_DIR\""
  else
    info "Repository already exists: $INSTALL_DIR"
  fi
  run_root "cd \"$INSTALL_DIR\" && git fetch origin \"$BRANCH\" --prune"
}

write_manager_command(){
  local manager="/usr/local/bin/pik"
  info "Installing helper command: $manager"
  run_root "cat > '$manager' <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR=\"${INSTALL_DIR:-/opt/PIKExchangeBot}\"
if [[ -f \"\${INSTALL_DIR}/pik_menu.sh\" ]]; then
  exec bash \"\${INSTALL_DIR}/pik_menu.sh\" \"\$@\"
fi
echo \"pik_menu.sh not found in \${INSTALL_DIR}\" >&2
exit 1
EOS
chmod +x '$manager'"
  ok "Command installed: pik"
}

do_install(){
  ensure_repo
  [[ -f "$INSTALL_DIR/install.sh" ]] || { err "install.sh not found"; exit 1; }
  info "Running installer..."
  run_root "cd \"$INSTALL_DIR\" && FORCE_PROMPT=1 bash install.sh"
  write_manager_command
  ok "Install finished."
  echo ""; echo "Update later with: pik update"
}

do_update(){
  ensure_repo
  [[ -f "$INSTALL_DIR/update.sh" ]] || { err "update.sh not found"; exit 1; }
  info "Updating ..."
  run_root "cd \"$INSTALL_DIR\" && bash update.sh pull"
  ok "Update finished."
}

do_restart(){ need_cmd systemctl; run_root "systemctl restart '$SERVICE'"; ok "restarted"; }
do_status(){ need_cmd systemctl; run_root "systemctl --no-pager --full status '$SERVICE'"; }

do_configure(){
  ensure_repo
  info "Running configure-only..."
  run_root "cd \"$INSTALL_DIR\" && FORCE_PROMPT=1 bash install.sh --configure-only"
  write_manager_command
  ok "Configuration finished."
}

do_uninstall(){
  [[ -d "$INSTALL_DIR" ]] || { warn "not found: $INSTALL_DIR"; exit 0; }
  info "Uninstalling from $INSTALL_DIR ..."
  run_root "cd \"$INSTALL_DIR\" && bash uninstall.sh --force"
  ok "Uninstalled"
}

case "$CMD" in
  install) do_install ;;
  update) do_update ;;
  restart) do_restart ;;
  status) do_status ;;
  configure) do_configure ;;
  uninstall) do_uninstall ;;
  help|-h|--help)
    cat <<USAGE
PIK bootstrap commands:
  install    Clone (if needed) and run install.sh (default)
  update     Run update.sh pull
  restart    Restart systemd service ($SERVICE)
  status     Show systemd status
  configure  Configure .env only (token/admin/support)
  uninstall  Run uninstall.sh --force

Environment overrides: REPO_URL, BRANCH, INSTALL_DIR, SERVICE
USAGE
    ;;
  *) err "Unknown command: $CMD"; exit 2 ;;
esac

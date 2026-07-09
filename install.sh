#!/usr/bin/env bash
# ══════════════════════════════════════════════════════
# PIK Exchange Bot — Install & Setup (auto-run service)
# ══════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info(){ echo -e "${BLUE}ℹ${NC} $1"; }
ok(){ echo -e "${GREEN}✓${NC} $1"; }
warn(){ echo -e "${YELLOW}⚠${NC} $1"; }
err(){ echo -e "${RED}✗${NC} $1"; }

CONFIGURE_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --configure-only) CONFIGURE_ONLY=1 ;;
  esac
done

echo -e "\n${BLUE}══════════════════════════════════════${NC}"
echo -e "${BLUE} PIK Exchange Bot — Install${NC}"
echo -e "${BLUE}══════════════════════════════════════${NC}\n"

apt_run(){
  if command -v apt-get >/dev/null 2>&1; then
    if [[ "$(id -u)" -eq 0 ]]; then apt-get "$@"
    elif command -v sudo >/dev/null 2>&1; then sudo apt-get "$@"
    else return 1; fi
  else return 1; fi
}

install_venv_support(){
  if ! command -v apt-get >/dev/null 2>&1; then
    err "apt-get not available. Install python3-venv manually and retry."; return 1
  fi
  local py_mm
  py_mm="$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  info "Installing venv support for Python ${py_mm}..."
  apt_run update -q
  apt_run install -y python3-venv "python${py_mm}-venv" -q || apt_run install -y python3-venv -q
}

create_venv(){
  info "Creating virtual environment (.venv)..."
  if python3 -m venv .venv 2>/tmp/pik_venv_err.log; then return 0; fi
  if grep -qiE "ensurepip is not available|No module named ensurepip" /tmp/pik_venv_err.log; then
    warn "ensurepip missing; installing python3-venv..."
    install_venv_support || { cat /tmp/pik_venv_err.log; exit 1; }
    python3 -m venv .venv || { err "venv creation failed"; exit 1; }
    return 0
  fi
  cat /tmp/pik_venv_err.log || true
  err "venv creation failed."; exit 1
}

run_as_root(){
  if [[ "$(id -u)" -eq 0 ]]; then bash "$@"
  elif command -v sudo >/dev/null 2>&1; then sudo bash "$@"
  else err "This step needs root but sudo is not available."; exit 1; fi
}

upsert_env(){
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

ensure_env_template(){
  if [[ ! -f ".env.example" ]]; then
    warn ".env.example not found; creating a default template."
    cat > .env.example <<'ENVEOF'
BOT_TOKEN=
ADMIN_IDS=0
SUPPORT_USERNAME=
RATE_URL=https://alanchand.com/en
DB_PATH=pik.db
ENVEOF
  fi
  if [[ ! -f ".env" ]]; then cp .env.example .env; warn ".env created."; fi
}

configure_env_values(){
  ensure_env_template
  local current force_prompt
  force_prompt="${FORCE_PROMPT:-1}"

  prompt_value(){
    local key="$1" label="$2" required="${3:-1}" val
    current="$(grep -m1 "^${key}=" .env | cut -d= -f2- || true)"

    if [[ "$force_prompt" != "1" ]]; then
      if [[ "$key" == "BOT_TOKEN" && -n "$current" ]] || \
         [[ "$key" == "ADMIN_IDS" && -n "$current" && "$current" != "0" ]]; then
        return 0
      fi
    fi

    if [[ ! -t 0 ]]; then
      if [[ -z "$current" && "$required" == "1" ]]; then
        err "${key} is required but no interactive TTY is available."; exit 1
      fi
      return 0
    fi

    if [[ -n "$current" ]]; then read -r -p " ${label} [${current}]: " val
    else read -r -p " ${label}: " val; fi

    if [[ -z "$val" ]]; then
      if [[ -n "$current" ]]; then val="$current"
      elif [[ "$required" == "1" ]]; then err "${key} cannot be empty"; exit 1; fi
    fi
    upsert_env "$key" "$val"
  }

  prompt_value "BOT_TOKEN" "Telegram bot token" 1
  prompt_value "ADMIN_IDS" "Admin numeric ID(s), comma-separated (from @userinfobot)" 1
  prompt_value "SUPPORT_USERNAME" "Support username/link shown to users (e.g. @pik_support)" 0
  ok ".env configuration checked"
}

install_pik_command(){
  local target="/usr/local/bin/pik"
  local tmpf pik_dir
  tmpf="$(mktemp)"; pik_dir="$(pwd)"
  cat > "$tmpf" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
PIK_DIR="__PIK_DIR__"
if [[ -f "${PIK_DIR}/pik_menu.sh" ]]; then
  exec bash "${PIK_DIR}/pik_menu.sh" "$@"
fi
echo "pik_menu.sh not found in ${PIK_DIR}" >&2
exit 1
EOF
  sed -i "s|__PIK_DIR__|${pik_dir}|g" "$tmpf"
  if [[ "$(id -u)" -eq 0 ]]; then mv "$tmpf" "$target"; chmod +x "$target"
  elif command -v sudo >/dev/null 2>&1; then sudo mv "$tmpf" "$target"; sudo chmod +x "$target"
  else rm -f "$tmpf"; err "Could not install $target (need root/sudo)."; return 1; fi
  ok "Command installed: pik"
}

# ---- python/pip ----
if ! command -v python3 >/dev/null 2>&1; then
  info "Installing Python3..."
  apt_run update -q
  apt_run install -y python3 python3-venv python3-pip curl -q
fi
if ! python3 -m pip --version >/dev/null 2>&1; then
  info "Installing pip..."
  apt_run update -q && apt_run install -y python3-pip -q || python3 -m ensurepip --upgrade || true
fi
ok "Python $(python3 --version | awk '{print $2}')"

[[ ! -d ".venv" ]] && create_venv
PYTHON_BIN="$(pwd)/.venv/bin/python"
PIP_BIN="$(pwd)/.venv/bin/pip"

if [[ "$CONFIGURE_ONLY" -eq 1 ]]; then
  info "Configure-only mode"
  configure_env_values
  install_pik_command || true
  ok "Configuration completed"; exit 0
fi

info "Upgrading pip inside venv..."
"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel -q
info "Installing Python packages..."
"$PIP_BIN" install -r requirements.txt -q
ok "Packages installed"

configure_env_values

info "Installing & starting systemd service (pik-bot)..."
run_as_root ./setup_service.sh
ok "Service installed + started"

info "Installing command: pik"
install_pik_command || true

echo ""
ok "Installation completed!"
echo -e "${GREEN}══════════════════════════════════════${NC}"
echo -e " Service status: ${YELLOW}systemctl status pik-bot${NC}"
echo -e " Live logs:      ${YELLOW}journalctl -u pik-bot -f${NC}"
echo -e " Manager:        ${YELLOW}pik${NC}"
echo -e " Uninstall:      ${YELLOW}bash uninstall.sh${NC}"
echo -e "${GREEN}══════════════════════════════════════${NC}\n"

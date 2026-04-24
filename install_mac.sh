#!/bin/bash
# ============================================================================
# Hermes Mac Installer — installs everything on your Mac Mini
# ============================================================================
# Run as your normal user (NOT root). Sudo only when needed (Homebrew etc).
#
# Usage:
#   curl -fsSL https://<your-url>/install_mac.sh | bash
#   or: bash install_mac.sh
#
# What this installs:
#   - Homebrew (if missing)
#   - Python 3.12
#   - PostgreSQL 16
#   - Ollama (for local LLM optional)
#   - Tailscale (for Hetzner connection)
#   - Creates ~/hermes-mac/ with all Hermes code
#   - Sets up postgres database 'hermes' with schema
#   - Creates systemd/launchd service (runs on login)
# ============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[hermes-install]${NC} $*"; }
ok()  { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err() { echo -e "${RED}[error]${NC} $*" >&2; }

# ============================================================================
# Pre-flight checks
# ============================================================================
log "Checking environment..."

if [[ "$OSTYPE" != darwin* ]]; then
    err "This installer is for macOS only. Detected: $OSTYPE"
    exit 1
fi

if [[ $(uname -m) != "arm64" ]]; then
    warn "Not Apple Silicon. Works but slower for local LLM."
fi

HERMES_HOME="$HOME/hermes-mac"
log "Install location: $HERMES_HOME"

if [ -d "$HERMES_HOME" ]; then
    warn "$HERMES_HOME already exists. Existing files will be updated."
fi

# ============================================================================
# 1. Homebrew
# ============================================================================
if ! command -v brew &>/dev/null; then
    log "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add to PATH on Apple Silicon
    if [[ -f /opt/homebrew/bin/brew ]]; then
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    ok "Homebrew installed"
else
    ok "Homebrew already installed"
fi

# ============================================================================
# 2. System deps
# ============================================================================
log "Installing system dependencies..."

brew install python@3.12 postgresql@16 git uv || true
brew services start postgresql@16 2>/dev/null || true

# Optional: Ollama for local LLM (user can skip)
if ! command -v ollama &>/dev/null; then
    log "Installing Ollama (local LLM — optional, you can skip if using Hetzner)..."
    brew install --cask ollama || warn "Ollama install failed — OK if you're using Hetzner LLM"
fi

# Optional: Tailscale
if ! command -v tailscale &>/dev/null; then
    log "Installing Tailscale..."
    brew install --cask tailscale || warn "Tailscale not installed — install manually if needed"
fi

ok "System deps installed"

# ============================================================================
# 3. Clone / update hermes-mac repo
# ============================================================================
log "Setting up $HERMES_HOME..."

mkdir -p "$HERMES_HOME"
cd "$HERMES_HOME"

# In the real install, this would clone a git repo. For now, assume files
# are being copied here separately from hermes_prep/ on Hetzner.
mkdir -p "$HERMES_HOME/src"
mkdir -p "$HERMES_HOME/logs"
mkdir -p "$HERMES_HOME/data"
mkdir -p "$HERMES_HOME/skills"
mkdir -p "$HERMES_HOME/sessions"

ok "Directories created"

# ============================================================================
# 4. Python venv
# ============================================================================
log "Setting up Python virtual env..."

if [ ! -d "$HERMES_HOME/venv" ]; then
    python3.12 -m venv "$HERMES_HOME/venv"
fi
source "$HERMES_HOME/venv/bin/activate"

pip install --upgrade pip wheel setuptools >/dev/null

# Core deps
pip install \
    aiohttp \
    asyncpg \
    websockets \
    telethon \
    python-dotenv \
    fastapi \
    uvicorn \
    redis \
    slowapi \
    "mcp[cli]" \
    python-telegram-bot \
    || { err "pip install failed"; exit 1; }

ok "Python deps installed"

# ============================================================================
# 5. Postgres DB
# ============================================================================
log "Setting up Postgres database 'hermes'..."

psql postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='hermes'" 2>/dev/null | grep -q 1 || \
    createuser -s hermes 2>/dev/null || true
psql postgres -tAc "SELECT 1 FROM pg_database WHERE datname='hermes'" 2>/dev/null | grep -q 1 || \
    createdb -O hermes hermes

if [ -f "$HERMES_HOME/src/schema.sql" ]; then
    psql -U hermes -d hermes -f "$HERMES_HOME/src/schema.sql" >/dev/null 2>&1 || \
        warn "Schema apply had issues — check manually"
fi

ok "Postgres ready"

# ============================================================================
# 6. Ollama model (optional, user can skip)
# ============================================================================
if command -v ollama &>/dev/null; then
    log "Ollama detected. Pulling Hermes 14B (~9GB, optional — skip with Ctrl+C if using Hetzner)..."
    # ollama pull nous-hermes2:Q4_K_M &
    # Skip auto-pull; user can do manually: ollama pull nous-hermes2:Q4_K_M
    warn "SKIP AUTO-PULL. If you want local LLM, run: ollama pull nous-hermes2:Q4_K_M"
fi

# ============================================================================
# 7. Env template
# ============================================================================
if [ ! -f "$HERMES_HOME/.env" ]; then
    log "Creating .env from template..."
    if [ -f "$HERMES_HOME/src/.env.hermes.template" ]; then
        cp "$HERMES_HOME/src/.env.hermes.template" "$HERMES_HOME/.env"
        chmod 600 "$HERMES_HOME/.env"
        warn "Edit $HERMES_HOME/.env and fill in API keys BEFORE starting service"
    fi
fi

# ============================================================================
# 8. LaunchAgent (autostart on login)
# ============================================================================
log "Setting up launchd service..."

LAUNCHD_PLIST="$HOME/Library/LaunchAgents/com.dual.hermes.plist"

cat > "$LAUNCHD_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dual.hermes</string>
    <key>ProgramArguments</key>
    <array>
        <string>$HERMES_HOME/venv/bin/python</string>
        <string>$HERMES_HOME/src/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$HERMES_HOME</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HERMES_HOME</key>
        <string>$HERMES_HOME</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HERMES_HOME/logs/hermes.out</string>
    <key>StandardErrorPath</key>
    <string>$HERMES_HOME/logs/hermes.err</string>
</dict>
</plist>
EOF

ok "LaunchAgent created at $LAUNCHD_PLIST (not loaded yet — load with: launchctl load $LAUNCHD_PLIST)"

# ============================================================================
# Done
# ============================================================================
echo
echo "============================================"
ok "Hermes Mac installation complete!"
echo "============================================"
echo
echo "Next steps:"
echo "  1. Copy source files from Hetzner: hermes_prep/ → $HERMES_HOME/src/"
echo "  2. Edit $HERMES_HOME/.env with your API keys"
echo "  3. Setup Tailscale: tailscale up  (for Hetzner LLM)"
echo "  4. Test: cd $HERMES_HOME && source venv/bin/activate && python src/main.py"
echo "  5. Load service: launchctl load $LAUNCHD_PLIST"
echo
echo "Logs: tail -f $HERMES_HOME/logs/hermes.out"

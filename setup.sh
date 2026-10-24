#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [[ $EUID -eq 0 ]]; then
  echo "Tip: running a Discord bot as root is not recommended."
fi

read -r -s -p "Discord bot token: " DISCORD_TOKEN
echo
if [[ -z "$DISCORD_TOKEN" ]]; then echo "Token cannot be empty."; exit 1; fi

read -r -p "Main admin Discord user ID: " MAIN_ADMIN_ID
if [[ ! "$MAIN_ADMIN_ID" =~ ^[0-9]{15,22}$ ]]; then
  echo "That doesn't look like a Discord user ID (digits only)."
  exit 1
fi

read -r -p "Embed image URL (https://...; optional, press Enter to skip): " EMBED_IMAGE
if [[ -n "$EMBED_IMAGE" && ! "$EMBED_IMAGE" =~ ^https:// ]]; then
  echo "Image URL must start with https://"; exit 1
fi

read -r -p "Bot display name [VPS-Manager]: " BOT_NAME
BOT_NAME="${BOT_NAME:-VPS-Manager}"
read -r -p "Command prefix [!]: " PREFIX
PREFIX="${PREFIX:-!}"
read -r -p "Developer/footer name [Server Admin]: " BOT_DEVELOPER
BOT_DEVELOPER="${BOT_DEVELOPER:-Server Admin}"
read -r -p "Pinggy token (optional; leave blank for free mode): " PINGGY_TOKEN
read -r -p "Public server IP fallback [0.0.0.0]: " YOUR_SERVER_IP
YOUR_SERVER_IP="${YOUR_SERVER_IP:-0.0.0.0}"
read -r -p "LXD storage pool [default]: " DEFAULT_STORAGE_POOL
DEFAULT_STORAGE_POOL="${DEFAULT_STORAGE_POOL:-default}"

umask 077
cat > .env <<EOF
DISCORD_TOKEN=${DISCORD_TOKEN}
MAIN_ADMIN_ID=${MAIN_ADMIN_ID}
EMBED_IMAGE=${EMBED_IMAGE}
BOT_NAME=${BOT_NAME}
PREFIX=${PREFIX}
BOT_DEVELOPER=${BOT_DEVELOPER}
PINGGY_TOKEN=${PINGGY_TOKEN}
YOUR_SERVER_IP=${YOUR_SERVER_IP}
DEFAULT_STORAGE_POOL=${DEFAULT_STORAGE_POOL}
EOF
chmod 600 .env
unset DISCORD_TOKEN PINGGY_TOKEN

echo
echo "Installing system packages..."
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y python3 python3-venv python3-pip git
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo
echo "Setup complete."
echo "Start the bot with:"
echo "  cd \"$APP_DIR\" && .venv/bin/python bot.py"
echo
echo "To keep it running after SSH disconnects (quick option):"
echo "  cd \"$APP_DIR\" && nohup .venv/bin/python bot.py > bot-console.log 2>&1 &"
echo
echo "GitHub push commands are in README.md. Do NOT commit .env or database files."

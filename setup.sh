#!/bin/bash
# VPS Manager Bot Setup Script

set -e

echo "================================"
echo "VPS Manager Bot Setup"
echo "================================"
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "Creating .env file..."
    cp .env.example .env
    echo ""
    echo "⚠️  .env file created. Please edit it with your settings:"
    echo "   nano .env"
    echo ""
    echo "Required settings:"
    echo "  - DISCORD_TOKEN: Your Discord bot token"
    echo "  - BOT_DEVELOPER: Your name/handle"
    echo "  - MAIN_ADMIN_ID: Your Discord user ID"
    echo "  - COMPANY_NAME: Your hosting company name"
    echo "  - PINGGY_TOKEN: (Optional) Your Pinggy API token"
    echo ""
    read -p "Press Enter after editing .env..."
else
    echo "✓ .env file already exists"
fi

echo ""
echo "================================"
echo "Setting up Python environment..."
echo "================================"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed"
    echo "Install with: apt-get install -y python3 python3-pip"
    exit 1
fi

echo "✓ Python $(python3 --version | cut -d' ' -f2) found"

# Create venv
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

echo ""
echo "================================"
echo "Installing dependencies..."
echo "================================"

.venv/bin/pip install --upgrade pip setuptools wheel -q
.venv/bin/pip install -r requirements.txt

echo "✓ Dependencies installed"

echo ""
echo "================================"
echo "Checking LXC/LXD..."
echo "================================"

if ! command -v lxc &> /dev/null; then
    echo "❌ LXC is not installed"
    echo "Install with: snap install lxd"
    echo "Then run: lxd init"
    exit 1
fi

echo "✓ LXC found: $(lxc --version)"

# Check if LXD is initialized
if ! lxc list &> /dev/null; then
    echo "❌ LXD is not initialized"
    echo "Run: sudo lxd init"
    exit 1
fi

echo "✓ LXD initialized"

echo ""
echo "================================"
echo "Setup complete!"
echo "================================"
echo ""
echo "To start the bot, use:"
echo ""
echo "  cd \"/root/Initial-VPS-manager-bot\" && .venv/bin/python bot.py"
echo ""
echo "Or for background (systemd):"
echo "  sudo systemctl start vps-bot"
echo ""

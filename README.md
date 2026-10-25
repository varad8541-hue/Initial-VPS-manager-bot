# VPS Manager Discord Bot

Professional Discord bot for managing LXC-based VPS provisioning with Pinggy SSH tunnels.

## Features

- **VPS Deployment** – Create Ubuntu/Debian containers with custom resources
- **OS Selection** – Choose from Ubuntu 20.04/22.04/24.04 and Debian 11/12
- **SSH Tunnels** – Automatic Pinggy SSH tunnel creation (free or paid)
- **Container Management** – Start, stop, reinstall, change passwords
- **Professional UI** – Clean embeds with company branding
- **Private Passwords** – Sent via DM, never shown in public chat
- **Admin Controls** – User management and VPS oversight

## Requirements

- Python 3.8+
- LXD/LXC installed and initialized
- Discord bot token
- (Optional) Pinggy API token for paid tunnels

## Quick Start

### 1. Clone Repository
```bash
git clone https://github.com/varad8541-hue/Initial-VPS-manager-bot.git
cd Initial-VPS-manager-bot
```

### 2. Run Setup
```bash
bash setup.sh
```

This will:
- Create `.env` file (edit with your settings)
- Create Python virtual environment
- Install dependencies
- Check LXC/LXD

### 3. Configure `.env`
Edit `.env` with your settings:
```bash
nano .env
```

Required:
- `DISCORD_TOKEN` – Your Discord bot token
- `MAIN_ADMIN_ID` – Your Discord user ID (for admin access)
- `BOT_DEVELOPER` – Your name/handle
- `COMPANY_NAME` – Your hosting company name

Optional:
- `COMPANY_ICON` – URL to company logo
- `PINGGY_TOKEN` – Pinggy API token for paid tunnels

### 4. Initialize LXD
If not already done:
```bash
sudo lxd init
```

### 5. Start Bot
```bash
cd "/root/Initial-VPS-manager-bot" && .venv/bin/python bot.py
```

## Commands

### User Commands
- `!ping` – Check bot status
- `!myvps` – List your VPS
- `!manage` – Manage your VPS (Start/Stop/SSH/Password/Stats)

### Admin Commands
- `!create <ram> <cpu> <disk> @user` – Deploy new VPS
- `!start <container>` – Start a container
- `!stop <container>` – Stop a container
- `!delete <container>` – Delete a container
- `!list-all` – Show all VPS
- `!admin-add @user` – Add admin user

### Main Admin Commands
- `!admin-add @user` – Promote user to admin

## Systemd Service (Optional)

Create `/etc/systemd/system/vps-bot.service`:
```ini
[Unit]
Description=VPS Manager Discord Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/Initial-VPS-manager-bot
ExecStart=/root/Initial-VPS-manager-bot/.venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable vps-bot
sudo systemctl start vps-bot
sudo systemctl status vps-bot
```

View logs:
```bash
sudo journalctl -u vps-bot -f
```

## Troubleshooting

### Bot won't start
```bash
cd "/root/Initial-VPS-manager-bot" && .venv/bin/python bot.py
```

### LXC command errors
```bash
lxc list  # Check if LXD is responsive
lxd init  # Initialize LXD if needed
```

### Tunnel not working
- Check `PINGGY_TOKEN` in `.env`
- Leave blank for free Pinggy QR tunnels
- Ensure SSH is running in container

### Permission denied
```bash
sudo usermod -aG lxd $USER
newgrp lxd
```

## Support

For issues, check `bot.log` for detailed error messages.

## License

MIT

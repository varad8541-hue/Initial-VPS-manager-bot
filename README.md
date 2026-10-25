# VPS Manager Discord Bot

A Discord bot for managing LXC-based VPS provisioning with Pinggy SSH tunnels.

## Features

- **VPS Creation**: Deploy Ubuntu/Debian containers with custom RAM/CPU/disk
- **SSH Tunnels**: Auto-setup Pinggy tunnels for remote access
- **Container Management**: Start, stop, reinstall, change passwords
- **Port Forwarding**: Expose VPS ports to the host
- **Admin Controls**: User management, suspension, resource quotas
- **tmate Integration**: One-click shared terminal sessions

## Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/YOUR_USERNAME/vps-manager-bot.git
   cd vps-manager-bot
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure `.env`:
   ```bash
   cp .env.example .env
   # Edit .env with your Discord token, Pinggy token, etc.
   ```

4. Run:
   ```bash
   python bot.py
   ```

## Requirements

- Python 3.8+
- LXD/LXC installed on the host
- Discord.py
- Pinggy token (optional, free tier available)

## Commands

### User
- `!myvps` – List your VPS
- `!manage [user]` – Control VPS (start/stop/SSH/reinstall)
- `!pinggy [n]` – Create SSH tunnel
- `!ports` – Manage port forwards

### Admin
- `!create <ram> <cpu> <disk> @user` – Deploy new VPS
- `!delete-vps @user <n> [reason]`
- `!suspend-vps <container>`
- `!unsuspend-vps <container>`

### Main Admin
- `!admin-add @user`
- `!admin-remove @user`

## License

MIT

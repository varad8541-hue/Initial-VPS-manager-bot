# VPS Manager Discord Bot

## Important safety notes
- This bot can create, stop, reinstall, and delete LXC containers. Reinstall/delete actions erase data.
- Run it only on a server you administer and only with a Discord bot you control.
- Use a dedicated, unprivileged Linux account where possible; grant only the LXD permissions the bot actually needs.
- Never commit `.env`, `vps.db`, logs, or bot tokens to GitHub. If a token was exposed, reset it in the Discord Developer Portal.
- The existing remote-node agent endpoint (`/api/execute`) can run commands on remote nodes. Only configure trusted agents protected by authentication and HTTPS.

## Setup on Ubuntu/Debian

```bash
chmod +x setup.sh
./setup.sh
```

The setup script interactively asks for the Discord token, main admin ID, embed image URL, bot name, prefix, developer footer, optional Pinggy token, public IP fallback, and LXD storage pool. It creates a private `.env`, virtual environment, and installs Python dependencies.

Enable **Message Content Intent** (and Server Members Intent if you use member-dependent commands) in the Discord Developer Portal.

Start it:

```bash
.venv/bin/python bot.py
```

Quick background run:

```bash
nohup .venv/bin/python bot.py > bot-console.log 2>&1 &
```

Check logs:

```bash
tail -f bot-console.log
```

## GitHub push commands

Create an empty repository on GitHub first, then from this project directory:

```bash
git init
git add bot.py setup.sh requirements.txt README.md .gitignore .env.example
git commit -m "Initial VPS manager bot"
git branch -M main
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

Replace the remote URL with your repository URL. Do not use `git add .` unless you have verified `.gitignore` is working and `.env` is not staged.

## Commands
Default prefix is `!`. Use `!help` in your Discord server for the full command list.

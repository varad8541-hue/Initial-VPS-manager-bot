"""
VPS Manager Discord Bot
Clean rewrite – LXC based VPS provisioning with Pinggy SSH tunnels
"""

import discord
from discord.ext import commands
import asyncio
import subprocess
import json
import os
import sqlite3
import logging
import random
import secrets
import string
import re
import time
import shutil
from datetime import datetime
from typing import Optional, List, Dict, Any
import requests

from dotenv import load_dotenv

load_dotenv()

# ───────────────────────────── CONFIG ─────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN", "")
BOT_NAME        = os.getenv("BOT_NAME", "VPS-Manager")
PREFIX          = os.getenv("PREFIX", "!")
BOT_VERSION     = "2.0"
BOT_DEVELOPER   = os.getenv("BOT_DEVELOPER", "Unknown")

# Main admin Discord IDs (comma separated)
MAIN_ADMIN_IDS  = [uid.strip() for uid in os.getenv("MAIN_ADMIN_ID", "").split(",") if uid.strip()]

PINGGY_TOKEN    = os.getenv("PINGGY_TOKEN", "")          # optional paid token
YOUR_SERVER_IP  = os.getenv("YOUR_SERVER_IP", "0.0.0.0")
DEFAULT_POOL    = os.getenv("DEFAULT_STORAGE_POOL", "default")

# Image used in every embed
EMBED_IMAGE = os.getenv("EMBED_IMAGE", "")

OS_OPTIONS = [
    {"label": "Ubuntu 20.04 LTS", "value": "ubuntu:20.04"},
    {"label": "Ubuntu 22.04 LTS", "value": "ubuntu:22.04"},
    {"label": "Ubuntu 24.04 LTS", "value": "ubuntu:24.04"},
    {"label": "Debian 10 (Buster)", "value": "images:debian/10"},
    {"label": "Debian 11 (Bullseye)", "value": "images:debian/11"},
    {"label": "Debian 12 (Bookworm)", "value": "images:debian/12"},
    {"label": "Debian 13 (Trixie)", "value": "images:debian/13"},
]

# ───────────────────────────── LOGGING ─────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
log = logging.getLogger("vps_bot")

# ───────────────────────────── DATABASE ─────────────────────────────
def get_db():
    conn = sqlite3.connect("vps.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS admins (
        user_id TEXT PRIMARY KEY
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS main_admins (
        user_id TEXT PRIMARY KEY
    )""")
    for uid in MAIN_ADMIN_IDS:
        c.execute("INSERT OR IGNORE INTO main_admins (user_id) VALUES (?)", (uid,))

    c.execute("""CREATE TABLE IF NOT EXISTS nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        location TEXT,
        total_vps INTEGER DEFAULT 50,
        api_key TEXT,
        url TEXT,
        is_local INTEGER DEFAULT 0
    )""")
    c.execute("SELECT COUNT(*) FROM nodes WHERE is_local=1")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO nodes (name, location, total_vps, is_local) VALUES (?,?,?,1)",
                  ("Local Node", "Local", 100))

    c.execute("""CREATE TABLE IF NOT EXISTS vps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        node_id INTEGER NOT NULL DEFAULT 1,
        container_name TEXT UNIQUE NOT NULL,
        ram TEXT NOT NULL,
        cpu TEXT NOT NULL,
        storage TEXT NOT NULL,
        config TEXT,
        os_version TEXT DEFAULT 'ubuntu:22.04',
        status TEXT DEFAULT 'stopped',
        suspended INTEGER DEFAULT 0,
        root_password TEXT DEFAULT '',
        pinggy_address TEXT DEFAULT '',
        created_at TEXT,
        shared_with TEXT DEFAULT '[]'
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS port_forwards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        container TEXT NOT NULL,
        vps_port INTEGER NOT NULL,
        host_port INTEGER NOT NULL,
        created_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS port_quota (
        user_id TEXT PRIMARY KEY,
        slots INTEGER DEFAULT 0
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('cpu_threshold','90')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ram_threshold','90')")

    conn.commit()
    conn.close()

init_db()

# ───────────────────────────── HELPERS ─────────────────────────────
def generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

def get_public_ip() -> str:
    try:
        r = requests.get("https://ifconfig.me/ip", timeout=5)
        return r.text.strip() or YOUR_SERVER_IP
    except Exception:
        return YOUR_SERVER_IP

def is_main_admin(user_id: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM main_admins WHERE user_id=?", (user_id,))
    ok = c.fetchone() is not None
    conn.close()
    return ok

def is_admin(user_id: str) -> bool:
    if is_main_admin(user_id):
        return True
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
    ok = c.fetchone() is not None
    conn.close()
    return ok

def get_nodes() -> List[Dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM nodes").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_node(node_id: int) -> Optional[Dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_vps(user_id: str) -> List[Dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM vps WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["shared_with"] = json.loads(d.get("shared_with") or "[]")
        d["suspended"] = bool(d["suspended"])
        result.append(d)
    return result

def save_vps(vps: Dict):
    conn = get_db()
    c = conn.cursor()
    shared = json.dumps(vps.get("shared_with", []))
    if vps.get("id"):
        c.execute("""UPDATE vps SET
            node_id=?, ram=?, cpu=?, storage=?, config=?, os_version=?,
            status=?, suspended=?, root_password=?, pinggy_address=?, shared_with=?
            WHERE id=?""",
            (vps["node_id"], vps["ram"], vps["cpu"], vps["storage"], vps.get("config"),
             vps.get("os_version"), vps["status"], int(vps.get("suspended", False)),
             vps.get("root_password", ""), vps.get("pinggy_address", ""),
             shared, vps["id"]))
    else:
        c.execute("""INSERT INTO vps
            (user_id, node_id, container_name, ram, cpu, storage, config, os_version,
             status, suspended, root_password, pinggy_address, created_at, shared_with)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vps["user_id"], vps["node_id"], vps["container_name"], vps["ram"],
             vps["cpu"], vps["storage"], vps.get("config"), vps.get("os_version"),
             vps["status"], int(vps.get("suspended", False)),
             vps.get("root_password", ""), vps.get("pinggy_address", ""),
             vps.get("created_at", datetime.now().isoformat()), shared))
        vps["id"] = c.lastrowid
    conn.commit()
    conn.close()

def delete_vps_db(container: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM vps WHERE container_name=?", (container,))
    c.execute("DELETE FROM port_forwards WHERE container=?", (container,))
    conn.commit()
    conn.close()

def find_node_for_container(container: str) -> int:
    conn = get_db()
    row = conn.execute("SELECT node_id FROM vps WHERE container_name=?", (container,)).fetchone()
    conn.close()
    return row["node_id"] if row else 1

# ───────────────────────────── LXC CORE ─────────────────────────────
async def run_lxc(cmd: str, node_id: int = 1, timeout: int = 120) -> str:
    """Execute an lxc command on the given node."""
    node = get_node(node_id)
    if not node:
        raise Exception(f"Node {node_id} not found")

    full = f"lxc {cmd}"

    if node["is_local"]:
        proc = await asyncio.create_subprocess_exec(
            *full.split(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise Exception(f"Command timed out: {full}")

        if proc.returncode != 0:
            err = stderr.decode().strip() or "unknown error"
            raise Exception(f"LXC failed: {err}")
        return stdout.decode().strip()
    else:
        # Remote node via agent API
        url = f"{node['url']}/api/execute"
        r = requests.post(url, json={"command": full},
                          params={"api_key": node["api_key"]}, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if data.get("returncode", 1) != 0:
            raise Exception(f"Remote LXC failed: {data.get('stderr', 'unknown')}")
        return data.get("stdout", "")

async def safe_start(container: str, node_id: int):
    try:
        await run_lxc(f"start {container}", node_id)
    except Exception as e:
        if "already running" not in str(e).lower():
            raise

async def apply_lxc_config(container: str, node_id: int):
    """Make container Docker-friendly / privileged."""
    configs = [
        f"config set {container} security.nesting true",
        f"config set {container} security.privileged true",
        f"config set {container} security.syscalls.intercept.mknod true",
        f"config set {container} security.syscalls.intercept.setxattr true",
        f"config set {container} linux.kernel_modules overlay,loop,nf_nat,ip_tables",
    ]
    for c in configs:
        try:
            await run_lxc(c, node_id)
        except Exception:
            pass
    try:
        await run_lxc(f"config device add {container} fuse unix-char path=/dev/fuse", node_id)
    except Exception:
        pass

async def setup_ssh(container: str, node_id: int) -> str:
    """Install openssh, set random root password, enable root login."""
    password = generate_password()
    cmds = [
        "DEBIAN_FRONTEND=noninteractive apt-get update -y",
        "DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server",
        f"echo 'root:{password}' | chpasswd",
        "sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config",
        "sed -i 's/#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config",
        "mkdir -p /run/sshd",
        "systemctl enable ssh || systemctl enable sshd || true",
        "systemctl restart ssh || systemctl restart sshd || service ssh restart || true",
    ]
    for cmd in cmds:
        try:
            await run_lxc(f"exec {container} -- bash -c \"{cmd}\"", node_id, timeout=180)
        except Exception as e:
            log.warning(f"SSH setup step failed: {e}")
    return password

# ───────────────────────────── PINGGY ─────────────────────────────
PINGGY_LOG = "/root/.pinggy.log"
ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

def parse_pinggy(text: str) -> Optional[str]:
    if not text:
        return None
    clean = ANSI_RE.sub("", text)
    m = re.search(r"tcp://([a-zA-Z0-9.\-]+):(\d+)", clean, re.I)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    return None

async def establish_pinggy(container: str, node_id: int) -> Optional[str]:
    # Make sure sshd is up
    try:
        await run_lxc(
            f"exec {container} -- bash -c \"service ssh start || service sshd start || /usr/sbin/sshd || true\"",
            node_id
        )
    except Exception:
        pass

    # Kill old tunnel
    try:
        await run_lxc(
            f"exec {container} -- bash -c \"pkill -9 -f pinggy.io; rm -f {PINGGY_LOG}\"",
            node_id
        )
    except Exception:
        pass

    user = f"{PINGGY_TOKEN}+tcp" if PINGGY_TOKEN else "qr+tcp"
    host = "a.pinggy.io" if PINGGY_TOKEN else "free.pinggy.io"

    tunnel = (
        f"setsid nohup ssh -p 443 -T "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ServerAliveInterval=15 -o ExitOnForwardFailure=yes "
        f"-R0:localhost:22 {user}@{host} "
        f"> {PINGGY_LOG} 2>&1 < /dev/null & disown"
    )
    try:
        await run_lxc(f"exec {container} -- bash -c \"{tunnel}\"", node_id)
    except Exception as e:
        log.error(f"Pinggy start failed: {e}")
        return None

    for _ in range(6):
        await asyncio.sleep(4)
        try:
            log_out = await run_lxc(f"exec {container} -- cat {PINGGY_LOG}", node_id)
            addr = parse_pinggy(log_out if isinstance(log_out, str) else "")
            if addr:
                log.info(f"Pinggy ready: {addr}")
                return addr
        except Exception:
            pass
    return None

# ───────────────────────────── EMBEDS ─────────────────────────────
def make_embed(title: str, description: str = "", color: int = 0x1a1a1a) -> discord.Embed:
    emb = discord.Embed(
        title=f"✦ {BOT_NAME} · {title}",
        description=description[:4090],
        color=color,
        timestamp=datetime.utcnow()
    )
    emb.set_thumbnail(url=EMBED_IMAGE)
    emb.set_footer(
        text=f"{BOT_NAME} v{BOT_VERSION} · {BOT_DEVELOPER}",
        icon_url=EMBED_IMAGE
    )
    return emb

def ok(title, desc=""):   return make_embed(title, desc, 0x00ff88)
def err(title, desc=""):  return make_embed(title, desc, 0xff3366)
def info(title, desc=""): return make_embed(title, desc, 0x00ccff)
def warn(title, desc=""): return make_embed(title, desc, 0xffaa00)

# ───────────────────────────── BOT SETUP ─────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

def admin_only():
    async def pred(ctx):
        if is_admin(str(ctx.author.id)):
            return True
        raise commands.CheckFailure("Admin only.")
    return commands.check(pred)

def main_admin_only():
    async def pred(ctx):
        if is_main_admin(str(ctx.author.id)):
            return True
        raise commands.CheckFailure("Main admin only.")
    return commands.check(pred)

# ───────────────────────────── EVENTS ─────────────────────────────
@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} ({bot.user.id})")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name=f"{BOT_NAME} VPS"
    ))

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        await ctx.send(embed=err("Access Denied", str(error)))
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=err("Missing Argument", f"Check `{PREFIX}help`"))
    else:
        log.error(f"Error: {error}")
        await ctx.send(embed=err("Error", str(error)[:500]))

# ───────────────────────────── USER COMMANDS ─────────────────────────────
@bot.command(name="ping")
async def cmd_ping(ctx):
    await ctx.send(embed=ok("Pong", f"Latency: `{round(bot.latency*1000)}ms`"))

@bot.command(name="myvps")
async def cmd_myvps(ctx):
    vps_list = get_user_vps(str(ctx.author.id))
    if not vps_list:
        await ctx.send(embed=err("No VPS", f"You have no VPS yet. Contact an admin."))
        return

    emb = info("Your VPS", f"You own **{len(vps_list)}** VPS")
    for i, v in enumerate(vps_list, 1):
        status = "⛔ SUSPENDED" if v["suspended"] else ("🟢 RUNNING" if v["status"] == "running" else "🔴 STOPPED")
        node = get_node(v["node_id"])
        emb.add_field(
            name=f"#{i} · `{v['container_name']}`",
            value=f"{status}\n`{v['config']}`\nNode: `{node['name'] if node else '?'}`",
            inline=False
        )
    emb.add_field(name="Quick", value=f"`{PREFIX}manage` · `{PREFIX}pinggy`", inline=False)
    await ctx.send(embed=emb)

@bot.command(name="pinggy")
async def cmd_pinggy(ctx, number: int = 1):
    vps_list = get_user_vps(str(ctx.author.id))
    if not vps_list or number < 1 or number > len(vps_list):
        await ctx.send(embed=err("Invalid", f"Use `{PREFIX}myvps` to see your list."))
        return

    v = vps_list[number - 1]
    if v["suspended"]:
        await ctx.send(embed=err("Suspended", "This VPS is suspended."))
        return

    msg = await ctx.send(embed=info("Tunnel", "Creating Pinggy SSH tunnel… (~15s)"))
    addr = await establish_pinggy(v["container_name"], v["node_id"])
    if not addr:
        await msg.edit(embed=err("Failed", "Could not create tunnel. Try again later."))
        return

    v["pinggy_address"] = addr
    save_vps(v)
    host, port = addr.split(":")
    pw = v.get("root_password") or "use !manage → Change Password"

    dm = ok("SSH Tunnel Ready")
    dm.add_field(name="Login", value=(
        f"**Host:** `{host}`\n**Port:** `{port}`\n"
        f"**User:** `root`\n**Password:** `{pw}`\n\n"
        f"```ssh root@{host} -p {port}```"
    ), inline=False)
    try:
        await ctx.author.send(embed=dm)
        await msg.edit(embed=ok("Done", "Check your DMs for the SSH command."))
    except discord.Forbidden:
        await msg.edit(embed=dm)

# ───────────────────────────── MANAGE VIEW ─────────────────────────────
class ManageView(discord.ui.View):
    def __init__(self, owner_id: str, vps_list: List[Dict], admin: bool = False):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.vps_list = vps_list
        self.admin = admin
        self.idx = 0 if len(vps_list) == 1 else None

        if len(vps_list) > 1:
            opts = [
                discord.SelectOption(
                    label=f"VPS #{i+1} · {v['container_name']}",
                    description=v.get("config", "")[:50],
                    value=str(i)
                ) for i, v in enumerate(vps_list)
            ]
            sel = discord.ui.Select(placeholder="Select a VPS", options=opts)
            sel.callback = self._select
            self.add_item(sel)
        else:
            self._add_buttons()

    def _add_buttons(self):
        self.clear_items()
        buttons = [
            ("▶ Start",    discord.ButtonStyle.success,   "start"),
            ("⏸ Stop",     discord.ButtonStyle.secondary, "stop"),
            ("🔑 SSH",      discord.ButtonStyle.primary,   "ssh"),
            ("🔌 Tunnel",   discord.ButtonStyle.primary,   "tunnel"),
            ("🔐 Pass",     discord.ButtonStyle.danger,    "pass"),
            ("📊 Stats",    discord.ButtonStyle.secondary, "stats"),
            ("🔄 Reinstall", discord.ButtonStyle.danger,   "reinstall"),
        ]
        for label, style, act in buttons:
            btn = discord.ui.Button(label=label, style=style)
            btn.callback = lambda i, a=act: self._action(i, a)
            self.add_item(btn)

    async def _select(self, inter: discord.Interaction):
        if str(inter.user.id) != self.owner_id and not self.admin:
            await inter.response.send_message(embed=err("Not yours"), ephemeral=True)
            return
        self.idx = int(inter.data["values"][0])
        self._add_buttons()
        emb = await self._build_embed()
        await inter.response.edit_message(embed=emb, view=self)

    async def _build_embed(self) -> discord.Embed:
        v = self.vps_list[self.idx]
        status = "⛔ SUSPENDED" if v["suspended"] else v["status"].upper()
        color = 0x00ff88 if v["status"] == "running" and not v["suspended"] else 0xff3366
        emb = make_embed(f"Manage · {v['container_name']}", color=color)
        emb.add_field(name="Status", value=f"`{status}`", inline=True)
        emb.add_field(name="Resources", value=v.get("config", "—"), inline=True)
        emb.add_field(name="OS", value=v.get("os_version", "—"), inline=True)
        if v.get("pinggy_address"):
            h, p = v["pinggy_address"].split(":")
            emb.add_field(name="SSH Tunnel", value=f"```ssh root@{h} -p {p}```", inline=False)
        return emb

    async def _action(self, inter: discord.Interaction, action: str):
        if str(inter.user.id) != self.owner_id and not self.admin:
            await inter.response.send_message(embed=err("Not yours"), ephemeral=True)
            return
        if self.idx is None:
            await inter.response.send_message(embed=err("Select a VPS first"), ephemeral=True)
            return

        v = self.vps_list[self.idx]
        container = v["container_name"]
        node_id = v["node_id"]

        if v["suspended"] and action not in ("stats",):
            await inter.response.send_message(embed=err("Suspended"), ephemeral=True)
            return

        await inter.response.defer(ephemeral=True)

        try:
            if action == "start":
                await safe_start(container, node_id)
                addr = await establish_pinggy(container, node_id)
                v["status"] = "running"
                if addr:
                    v["pinggy_address"] = addr
                save_vps(v)
                await inter.followup.send(embed=ok("Started", f"`{container}` is running."), ephemeral=True)

            elif action == "stop":
                await run_lxc(f"stop {container}", node_id)
                v["status"] = "stopped"
                v["pinggy_address"] = ""
                save_vps(v)
                await inter.followup.send(embed=ok("Stopped", f"`{container}` stopped."), ephemeral=True)

            elif action == "ssh":
                # tmate fallback
                try:
                    await run_lxc(f"exec {container} -- which tmate", node_id)
                except Exception:
                    await run_lxc(f"exec {container} -- apt-get update -y", node_id)
                    await run_lxc(f"exec {container} -- apt-get install -y tmate", node_id)
                sock = f"/tmp/tmate-{secrets.token_hex(4)}.sock"
                await run_lxc(f"exec {container} -- tmate -S {sock} new-session -d", node_id)
                await asyncio.sleep(3)
                out = await run_lxc(f"exec {container} -- tmate -S {sock} display -p '#{{tmate_ssh}}'", node_id)
                if out:
                    try:
                        await inter.user.send(embed=ok("tmate SSH", f"```{out}```"))
                        await inter.followup.send(embed=ok("Sent", "Check DMs."), ephemeral=True)
                    except discord.Forbidden:
                        await inter.followup.send(embed=ok("tmate", f"```{out}```"), ephemeral=True)
                else:
                    await inter.followup.send(embed=err("tmate failed"), ephemeral=True)

            elif action == "tunnel":
                addr = await establish_pinggy(container, node_id)
                if addr:
                    v["pinggy_address"] = addr
                    save_vps(v)
                    h, p = addr.split(":")
                    pw = v.get("root_password", "?")
                    dm = ok("Tunnel Ready")
                    dm.add_field(name="SSH", value=f"```ssh root@{h} -p {p}```\nPassword: `{pw}`", inline=False)
                    try:
                        await inter.user.send(embed=dm)
                        await inter.followup.send(embed=ok("Done", "Check DMs."), ephemeral=True)
                    except discord.Forbidden:
                        await inter.followup.send(embed=dm, ephemeral=True)
                else:
                    await inter.followup.send(embed=err("Tunnel failed"), ephemeral=True)

            elif action == "pass":
                new_pw = generate_password()
                await run_lxc(f"exec {container} -- bash -c \"echo 'root:{new_pw}' | chpasswd\"", node_id)
                v["root_password"] = new_pw
                save_vps(v)
                try:
                    await inter.user.send(embed=ok("New Password", f"`{new_pw}`"))
                    await inter.followup.send(embed=ok("Changed", "Check DMs."), ephemeral=True)
                except discord.Forbidden:
                    await inter.followup.send(embed=ok("New Password", f"`{new_pw}`"), ephemeral=True)

            elif action == "stats":
                # simple status
                try:
                    out = await run_lxc(f"info {container}", node_id)
                    status_line = next((l for l in out.splitlines() if l.startswith("Status:")), "Status: unknown")
                except Exception:
                    status_line = "Status: unknown"
                await inter.followup.send(embed=info("Stats", f"`{container}`\n{status_line}"), ephemeral=True)

            elif action == "reinstall":
                if self.admin:
                    await inter.followup.send(embed=err("Only the owner can reinstall"), ephemeral=True)
                    return
                # Confirm + OS select
                class ReinstallConfirm(discord.ui.View):
                    def __init__(self, parent, vps):
                        super().__init__(timeout=60)
                        self.parent = parent
                        self.vps = vps
                    @discord.ui.button(label="Yes, wipe & reinstall", style=discord.ButtonStyle.danger)
                    async def yes(self, i: discord.Interaction, b: discord.ui.Button):
                        await i.response.edit_message(embed=info("Choose OS"), view=ReinstallOS(self.parent, self.vps))
                    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                    async def no(self, i: discord.Interaction, b: discord.ui.Button):
                        await i.response.edit_message(embed=info("Cancelled"), view=None)

                class ReinstallOS(discord.ui.View):
                    def __init__(self, parent, vps):
                        super().__init__(timeout=120)
                        self.parent = parent
                        self.vps = vps
                        opts = [discord.SelectOption(label=o["label"], value=o["value"]) for o in OS_OPTIONS]
                        sel = discord.ui.Select(placeholder="Select new OS", options=opts)
                        sel.callback = self.do_reinstall
                        self.add_item(sel)
                    async def do_reinstall(self, i: discord.Interaction):
                        os_ver = i.data["values"][0]
                        await i.response.edit_message(embed=info("Reinstalling…", f"Wiping and installing `{os_ver}`…"), view=None)
                        name = self.vps["container_name"]
                        nid = self.vps["node_id"]
                        ram_gb = int(self.vps["ram"].replace("GB", ""))
                        cpu = int(self.vps["cpu"])
                        disk_gb = int(self.vps["storage"].replace("GB", ""))
                        try:
                            await run_lxc(f"delete {name} --force", nid)
                            await run_lxc(f"init {os_ver} {name} -s {DEFAULT_POOL}", nid, timeout=180)
                            await run_lxc(f"config set {name} limits.memory {ram_gb*1024}MB", nid)
                            await run_lxc(f"config set {name} limits.cpu {cpu}", nid)
                            await run_lxc(f"config device set {name} root size={disk_gb}GB", nid)
                            await apply_lxc_config(name, nid)
                            await safe_start(name, nid)
                            await asyncio.sleep(8)
                            pw = await setup_ssh(name, nid)
                            addr = await establish_pinggy(name, nid)
                            self.vps["os_version"] = os_ver
                            self.vps["status"] = "running"
                            self.vps["suspended"] = False
                            self.vps["root_password"] = pw
                            self.vps["pinggy_address"] = addr or ""
                            self.vps["created_at"] = datetime.now().isoformat()
                            save_vps(self.vps)
                            emb = ok("Reinstall complete", f"`{name}` is now running `{os_ver}`")
                            if addr:
                                h, p = addr.split(":")
                                emb.add_field(name="SSH", value=f"```ssh root@{h} -p {p}```\nPassword: `{pw}`", inline=False)
                            await i.followup.send(embed=emb, ephemeral=True)
                            try:
                                await i.user.send(embed=emb)
                            except Exception:
                                pass
                        except Exception as e:
                            await i.followup.send(embed=err("Reinstall failed", str(e)[:400]), ephemeral=True)

                await inter.followup.send(
                    embed=warn("Reinstall?", "This **erases all data** on the VPS. Continue?"),
                    view=ReinstallConfirm(self, v),
                    ephemeral=True
                )
                return  # don't refresh yet

        except Exception as e:
            await inter.followup.send(embed=err("Failed", str(e)[:400]), ephemeral=True)

        # refresh embed
        try:
            new_emb = await self._build_embed()
            await inter.message.edit(embed=new_emb, view=self)
        except Exception:
            pass

@bot.command(name="manage")
async def cmd_manage(ctx, user: discord.Member = None):
    if user:
        if not is_admin(str(ctx.author.id)):
            await ctx.send(embed=err("Admin only"))
            return
        uid = str(user.id)
        vps_list = get_user_vps(uid)
        if not vps_list:
            await ctx.send(embed=err("No VPS", f"{user.mention} has no VPS."))
            return
        view = ManageView(str(ctx.author.id), vps_list, admin=True)
    else:
        uid = str(ctx.author.id)
        vps_list = get_user_vps(uid)
        if not vps_list:
            await ctx.send(embed=err("No VPS", "You have no VPS."))
            return
        view = ManageView(uid, vps_list)

    if len(vps_list) == 1:
        emb = await view._build_embed()
    else:
        emb = info("Select VPS", "Choose a VPS from the dropdown.")
    await ctx.send(embed=emb, view=view)

# ───────────────────────────── ADMIN: CREATE ─────────────────────────────
class OSSelect(discord.ui.View):
    def __init__(self, ram, cpu, disk, target: discord.Member, ctx, node_id: int):
        super().__init__(timeout=120)
        self.ram, self.cpu, self.disk = ram, cpu, disk
        self.target, self.ctx, self.node_id = target, ctx, node_id
        opts = [discord.SelectOption(label=o["label"], value=o["value"]) for o in OS_OPTIONS]
        sel = discord.ui.Select(placeholder="Choose OS", options=opts)
        sel.callback = self._create
        self.add_item(sel)

    async def _create(self, inter: discord.Interaction):
        if inter.user.id != self.ctx.author.id:
            await inter.response.send_message(embed=err("Not yours"), ephemeral=True)
            return
        os_ver = inter.data["values"][0]
        await inter.response.edit_message(embed=info("Creating…", f"Deploying `{os_ver}`…"), view=None)

        user_id = str(self.target.id)
        count = len(get_user_vps(user_id)) + 1
        name = f"{BOT_NAME.lower()}-{user_id[-6:]}-{count}"
        ram_mb = self.ram * 1024

        try:
            await run_lxc(f"init {os_ver} {name} -s {DEFAULT_POOL}", self.node_id, timeout=180)
            await run_lxc(f"config set {name} limits.memory {ram_mb}MB", self.node_id)
            await run_lxc(f"config set {name} limits.cpu {self.cpu}", self.node_id)
            await run_lxc(f"config device set {name} root size={self.disk}GB", self.node_id)
            await apply_lxc_config(name, self.node_id)
            await safe_start(name, self.node_id)
            await asyncio.sleep(8)
            password = await setup_ssh(name, self.node_id)
            addr = await establish_pinggy(name, self.node_id)

            vps = {
                "user_id": user_id,
                "node_id": self.node_id,
                "container_name": name,
                "ram": f"{self.ram}GB",
                "cpu": str(self.cpu),
                "storage": f"{self.disk}GB",
                "config": f"{self.ram}GB / {self.cpu} CPU / {self.disk}GB",
                "os_version": os_ver,
                "status": "running",
                "suspended": False,
                "root_password": password,
                "pinggy_address": addr or "",
                "created_at": datetime.now().isoformat(),
                "shared_with": [],
            }
            save_vps(vps)

            emb = ok("VPS Created", f"Deployed for {self.target.mention}")
            emb.add_field(name="Container", value=f"`{name}`", inline=True)
            emb.add_field(name="Resources", value=vps["config"], inline=True)
            emb.add_field(name="OS", value=os_ver, inline=True)
            await inter.followup.send(embed=emb)

            # DM owner
            dm = ok("Your VPS is ready!")
            dm.add_field(name="Details", value=f"`{name}`\n{vps['config']}\nOS: {os_ver}", inline=False)
            if addr:
                h, p = addr.split(":")
                dm.add_field(name="SSH", value=f"```ssh root@{h} -p {p}```\nPassword: `{password}`", inline=False)
            else:
                dm.add_field(name="Password", value=f"`{password}`\nUse `{PREFIX}pinggy` for tunnel.", inline=False)
            try:
                await self.target.send(embed=dm)
            except discord.Forbidden:
                pass

        except Exception as e:
            await inter.followup.send(embed=err("Create failed", str(e)[:500]))

class NodeSelect(discord.ui.View):
    def __init__(self, ram, cpu, disk, target, ctx):
        super().__init__(timeout=120)
        self.ram, self.cpu, self.disk = ram, cpu, disk
        self.target, self.ctx = target, ctx
        nodes = get_nodes()
        opts = []
        for n in nodes:
            opts.append(discord.SelectOption(
                label=n["name"],
                description=f"{n['location']} · capacity {n['total_vps']}",
                value=str(n["id"])
            ))
        if not opts:
            self.add_item(discord.ui.Select(placeholder="No nodes", disabled=True))
        else:
            sel = discord.ui.Select(placeholder="Select node", options=opts)
            sel.callback = self._node
            self.add_item(sel)

    async def _node(self, inter: discord.Interaction):
        if inter.user.id != self.ctx.author.id:
            await inter.response.send_message(embed=err("Not yours"), ephemeral=True)
            return
        node_id = int(inter.data["values"][0])
        await inter.response.edit_message(embed=info("Choose OS"), view=OSSelect(
            self.ram, self.cpu, self.disk, self.target, self.ctx, node_id
        ))

@bot.command(name="create")
@admin_only()
async def cmd_create(ctx, ram: int, cpu: int, disk: int, user: discord.Member):
    if ram < 1 or cpu < 1 or disk < 1:
        await ctx.send(embed=err("Invalid", "RAM/CPU/Disk must be ≥ 1"))
        return
    await ctx.send(
        embed=info("Create VPS", f"{user.mention} · {ram}GB / {cpu} CPU / {disk}GB"),
        view=NodeSelect(ram, cpu, disk, user, ctx)
    )

# ───────────────────────────── ADMIN: DELETE / SUSPEND ─────────────────────────────
@bot.command(name="delete-vps")
@admin_only()
async def cmd_delete(ctx, user: discord.Member, number: int, *, reason: str = "Admin"):
    vps_list = get_user_vps(str(user.id))
    if number < 1 or number > len(vps_list):
        await ctx.send(embed=err("Invalid VPS number"))
        return
    v = vps_list[number - 1]
    name = v["container_name"]
    node_id = v["node_id"]

    try:
        await run_lxc(f"delete {name} --force", node_id)
    except Exception as e:
        if "not found" not in str(e).lower():
            await ctx.send(embed=warn("LXC delete warning", str(e)[:300]))

    delete_vps_db(name)
    await ctx.send(embed=ok("Deleted", f"`{name}` removed for {user.mention}\nReason: {reason}"))

@bot.command(name="suspend-vps")
@admin_only()
async def cmd_suspend(ctx, container: str, *, reason: str = "Admin action"):
    node_id = find_node_for_container(container)
    try:
        await run_lxc(f"stop {container}", node_id)
    except Exception:
        pass

    conn = get_db()
    conn.execute("UPDATE vps SET suspended=1, status='stopped', pinggy_address='' WHERE container_name=?", (container,))
    conn.commit()
    conn.close()
    await ctx.send(embed=ok("Suspended", f"`{container}` · {reason}"))

@bot.command(name="unsuspend-vps")
@admin_only()
async def cmd_unsuspend(ctx, container: str):
    node_id = find_node_for_container(container)
    try:
        await safe_start(container, node_id)
        addr = await establish_pinggy(container, node_id)
    except Exception as e:
        await ctx.send(embed=err("Start failed", str(e)))
        return

    conn = get_db()
    conn.execute("UPDATE vps SET suspended=0, status='running', pinggy_address=? WHERE container_name=?",
                 (addr or "", container))
    conn.commit()
    conn.close()
    await ctx.send(embed=ok("Unsuspended", f"`{container}` is running again."))

# ───────────────────────────── ADMIN: LIST / INFO ─────────────────────────────
@bot.command(name="list-all")
@admin_only()
async def cmd_list_all(ctx):
    conn = get_db()
    rows = conn.execute("SELECT * FROM vps ORDER BY user_id").fetchall()
    conn.close()
    if not rows:
        await ctx.send(embed=info("Empty", "No VPS in database."))
        return

    emb = info("All VPS", f"Total: **{len(rows)}**")
    for r in rows[:15]:
        status = "⛔" if r["suspended"] else ("🟢" if r["status"] == "running" else "🔴")
        emb.add_field(
            name=f"{status} `{r['container_name']}`",
            value=f"<@{r['user_id']}> · {r['config']}",
            inline=False
        )
    if len(rows) > 15:
        emb.set_footer(text=f"Showing 15 of {len(rows)}")
    await ctx.send(embed=emb)

@bot.command(name="userinfo")
@admin_only()
async def cmd_userinfo(ctx, user: discord.Member):
    vps_list = get_user_vps(str(user.id))
    emb = info(f"User · {user.name}", f"ID: `{user.id}`")
    emb.add_field(name="VPS count", value=str(len(vps_list)), inline=True)
    emb.add_field(name="Admin", value="Yes" if is_admin(str(user.id)) else "No", inline=True)
    for i, v in enumerate(vps_list, 1):
        emb.add_field(name=f"#{i}", value=f"`{v['container_name']}`\n{v['config']}", inline=False)
    await ctx.send(embed=emb)

@bot.command(name="lxc-list")
@admin_only()
async def cmd_lxc_list(ctx, node_id: int = 1):
    try:
        out = await run_lxc("list", node_id)
        await ctx.send(embed=info(f"LXC list · node {node_id}", f"```\n{out[:1800]}\n```"))
    except Exception as e:
        await ctx.send(embed=err("Failed", str(e)))

# ───────────────────────────── ADMIN MANAGEMENT ─────────────────────────────
@bot.command(name="admin-add")
@main_admin_only()
async def cmd_admin_add(ctx, user: discord.Member):
    uid = str(user.id)
    if is_admin(uid):
        await ctx.send(embed=err("Already admin"))
        return
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (uid,))
    conn.commit()
    conn.close()
    await ctx.send(embed=ok("Admin added", user.mention))

@bot.command(name="admin-remove")
@main_admin_only()
async def cmd_admin_remove(ctx, user: discord.Member):
    uid = str(user.id)
    if is_main_admin(uid):
        await ctx.send(embed=err("Cannot remove main admin"))
        return
    conn = get_db()
    conn.execute("DELETE FROM admins WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    await ctx.send(embed=ok("Admin removed", user.mention))

@bot.command(name="admin-list")
@main_admin_only()
async def cmd_admin_list(ctx):
    conn = get_db()
    mains = [r["user_id"] for r in conn.execute("SELECT user_id FROM main_admins")]
    admins = [r["user_id"] for r in conn.execute("SELECT user_id FROM admins")]
    conn.close()
    emb = info("Admins")
    emb.add_field(name="Main", value="\n".join(f"<@{u}>" for u in mains) or "—", inline=False)
    emb.add_field(name="Admins", value="\n".join(f"<@{u}>" for u in admins) or "—", inline=False)
    await ctx.send(embed=emb)

# ───────────────────────────── PORTS ─────────────────────────────
def get_port_quota(uid: str) -> int:
    conn = get_db()
    row = conn.execute("SELECT slots FROM port_quota WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    return row["slots"] if row else 0

def get_used_ports(uid: str) -> int:
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) as c FROM port_forwards WHERE user_id=?", (uid,)).fetchone()["c"]
    conn.close()
    return n

def get_free_host_port() -> int:
    conn = get_db()
    used = {r["host_port"] for r in conn.execute("SELECT host_port FROM port_forwards")}
    conn.close()
    for _ in range(50):
        p = random.randint(20000, 50000)
        if p not in used:
            return p
    return None

async def add_port_forward(uid: str, container: str, vps_port: int, node_id: int) -> Optional[int]:
    host_port = get_free_host_port()
    if not host_port:
        return None
    try:
        await run_lxc(f"config device add {container} tcp_{host_port} proxy listen=tcp:0.0.0.0:{host_port} connect=tcp:127.0.0.1:{vps_port}", node_id)
        await run_lxc(f"config device add {container} udp_{host_port} proxy listen=udp:0.0.0.0:{host_port} connect=udp:127.0.0.1:{vps_port}", node_id)
        conn = get_db()
        conn.execute("INSERT INTO port_forwards (user_id, container, vps_port, host_port, created_at) VALUES (?,?,?,?,?)",
                     (uid, container, vps_port, host_port, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return host_port
    except Exception as e:
        log.error(f"Port forward failed: {e}")
        return None

@bot.command(name="ports")
async def cmd_ports(ctx, sub: str = None, *args):
    uid = str(ctx.author.id)
    slots = get_port_quota(uid)
    used = get_used_ports(uid)

    if sub is None:
        await ctx.send(embed=info("Ports", (
            f"**Quota:** `{used}/{slots}`\n\n"
            f"`{PREFIX}ports list`\n"
            f"`{PREFIX}ports add <vps#> <port>`\n"
            f"`{PREFIX}ports remove <id>`"
        )))
        return

    if sub == "list":
        conn = get_db()
        rows = conn.execute("SELECT * FROM port_forwards WHERE user_id=? ORDER BY id", (uid,)).fetchall()
        conn.close()
        emb = info("Your Port Forwards", f"Quota `{used}/{slots}`")
        if not rows:
            emb.description += "\n\nNo active forwards."
        for r in rows:
            emb.add_field(
                name=f"ID {r['id']}",
                value=f"`{r['container']}` · VPS:{r['vps_port']} → Host:{r['host_port']}",
                inline=False
            )
        await ctx.send(embed=emb)

    elif sub == "add":
        if len(args) < 2:
            await ctx.send(embed=err("Usage", f"`{PREFIX}ports add <vps#> <port>`"))
            return
        try:
            num = int(args[0])
            vps_port = int(args[1])
            if not (1 <= vps_port <= 65535):
                raise ValueError
        except ValueError:
            await ctx.send(embed=err("Invalid", "VPS number and port must be valid integers (port 1-65535)."))
            return
        vps_list = get_user_vps(uid)
        if num < 1 or num > len(vps_list):
            await ctx.send(embed=err("Invalid VPS", f"Use `{PREFIX}myvps`"))
            return
        if used >= slots:
            await ctx.send(embed=err("Quota full", f"You have `{used}/{slots}` slots. Ask an admin for more."))
            return
        v = vps_list[num - 1]
        host = await add_port_forward(uid, v["container_name"], vps_port, v["node_id"])
        if host:
            pub = get_public_ip()
            await ctx.send(embed=ok("Port Forwarded", f"`{pub}:{host}` → VPS port `{vps_port}` (TCP+UDP)"))
        else:
            await ctx.send(embed=err("Failed", "Could not create forward."))

    elif sub == "remove":
        if not args:
            await ctx.send(embed=err("Usage", f"`{PREFIX}ports remove <id>`"))
            return
        try:
            fid = int(args[0])
        except ValueError:
            await ctx.send(embed=err("Invalid ID"))
            return
        conn = get_db()
        row = conn.execute("SELECT * FROM port_forwards WHERE id=? AND user_id=?", (fid, uid)).fetchone()
        if not row:
            conn.close()
            await ctx.send(embed=err("Not found"))
            return
        container, host_port = row["container"], row["host_port"]
        node_id = find_node_for_container(container)
        try:
            await run_lxc(f"config device remove {container} tcp_{host_port}", node_id)
            await run_lxc(f"config device remove {container} udp_{host_port}", node_id)
        except Exception:
            pass
        conn.execute("DELETE FROM port_forwards WHERE id=?", (fid,))
        conn.commit()
        conn.close()
        await ctx.send(embed=ok("Removed", f"Forward ID {fid} deleted."))

    else:
        await ctx.send(embed=err("Unknown subcommand", "list / add / remove"))

@bot.command(name="ports-add-user")
@admin_only()
async def cmd_ports_add_user(ctx, amount: int, user: discord.Member):
    if amount < 1:
        await ctx.send(embed=err("Amount must be positive"))
        return
    uid = str(user.id)
    conn = get_db()
    conn.execute("""INSERT INTO port_quota (user_id, slots) VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET slots = slots + ?""", (uid, amount, amount))
    conn.commit()
    new = get_port_quota(uid)
    conn.close()
    await ctx.send(embed=ok("Ports allocated", f"{user.mention} now has `{new}` slots."))

# ───────────────────────────── HELP ─────────────────────────────
@bot.command(name="help")
async def cmd_help(ctx):
    emb = info("Help", "Main commands")
    emb.add_field(name="User", value=(
        f"`{PREFIX}myvps` – list your VPS\n"
        f"`{PREFIX}manage` – start / stop / SSH / tunnel / reinstall / password\n"
        f"`{PREFIX}pinggy [n]` – fresh Pinggy SSH tunnel\n"
        f"`{PREFIX}ports` – list / add / remove port forwards"
    ), inline=False)

    if is_admin(str(ctx.author.id)):
        emb.add_field(name="Admin", value=(
            f"`{PREFIX}create <ram> <cpu> <disk> @user` – create VPS (full OS list)\n"
            f"`{PREFIX}delete-vps @user <n> [reason]`\n"
            f"`{PREFIX}suspend-vps <container> [reason]`\n"
            f"`{PREFIX}unsuspend-vps <container>`\n"
            f"`{PREFIX}list-all` · `{PREFIX}userinfo @user`\n"
            f"`{PREFIX}lxc-list [node]`\n"
            f"`{PREFIX}ports-add-user <amount> @user`"
        ), inline=False)

    if is_main_admin(str(ctx.author.id)):
        emb.add_field(name="Main Admin", value=(
            f"`{PREFIX}admin-add @user`\n"
            f"`{PREFIX}admin-remove @user`\n"
            f"`{PREFIX}admin-list`"
        ), inline=False)

    await ctx.send(embed=emb)

@bot.command(name="about")
async def cmd_about(ctx):
    emb = info(f"About {BOT_NAME}")
    emb.add_field(name="Version", value=BOT_VERSION, inline=True)
    emb.add_field(name="Dev", value=BOT_DEVELOPER, inline=True)
    emb.add_field(name="Ping", value=f"{round(bot.latency*1000)}ms", inline=True)
    await ctx.send(embed=emb)

# ───────────────────────────── RUN ─────────────────────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        log.error("DISCORD_TOKEN not set")
    else:
        bot.run(DISCORD_TOKEN)

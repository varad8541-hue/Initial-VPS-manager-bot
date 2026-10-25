#!/usr/bin/env python3
"""
VPS Manager Discord Bot v4 - Professional UI with OS selection
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
import shlex
from datetime import datetime
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-8s | %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
BOT_NAME = os.getenv("BOT_NAME", "VPS-Manager")
COMPANY_NAME = os.getenv("COMPANY_NAME", "Chaudhary Nodes")
COMPANY_ICON = os.getenv("COMPANY_ICON", "")
PREFIX = os.getenv("PREFIX", "!")
BOT_VERSION = "4.0"
BOT_DEVELOPER = os.getenv("BOT_DEVELOPER", "Unknown")
MAIN_ADMIN_IDS = [uid.strip() for uid in os.getenv("MAIN_ADMIN_ID", "").split(",") if uid.strip()]
DEFAULT_POOL = os.getenv("DEFAULT_STORAGE_POOL", "default")
PINGGY_TOKEN = os.getenv("PINGGY_TOKEN", "")

PINGGY_LOG = "/root/.pinggy.log"
ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

OS_OPTIONS = [
    {"label": "Ubuntu 20.04 LTS", "value": "ubuntu:20.04"},
    {"label": "Ubuntu 22.04 LTS", "value": "ubuntu:22.04"},
    {"label": "Ubuntu 24.04 LTS", "value": "ubuntu:24.04"},
    {"label": "Debian 11 (Bullseye)", "value": "images:debian/11"},
    {"label": "Debian 12 (Bookworm)", "value": "images:debian/12"},
]

log.info(f"Bot: {BOT_NAME} v{BOT_VERSION} | Company: {COMPANY_NAME}")

# ═══════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════
def get_db():
    conn = sqlite3.connect("vps.db")
    conn.row_factory = sqlite3.Row
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
        is_local INTEGER DEFAULT 1
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
        created_at TEXT
    )""")
    
    conn.commit()
    conn.close()
    log.info("Database initialized")

init_db()

# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════
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

def get_user_vps(user_id: str) -> List[Dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM vps WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_vps(vps: Dict):
    conn = get_db()
    c = conn.cursor()
    if vps.get("id"):
        c.execute("""UPDATE vps SET
            ram=?, cpu=?, storage=?, config=?, os_version=?,
            status=?, suspended=?, root_password=?
            WHERE id=?""",
            (vps["ram"], vps["cpu"], vps["storage"], vps.get("config"),
             vps.get("os_version"), vps["status"], int(vps.get("suspended", False)),
             vps.get("root_password", ""), vps["id"]))
    else:
        c.execute("""INSERT INTO vps
            (user_id, node_id, container_name, ram, cpu, storage, config, os_version, status, root_password, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (vps["user_id"], 1, vps["container_name"], vps["ram"],
             vps["cpu"], vps["storage"], vps.get("config"), vps.get("os_version"),
             vps["status"], vps.get("root_password", ""), datetime.now().isoformat()))
        vps["id"] = c.lastrowid
    conn.commit()
    conn.close()

def generate_password(length: int = 16) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))

# ═══════════════════════════════════════════════════════════════════════════
# LXC RUNNER - CRITICAL FIX
# ═══════════════════════════════════════════════════════════════════════════
async def run_lxc(cmd: str, timeout: int = 120) -> str:
    """Run an LXC command with proper parsing using shlex"""
    full_cmd = f"lxc {cmd}"
    log.info(f"[LXC] {full_cmd}")
    
    args = shlex.split(full_cmd)
    log.debug(f"[LXC] Parsed args: {args}")
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            log.error(f"[LXC] Command timed out after {timeout}s")
            raise Exception(f"LXC command timed out: {full_cmd}")
        
        if proc.returncode != 0:
            err_msg = stderr.decode().strip() or "unknown error"
            log.error(f"[LXC] Failed: {err_msg}")
            raise Exception(f"LXC error: {err_msg}")
        
        result = stdout.decode().strip()
        log.debug(f"[LXC] Success")
        return result
    except Exception as e:
        log.error(f"[LXC] Exception: {e}")
        raise

async def safe_start(container: str):
    try:
        await run_lxc(f"start {container}")
    except Exception as e:
        if "already running" not in str(e).lower():
            raise

async def setup_ssh(container: str) -> str:
    """Install SSH and set root password"""
    password = generate_password()
    cmds = [
        "apt-get update -y",
        "apt-get install -y openssh-server ssh",
        f"echo 'root:{password}' | chpasswd",
        "sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config",
        "sed -i 's/#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config",
        "mkdir -p /run/sshd",
        "systemctl restart ssh || service ssh restart || true",
    ]
    
    for cmd in cmds:
        try:
            await run_lxc(f"exec {container} -- bash -c \"{cmd}\"", timeout=180)
        except Exception as e:
            log.warning(f"[SSH] Setup step failed: {e}")
    
    log.info(f"[SSH] {container} ready")
    return password

def parse_pinggy(text: str) -> Optional[str]:
    """Extract Pinggy address from output"""
    if not text:
        return None
    clean = ANSI_RE.sub("", text)
    m = re.search(r"tcp://([a-zA-Z0-9.\-]+):(\d+)", clean, re.I)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    return None

async def establish_pinggy(container: str) -> Optional[str]:
    """Set up Pinggy SSH tunnel"""
    # Ensure SSH is running
    try:
        await run_lxc(f"exec {container} -- bash -c \"service ssh start || systemctl start ssh || true\"")
    except:
        pass
    
    # Kill old tunnel
    try:
        await run_lxc(f"exec {container} -- bash -c \"pkill -9 -f pinggy.io; rm -f {PINGGY_LOG}\"")
    except:
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
        await run_lxc(f"exec {container} -- bash -c \"{tunnel}\"")
    except Exception as e:
        log.error(f"[Pinggy] Start failed: {e}")
        return None
    
    # Wait for tunnel to establish
    for _ in range(6):
        await asyncio.sleep(4)
        try:
            log_out = await run_lxc(f"exec {container} -- cat {PINGGY_LOG}")
            addr = parse_pinggy(log_out if isinstance(log_out, str) else "")
            if addr:
                log.info(f"[Pinggy] Ready: {addr}")
                return addr
        except Exception:
            pass
    
    return None

# ═══════════════════════════════════════════════════════════════════════════
# EMBEDS
# ═══════════════════════════════════════════════════════════════════════════
def make_embed(title: str, description: str = "", color: int = 0x1a1a1a) -> discord.Embed:
    emb = discord.Embed(
        title=f"✦ {COMPANY_NAME} · {title}",
        description=description[:4090],
        color=color,
        timestamp=datetime.utcnow()
    )
    if COMPANY_ICON:
        emb.set_thumbnail(url=COMPANY_ICON)
    emb.set_footer(text=f"{BOT_NAME} v{BOT_VERSION} · {COMPANY_NAME}")
    return emb

def ok(title, desc=""):   return make_embed(title, desc, 0x00ff88)
def err(title, desc=""):  return make_embed(title, desc, 0xff3366)
def info(title, desc=""): return make_embed(title, desc, 0x00ccff)
def warn(title, desc=""): return make_embed(title, desc, 0xffaa00)

# ═══════════════════════════════════════════════════════════════════════════
# BOT SETUP
# ═══════════════════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════════════════
# EVENTS
# ═══════════════════════════════════════════════════════════════════════════
@bot.event
async def on_ready():
    log.info(f"✓ Bot logged in as {bot.user} ({bot.user.id})")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name=f"{COMPANY_NAME} VPS"
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
        log.error(f"Command error: {error}")
        await ctx.send(embed=err("Error", str(error)[:500]))

# ═══════════════════════════════════════════════════════════════════════════
# OS SELECT VIEW
# ═══════════════════════════════════════════════════════════════════════════
class OSSelect(discord.ui.View):
    def __init__(self, ram: int, cpu: int, disk: int, user: discord.Member, ctx):
        super().__init__(timeout=120)
        self.ram = ram
        self.cpu = cpu
        self.disk = disk
        self.user = user
        self.ctx = ctx
        
        opts = [discord.SelectOption(label=o["label"], value=o["value"]) for o in OS_OPTIONS]
        sel = discord.ui.Select(placeholder="Select OS", options=opts)
        sel.callback = self.on_os_select
        self.add_item(sel)
    
    async def on_os_select(self, inter: discord.Interaction):
        if inter.user.id != self.ctx.author.id:
            await inter.response.send_message(embed=err("Not yours"), ephemeral=True)
            return
        
        os_ver = inter.data["values"][0]
        await inter.response.edit_message(embed=info("Creating…", f"Deploying {os_ver}…"), view=None)
        
        user_id = str(self.user.id)
        count = len(get_user_vps(user_id)) + 1
        container = f"vps-{user_id[-6:]}-{count}"
        
        try:
            log.info(f"[CREATE] {container} for {self.user} ({user_id})")
            
            # Clean up if it exists
            try:
                await run_lxc(f"delete {container} --force")
                log.info(f"[CREATE] Cleaned up orphaned container {container}")
            except:
                pass
            
            await run_lxc(f"init {os_ver} {container} -s {DEFAULT_POOL}", timeout=180)
            await run_lxc(f"config set {container} limits.memory {self.ram*1024}MB")
            await run_lxc(f"config set {container} limits.cpu {self.cpu}")
            await run_lxc(f"config device set {container} root size={self.disk}GB")
            
            await safe_start(container)
            await asyncio.sleep(5)
            
            password = await setup_ssh(container)
            addr = await establish_pinggy(container)
            
            vps = {
                "user_id": user_id,
                "container_name": container,
                "ram": f"{self.ram}GB",
                "cpu": str(self.cpu),
                "storage": f"{self.disk}GB",
                "config": f"{self.ram}GB RAM / {self.cpu} CPU / {self.disk}GB disk",
                "os_version": os_ver,
                "status": "running",
                "root_password": password,
            }
            save_vps(vps)
            
            emb = ok("VPS Created", f"`{container}`")
            emb.add_field(name="Configuration", value=vps["config"], inline=False)
            emb.add_field(name="OS", value=os_ver, inline=True)
            emb.add_field(name="Status", value="🟢 RUNNING", inline=True)
            emb.add_field(name="Next", value=f"Use `{PREFIX}manage` to view your VPS", inline=False)
            
            await inter.followup.send(embed=emb)
            
            dm_emb = ok("VPS Password - Keep Safe!", container)
            dm_emb.add_field(name="Password", value=f"```{password}```", inline=False)
            if addr:
                h, p = addr.split(":")
                dm_emb.add_field(name="SSH Tunnel", value=f"```ssh root@{h} -p {p}```", inline=False)
            else:
                dm_emb.add_field(name="Note", value=f"Use `{PREFIX}manage` → Tunnel to create SSH tunnel", inline=False)
            await self.user.send(embed=dm_emb)
            
        except Exception as e:
            log.error(f"[CREATE] Failed: {e}")
            # Clean up on failure
            try:
                await run_lxc(f"delete {container} --force")
            except:
                pass
            await inter.followup.send(embed=err("Create Failed", str(e)[:400]))

# ═══════════════════════════════════════════════════════════════════════════
# MANAGE VIEW
# ═══════════════════════════════════════════════════════════════════════════
class ManageView(discord.ui.View):
    def __init__(self, owner_id: str, vps_list: List[Dict]):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.vps_list = vps_list
        self.idx = 0 if len(vps_list) == 1 else None
        
        if len(vps_list) > 1:
            opts = [
                discord.SelectOption(
                    label=f"VPS #{i+1} · {v['container_name']}",
                    description=v['config'][:50],
                    value=str(i)
                ) for i, v in enumerate(vps_list)
            ]
            sel = discord.ui.Select(placeholder="Select VPS", options=opts)
            sel.callback = self.on_select
            self.add_item(sel)
        else:
            self.add_control_buttons()
    
    async def on_select(self, inter: discord.Interaction):
        if str(inter.user.id) != self.owner_id:
            await inter.response.send_message(embed=err("Not yours"), ephemeral=True)
            return
        self.idx = int(inter.data["values"][0])
        self.clear_items()
        self.add_control_buttons()
        emb = self.build_embed()
        await inter.response.edit_message(embed=emb, view=self)
    
    def add_control_buttons(self):
        buttons = [
            ("▶ Start", discord.ButtonStyle.success, "start"),
            ("⏸ Stop", discord.ButtonStyle.secondary, "stop"),
            ("🔌 Tunnel", discord.ButtonStyle.primary, "tunnel"),
            ("🔐 Change Pass", discord.ButtonStyle.danger, "pass"),
            ("📊 Stats", discord.ButtonStyle.secondary, "stats"),
            ("🔄 Reinstall", discord.ButtonStyle.danger, "reinstall"),
        ]
        for label, style, action in buttons:
            btn = discord.ui.Button(label=label, style=style)
            btn.callback = lambda i, a=action: self.on_action(i, a)
            self.add_item(btn)
    
    def build_embed(self) -> discord.Embed:
        v = self.vps_list[self.idx]
        status = "⛔ SUSPENDED" if v["suspended"] else v["status"].upper()
        color = 0x00ff88 if v["status"] == "running" and not v["suspended"] else 0xff3366
        
        emb = make_embed(f"VPS Management - {v['container_name']}", color=color)
        emb.add_field(name="Container", value=f"`{v['container_name']}`", inline=True)
        emb.add_field(name="Status", value=f"**{status}**", inline=True)
        emb.add_field(name="📦 Allocated Resources", value=v['config'], inline=False)
        emb.add_field(name="RAM", value=v['ram'], inline=True)
        emb.add_field(name="CPU", value=v['cpu'] + " Cores", inline=True)
        emb.add_field(name="Storage", value=v['storage'], inline=True)
        emb.add_field(name="OS", value=v['os_version'], inline=True)
        emb.add_field(name="Created", value=v['created_at'][:10], inline=True)
        emb.add_field(name="🎮 Controls", value="Use the buttons below to manage your VPS", inline=False)
        
        return emb
    
    async def on_action(self, inter: discord.Interaction, action: str):
        if str(inter.user.id) != self.owner_id:
            await inter.response.send_message(embed=err("Not yours"), ephemeral=True)
            return
        
        v = self.vps_list[self.idx]
        container = v["container_name"]
        
        await inter.response.defer(ephemeral=True)
        
        try:
            if action == "start":
                await safe_start(container)
                conn = get_db()
                conn.execute("UPDATE vps SET status='running' WHERE container_name=?", (container,))
                conn.commit()
                conn.close()
                v["status"] = "running"
                await inter.followup.send(embed=ok("Started", f"`{container}` is running"), ephemeral=True)
            
            elif action == "stop":
                await run_lxc(f"stop {container}")
                conn = get_db()
                conn.execute("UPDATE vps SET status='stopped' WHERE container_name=?", (container,))
                conn.commit()
                conn.close()
                v["status"] = "stopped"
                await inter.followup.send(embed=ok("Stopped", f"`{container}` is stopped"), ephemeral=True)
            
            elif action == "pass":
                new_pass = generate_password()
                await run_lxc(f"exec {container} -- bash -c \"echo 'root:{new_pass}' | chpasswd\"")
                conn = get_db()
                conn.execute("UPDATE vps SET root_password=? WHERE container_name=?", (new_pass, container))
                conn.commit()
                conn.close()
                v["root_password"] = new_pass
                
                dm_emb = ok("Password Changed", container)
                dm_emb.add_field(name="New Password", value=f"```{new_pass}```", inline=False)
                try:
                    await inter.user.send(embed=dm_emb)
                    await inter.followup.send(embed=ok("Changed", "Check your DMs for new password"), ephemeral=True)
                except:
                    await inter.followup.send(embed=ok("New Password", f"```{new_pass}```"), ephemeral=True)
            
            elif action == "stats":
                try:
                    out = await run_lxc(f"info {container}")
                    status_line = next((l for l in out.splitlines() if "Status:" in l), "Status: unknown")
                    await inter.followup.send(embed=info("Stats", f"`{container}`\n{status_line}"), ephemeral=True)
                except Exception as e:
                    await inter.followup.send(embed=err("Stats", "Could not retrieve stats"), ephemeral=True)
            
            elif action == "tunnel":
                await inter.response.edit_message(embed=info("Tunnel", "Creating Pinggy SSH tunnel..."), view=None)
                addr = await establish_pinggy(container)
                if addr:
                    h, p = addr.split(":")
                    pw = v.get("root_password", "?")
                    
                    dm_emb = ok("SSH Tunnel Ready", container)
                    dm_emb.add_field(name="Login", value=(
                        f"**Host:** `{h}`\n"
                        f"**Port:** `{p}`\n"
                        f"**User:** `root`\n"
                        f"**Password:** `{pw}`\n\n"
                        f"```ssh root@{h} -p {p}```"
                    ), inline=False)
                    
                    try:
                        await inter.user.send(embed=dm_emb)
                        await inter.followup.send(embed=ok("Tunnel Ready", "Check DMs for details"), ephemeral=True)
                    except:
                        await inter.followup.send(embed=dm_emb, ephemeral=True)
                else:
                    await inter.followup.send(embed=err("Tunnel Failed", "Could not create tunnel"), ephemeral=True)
                return
            
            elif action == "reinstall":
                await inter.followup.send(embed=warn("Reinstall", "Feature coming soon"), ephemeral=True)
            
            emb = self.build_embed()
            await inter.message.edit(embed=emb, view=self)
        
        except Exception as e:
            log.error(f"Action {action} failed: {e}")
            await inter.followup.send(embed=err("Failed", str(e)[:400]), ephemeral=True)

# ═══════════════════════════════════════════════════════════════════════════
# COMMANDS - USER
# ═══════════════════════════════════════════════════════════════════════════
@bot.command(name="ping")
async def cmd_ping(ctx):
    await ctx.send(embed=ok("Pong", f"Latency: `{round(bot.latency*1000)}ms`"))

@bot.command(name="myvps")
async def cmd_myvps(ctx):
    vps_list = get_user_vps(str(ctx.author.id))
    if not vps_list:
        await ctx.send(embed=err("No VPS", "You have no VPS yet. Ask an admin."))
        return
    
    emb = info("Your VPS", f"**{len(vps_list)}** VPS")
    for i, v in enumerate(vps_list, 1):
        status = "⛔ SUSPENDED" if v["suspended"] else ("🟢 RUNNING" if v["status"] == "running" else "🔴 STOPPED")
        emb.add_field(
            name=f"#{i} · `{v['container_name']}`",
            value=f"{status}\n{v['config']}\nOS: {v['os_version']}",
            inline=False
        )
    emb.add_field(name="Manage", value=f"Use `{PREFIX}manage` to control your VPS", inline=False)
    await ctx.send(embed=emb)

@bot.command(name="manage")
async def cmd_manage(ctx, user: discord.Member = None):
    if user:
        if not is_admin(str(ctx.author.id)):
            await ctx.send(embed=err("Admin only"))
            return
        vps_list = get_user_vps(str(user.id))
    else:
        vps_list = get_user_vps(str(ctx.author.id))
    
    if not vps_list:
        await ctx.send(embed=err("No VPS", "No VPS found."))
        return
    
    view = ManageView(str(ctx.author.id), vps_list)
    
    if len(vps_list) == 1:
        emb = view.build_embed()
    else:
        emb = info("Select VPS", "Choose a VPS from the dropdown")
    
    await ctx.send(embed=emb, view=view)

# ═══════════════════════════════════════════════════════════════════════════
# COMMANDS - ADMIN
# ═══════════════════════════════════════════════════════════════════════════
@bot.command(name="create")
@admin_only()
async def cmd_create(ctx, ram: int, cpu: int, disk: int, user: discord.Member):
    """Create VPS: !create 4 2 20 @user"""
    if ram < 1 or cpu < 1 or disk < 1:
        await ctx.send(embed=err("Invalid", "RAM/CPU/Disk must be ≥ 1"))
        return
    
    emb = info("Choose OS", f"Creating {ram}GB / {cpu} CPU / {disk}GB for {user.mention}")
    await ctx.send(embed=emb, view=OSSelect(ram, cpu, disk, user, ctx))

@bot.command(name="start")
@admin_only()
async def cmd_start(ctx, container: str):
    try:
        await safe_start(container)
        conn = get_db()
        conn.execute("UPDATE vps SET status='running' WHERE container_name=?", (container,))
        conn.commit()
        conn.close()
        await ctx.send(embed=ok("Started", f"`{container}` is running"))
    except Exception as e:
        await ctx.send(embed=err("Failed", str(e)))

@bot.command(name="stop")
@admin_only()
async def cmd_stop(ctx, container: str):
    try:
        await run_lxc(f"stop {container}")
        conn = get_db()
        conn.execute("UPDATE vps SET status='stopped' WHERE container_name=?", (container,))
        conn.commit()
        conn.close()
        await ctx.send(embed=ok("Stopped", f"`{container}` stopped"))
    except Exception as e:
        await ctx.send(embed=err("Failed", str(e)))

@bot.command(name="delete")
@admin_only()
async def cmd_delete(ctx, container: str):
    try:
        await run_lxc(f"delete {container} --force")
        conn = get_db()
        conn.execute("DELETE FROM vps WHERE container_name=?", (container,))
        conn.commit()
        conn.close()
        await ctx.send(embed=ok("Deleted", f"`{container}` removed"))
    except Exception as e:
        await ctx.send(embed=err("Failed", str(e)))

@bot.command(name="list-all")
@admin_only()
async def cmd_list_all(ctx):
    conn = get_db()
    rows = conn.execute("SELECT * FROM vps ORDER BY user_id").fetchall()
    conn.close()
    
    if not rows:
        await ctx.send(embed=info("Empty", "No VPS"))
        return
    
    emb = info("All VPS", f"Total: **{len(rows)}**")
    for r in rows[:15]:
        status = "⛔" if r["suspended"] else ("🟢" if r["status"] == "running" else "🔴")
        emb.add_field(
            name=f"{status} `{r['container_name']}`",
            value=f"<@{r['user_id']}> · {r['config']}",
            inline=False
        )
    await ctx.send(embed=emb)

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

@bot.command(name="help")
async def cmd_help(ctx):
    emb = info("Help")
    emb.add_field(name="User", value=(
        f"`{PREFIX}ping` – check bot\n"
        f"`{PREFIX}myvps` – list your VPS\n"
        f"`{PREFIX}manage` – manage your VPS"
    ), inline=False)
    
    if is_admin(str(ctx.author.id)):
        emb.add_field(name="Admin", value=(
            f"`{PREFIX}create <ram> <cpu> <disk> @user` – deploy VPS\n"
            f"`{PREFIX}start <container>` – start VPS\n"
            f"`{PREFIX}stop <container>` – stop VPS\n"
            f"`{PREFIX}delete <container>` – delete VPS\n"
            f"`{PREFIX}list-all` – show all VPS"
        ), inline=False)
    
    if is_main_admin(str(ctx.author.id)):
        emb.add_field(name="Main Admin", value=(
            f"`{PREFIX}admin-add @user` – add admin"
        ), inline=False)
    
    await ctx.send(embed=emb)

# ═══════════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        log.error("DISCORD_TOKEN not set in .env")
        exit(1)
    
    log.info("Starting bot…")
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        log.error(f"Failed to start: {e}")
        exit(1)

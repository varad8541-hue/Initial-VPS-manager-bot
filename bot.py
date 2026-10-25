#!/usr/bin/env python3
"""
VPS Manager Discord Bot v3 - Clean rewrite with logging
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
PREFIX = os.getenv("PREFIX", "!")
BOT_VERSION = "3.0"
BOT_DEVELOPER = os.getenv("BOT_DEVELOPER", "Unknown")
MAIN_ADMIN_IDS = [uid.strip() for uid in os.getenv("MAIN_ADMIN_ID", "").split(",") if uid.strip()]
DEFAULT_POOL = os.getenv("DEFAULT_STORAGE_POOL", "default")

log.info(f"Bot: {BOT_NAME} v{BOT_VERSION} | Dev: {BOT_DEVELOPER}")

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
                  ("Local", "Local", 100))
    
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
    
    # CRITICAL: Use shlex.split to properly handle flags like "-s default"
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
        log.debug(f"[LXC] Success: {result[:100]}")
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
        "apt-get install -y openssh-server",
        f"echo 'root:{password}' | chpasswd",
        "sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config",
        "systemctl restart ssh || service ssh restart || true",
    ]
    
    for cmd in cmds:
        try:
            await run_lxc(f"exec {container} -- bash -c \"{cmd}\"", timeout=180)
        except Exception as e:
            log.warning(f"[SSH] Setup step failed: {e}")
    
    log.info(f"[SSH] {container} ready with password {password[:4]}****")
    return password

# ═══════════════════════════════════════════════════════════════════════════
# EMBEDS
# ═══════════════════════════════════════════════════════════════════════════
def make_embed(title: str, description: str = "", color: int = 0x1a1a1a) -> discord.Embed:
    emb = discord.Embed(
        title=f"✦ {BOT_NAME} · {title}",
        description=description[:4090],
        color=color,
        timestamp=datetime.utcnow()
    )
    emb.set_footer(text=f"{BOT_NAME} v{BOT_VERSION} · {BOT_DEVELOPER}")
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
        log.error(f"Command error: {error}")
        await ctx.send(embed=err("Error", str(error)[:500]))

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
            value=f"{status}\n`{v['config']}`",
            inline=False
        )
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
    
    emb = info("Manage VPS", f"**{len(vps_list)}** container(s)")
    for i, v in enumerate(vps_list, 1):
        status = "⛔" if v["suspended"] else ("🟢" if v["status"] == "running" else "🔴")
        emb.add_field(
            name=f"{status} #{i} `{v['container_name']}`",
            value=f"{v['config']}\nOS: {v['os_version']}",
            inline=False
        )
    await ctx.send(embed=emb)

# ═══════════════════════════════════════════════════════════════════════════
# COMMANDS - ADMIN
# ═══════════════════════════════════════════════════════════════════════════
@bot.command(name="create")
@admin_only()
async def cmd_create(ctx, ram: int, cpu: int, disk: int, user: discord.Member):
    """Create a VPS: !create 4 2 20 @user"""
    if ram < 1 or cpu < 1 or disk < 1:
        await ctx.send(embed=err("Invalid", "RAM/CPU/Disk must be ≥ 1"))
        return
    
    msg = await ctx.send(embed=info("Creating…", f"Deploying VPS for {user.mention}…"))
    
    user_id = str(user.id)
    count = len(get_user_vps(user_id)) + 1
    container = f"vps-{user_id[-6:]}-{count}"
    
    try:
        log.info(f"[CREATE] {container} for {user} ({user_id})")
        
        # Create container
        await run_lxc(f"init ubuntu:22.04 {container} -s {DEFAULT_POOL}", timeout=180)
        log.info(f"[CREATE] Container created: {container}")
        
        # Configure resources
        await run_lxc(f"config set {container} limits.memory {ram*1024}MB")
        await run_lxc(f"config set {container} limits.cpu {cpu}")
        await run_lxc(f"config device set {container} root size={disk}GB")
        log.info(f"[CREATE] Resources set: {ram}GB RAM, {cpu} CPU, {disk}GB disk")
        
        # Start container
        await safe_start(container)
        await asyncio.sleep(5)
        log.info(f"[CREATE] Container started")
        
        # Setup SSH
        password = await setup_ssh(container)
        
        # Save to DB
        vps = {
            "user_id": user_id,
            "container_name": container,
            "ram": f"{ram}GB",
            "cpu": str(cpu),
            "storage": f"{disk}GB",
            "config": f"{ram}GB RAM / {cpu} CPU / {disk}GB disk",
            "os_version": "ubuntu:22.04",
            "status": "running",
            "root_password": password,
        }
        save_vps(vps)
        log.info(f"[CREATE] VPS saved to DB")
        
        # Notify
        emb = ok("VPS Created", f"Container: `{container}`")
        emb.add_field(name="Resources", value=vps["config"], inline=True)
        emb.add_field(name="Password", value=f"`{password}`", inline=True)
        emb.add_field(name="Start VPS", value=f"`{PREFIX}manage` then Start", inline=False)
        
        await msg.edit(embed=emb)
        
        # DM user
        try:
            await user.send(embed=ok("Your VPS Ready!", f"Container: `{container}`\nPassword: `{password}`"))
        except:
            pass
        
    except Exception as e:
        log.error(f"[CREATE] Failed: {e}")
        await msg.edit(embed=err("Create Failed", str(e)[:400]))

@bot.command(name="start")
@admin_only()
async def cmd_start(ctx, container: str):
    """Start a container: !start vps-abc-1"""
    try:
        await safe_start(container)
        
        # Update DB
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
    """Stop a container: !stop vps-abc-1"""
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
    """Delete a container: !delete vps-abc-1"""
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
    """List all VPS"""
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
        f"`{PREFIX}manage` – view VPS"
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

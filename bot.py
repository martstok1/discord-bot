import os, json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
import discord
from discord.ext import tasks
from discord import app_commands
from dotenv import load_dotenv

# ===== .env laden =====
# Lokaal: leest .env bestand
# Online (Railway): gebruikt automatisch Railway Variables
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_COD = int(os.getenv("CHANNEL_ID_COD", "0"))
CHANNEL_ID_BF  = int(os.getenv("CHANNEL_ID_BF", "0"))

FOLLOW_COD = os.getenv("FOLLOW_ACCOUNT_COD", "CODUpdates").lstrip("@")
FOLLOW_BF  = os.getenv("FOLLOW_ACCOUNT_BF", "Battlefield").lstrip("@")

NITTER_SOURCES = [s.strip() for s in os.getenv("NITTER_SOURCES", "https://nitter.net").split(",") if s.strip()]
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "180"))

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN ontbreekt in .env of Railway Variables")

STATE_FILE = "last_seen.json"  # {"COD": last_id, "BF": last_id}

# ===== Discord client =====
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)  # slash commands

# ===== persistent state =====
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}
def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

state = load_state()
state.setdefault("COD", None)
state.setdefault("BF",  None)

# ===== helpers =====
async def fetch_latest(account: str, limit: int = 10):
    """
    Haal de laatste posts van een X-account via Nitter.
    Retourneert lijst items: [{id, url, text, time}] van nieuw -> oud
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    for base in NITTER_SOURCES:
        url = f"{base.rstrip('/')}/{account}"
        try:
            async with httpx.AsyncClient(timeout=25, headers=headers, follow_redirects=True) as client:
                r = await client.get(url)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            timeline = soup.select("div.timeline > div.timeline-item")
            items = []
            for item in timeline[:max(10, limit)]:
                a = item.select_one("a.tweet-date")
                if not a or not a.get("href"):
                    continue
                link = a["href"]
                if "/status/" not in link:
                    continue
                tw_id = link.split("/status/")[-1].split("?")[0]
                tw_url = f"https://twitter.com/{account}/status/{tw_id}"
                content = item.select_one("div.tweet-content")
                text = content.get_text("\n", strip=True) if content else "(geen tekst)"
                time_tag = item.select_one("span.tweet-date > a > time")
                if time_tag and time_tag.has_attr("datetime"):
                    try:
                        ts = datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
                    except Exception:
                        ts = datetime.now(timezone.utc)
                else:
                    ts = datetime.now(timezone.utc)
                items.append({"id": tw_id, "url": tw_url, "text": text, "time": ts})
            items.sort(key=lambda x: x["time"], reverse=True)  # nieuw -> oud
            return items[:limit]
        except Exception:
            continue
    return []

async def post_new(label: str, channel_id: int, account: str, state_key: str):
    """Post nieuwe items sinds last_id (oud -> nieuw)."""
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        return
    items = await fetch_latest(account, limit=6)
    if not items:
        return
    last_seen = state.get(state_key)
    for t in sorted(items, key=lambda x: x["time"]):  # oud -> nieuw posten
        if last_seen is None or t["id"] > last_seen:
            msg = f"📰 **{label} Update — @{account}**\n{t['text']}\n🔗 {t['url']}"
            try:
                await channel.send(msg)
                state[state_key] = t["id"]
                save_state(state)
            except Exception as e:
                print("Post error:", e)

# ===== background loop =====
@tasks.loop(seconds=POLL_SECONDS)
async def poll_loop():
    await post_new("COD", CHANNEL_ID_COD, FOLLOW_COD, "COD")
    await post_new("Battlefield", CHANNEL_ID_BF, FOLLOW_BF, "BF")

# ===== lifecycle =====
@bot.event
async def on_ready():
    print(f"✅ Game Intel Bot ingelogd als {bot.user}")
    if not poll_loop.is_running():
        poll_loop.start()
    try:
        await tree.sync()
        print("✅ Slash commands gesynchroniseerd")
    except Exception as e:
        print("Slash sync fout:", e)

# ===== slash commands =====
@tree.command(name="cod_force", description="Forceer nu een check voor COD-updates")
async def cod_force(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await post_new("COD", CHANNEL_ID_COD, FOLLOW_COD, "COD")
    await interaction.followup.send("✅ COD check uitgevoerd.")

@tree.command(name="bf_force", description="Forceer nu een check voor Battlefield-updates")
async def bf_force(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await post_new("Battlefield", CHANNEL_ID_BF, FOLLOW_BF, "BF")
    await interaction.followup.send("✅ BF check uitgevoerd.")

@tree.command(name="cod_get", description="Haal een eerdere COD-post op (1=nieuwste, 2=vorige, ...)")
@app_commands.describe(nummer="1 = nieuwste, 2 = vorige, 3 = twee eerder (max 10)")
async def cod_get(interaction: discord.Interaction, nummer: app_commands.Range[int, 1, 10]):
    await interaction.response.defer(thinking=True)
    items = await fetch_latest(FOLLOW_COD, limit=nummer)
    if not items or len(items) < nummer:
        await interaction.followup.send("⚠️ Geen item gevonden, probeer later opnieuw.")
        return
    t = items[nummer - 1]
    await interaction.followup.send(f"📦 **COD Update #{nummer} — @{FOLLOW_COD}**\n{t['text']}\n🔗 {t['url']}")

@tree.command(name="bf_get", description="Haal een eerdere BF-post op (1=nieuwste, 2=vorige, ...)")
@app_commands.describe(nummer="1 = nieuwste, 2 = vorige, 3 = twee eerder (max 10)")
async def bf_get(interaction: discord.Interaction, nummer: app_commands.Range[int, 1, 10]):
    await interaction.response.defer(thinking=True)
    items = await fetch_latest(FOLLOW_BF, limit=nummer)
    if not items or len(items) < nummer:
        await interaction.followup.send("⚠️ Geen item gevonden, probeer later opnieuw.")
        return
    t = items[nummer - 1]
    await interaction.followup.send(f"📦 **BF Update #{nummer} — @{FOLLOW_BF}**\n{t['text']}\n🔗 {t['url']}")

bot.run(TOKEN)

import os
import json
from datetime import datetime, timezone
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
import discord
from discord.ext import tasks
from discord import app_commands
from dotenv import load_dotenv

# ===== .env laden =====
env_path = Path(__file__).with_name('.env')
load_dotenv(dotenv_path=env_path)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_COD = int(os.getenv("CHANNEL_ID_COD", "0"))
CHANNEL_ID_BF = int(os.getenv("CHANNEL_ID_BF", "0"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "180"))

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN ontbreekt in .env")

STATE_FILE = "last_seen.json"

# ===== Discord client =====
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

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
state.setdefault("BF", None)

# ===== helpers =====
async def fetch_cod_news(limit=3):
    """Haal laatste COD-nieuws op van GameRant"""
    url = "https://gamerant.com/call-of-duty-news/"
    headers = {"User-Agent": "Mozilla/5.0"}
    items = []

    try:
        async with httpx.AsyncClient(timeout=25, headers=headers) as client:
            r = await client.get(url)
        if r.status_code != 200:
            print(f"[COD] Fout bij ophalen: {r.status_code}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.select("article")[:limit]
        for art in articles:
            a_tag = art.select_one("a")
            if not a_tag:
                continue
            link = a_tag.get("href")
            title = a_tag.get_text(strip=True)
            ts = datetime.now(timezone.utc)
            items.append({
                "id": link,
                "url": link,
                "text": title,
                "time": ts,
                "media": None
            })
    except Exception as e:
        print(f"[COD] Fout bij ophalen nieuws: {e}")
    return items

async def fetch_bf_news(limit=3):
    """Haal laatste Battlefield-nieuws op van EA"""
    url = "https://www.ea.com/games/battlefield/battlefield-6/news"
    headers = {"User-Agent": "Mozilla/5.0"}
    items = []

    try:
        async with httpx.AsyncClient(timeout=25, headers=headers) as client:
            r = await client.get(url)
        if r.status_code != 200:
            print(f"[BF] Fout bij ophalen: {r.status_code}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.select("li.article-tile")[:limit]
        for art in articles:
            a_tag = art.select_one("a")
            if not a_tag:
                continue
            link = "https://www.ea.com" + a_tag.get("href")
            title = art.get_text(strip=True)
            ts = datetime.now(timezone.utc)
            items.append({
                "id": link,
                "url": link,
                "text": title,
                "time": ts,
                "media": None
            })
    except Exception as e:
        print(f"[BF] Fout bij ophalen nieuws: {e}")
    return items

async def post_new(label, channel_id, fetch_func, state_key, color):
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        return
    items = await fetch_func(limit=3)
    if not items:
        return
    last_seen = state.get(state_key)
    for t in sorted(items, key=lambda x: x["time"]):
        if last_seen is None or t["id"] != last_seen:
            embed = discord.Embed(
                title=f"{label} Update",
                description=t['text'],
                url=t['url'],
                color=color,
                timestamp=t['time']
            )
            embed.set_footer(text="Bron: Officiële website")
            if t.get("media"):
                embed.set_image(url=t["media"])
            await channel.send(embed=embed)
            state[state_key] = t["id"]
            save_state(state)

# ===== background loop =====
@tasks.loop(seconds=POLL_SECONDS)
async def poll_loop():
    print("[DEBUG] Polling gestart...")
    await post_new("COD", CHANNEL_ID_COD, fetch_cod_news, "COD", discord.Color.orange())
    await post_new("Battlefield", CHANNEL_ID_BF, fetch_bf_news, "BF", discord.Color.blue())

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

# ===== Slash command =====
@tree.command(name="get_news", description="Haal een specifiek nieuwsbericht op (1=nieuwste)")
@app_commands.describe(game="cod of bf", nummer="1 = nieuwste, 2 = vorige, enz.")
async def get_news(interaction: discord.Interaction, game: str, nummer: app_commands.Range[int, 1, 10]):
    game = game.lower()
    if game not in ["cod", "bf"]:
        await interaction.response.send_message("❌ Ongeldig spel. Kies 'cod' of 'bf'.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    if game == "cod":
        items = await fetch_cod_news(limit=nummer)
        kleur = discord.Color.orange()
    else:
        items = await fetch_bf_news(limit=nummer)
        kleur = discord.Color.blue()

    if not items or len(items) < nummer:
        await interaction.followup.send("⚠️ Geen nieuwsbericht gevonden.")
        return

    t = items[nummer - 1]
    embed = discord.Embed(
        title=f"{game.upper()} Nieuws #{nummer}",
        description=t['text'],
        url=t['url'],
        color=kleur,
        timestamp=t['time']
    )
    if t.get("media"):
        embed.set_image(url=t["media"])
    await interaction.followup.send(embed=embed)

bot.run(TOKEN)

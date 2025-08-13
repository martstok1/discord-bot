import os
import json
from datetime import datetime, timezone
from pathlib import Path
import discord
from discord.ext import tasks
from discord import app_commands
from dotenv import load_dotenv
import feedparser

# ===== .env laden =====
env_path = Path(__file__).with_name('.env')
load_dotenv(dotenv_path=env_path)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_COD = int(os.getenv("CHANNEL_ID_COD", "0"))
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

# ===== COD via RSS =====
async def fetch_cod_rss(limit=3):
    """Haal COD nieuws op via Kotaku RSS"""
    feed_url = "https://kotaku.com/tag/call-of-duty/rss"
    parsed = feedparser.parse(feed_url)
    items = []

    for entry in parsed.entries[:limit]:
        ts = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

        # Link opschonen
        clean_link = entry.link.replace("https://editors.", "https://").replace("http://editors.", "http://")

        items.append({
            "id": clean_link,
            "url": clean_link,
            "text": entry.title,
            "time": ts
        })
    return items

async def post_new_cod():
    channel = bot.get_channel(CHANNEL_ID_COD)
    if not channel:
        return

    items = await fetch_cod_rss(limit=3)
    if not items:
        return

    last_seen = state.get("COD")
    for t in sorted(items, key=lambda x: x["time"]):
        if last_seen != t["id"]:  # Alleen posten als het nieuw is
            embed = discord.Embed(
                title="COD Update",
                description=t['text'],
                url=t['url'],
                color=discord.Color.orange(),
                timestamp=t['time']
            )
            embed.set_footer(text="Bron: Kotaku RSS")
            await channel.send(embed=embed)
            state["COD"] = t["id"]
            save_state(state)

# ===== test command =====
@tree.command(name="cod_last", description="Laatste COD nieuwsbericht")
async def cod_last(interaction: discord.Interaction):
    items = await fetch_cod_rss(limit=1)
    if not items:
        await interaction.response.send_message("Geen nieuws gevonden.")
        return
    t = items[0]
    embed = discord.Embed(
        title="Laatste COD Nieuws",
        description=t['text'],
        url=t['url'],
        color=discord.Color.orange(),
        timestamp=t['time']
    )
    embed.set_footer(text="Bron: Kotaku RSS")
    await interaction.response.send_message(embed=embed)

# ===== background loop =====
@tasks.loop(seconds=POLL_SECONDS)
async def poll_loop():
    print("[DEBUG] Polling gestart...")
    await post_new_cod()

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

bot.run(TOKEN)

import os
import json
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import tasks
from discord import app_commands

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ===== CONFIG =====
env_path = Path(__file__).with_name('.env')
load_dotenv(dotenv_path=env_path)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_COD = int(os.getenv("CHANNEL_ID_COD", "0"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "180"))

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN ontbreekt in .env")

STATE_FILE = "last_seen.json"
KOTAKU_LOGO = "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5f/Kotaku_logo.svg/512px-Kotaku_logo.svg.png"

# ===== Discord client =====
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ===== Persistent state =====
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

# ===== Helper: scrape eerste afbeelding =====
def get_article_image(url):
    try:
        r = requests.get(url, timeout=5)
        soup = BeautifulSoup(r.text, "html.parser")
        img_tag = soup.find("img")
        if img_tag and img_tag.get("src"):
            return img_tag["src"]
    except Exception as e:
        print("[WARN] Kan afbeelding niet ophalen:", e)
    return None

# ===== COD via RSS =====
async def fetch_cod_rss(limit=3):
    feed_url = "https://kotaku.com/tag/call-of-duty/rss"
    parsed = feedparser.parse(feed_url)
    items = []

    for entry in parsed.entries[:limit]:
        ts = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        clean_link = entry.link
        summary = getattr(entry, "summary", "")

        # Eerste afbeelding ophalen (scrape)
        first_image = get_article_image(clean_link)

        items.append({
            "id": clean_link,
            "url": clean_link,
            "title": entry.title,
            "summary": summary,
            "time": ts,
            "image": first_image
        })

    return items

# ===== Embed maken =====
def create_embed(article):
    embed = discord.Embed(
        title=article["title"],
        description=article["summary"],
        url=article["url"],
        color=discord.Color.orange(),
        timestamp=article["time"]
    )

    # Thumbnail = Kotaku logo
    embed.set_thumbnail(url=KOTAKU_LOGO)

    # Grote afbeelding onderaan = eerste artikelafbeelding
    if article["image"]:
        embed.set_image(url=article["image"])

    # Footer = eerste afbeelding van artikel (alleen afbeelding, geen tekst)
    if article["image"]:
        embed.set_footer(text="", icon_url=article["image"])

    return embed

# ===== Post nieuwe COD update =====
async def post_new_cod():
    items = await fetch_cod_rss(limit=1)
    if not items:
        return

    latest_item = items[0]
    last_seen = state.get("COD")

    # Eerste keer alleen onthouden
    if last_seen is None:
        state["COD"] = latest_item["id"]
        save_state(state)
        print("[INFO] Eerste start: laatste artikel onthouden, geen post.")
        return

    # Alleen posten als het nieuw is
    if latest_item["id"] != last_seen:
        channel = bot.get_channel(CHANNEL_ID_COD)
        if channel:
            embed = create_embed(latest_item)
            await channel.send(embed=embed)

        state["COD"] = latest_item["id"]
        save_state(state)
        print("[INFO] Nieuw artikel gepost.")

# ===== Slash command =====
@tree.command(name="cod_last", description="Laatste COD nieuwsbericht")
async def cod_last(interaction: discord.Interaction):
    items = await fetch_cod_rss(limit=1)
    if not items:
        await interaction.response.send_message("Geen nieuws gevonden.")
        return
    embed = create_embed(items[0])
    await interaction.response.send_message(embed=embed)

# ===== Background loop =====
@tasks.loop(seconds=POLL_SECONDS)
async def poll_loop():
    await post_new_cod()

# ===== Lifecycle =====
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

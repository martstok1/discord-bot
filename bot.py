import os
import json
from datetime import datetime, timezone
from pathlib import Path
import discord
from discord.ext import tasks
from discord import app_commands
from dotenv import load_dotenv
import feedparser
import requests
from bs4 import BeautifulSoup
import re

# ===== .env laden =====
env_path = Path(__file__).with_name('.env')
load_dotenv(dotenv_path=env_path)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_COD = int(os.getenv("CHANNEL_ID_COD", "0"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "180"))

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN ontbreekt in .env")

STATE_FILE = "last_seen.json"
KOTAKU_LOGO = "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5c/Kotaku_logo.svg/320px-Kotaku_logo.svg.png"

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

# ===== helpers =====
def strip_html(html):
    """Verwijder HTML-tags en maak korte samenvatting"""
    text = re.sub(r"<.*?>", "", html)  # verwijder tags
    text = re.sub(r"\s+", " ", text).strip()
    return text

def get_article_image(url):
    """Haal eerste afbeelding uit artikel"""
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
    """Haal COD nieuws op via Kotaku RSS"""
    feed_url = "https://kotaku.com/tag/call-of-duty/rss"
    parsed = feedparser.parse(feed_url)
    items = []

    for entry in parsed.entries[:limit]:
        ts = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        clean_link = entry.link

        # Samenvatting opschonen
        summary_text = strip_html(entry.summary) if "summary" in entry else "Geen beschrijving beschikbaar."

        # Probeer afbeelding te vinden
        image_url = get_article_image(clean_link)

        items.append({
            "id": clean_link,
            "url": clean_link,
            "title": entry.title,
            "summary": summary_text,
            "time": ts,
            "image": image_url
        })

    return items

# ===== post nieuwe COD update =====
async def post_new_cod():
    items = await fetch_cod_rss(limit=1)  # Alleen nieuwste ophalen
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
            embed = discord.Embed(
                title=latest_item['title'],
                description=latest_item['summary'],
                url=latest_item['url'],
                color=discord.Color.orange(),
                timestamp=latest_item['time']
            )
            embed.set_thumbnail(url=KOTAKU_LOGO)
            if latest_item.get('image'):
                embed.set_image(url=latest_item['image'])
                embed.set_footer(icon_url=latest_item['image'], text=" ")

            await channel.send(embed=embed)

        state["COD"] = latest_item["id"]
        save_state(state)
        print("[INFO] Nieuw artikel gepost.")

# ===== test command =====
@tree.command(name="cod_last", description="Laatste COD nieuwsbericht")
async def cod_last(interaction: discord.Interaction):
    items = await fetch_cod_rss(limit=1)
    if not items:
        await interaction.response.send_message("Geen nieuws gevonden.")
        return

    t = items[0]
    embed = discord.Embed(
        title=t['title'],
        description=t['summary'],
        url=t['url'],
        color=discord.Color.orange(),
        timestamp=t['time']
    )
    embed.set_thumbnail(url=KOTAKU_LOGO)
    if t.get("image"):
        embed.set_image(url=t["image"])
        embed.set_footer(icon_url=t["image"], text=" ")

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

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

# ===== scrape grote artikelafbeelding =====
def get_article_image(url):
    try:
        r = requests.get(url, timeout=5)
        soup = BeautifulSoup(r.text, "html.parser")
        meta_img = soup.find("meta", property="og:image")
        if meta_img and meta_img.get("content"):
            return meta_img["content"]
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

        # Probeer afbeelding te vinden uit RSS
        image_url = None
        if 'media_content' in entry and len(entry.media_content) > 0:
            image_url = entry.media_content[0].get('url')
        elif 'media_thumbnail' in entry and len(entry.media_thumbnail) > 0:
            image_url = entry.media_thumbnail[0].get('url')

        # Als RSS geen afbeelding heeft → haal van artikelpagina
        if not image_url:
            image_url = get_article_image(clean_link)

        items.append({
            "id": clean_link,
            "url": clean_link,
            "text": entry.title,
            "time": ts,
            "image": image_url
        })

    return items

# ===== post nieuwe COD update =====
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
            embed = discord.Embed(
                title="COD Update",
                description=latest_item['text'],
                url=latest_item['url'],
                color=discord.Color.orange(),
                timestamp=latest_item['time']
            )
            embed.set_footer(text="Bron: Kotaku RSS")
            if latest_item.get('image'):
                embed.set_image(url=latest_item['image'])

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
        title="Laatste COD Nieuws",
        description=t['text'],
        url=t['url'],
        color=discord.Color.orange(),
        timestamp=t['time']
    )
    embed.set_footer(text="Bron: Kotaku RSS")

    if t.get("image"):
        embed.set_image(url=t["image"])

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

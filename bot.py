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

# ====== CONFIG ======
env_path = Path(__file__).with_name('.env')
load_dotenv(dotenv_path=env_path)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_COD = int(os.getenv("CHANNEL_ID_COD", "0"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "180"))

STATE_FILE = "last_seen.json"
KOTAKU_LOGO = "https://upload.wikimedia.org/wikipedia/commons/2/28/Kotaku_logo.svg"

# ====== DISCORD CLIENT ======
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ====== STATE FUNCTIONS ======
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

# ====== HELPER: SCRAPE ARTICLE IMAGE ======
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

# ====== FETCH COD RSS ======
async def fetch_cod_rss(limit=3):
    feed_url = "https://kotaku.com/tag/call-of-duty/rss"
    parsed = feedparser.parse(feed_url)
    items = []

    for entry in parsed.entries[:limit]:
        ts = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        clean_link = entry.link
        summary = getattr(entry, "summary", entry.title)  # Samenvatting of fallback

        # Afbeelding uit RSS of via scrape
        image_url = None
        if 'media_content' in entry and len(entry.media_content) > 0:
            image_url = entry.media_content[0].get('url')
        elif 'media_thumbnail' in entry and len(entry.media_thumbnail) > 0:
            image_url = entry.media_thumbnail[0].get('url')
        else:
            image_url = get_article_image(clean_link)

        items.append({
            "id": clean_link,
            "url": clean_link,
            "title": entry.title,
            "summary": summary,
            "time": ts,
            "image": image_url
        })

    return items

# ====== EMBED BUILDER ======
def build_embed(article):
    embed = discord.Embed(
        title=article["title"],
        description=article["summary"],
        url=article["url"],
        color=discord.Color.orange(),
        timestamp=article["time"]
    )

    # Thumbnail (logo)
    embed.set_thumbnail(url=KOTAKU_LOGO)

    # Velden
    embed.add_field(name="📅 Gepubliceerd", value=article["time"].strftime("%d-%m-%Y %H:%M"), inline=True)
    embed.add_field(name="📰 Bron", value="Kotaku", inline=True)

    # Grote afbeelding onderaan
    if article.get("image"):
        embed.set_image(url=article["image"])

    # Footer
    embed.set_footer(text="Bron: Kotaku", icon_url=KOTAKU_LOGO)

    return embed

# ====== POST NIEUW ARTIKEL ======
async def post_new_cod():
    items = await fetch_cod_rss(limit=1)
    if not items:
        return

    latest_item = items[0]
    last_seen = state.get("COD")

    if last_seen is None:
        state["COD"] = latest_item["id"]
        save_state(state)
        print("[INFO] Eerste keer: artikel onthouden.")
        return

    if latest_item["id"] != last_seen:
        channel = bot.get_channel(CHANNEL_ID_COD)
        if channel:
            await channel.send(embed=build_embed(latest_item))
        state["COD"] = latest_item["id"]
        save_state(state)
        print("[INFO] Nieuw artikel gepost.")

# ====== SLASH COMMAND ======
@tree.command(name="cod_last", description="Laatste COD nieuwsbericht")
async def cod_last(interaction: discord.Interaction):
    items = await fetch_cod_rss(limit=1)
    if not items:
        await interaction.response.send_message("Geen nieuws gevonden.")
        return
    await interaction.response.send_message(embed=build_embed(items[0]))

# ====== BACKGROUND LOOP ======
@tasks.loop(seconds=POLL_SECONDS)
async def poll_loop():
    await post_new_cod()

# ====== ON READY ======
@bot.event
async def on_ready():
    print(f"✅ Ingelogd als {bot.user}")
    if not poll_loop.is_running():
        poll_loop.start()
    try:
        await tree.sync()
        print("✅ Slash commands gesynchroniseerd")
    except Exception as e:
        print("Slash sync fout:", e)

bot.run(TOKEN)

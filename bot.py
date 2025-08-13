import os
import json
import re
import html
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import tasks
from discord import app_commands
from dotenv import load_dotenv

import feedparser
import requests
from bs4 import BeautifulSoup

# ============ .env ============
env_path = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=env_path)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_COD = int(os.getenv("CHANNEL_ID_COD", "0"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "180"))
THUMB_URL = "https://i.imgur.com/lT8lJC5.jpeg"  # Vaste COD-logo

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN ontbreekt in .env")

STATE_FILE = "last_seen.json"

# ============ Discord client ============
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ============ state opslaan/lezen ============
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

# ============ helpers ============
TAG_RE = re.compile(r"<[^>]+>")

def clean_html(text: str, max_len: int = 350) -> str:
    """Strip HTML + decode entities + nettere spaties + afkappen."""
    if not text:
        return ""
    text = TAG_RE.sub("", text)  # HTML-tags verwijderen
    text = html.unescape(text)   # HTML entities decoden
    text = re.sub(r"\s+", " ", text).strip()  # dubbele spaties weghalen
    if len(text) > max_len:
        text = text[:max_len - 1].rstrip() + "…"
    return text

def get_article_image(url: str) -> str | None:
    """Probeer een afbeelding uit de artikelpagina te halen."""
    try:
        r = requests.get(url, timeout=6)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        og = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if og and og.get("content"):
            return og["content"]
        img = soup.find("img")
        if img and img.get("src"):
            return img["src"]
    except Exception as e:
        print("[WARN] Kan artikel-afbeelding niet ophalen:", e)
    return None

def build_embed(item: dict, title_prefix: str = "COD") -> discord.Embed:
    """Maak de uiteindelijke embed opmaak."""
    embed = discord.Embed(
        title=item["title"],
        description=item["text"],
        url=item["url"],
        color=discord.Color.orange(),
        timestamp=item["time"],
    )
    embed.set_thumbnail(url=THUMB_URL)  # COD logo als thumbnail

    if item.get("image"):
        embed.set_image(url=item["image"])  # Grote afbeelding onderaan

    # Footer alleen datum/tijd
    pub_time = item["time"].strftime("%d-%m-%Y, %H:%M")
    embed.set_footer(text=pub_time)

    return embed

# ============ COD via RSS ============
async def fetch_cod_rss(limit: int = 3) -> list[dict]:
    FEED_URL = "https://kotaku.com/tag/call-of-duty/rss"
    parsed = feedparser.parse(FEED_URL)
    items: list[dict] = []

    for entry in parsed.entries[:limit]:
        ts = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc) if hasattr(entry, "published_parsed") else datetime.now(timezone.utc)
        link = entry.link
        title = entry.title
        descr = clean_html(getattr(entry, "summary", ""), max_len=350)

        image_url = None
        media_content = getattr(entry, "media_content", None)
        media_thumb = getattr(entry, "media_thumbnail", None)
        if media_content and len(media_content) > 0 and media_content[0].get("url"):
            image_url = media_content[0]["url"]
        elif media_thumb and len(media_thumb) > 0 and media_thumb[0].get("url"):
            image_url = media_thumb[0]["url"]
        if not image_url:
            image_url = get_article_image(link)

        items.append({
            "id": link,
            "url": link,
            "title": title,
            "text": descr or title,
            "time": ts,
            "image": image_url,
        })

    return items

# ============ posten / commands ============
async def post_new_cod():
    items = await fetch_cod_rss(limit=1)
    if not items:
        return

    latest = items[0]
    last_seen = state.get("COD")

    if last_seen is None:
        state["COD"] = latest["id"]
        save_state(state)
        print("[INFO] Eerste start: laatste artikel onthouden, geen post.")
        return

    if latest["id"] != last_seen:
        channel = bot.get_channel(CHANNEL_ID_COD)
        if channel:
            embed = build_embed(latest, title_prefix="COD")
            await channel.send(embed=embed)
        state["COD"] = latest["id"]
        save_state(state)
        print("[INFO] Nieuw artikel gepost.")

@tree.command(name="cod_last", description="Laatste COD-nieuws (Kotaku)")
async def cod_last(interaction: discord.Interaction):
    items = await fetch_cod_rss(limit=1)
    if not items:
        await interaction.response.send_message("Geen nieuws gevonden.")
        return
    embed = build_embed(items[0], title_prefix="COD")
    await interaction.response.send_message(embed=embed)

# ============ background loop ============
@tasks.loop(seconds=POLL_SECONDS)
async def poll_loop():
    await post_new_cod()

# ============ lifecycle ============
@bot.event
async def on_ready():
    print(f"✅ Game Intel Bot ingelogd als {bot.user}")
    try:
        await tree.sync()
        print("✅ Slash commands gesynchroniseerd")
    except Exception as e:
        print("Slash sync fout:", e)
    if not poll_loop.is_running():
        poll_loop.start()

bot.run(TOKEN)

# bot.py
import os
import json
import html as html_lib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
import feedparser
import discord
from discord.ext import tasks
from discord import app_commands
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ========== Config / .env ==========
env_path = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=env_path)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_COD = int(os.getenv("CHANNEL_ID_COD", "0"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "300"))  # elke 5 min standaard

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN ontbreekt in .env")

STATE_FILE = "last_seen.json"
KOTAKU_FEED = "https://kotaku.com/tag/call-of-duty/rss"
KOTAKU_LOGO = "https://upload.wikimedia.org/wikipedia/commons/5/58/Kotaku_2018_logo.svg"

# ========== Discord client ==========
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ========== Persistent state ==========
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

state = load_state()
state.setdefault("COD", None)

# ========== Helpers ==========
SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
)

def clean_summary(summary_html: str, max_len: int = 320) -> str:
    """Strip alle HTML en decodeer entiteiten; knip op nette lengte."""
    if not summary_html:
        return ""
    # HTML -> tekst
    soup = BeautifulSoup(summary_html, "html.parser")
    text = soup.get_text(" ", strip=True)
    text = html_lib.unescape(text)
    # Net knippen
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text

def get_article_image(url: str) -> str | None:
    """Pak og:image of eerste <img> uit artikelpagina."""
    try:
        r = SESSION.get(url, timeout=8)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")

        # 1) Open Graph
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return urljoin(url, og["content"])

        # 2) Twitter card
        tw = soup.find("meta", attrs={"name": "twitter:image"})
        if tw and tw.get("content"):
            return urljoin(url, tw["content"])

        # 3) Eerste plaatje in content
        img = soup.find("img")
        if img and img.get("src"):
            return urljoin(url, img["src"])
    except Exception as e:
        print("[WARN] Kan afbeelding niet ophalen:", e)
    return None

def fmt_timestamp(ts: datetime) -> str:
    # NL notatie: dd-mm-jjjj, HH:MM
    return ts.astimezone(timezone.utc).strftime("%d-%m-%Y, %H:%M")

# ========== Data ophalen ==========
def fetch_cod_articles(limit: int = 1) -> list[dict]:
    """Lees Kotaku RSS en geef lijst met dicts: id, url, title, summary, time, image."""
    parsed = feedparser.parse(KOTAKU_FEED)
    items: list[dict] = []

    for entry in parsed.entries[:limit]:
        # publicatietijd
        ts = (
            datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if getattr(entry, "published_parsed", None)
            else datetime.now(timezone.utc)
        )
        link = entry.link
        title = html_lib.unescape(entry.title)

        # Samenvatting netjes
        summary_html = getattr(entry, "summary", "")
        summary = clean_summary(summary_html)

        # Probeer eerst feed media, anders scrape
        image_url = None
        if hasattr(entry, "media_content") and entry.media_content:
            image_url = entry.media_content[0].get("url")
        if not image_url and hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
            image_url = entry.media_thumbnail[0].get("url")
        if not image_url:
            image_url = get_article_image(link)

        items.append(
            {
                "id": link,          # uniek genoeg
                "url": link,
                "title": title,
                "summary": summary,
                "time": ts,
                "image": image_url,
            }
        )

    return items

# ========== Embed builder ==========
def build_embed(item: dict) -> discord.Embed:
    embed = discord.Embed(
        title=item["title"],
        url=item["url"],
        description=item.get("summary") or "",
        color=discord.Color.orange(),
        timestamp=item["time"],
    )
    # thumbnail = site logo
    embed.set_thumbnail(url=KOTAKU_LOGO)

    # grote afbeelding onderaan
    if item.get("image"):
        embed.set_image(url=item["image"])

    # footer met datum/tijd
    embed.set_footer(text=fmt_timestamp(item["time"]))
    return embed

# ========== Auto-Poster (alleen nieuwe) ==========
async def post_new_cod_if_any():
    """Check laatste artikel; post alleen als nieuw. Eerste run: alleen onthouden."""
    items = fetch_cod_articles(limit=1)
    if not items:
        return
    latest = items[0]
    last_seen = state.get("COD")

    # Eerste run: alleen onthouden
    if last_seen is None:
        state["COD"] = latest["id"]
        save_state(state)
        print("[INFO] Eerste start: laatste artikel onthouden, geen post.")
        return

    # Nieuw? Posten!
    if latest["id"] != last_seen:
        channel = bot.get_channel(CHANNEL_ID_COD)
        if channel:
            embed = build_embed(latest)
            await channel.send(embed=embed)
            print("[INFO] Nieuw artikel gepost.")
        state["COD"] = latest["id"]
        save_state(state)

# ========== Slash Commands ==========
@tree.command(name="cod_last", description="Toon het laatste COD-nieuws (Kotaku)")
async def cod_last(interaction: discord.Interaction):
    items = fetch_cod_articles(limit=1)
    if not items:
        await interaction.response.send_message("Geen nieuws gevonden.", ephemeral=True)
        return
    embed = build_embed(items[0])
    await interaction.response.send_message(embed=embed)

# ========== Background loop ==========
@tasks.loop(seconds=POLL_SECONDS)
async def poll_loop():
    print("[DEBUG] Polling…")
    await post_new_cod_if_any()

# ========== Lifecycle ==========
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

# ========== Start ==========
bot.run(TOKEN)

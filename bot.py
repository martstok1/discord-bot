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

FOLLOW_COD = os.getenv("FOLLOW_ACCOUNT_COD", "CODUpdates").lstrip("@")
FOLLOW_BF = os.getenv("FOLLOW_ACCOUNT_BF", "Battlefield").lstrip("@")

NITTER_SOURCES = [s.strip() for s in os.getenv("NITTER_SOURCES", "https://nitter.net").split(",") if s.strip()]
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "180"))

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN ontbreekt in .env / Railway Variables")

STATE_FILE = "last_seen.json"
state = {}

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

# ===== Discord client =====
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ===== helpers =====
async def fetch_latest(account: str, limit: int = 10):
    """Haal laatste posts van een X-account via Nitter"""
    headers = {"User-Agent": "Mozilla/5.0"}
    all_items = []

    print(f"[DEBUG] Start fetch voor account: {account}")

    for base in NITTER_SOURCES:
        url = f"{base.rstrip('/')}/{account}"
        print(f"[DEBUG] Ophalen vanaf: {url}")

        try:
            async with httpx.AsyncClient(timeout=25, headers=headers, follow_redirects=True) as client:
                r = await client.get(url)

            print(f"[DEBUG] Statuscode: {r.status_code}")

            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            timeline = soup.select("div.timeline > div.timeline-item")
            print(f"[DEBUG] Gevonden timeline items: {len(timeline)}")

            for item in timeline[:limit]:
                a = item.select_one("a.tweet-date")
                if not a or not a.get("href"):
                    continue
                link = a["href"]
                if "/status/" not in link:
                    continue

                tw_id = link.split("/status/")[-1].split("?")[0]
                tw_url = f"https://x.com/{account}/status/{tw_id}"

                content = item.select_one("div.tweet-content")
                text = content.get_text("\n", strip=True) if content else "(geen tekst)"

                time_tag = item.select_one("span.tweet-date > a > time")
                ts = datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00")) if time_tag and time_tag.has_attr("datetime") else datetime.now(timezone.utc)

                media_url = None
                img_tag = item.select_one("a.still-image img")
                if img_tag and img_tag.get("src"):
                    media_url = img_tag["src"]
                else:
                    vid_tag = item.select_one("video > source")
                    if vid_tag and vid_tag.get("src"):
                        media_url = vid_tag["src"]

                print(f"[DEBUG] Post gevonden: {tw_id} | {text[:50]}... | Media: {media_url}")

                all_items.append({
                    "id": tw_id,
                    "url": tw_url,
                    "text": text,
                    "time": ts,
                    "media": media_url
                })

            if all_items:
                break

        except Exception as e:
            print(f"[ERROR] Ophalen mislukt voor {url}: {e}")

    all_items.sort(key=lambda x: x["time"], reverse=True)
    return all_items[:limit]

async def post_items(label: str, channel_id: int, account: str, color: discord.Color, limit: int = 3):
    """Plaats meerdere items in mooie embeds"""
    channel = bot.get_channel(channel_id)
    if not channel:
        print(f"[ERROR] Kanaal {channel_id} niet gevonden")
        return

    items = await fetch_latest(account, limit=limit)
    if not items:
        await channel.send(f"⚠️ Geen {label}-posts gevonden.")
        return

    for t in reversed(items):
        embed = discord.Embed(
            title=f"{label} Update — @{account}",
            description=t['text'],
            url=t['url'],
            color=color,
            timestamp=t['time']
        )
        embed.set_footer(text="Bron: X (Twitter)")
        if t.get("media"):
            embed.set_image(url=t["media"])

        await channel.send(embed=embed)

# ===== background loop =====
@tasks.loop(seconds=POLL_SECONDS)
async def poll_loop():
    print("[DEBUG] Polling gestart...")
    # COD
    items_cod = await fetch_latest(FOLLOW_COD, limit=1)
    if items_cod:
        last_seen = state.get("COD")
        if last_seen != items_cod[0]["id"]:
            await post_items("COD", CHANNEL_ID_COD, FOLLOW_COD, discord.Color.from_str("#FFB300"), limit=1)
            state["COD"] = items_cod[0]["id"]
            save_state(state)

    # BF
    items_bf = await fetch_latest(FOLLOW_BF, limit=1)
    if items_bf:
        last_seen = state.get("BF")
        if last_seen != items_bf[0]["id"]:
            await post_items("Battlefield", CHANNEL_ID_BF, FOLLOW_BF, discord.Color.from_str("#1E90FF"), limit=1)
            state["BF"] = items_bf[0]["id"]
            save_state(state)

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
@tree.command(name="cod_force", description="Forceer nu de laatste 3 COD-updates")
async def cod_force(interaction: discord.Interaction):
    if interaction.channel_id != CHANNEL_ID_COD:
        await interaction.response.send_message("⚠️ Deze command mag alleen in het COD-kanaal.", ephemeral=True)
        return
    await interaction.response.defer()
    await post_items("COD", CHANNEL_ID_COD, FOLLOW_COD, discord.Color.from_str("#FFB300"), limit=3)
    await interaction.followup.send("✅ Laatste 3 COD-updates gepost.")

@tree.command(name="bf_force", description="Forceer nu de laatste 3 Battlefield-updates")
async def bf_force(interaction: discord.Interaction):
    if interaction.channel_id != CHANNEL_ID_BF:
        await interaction.response.send_message("⚠️ Deze command mag alleen in het Battlefield-kanaal.", ephemeral=True)
        return
    await interaction.response.defer()
    await post_items("Battlefield", CHANNEL_ID_BF, FOLLOW_BF, discord.Color.from_str("#1E90FF"), limit=3)
    await interaction.followup.send("✅ Laatste 3 Battlefield-updates gepost.")

bot.run(TOKEN)

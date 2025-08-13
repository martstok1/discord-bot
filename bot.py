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
async def fetch_latest(account: str, limit: int = 10):
    """Haal laatste posts van een X-account via Nitter"""
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
                tw_url = f"https://x.com/{account}/status/{tw_id}"
                content = item.select_one("div.tweet-content")
                text = content.get_text("\n", strip=True) if content else "(geen tekst)"
                time_tag = item.select_one("span.tweet-date > a > time")
                ts = datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00")) if time_tag and time_tag.has_attr("datetime") else datetime.now(timezone.utc)

                # Media zoeken
                media_url = None
                img_tag = item.select_one("a.still-image img")
                if img_tag and img_tag.get("src"):
                    media_url = img_tag["src"]
                else:
                    vid_tag = item.select_one("video > source")
                    if vid_tag and vid_tag.get("src"):
                        media_url = vid_tag["src"]

                items.append({
                    "id": tw_id,
                    "url": tw_url,
                    "text": text,
                    "time": ts,
                    "media": media_url
                })
            items.sort(key=lambda x: x["time"], reverse=True)
            return items[:limit]
        except Exception as e:
            print(f"Fout bij ophalen van {account}: {e}")
            continue
    return []

async def post_new(label: str, channel_id: int, account: str, state_key: str, color: discord.Color, max_items: int = 1):
    """Plaats nieuwe items in mooie embeds"""
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        return
    items = await fetch_latest(account, limit=max_items)
    if not items:
        return
    last_seen = state.get(state_key)
    for t in sorted(items, key=lambda x: x["time"]):
        if last_seen is None or t["id"] > last_seen:
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
            try:
                await channel.send(embed=embed)
                state[state_key] = t["id"]
                save_state(state)
            except Exception as e:
                print("Post error:", e)

# ===== background loop =====
@tasks.loop(seconds=POLL_SECONDS)
async def poll_loop():
    await post_new("COD", CHANNEL_ID_COD, FOLLOW_COD, "COD", discord.Color.from_str("#FFB300"))
    await post_new("Battlefield", CHANNEL_ID_BF, FOLLOW_BF, "BF", discord.Color.from_str("#1E90FF"))

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

# ===== channel check helper =====
def wrong_channel(interaction: discord.Interaction, required_id: int) -> bool:
    return interaction.channel_id != required_id

# ===== slash commands =====
@tree.command(name="cod_force", description="Forceer nu een check voor COD-updates (laatste 3)")
async def cod_force(interaction: discord.Interaction):
    if wrong_channel(interaction, CHANNEL_ID_COD):
        await interaction.response.send_message("⚠️ Deze command werkt alleen in het COD-updates kanaal.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    await post_new("COD", CHANNEL_ID_COD, FOLLOW_COD, "COD", discord.Color.from_str("#FFB300"), max_items=3)
    await interaction.followup.send("✅ Laatste 3 COD-updates gepost.")

@tree.command(name="bf_force", description="Forceer nu een check voor Battlefield-updates (laatste 3)")
async def bf_force(interaction: discord.Interaction):
    if wrong_channel(interaction, CHANNEL_ID_BF):
        await interaction.response.send_message("⚠️ Deze command werkt alleen in het Battlefield-updates kanaal.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    await post_new("Battlefield", CHANNEL_ID_BF, FOLLOW_BF, "BF", discord.Color.from_str("#1E90FF"), max_items=3)
    await interaction.followup.send("✅ Laatste 3 Battlefield-updates gepost.")

@tree.command(name="cod_get", description="Haal een eerdere COD-post op (1=nieuwste, 2=vorige, ...)")
@app_commands.describe(nummer="1 = nieuwste, 2 = vorige, 3 = twee eerder (max 10)")
async def cod_get(interaction: discord.Interaction, nummer: app_commands.Range[int, 1, 10]):
    if wrong_channel(interaction, CHANNEL_ID_COD):
        await interaction.response.send_message("⚠️ Deze command werkt alleen in het COD-updates kanaal.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    items = await fetch_latest(FOLLOW_COD, limit=nummer)
    if not items or len(items) < nummer:
        await interaction.followup.send("⚠️ Geen item gevonden, probeer later opnieuw.")
        return
    t = items[nummer - 1]
    embed = discord.Embed(
        title=f"COD Update #{nummer} — @{FOLLOW_COD}",
        description=t['text'],
        url=t['url'],
        color=discord.Color.from_str("#FFB300"),
        timestamp=t['time']
    )
    if t.get("media"):
        embed.set_image(url=t["media"])
    await interaction.followup.send(embed=embed)

@tree.command(name="bf_get", description="Haal een eerdere BF-post op (1=nieuwste, 2=vorige, ...)")
@app_commands.describe(nummer="1 = nieuwste, 2 = vorige, 3 = twee eerder (max 10)")
async def bf_get(interaction: discord.Interaction, nummer: app_commands.Range[int, 1, 10]):
    if wrong_channel(interaction, CHANNEL_ID_BF):
        await interaction.response.send_message("⚠️ Deze command werkt alleen in het Battlefield-updates kanaal.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    items = await fetch_latest(FOLLOW_BF, limit=nummer)
    if not items or len(items) < nummer:
        await interaction.followup.send("⚠️ Geen item gevonden, probeer later opnieuw.")
        return
    t = items[nummer - 1]
    embed = discord.Embed(
        title=f"Battlefield Update #{nummer} — @{FOLLOW_BF}",
        description=t['text'],
        url=t['url'],
        color=discord.Color.from_str("#1E90FF"),
        timestamp=t['time']
    )
    if t.get("media"):
        embed.set_image(url=t["media"])
    await interaction.followup.send(embed=embed)

bot.run(TOKEN)

import os
import discord
from discord.ext import commands, tasks
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# .env laden
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_COD = int(os.getenv("DISCORD_CHANNEL_COD"))
CHANNEL_ID_BATTLEFIELD = int(os.getenv("DISCORD_CHANNEL_BATTLEFIELD"))

# Nitter bronnen splitten
nitter_sources = os.getenv("NITTER_SOURCES").split(",")
BATTLEFIELD_SOURCE = nitter_sources[0].strip()
COD_SOURCE = nitter_sources[1].strip()

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "180"))

# Discord bot setup
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Geheugen voor laatst geposte berichten
last_posts = {
    "battlefield": None,
    "cod": None
}

async def fetch_latest_post(url):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            tweet = soup.find("div", class_="timeline-item")
            if not tweet:
                return None

            content = tweet.find("div", class_="tweet-content media-body")
            link = tweet.find("a", href=True)
            if content and link:
                text = content.get_text(strip=True)
                post_url = f"https://nitter.net{link['href']}"
                return text, post_url
        return None
    except Exception as e:
        print(f"❌ Fout bij ophalen van {url}: {e}")
        return None

@tasks.loop(seconds=POLL_SECONDS)
async def check_updates():
    # Battlefield
    bf_post = await fetch_latest_post(BATTLEFIELD_SOURCE)
    if bf_post and bf_post != last_posts["battlefield"]:
        channel = bot.get_channel(CHANNEL_ID_BATTLEFIELD)
        if channel:
            await channel.send(f"📢 **Battlefield update:**\n{bf_post[0]}\n🔗 {bf_post[1]}")
        last_posts["battlefield"] = bf_post

    # COD
    cod_post = await fetch_latest_post(COD_SOURCE)
    if cod_post and cod_post != last_posts["cod"]:
        channel = bot.get_channel(CHANNEL_ID_COD)
        if channel:
            await channel.send(f"📢 **COD update:**\n{cod_post[0]}\n🔗 {cod_post[1]}")
        last_posts["cod"] = cod_post

@bot.event
async def on_ready():
    print(f"✅ Ingelogd als {bot.user}")
    check_updates.start()

# Handmatige commands
@bot.command()
async def battlefield_get(ctx):
    post = await fetch_latest_post(BATTLEFIELD_SOURCE)
    if post:
        await ctx.send(f"📢 **Battlefield:**\n{post[0]}\n🔗 {post[1]}")
    else:
        await ctx.send("⚠️ Geen item gevonden, probeer later opnieuw.")

@bot.command()
async def cod_get(ctx):
    post = await fetch_latest_post(COD_SOURCE)
    if post:
        await ctx.send(f"📢 **Call of Duty:**\n{post[0]}\n🔗 {post[1]}")
    else:
        await ctx.send("⚠️ Geen item gevonden, probeer later opnieuw.")

# Bot starten
bot.run(TOKEN)

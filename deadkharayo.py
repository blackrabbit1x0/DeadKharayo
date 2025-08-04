import os
import base64
import discord
from discord.ext import commands
import asyncio
from flask import Flask, request, send_file
from threading import Thread
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from dotenv import load_dotenv
import requests
import functools

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PIXEL_ALERT_CHANNEL = int(os.getenv("PIXEL_ALERT_CHANNEL"))
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
TRACKING_HOST = os.getenv("TRACKING_HOST")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Setup Brevo
configuration = sib_api_v3_sdk.Configuration()
configuration.api_key['api-key'] = BREVO_API_KEY
brevo_api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))

# ===== EMAIL LOGIC =====
def build_email_html(recipient_email, body_html):
    return f"""{body_html}<br><img src="{TRACKING_HOST}/track.png?email={recipient_email}" width="1" height="1">"""

def send_email_via_brevo(recipient, subject, body, attachment_path=None):
    html_content = build_email_html(recipient, body)
    email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": recipient}],
        sender={"email": SENDER_EMAIL, "name": "HR Softwarica"},
        subject=subject,
        html_content=html_content,
        headers={
            "X-Mailin-Track": "0",          # Disable open tracking
            "X-Mailin-Track-Clicks": "0"    # Disable click tracking (so links are not rewritten)
        }
    )

    if attachment_path:
        try:
            with open(attachment_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode()
                email.attachment = [sib_api_v3_sdk.SendSmtpEmailAttachment(
                    content=encoded,
                    name=os.path.basename(attachment_path)
                )]
        except Exception as e:
            print(f"[!] Attachment error: {e}")

    try:
        response = brevo_api.send_transac_email(email)
        print(f"✅ Sent! Message ID: {response.message_id}")
        return True
    except ApiException as e:
        print(f"❌ Email send failed: {e}")
        return False

# ===== DISCORD BOT =====
@bot.event
async def on_ready():
    print(f"[+] Logged in as {bot.user}")

@bot.command(name="sendmail")
async def sendmail(ctx):
    if ctx.author.id != ADMIN_ID:
        await ctx.send("❌ Not authorized.")
        return

    await ctx.send("📧 Enter recipient email:")
    recipient_msg = await bot.wait_for("message", timeout=60.0, check=lambda m: m.author == ctx.author)
    recipient = recipient_msg.content.strip()

    await ctx.send("✉️ Enter subject:")
    subject_msg = await bot.wait_for("message", timeout=60.0, check=lambda m: m.author == ctx.author)
    subject = subject_msg.content.strip()

    await ctx.send("📝 Enter HTML body:")
    body_msg = await bot.wait_for("message", timeout=120.0, check=lambda m: m.author == ctx.author)
    body = body_msg.content.strip()

    await ctx.send("📎 Upload attachment (or type `none` or paste a direct file link):")
    attachment_msg = await bot.wait_for("message", timeout=90.0, check=lambda m: m.author == ctx.author)
    attachment = None

    if attachment_msg.attachments:
        file = attachment_msg.attachments[0]
        filepath = f"./{file.filename}"
        await file.save(filepath)
        attachment = filepath

    elif attachment_msg.content.strip().lower() == "none":
        attachment = None

    elif attachment_msg.content.startswith("http://") or attachment_msg.content.startswith("https://"):
        try:
            url = attachment_msg.content.strip()
            filename = url.split("/")[-1].split("?")[0]
            await ctx.send("📥 Downloading file from URL...")

            r = requests.get(url, stream=True)
            content_type = r.headers.get("Content-Type", "")
            if "html" in content_type:
                await ctx.send("❌ That URL returned a webpage instead of a file. Please use a direct download link.")
                return

            with open(filename, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            attachment = filename
            await ctx.send(f"✅ Downloaded `{filename}` successfully.")
        except Exception as e:
            await ctx.send(f"❌ Failed to download file: {e}")
            return

    async def run_in_thread(func, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args))

    success = await run_in_thread(send_email_via_brevo, recipient, subject, body, attachment)
    await ctx.send("✅ Email sent!" if success else "❌ Email failed.")

# ===== PIXEL TRACKER =====
app = Flask(__name__)

@app.route("/track.png")
def track_pixel():
    email = request.args.get("email", "unknown")
    loop = asyncio.get_event_loop()
    if loop.is_running():
        asyncio.run_coroutine_threadsafe(alert_pixel_hit(email), loop)
    else:
        loop.run_until_complete(alert_pixel_hit(email))
    return send_file("pixel.gif", mimetype="image/gif")

async def alert_pixel_hit(email):
    channel = bot.get_channel(PIXEL_ALERT_CHANNEL)
    if channel:
        await channel.send(f"📡 Email opened by: `{email}`")

def run_tracker():
    app.run(host="0.0.0.0", port=5000)

# Start everything
Thread(target=run_tracker).start()
bot.run(DISCORD_TOKEN)

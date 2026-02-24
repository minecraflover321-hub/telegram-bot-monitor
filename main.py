import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from flask import Flask
import requests

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
CHECK_INTERVAL = 300  # 5 minutes
DATA_FILE = "data.json"

logging.basicConfig(level=logging.INFO)

# =========================
# FLASK KEEP ALIVE
# =========================

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# =========================
# DATA SYSTEM
# =========================

def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "users": {},
            "watch": {},
            "ban": {},
            "confirm": {}
        }
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

data = load_data()

# =========================
# ROLE SYSTEM
# =========================

def get_role(user_id):
    if str(user_id) == str(OWNER_ID):
        return "owner"
    return data["users"].get(str(user_id), {}).get("role", "user")

def subscription_active(user_id):
    user = data["users"].get(str(user_id))
    if not user:
        return False
    expiry = user.get("expiry")
    if not expiry:
        return False
    return datetime.utcnow() < datetime.fromisoformat(expiry)

# =========================
# INSTAGRAM CHECK
# =========================

def check_instagram(username):
    try:
        url = f"https://www.instagram.com/{username}/?__a=1&__d=dis"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return "ACTIVE"
        elif r.status_code == 404:
            return "BANNED"
        else:
            return "UNKNOWN"
    except:
        return "UNKNOWN"

# =========================
# MONITOR LOOP
# =========================

async def monitor_loop(app):
    while True:
        try:
            for user_id in list(data["watch"].keys()):
                for username in list(data["watch"][user_id]):
                    status = check_instagram(username)
                    key = f"{user_id}:{username}"

                    last = data["confirm"].get(key, {}).get("last")
                    count = data["confirm"].get(key, {}).get("count", 0)

                    if status == last and status in ["ACTIVE", "BANNED"]:
                        count += 1
                    else:
                        count = 1

                    data["confirm"][key] = {"last": status, "count": count}

                    if count >= 3:
                        if status == "BANNED":
                            data["watch"][user_id].remove(username)
                            data["ban"].setdefault(user_id, []).append(username)
                            await send_alert(app, user_id, username, "BANNED")
                        elif status == "ACTIVE" and username in data["ban"].get(user_id, []):
                            data["ban"][user_id].remove(username)
                            data["watch"].setdefault(user_id, []).append(username)
                            await send_alert(app, user_id, username, "UNBANNED")

                        data["confirm"][key] = {"last": None, "count": 0}

            save_data(data)

        except Exception as e:
            logging.error(f"Monitor error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)

# =========================
# ALERT MESSAGE
# =========================

async def send_alert(app, user_id, username, status):
    text = f"""
━━━━━━━━━━━━━━━
📊 ACCOUNT DETAILS
👤 Username: {username}
━━━━━━━━━━━━━━━

{"🚨 BANNED SUCCESSFULLY" if status == "BANNED" else "✅ UNBANNED SUCCESSFULLY"}

━━━━━━━━━━━━━━━
CHANNEL: @proxydominates
CONTACT: @proxyfxc
Powered by @proxyfxc
━━━━━━━━━━━━━━━
"""
    try:
        await app.bot.send_message(chat_id=int(user_id), text=text)
    except:
        pass

# =========================
# COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if user_id not in data["users"]:
        data["users"][user_id] = {
            "role": "user",
            "expiry": None
        }
        save_data(data)

    keyboard = [
        [InlineKeyboardButton("📋 Status", callback_data="status")]
    ]

    await update.message.reply_text(
        "🚀 Instagram Monitor Bot\nPowered by @proxyfxc",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if get_role(user_id) == "user":
        if not subscription_active(user_id):
            await update.message.reply_text("❌ Subscription expired.")
            return

        if len(data["watch"].get(user_id, [])) >= 20:
            await update.message.reply_text("⚠️ Watch limit reached (20).")
            return

    if not context.args:
        await update.message.reply_text("Usage: /watch username")
        return

    username = context.args[0]
    data["watch"].setdefault(user_id, []).append(username)
    save_data(data)

    await update.message.reply_text(f"✅ {username} added to Watch List.")

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if not context.args:
        await update.message.reply_text("Usage: /ban username")
        return

    username = context.args[0]
    data["ban"].setdefault(user_id, []).append(username)
    save_data(data)

    await update.message.reply_text(f"🚫 {username} added to Ban List.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    watch_list = data["watch"].get(user_id, [])
    ban_list = data["ban"].get(user_id, [])

    msg = f"""
📋 YOUR STATUS

👁 Watch List: {len(watch_list)}
🚫 Ban List: {len(ban_list)}

━━━━━━━━━━━━━━━
Powered by @proxyfxc
"""
    await update.message.reply_text(msg)

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_role(update.effective_user.id) not in ["owner", "admin"]:
        return

    if len(context.args) != 2:
        await update.message.reply_text("Usage: /approve user_id days")
        return

    user_id, days = context.args
    days = int(days)

    expiry = datetime.utcnow() + timedelta(days=days)

    data["users"].setdefault(user_id, {"role": "user"})
    data["users"][user_id]["expiry"] = expiry.isoformat()
    save_data(data)

    await update.message.reply_text("✅ Subscription approved.")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_role(update.effective_user.id) not in ["owner", "admin"]:
        return

    message = " ".join(context.args)
    for user_id in data["users"]:
        try:
            await context.bot.send_message(chat_id=int(user_id), text=message)
        except:
            pass

# =========================
# MAIN
# =========================

async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("watch", watch))
    application.add_handler(CommandHandler("ban", ban))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("approve", approve))
    application.add_handler(CommandHandler("broadcast", broadcast))

    application.create_task(monitor_loop(application))

    await application.run_polling()

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask).start()
    asyncio.run(main())

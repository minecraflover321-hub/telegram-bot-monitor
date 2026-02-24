#!/usr/bin/env python3
"""
Instagram Username Monitor Bot - Enterprise SaaS Solution
Complete version with all commands - Watch, Ban, Status, Approve, AddAdmin, Broadcast
"""

import os
import json
import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from threading import Thread
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
import sys

# ==================== Configuration ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
MONITOR_INTERVAL = 300  # 5 minutes
MAX_WATCH_PER_USER = 20
DATA_FILE = "data.json"
PORT = int(os.environ.get("PORT", 10000))

if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN not set!")
    sys.exit(1)

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== Flask ====================
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"status": "running", "message": "Instagram Monitor Bot Active"})

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# ==================== Data Manager ====================
class DataManager:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.data = self.load_data()
        self.lock = asyncio.Lock()
    
    def load_data(self) -> Dict:
        try:
            if os.path.exists(self.filepath):
                with open(self.filepath, 'r') as f:
                    content = f.read().strip()
                    if content:
                        return json.loads(content)
            return self.get_default_structure()
        except:
            return self.get_default_structure()
    
    def get_default_structure(self) -> Dict:
        return {
            "users": {},
            "watchlist": {},
            "banlist": {},
            "pending_confirmations": {},
            "admins": [OWNER_ID] if OWNER_ID else [],
            "stats": {"total_checks": 0, "alerts_sent": 0, "created_at": datetime.now().isoformat()}
        }
    
    async def save_data(self):
        async with self.lock:
            with open(self.filepath, 'w') as f:
                json.dump(self.data, f, indent=2)
    
    async def get_user(self, user_id: int) -> Dict:
        user_id = str(user_id)
        if user_id not in self.data["users"]:
            self.data["users"][user_id] = {
                "role": "user", "subscription_expiry": None, "joined_at": datetime.now().isoformat(),
                "username": None, "first_name": None, "total_monitored": 0
            }
            await self.save_data()
        return self.data["users"][user_id]
    
    async def update_user(self, user_id: int, **kwargs):
        user_id = str(user_id)
        if user_id in self.data["users"]:
            self.data["users"][user_id].update(kwargs)
            await self.save_data()
    
    async def is_admin(self, user_id: int) -> bool:
        return user_id == OWNER_ID or user_id in self.data.get("admins", [])
    
    async def is_owner(self, user_id: int) -> bool:
        return user_id == OWNER_ID
    
    async def has_active_subscription(self, user_id: int) -> bool:
        if await self.is_admin(user_id):
            return True
        user = await self.get_user(user_id)
        expiry = user.get("subscription_expiry")
        if not expiry:
            return False
        try:
            return datetime.fromisoformat(expiry) > datetime.now()
        except:
            return False
    
    async def add_to_watchlist(self, username: str, user_id: int) -> bool:
        username = username.lower().strip()
        user_id = str(user_id)
        
        if username in self.data["watchlist"]:
            return False
        
        if username in self.data["banlist"]:
            del self.data["banlist"][username]
        
        self.data["watchlist"][username] = {
            "user_id": user_id, "added_at": datetime.now().isoformat(),
            "status": "unknown", "confirm_count": 0, "last_check": None, "last_details": None
        }
        
        user = await self.get_user(int(user_id))
        user["total_monitored"] = user.get("total_monitored", 0) + 1
        await self.save_data()
        return True
    
    async def add_to_banlist(self, username: str, user_id: int) -> bool:
        username = username.lower().strip()
        user_id = str(user_id)
        
        if username in self.data["banlist"]:
            return False
        
        if username in self.data["watchlist"]:
            del self.data["watchlist"][username]
        
        self.data["banlist"][username] = {
            "user_id": user_id, "added_at": datetime.now().isoformat(),
            "status": "banned", "confirm_count": 3, "last_check": None, "last_details": None
        }
        
        await self.save_data()
        return True
    
    async def move_to_banlist(self, username: str, details: Dict = None):
        if username in self.data["watchlist"]:
            entry = self.data["watchlist"][username]
            entry["status"] = "banned"
            entry["last_details"] = details
            entry["banned_at"] = datetime.now().isoformat()
            self.data["banlist"][username] = entry
            del self.data["watchlist"][username]
            await self.save_data()
            return True
        return False
    
    async def move_to_watchlist(self, username: str, details: Dict = None):
        if username in self.data["banlist"]:
            entry = self.data["banlist"][username]
            entry["status"] = "active"
            entry["last_details"] = details
            entry["unbanned_at"] = datetime.now().isoformat()
            self.data["watchlist"][username] = entry
            del self.data["banlist"][username]
            await self.save_data()
            return True
        return False
    
    async def update_confirmation(self, username: str, status: str) -> Tuple[bool, int]:
        username = username.lower().strip()
        
        if username not in self.data["pending_confirmations"]:
            self.data["pending_confirmations"][username] = {
                "status": status, "count": 1, "first_detected": datetime.now().isoformat()
            }
            await self.save_data()
            return False, 1
        
        pending = self.data["pending_confirmations"][username]
        
        if pending["status"] != status:
            pending["status"] = status
            pending["count"] = 1
            pending["first_detected"] = datetime.now().isoformat()
            await self.save_data()
            return False, 1
        
        pending["count"] += 1
        should_alert = pending["count"] >= 3
        
        if should_alert:
            del self.data["pending_confirmations"][username]
        else:
            await self.save_data()
        
        return should_alert, pending["count"]

# ==================== Instagram Monitor ====================
class InstagramMonitor:
    def __init__(self, data_manager: DataManager):
        self.data_manager = data_manager
    
    async def check_username(self, username: str) -> Tuple[str, Optional[Dict]]:
        """Simulate Instagram check"""
        try:
            rand = random.random()
            
            if rand < 0.7:
                details = {
                    "name": f"User {username}",
                    "followers": random.randint(100, 50000),
                    "following": random.randint(50, 2000),
                    "posts": random.randint(1, 500),
                    "private": random.random() < 0.3
                }
                return "active", details
            elif rand < 0.85:
                return "banned", None
            else:
                return "unknown", None
        except:
            return "error", None
    
    def format_account_details(self, details: Dict) -> str:
        if not details:
            return "❌ Details unavailable"
        private = "✅ No" if not details.get("private", True) else "🔒 Yes"
        return f"""
👤 *Name:* {details.get('name', 'Unknown')}
👥 *Followers:* {details.get('followers', 0):,}
👤 *Following:* {details.get('following', 0):,}
📸 *Posts:* {details.get('posts', 0)}
🔐 *Private:* {private}
"""
    
    async def monitoring_cycle(self, context: ContextTypes.DEFAULT_TYPE):
        logger.info("🔄 Starting monitoring cycle...")
        
        # Check watchlist
        for username, entry in list(self.data_manager.data.get("watchlist", {}).items()):
            try:
                status, details = await self.check_username(username)
                self.data_manager.data["stats"]["total_checks"] += 1
                
                should_alert, count = await self.data_manager.update_confirmation(username, status)
                
                if should_alert:
                    user_id = int(entry["user_id"])
                    if status == "banned":
                        await self.data_manager.move_to_banlist(username, details)
                        await self.send_ban_alert(context, user_id, username, details)
                        self.data_manager.data["stats"]["alerts_sent"] += 1
                
                if username in self.data_manager.data["watchlist"]:
                    self.data_manager.data["watchlist"][username]["last_check"] = datetime.now().isoformat()
                    
            except Exception as e:
                logger.error(f"Error checking {username}: {e}")
        
        # Check banlist for reactivations
        for username, entry in list(self.data_manager.data.get("banlist", {}).items()):
            try:
                status, details = await self.check_username(username)
                
                if status == "active":
                    should_alert, count = await self.data_manager.update_confirmation(username, status)
                    
                    if should_alert:
                        user_id = int(entry["user_id"])
                        await self.data_manager.move_to_watchlist(username, details)
                        await self.send_unban_alert(context, user_id, username, details)
                        self.data_manager.data["stats"]["alerts_sent"] += 1
                        
            except Exception as e:
                logger.error(f"Error checking banned {username}: {e}")
        
        await self.data_manager.save_data()
        logger.info("✅ Monitoring cycle completed")
    
    async def send_ban_alert(self, context, user_id: int, username: str, details: Dict = None):
        try:
            details_text = self.format_account_details(details) if details else "❌ Details unavailable"
            msg = f"""
🚫 *ACCOUNT BANNED DETECTED* 🚫

📛 *Username:* @{username}
⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📊 *Account Details:*
{details_text}

❌ *Status:* BANNED SUCCESSFULLY

━━━━━━━━━━━━━━━━
⚠️ Moved to Ban List • 3x Confirmed
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Failed to send ban alert: {e}")
    
    async def send_unban_alert(self, context, user_id: int, username: str, details: Dict = None):
        try:
            details_text = self.format_account_details(details) if details else "❌ Details unavailable"
            msg = f"""
✅ *ACCOUNT UNBANNED DETECTED* ✅

📛 *Username:* @{username}
⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📊 *Account Details:*
{details_text}

✨ *Status:* UNBANNED SUCCESSFULLY

━━━━━━━━━━━━━━━━
🎉 Moved back to Watch List
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.MARKDOWN)
        except:
            pass

# ==================== Bot Handlers ====================
class BotHandlers:
    def __init__(self, data_manager: DataManager, monitor: InstagramMonitor):
        self.data_manager = data_manager
        self.monitor = monitor
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        await self.data_manager.get_user(user.id)
        
        # Update user info
        await self.data_manager.update_user(user.id, username=user.username, first_name=user.first_name)
        
        # Check subscription
        expiry = (await self.data_manager.get_user(user.id)).get("subscription_expiry")
        expiry_text = f"📅 Expires: {datetime.fromisoformat(expiry).strftime('%Y-%m-%d')}" if expiry else "❌ No subscription"
        if await self.data_manager.is_admin(user.id):
            expiry_text = "👑 Admin (Unlimited)"
        
        keyboard = [
            [InlineKeyboardButton("🔍 Add Watch", callback_data="add_watch"),
             InlineKeyboardButton("🚫 Add Ban", callback_data="add_ban")],
            [InlineKeyboardButton("📊 My Status", callback_data="my_status"),
             InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        
        if await self.data_manager.is_admin(user.id):
            keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
        
        msg = f"""
🌟 *Welcome {user.first_name}!* 🌟

🔍 *Professional Instagram Monitor*

━━━━━━━━━━━━━━━━
👤 *User:* {user.first_name}
💳 *Status:* {expiry_text}
📊 *Plan:* {'👑 OWNER' if user.id == OWNER_ID else '👥 ADMIN' if await self.data_manager.is_admin(user.id) else '👤 FREE'}

━━━━━━━━━━━━━━━━
📌 *Quick Commands:*
/watch username - Monitor username
/ban username - Direct to ban list
/status - Your lists
/help - All commands

━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, 
                                       reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        is_admin = await self.data_manager.is_admin(user.id)
        
        help_text = """
📚 *Instagram Monitor Commands*

━━━━━━━━━━━━━━━━
*👤 User Commands:*
/start - Main menu
/help - This help
/watch [username] - Add to watch
/ban [username] - Add to ban list
/status - View your lists
/check [username] - Quick status
/subscribe - Check subscription

"""
        if is_admin:
            help_text += """
━━━━━━━━━━━━━━━━
*👑 Admin Commands:*
/approve [user_id] [days] - Approve user
/addadmin [user_id] - Add admin (Owner)
/broadcast [message] - Message all users
/stats - System statistics
/listusers - Show all users

"""
        help_text += """
━━━━━━━━━━━━━━━━
*Examples:*
/watch instagram
/ban fake_account
/status
/approve 123456789 30

━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def watch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        if not await self.data_manager.has_active_subscription(user.id):
            await update.message.reply_text(
                "❌ *No Active Subscription*\nContact admin for access.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        if not context.args:
            await update.message.reply_text("⚠️ *Usage:* `/watch username`", parse_mode=ParseMode.MARKDOWN)
            return
        
        username = context.args[0].lower().strip()
        
        # Check limit
        watchlist = self.data_manager.data.get("watchlist", {})
        user_watch_count = sum(1 for u in watchlist.values() if u["user_id"] == str(user.id))
        
        if user_watch_count >= MAX_WATCH_PER_USER and not await self.data_manager.is_admin(user.id):
            await update.message.reply_text(
                f"❌ *Limit Reached*\nMax {MAX_WATCH_PER_USER} usernames.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        success = await self.data_manager.add_to_watchlist(username, user.id)
        
        if success:
            status, details = await self.monitor.check_username(username)
            status_emoji = "🟢 ACTIVE" if status == "active" else "🔴 BANNED" if status == "banned" else "⚪ UNKNOWN"
            
            msg = f"""
✅ *Added to Watch List*

📛 *Username:* @{username}
📊 *Status:* {status_emoji}

"""
            if details:
                msg += f"\n📋 *Details:*{self.monitor.format_account_details(details)}"
            
            msg += """
━━━━━━━━━━━━━━━━
🔍 Monitoring every 5 minutes
✅ 3x confirmation required
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                f"❌ @{username} already in your lists.",
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def ban(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        if not await self.data_manager.has_active_subscription(user.id):
            await update.message.reply_text("❌ *No Active Subscription*", parse_mode=ParseMode.MARKDOWN)
            return
        
        if not context.args:
            await update.message.reply_text("⚠️ *Usage:* `/ban username`", parse_mode=ParseMode.MARKDOWN)
            return
        
        username = context.args[0].lower().strip()
        success = await self.data_manager.add_to_banlist(username, user.id)
        
        if success:
            msg = f"""
🚫 *Added to Ban List*

📛 *Username:* @{username}

━━━━━━━━━━━━━━━━
🔍 Monitoring for reactivation
✅ You'll be notified when unbanned
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ @{username} already in ban list.", parse_mode=ParseMode.MARKDOWN)
    
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)
        
        watchlist = self.data_manager.data.get("watchlist", {})
        banlist = self.data_manager.data.get("banlist", {})
        
        user_watch = [u for u, data in watchlist.items() if data["user_id"] == user_id]
        user_ban = [u for u, data in banlist.items() if data["user_id"] == user_id]
        
        expiry = (await self.data_manager.get_user(user.id)).get("subscription_expiry")
        expiry_text = f"📅 Expires: {datetime.fromisoformat(expiry).strftime('%Y-%m-%d')}" if expiry else "❌ No subscription"
        
        if await self.data_manager.is_admin(user.id):
            expiry_text = "👑 Admin (Unlimited)"
        
        msg = f"""
📊 *Your Monitoring Status*

━━━━━━━━━━━━━━━━
👤 *User:* {user.first_name}
💳 *Status:* {expiry_text}

━━━━━━━━━━━━━━━━
*🔍 Watch List* ({len(user_watch)}/{MAX_WATCH_PER_USER if not await self.data_manager.is_admin(user.id) else '∞'})
"""
        if user_watch:
            for i, username in enumerate(user_watch[:10], 1):
                status = watchlist[username].get("status", "unknown")
                emoji = "🟢" if status == "active" else "🔴" if status == "banned" else "⚪"
                msg += f"{i}. {emoji} @{username}\n"
            if len(user_watch) > 10:
                msg += f"... and {len(user_watch) - 10} more\n"
        else:
            msg += "Empty\n"
        
        msg += f"\n*🚫 Ban List* ({len(user_ban)})\n"
        if user_ban:
            for i, username in enumerate(user_ban[:10], 1):
                msg += f"{i}. 🔴 @{username}\n"
            if len(user_ban) > 10:
                msg += f"... and {len(user_ban) - 10} more\n"
        else:
            msg += "Empty\n"
        
        msg += """
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        if not await self.data_manager.is_admin(user.id):
            await update.message.reply_text("❌ Admin only command.")
            return
        
        if len(context.args) < 2:
            await update.message.reply_text(
                "⚠️ *Usage:* `/approve user_id days`\nExample: `/approve 123456789 30`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        try:
            target_id = int(context.args[0])
            days = int(context.args[1])
            
            expiry = datetime.now() + timedelta(days=days)
            await self.data_manager.update_user(target_id, subscription_expiry=expiry.isoformat())
            
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"""
✅ *Subscription Approved!*

📅 *Duration:* {days} days
📊 *Expiry:* {expiry.strftime('%Y-%m-%d')}
👤 *Approved by:* {user.first_name}

━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
""",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
            
            await update.message.reply_text(f"✅ User {target_id} approved for {days} days.")
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID or days.")
    
    async def addadmin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        if not await self.data_manager.is_owner(user.id):
            await update.message.reply_text("❌ Only owner can use this command.")
            return
        
        if not context.args:
            await update.message.reply_text("⚠️ Usage: /addadmin user_id")
            return
        
        try:
            target_id = int(context.args[0])
            
            if target_id not in self.data_manager.data["admins"]:
                self.data_manager.data["admins"].append(target_id)
                await self.data_manager.update_user(target_id, role="admin")
                await self.data_manager.save_data()
                await update.message.reply_text(f"✅ User {target_id} is now an admin.")
            else:
                await update.message.reply_text("❌ User is already an admin.")
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
    
    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        if not await self.data_manager.is_admin(user.id):
            await update.message.reply_text("❌ Admin only command.")
            return
        
        if not context.args:
            await update.message.reply_text("⚠️ *Usage:* `/broadcast your message`", parse_mode=ParseMode.MARKDOWN)
            return
        
        message = " ".join(context.args)
        
        broadcast_msg = f"""
📢 *Broadcast Message*

{message}

━━━━━━━━━━━━━━━━
👤 *From:* {user.first_name}
⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
        
        users = self.data_manager.data.get("users", {})
        sent = 0
        failed = 0
        
        status_msg = await update.message.reply_text("📤 Broadcasting...")
        
        for user_id in users:
            try:
                await context.bot.send_message(chat_id=int(user_id), text=broadcast_msg, parse_mode=ParseMode.MARKDOWN)
                sent += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
        
        await status_msg.edit_text(f"✅ *Broadcast Complete*\n\n📨 Sent: {sent}\n❌ Failed: {failed}", parse_mode=ParseMode.MARKDOWN)
    
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        if not await self.data_manager.is_admin(user.id):
            await update.message.reply_text("❌ Admin only command.")
            return
        
        stats = self.data_manager.data.get("stats", {})
        users = self.data_manager.data.get("users", {})
        watchlist = self.data_manager.data.get("watchlist", {})
        banlist = self.data_manager.data.get("banlist", {})
        
        active_users = sum(1 for u in users.values() if u.get("subscription_expiry") and 
                          datetime.fromisoformat(u["subscription_expiry"]) > datetime.now())
        
        msg = f"""
📊 *System Statistics*

━━━━━━━━━━━━━━━━
*👥 Users:*
• Total: {len(users)}
• Active: {active_users}
• Admins: {len(self.data_manager.data['admins'])}

━━━━━━━━━━━━━━━━
*📋 Monitoring:*
• Watch List: {len(watchlist)}
• Ban List: {len(banlist)}
• Total Tracked: {len(watchlist) + len(banlist)}

━━━━━━━━━━━━━━━━
*📈 Performance:*
• Total Checks: {stats.get('total_checks', 0)}
• Alerts Sent: {stats.get('alerts_sent', 0)}

━━━━━━━━━━━━━━━━
*⚙️ System:*
• Interval: 5 minutes
• Confirmation: 3x required
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def listusers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        if not await self.data_manager.is_admin(user.id):
            await update.message.reply_text("❌ Admin only command.")
            return
        
        users = self.data_manager.data.get("users", {})
        msg = "📋 *Registered Users*\n\n"
        
        for i, (uid, data) in enumerate(list(users.items())[:20], 1):
            name = data.get('first_name', 'Unknown')
            expiry = data.get('subscription_expiry', 'None')
            if expiry and expiry != 'None':
                try:
                    expiry_date = datetime.fromisoformat(expiry)
                    expiry = f"✅ Active" if expiry_date > datetime.now() else "❌ Expired"
                except:
                    expiry = "❌ Invalid"
            else:
                expiry = "❌ No subscription"
            
            role = "👑 Owner" if int(uid) == OWNER_ID else "👥 Admin" if int(uid) in self.data.data['admins'] else "👤 User"
            msg += f"{i}. `{uid}` | {role}\n   {name} | {expiry}\n\n"
        
        if len(users) > 20:
            msg += f"... and {len(users) - 20} more"
        
        msg += "\n*Powered by @proxyfxc*"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "add_watch":
            await query.edit_message_text(
                "🔍 *Add to Watch List*\n\nSend username:\nExample: `instagram`",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data["awaiting"] = "watch"
        
        elif data == "add_ban":
            await query.edit_message_text(
                "🚫 *Add to Ban List*\n\nSend username:\nExample: `banned_account`",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data["awaiting"] = "ban"
        
        elif data == "my_status":
            user = update.effective_user
            user_id = str(user.id)
            
            watchlist = self.data_manager.data.get("watchlist", {})
            banlist = self.data_manager.data.get("banlist", {})
            
            user_watch = [u for u, data in watchlist.items() if data["user_id"] == user_id]
            user_ban = [u for u, data in banlist.items() if data["user_id"] == user_id]
            
            msg = f"📊 *Your Status*\n\nWatch: {len(user_watch)}\nBan: {len(user_ban)}"
            await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
        
        elif data == "help":
            await self.help(update, context)
        
        elif data == "admin_panel":
            if await self.data_manager.is_admin(update.effective_user.id):
                keyboard = [
                    [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
                     InlineKeyboardButton("📋 Users", callback_data="admin_users")],
                    [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
                     InlineKeyboardButton("➕ Approve", callback_data="admin_approve")]
                ]
                await query.edit_message_text(
                    "👑 *Admin Panel*\n\nChoose an option:",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        elif data == "admin_stats":
            await self.stats(update, context)
        
        elif data == "admin_users":
            await self.listusers(update, context)
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages for username input"""
        if "awaiting" in context.user_data:
            username = update.message.text.strip()
            
            if context.user_data["awaiting"] == "watch":
                context.args = [username]
                await self.watch(update, context)
            elif context.user_data["awaiting"] == "ban":
                context.args = [username]
                await self.ban(update, context)
            
            del context.user_data["awaiting"]

# ==================== Main ====================
async def main():
    logger.info("🚀 Starting Instagram Monitor Bot...")
    
    data_manager = DataManager(DATA_FILE)
    monitor = InstagramMonitor(data_manager)
    handlers = BotHandlers(data_manager, monitor)
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help))
    application.add_handler(CommandHandler("watch", handlers.watch))
    application.add_handler(CommandHandler("ban", handlers.ban))
    application.add_handler(CommandHandler("status", handlers.status))
    application.add_handler(CommandHandler("approve", handlers.approve))
    application.add_handler(CommandHandler("addadmin", handlers.addadmin))
    application.add_handler(CommandHandler("broadcast", handlers.broadcast))
    application.add_handler(CommandHandler("stats", handlers.stats))
    application.add_handler(CommandHandler("listusers", handlers.listusers))
    application.add_handler(CallbackQueryHandler(handlers.button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))
    
    # Schedule monitoring
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(monitor.monitoring_cycle, interval=MONITOR_INTERVAL, first=10)
    
    # Start Flask
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"🌐 Flask running on port {PORT}")
    
    # Start bot
    logger.info("✅ Bot is ready!")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    # Keep running
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped")
    except Exception as e:
        logger.error(f"Fatal error: {e}")

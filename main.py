import os
import json
import asyncio
import aiofiles
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from threading import Thread
import requests
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from telegram.constants import ParseMode
import random
import string

# ==================== CONFIGURATION ====================
# Environment Variables (set these on Render)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))  # Your Telegram ID
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "").split(",") if id]
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "@proxydominates")
CONTACT_USERNAME = os.environ.get("CONTACT_USERNAME", "@proxyfxc")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # For Render

# Constants
DATA_FILE = "data.json"
MONITOR_INTERVAL = 300  # 5 minutes in seconds
MAX_WATCHLIST_PER_USER = 20
CONFIRMATION_THRESHOLD = 3  # 3 consecutive checks needed for status change

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== FLASK KEEP-ALIVE SERVER ====================
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({
        "status": "active",
        "timestamp": datetime.now().isoformat(),
        "channel": CHANNEL_USERNAME,
        "contact": CONTACT_USERNAME
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ==================== DATABASE MANAGER ====================
class Database:
    def __init__(self):
        self.data = self.load_data()
        
    def load_data(self) -> dict:
        """Load data from JSON file with proper structure"""
        default_data = {
            "users": {},  # user_id: {"role": "user", "expiry": "YYYY-MM-DD", "username": "..."}
            "watchlist": {},  # username: {"user_id": int, "added": "YYYY-MM-DD", "status": str}
            "banlist": {},  # username: {"user_id": int, "banned_date": "YYYY-MM-DD"}
            "confirmations": {},  # username: {"status": str, "count": int, "last_check": "YYYY-MM-DD"}
            "admins": [OWNER_ID] + ADMIN_IDS,  # List of admin IDs
            "owner": OWNER_ID,
            "stats": {
                "total_checks": 0,
                "total_alerts": 0,
                "created": datetime.now().isoformat()
            }
        }
        
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r') as f:
                    loaded_data = json.load(f)
                    # Merge with default structure to ensure all keys exist
                    for key in default_data:
                        if key not in loaded_data:
                            loaded_data[key] = default_data[key]
                    return loaded_data
            else:
                self.save_data(default_data)
                return default_data
        except Exception as e:
            logger.error(f"Error loading database: {e}")
            return default_data
    
    def save_data(self, data=None):
        """Save data to JSON file"""
        if data:
            self.data = data
        try:
            with open(DATA_FILE, 'w') as f:
                json.dump(self.data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error saving database: {e}")
    
    def get_user(self, user_id: int) -> dict:
        """Get user data or create default"""
        user_id = str(user_id)
        if user_id not in self.data["users"]:
            self.data["users"][user_id] = {
                "role": "user",
                "expiry": None,
                "username": None,
                "joined": datetime.now().isoformat(),
                "total_alerts": 0
            }
            self.save_data()
        return self.data["users"][user_id]
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin or owner"""
        return user_id in self.data["admins"] or user_id == self.data["owner"]
    
    def is_owner(self, user_id: int) -> bool:
        """Check if user is owner"""
        return user_id == self.data["owner"]
    
    def has_active_subscription(self, user_id: int) -> bool:
        """Check if user has active subscription"""
        user = self.get_user(user_id)
        if self.is_admin(user_id):
            return True  # Admins always have access
        if not user.get("expiry"):
            return False
        expiry = datetime.fromisoformat(user["expiry"])
        return expiry > datetime.now()
    
    def add_to_watchlist(self, username: str, user_id: int) -> bool:
        """Add username to watchlist"""
        username = username.lower().strip()
        user_id = str(user_id)
        
        # Check if already in watchlist
        if username in self.data["watchlist"]:
            return False
        
        # Check if in banlist
        if username in self.data["banlist"]:
            return False
        
        self.data["watchlist"][username] = {
            "user_id": user_id,
            "added": datetime.now().isoformat(),
            "status": "pending"
        }
        self.save_data()
        return True
    
    def add_to_banlist(self, username: str, user_id: int) -> bool:
        """Add username to banlist"""
        username = username.lower().strip()
        user_id = str(user_id)
        
        # Remove from watchlist if present
        if username in self.data["watchlist"]:
            del self.data["watchlist"][username]
        
        # Remove from confirmations
        if username in self.data["confirmations"]:
            del self.data["confirmations"][username]
        
        self.data["banlist"][username] = {
            "user_id": user_id,
            "banned_date": datetime.now().isoformat(),
            "status": "banned"
        }
        self.save_data()
        return True
    
    def remove_from_watchlist(self, username: str) -> bool:
        """Remove username from watchlist"""
        username = username.lower().strip()
        if username in self.data["watchlist"]:
            del self.data["watchlist"][username]
            if username in self.data["confirmations"]:
                del self.data["confirmations"][username]
            self.save_data()
            return True
        return False
    
    def remove_from_banlist(self, username: str) -> bool:
        """Remove username from banlist"""
        username = username.lower().strip()
        if username in self.data["banlist"]:
            del self.data["banlist"][username]
            self.save_data()
            return True
        return False
    
    def move_to_banlist(self, username: str) -> Optional[dict]:
        """Move username from watchlist to banlist"""
        username = username.lower().strip()
        if username in self.data["watchlist"]:
            user_id = self.data["watchlist"][username]["user_id"]
            self.add_to_banlist(username, int(user_id))
            return {"user_id": int(user_id), "username": username}
        return None
    
    def move_to_watchlist(self, username: str) -> Optional[dict]:
        """Move username from banlist to watchlist"""
        username = username.lower().strip()
        if username in self.data["banlist"]:
            user_id = self.data["banlist"][username]["user_id"]
            self.add_to_watchlist(username, int(user_id))
            del self.data["banlist"][username]
            self.save_data()
            return {"user_id": int(user_id), "username": username}
        return None
    
    def update_confirmation(self, username: str, status: str) -> Tuple[bool, int]:
        """Update confirmation counter for username"""
        username = username.lower().strip()
        confirmations = self.data["confirmations"]
        
        if username not in confirmations:
            confirmations[username] = {
                "status": status,
                "count": 1,
                "last_check": datetime.now().isoformat()
            }
            self.save_data()
            return False, 1
        
        # If status changed, reset counter
        if confirmations[username]["status"] != status:
            confirmations[username] = {
                "status": status,
                "count": 1,
                "last_check": datetime.now().isoformat()
            }
            self.save_data()
            return False, 1
        
        # Same status, increment counter
        confirmations[username]["count"] += 1
        confirmations[username]["last_check"] = datetime.now().isoformat()
        count = confirmations[username]["count"]
        confirmed = count >= CONFIRMATION_THRESHOLD
        
        if confirmed:
            # Reset counter after confirmation
            del confirmations[username]
        else:
            self.save_data()
        
        return confirmed, count
    
    def get_user_watchlist(self, user_id: int) -> List[str]:
        """Get all usernames watched by a specific user"""
        user_id = str(user_id)
        return [username for username, data in self.data["watchlist"].items() 
                if data["user_id"] == user_id]
    
    def get_user_banlist(self, user_id: int) -> List[str]:
        """Get all usernames banned by a specific user"""
        user_id = str(user_id)
        return [username for username, data in self.data["banlist"].items() 
                if data["user_id"] == user_id]
    
    def get_all_users(self) -> List[int]:
        """Get all registered user IDs"""
        return [int(uid) for uid in self.data["users"].keys()]

# Initialize database
db = Database()

# ==================== INSTAGRAM SIMULATOR (Replace with actual API) ====================
class InstagramChecker:
    """Simulated Instagram checker - Replace with actual Instagram API"""
    
    @staticmethod
    async def check_username(username: str) -> Tuple[str, dict]:
        """
        Check Instagram username status
        Returns: (status, details)
        status: 'ACTIVE', 'BANNED', 'UNKNOWN', 'PRIVATE', 'PUBLIC'
        """
        # Simulate API delay
        await asyncio.sleep(1)
        
        # This is a SIMULATOR - Replace with actual Instagram API
        # For production, implement real Instagram checking logic here
        
        # Simulate different statuses for demonstration
        # In production, you would call Instagram's API
        import hashlib
        hash_val = int(hashlib.md5(username.encode()).hexdigest()[:8], 16)
        
        # Simulated response - Replace this with actual Instagram API call
        if hash_val % 10 == 0:
            status = "BANNED"
            details = {
                "name": f"User {username}",
                "followers": random.randint(0, 50000),
                "following": random.randint(0, 1000),
                "posts": random.randint(0, 500),
                "private": False
            }
        elif hash_val % 7 == 0:
            status = "PRIVATE"
            details = {
                "name": f"Private User",
                "followers": "Hidden",
                "following": "Hidden",
                "posts": "Hidden",
                "private": True
            }
        else:
            status = "ACTIVE"
            details = {
                "name": f"@{username}".title(),
                "followers": random.randint(100, 100000),
                "following": random.randint(50, 2000),
                "posts": random.randint(10, 2000),
                "private": False
            }
        
        return status, details

# ==================== TELEGRAM BOT HANDLERS ====================
class BotHandlers:
    def __init__(self):
        self.checker = InstagramChecker()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Welcome message with professional UI"""
        user = update.effective_user
        db.get_user(user.id)
        
        welcome_text = f"""
🌟 *WELCOME TO INSTA MONITOR PRO* 🌟
━━━━━━━━━━━━━━━━━━━━━
🔰 *User*: {user.mention_html()}
🆔 *ID*: `{user.id}`
📊 *Status*: {'⭐ ADMIN' if db.is_admin(user.id) else '👤 USER'}
💎 *Subscription*: {'✅ ACTIVE' if db.has_active_subscription(user.id) else '❌ INACTIVE'}

🚀 *Your Ultimate Instagram Monitoring Tool*
• Real-time username tracking
• Auto-detection of bans/unbans
• 3-step confirmation system
• Professional alerts

━━━━━━━━━━━━━━━━━━━━━
📢 Channel: {CHANNEL_USERNAME}
📞 Contact: {CONTACT_USERNAME}
━━━━━━━━━━━━━━━━━━━━━

👇 *Select an option below* 👇
        """
        
        keyboard = [
            [InlineKeyboardButton("➕ Add to Watchlist", callback_data="add_watch")],
            [InlineKeyboardButton("📋 My Watchlist", callback_data="view_watch"),
             InlineKeyboardButton("🚫 My Banlist", callback_data="view_ban")],
            [InlineKeyboardButton("📊 Account Status", callback_data="status"),
             InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
        ]
        
        if db.is_admin(user.id):
            keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button callbacks"""
        query = update.callback_query
        await query.answer()
        
        user = update.effective_user
        data = query.data
        
        if data == "add_watch":
            await query.edit_message_text(
                "📝 *Add to Watchlist*\n\n"
                "Send me the Instagram username you want to monitor.\n"
                "Example: `therock` or `@therock`\n\n"
                "_I'll remove the @ automatically_",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['awaiting'] = 'watch_username'
        
        elif data == "view_watch":
            watchlist = db.get_user_watchlist(user.id)
            if not watchlist:
                await query.edit_message_text(
                    "📋 *Your Watchlist*\n\n"
                    "✨ Your watchlist is empty!\n"
                    "Use the button below to add usernames.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("➕ Add Username", callback_data="add_watch")
                    ]])
                )
                return
            
            text = "📋 *YOUR WATCHLIST*\n"
            text += "━━━━━━━━━━━━━━━━━━━━━\n"
            
            for i, username in enumerate(watchlist, 1):
                text += f"{i}. `@{username}`\n"
            
            text += f"\n📊 *Total*: {len(watchlist)}/{MAX_WATCHLIST_PER_USER}\n"
            text += "━━━━━━━━━━━━━━━━━━━━━"
            
            keyboard = [
                [InlineKeyboardButton("➕ Add More", callback_data="add_watch"),
                 InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
            ]
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data == "view_ban":
            banlist = db.get_user_banlist(user.id)
            if not banlist:
                await query.edit_message_text(
                    "🚫 *Your Banlist*\n\n"
                    "No banned usernames found.\n"
                    "Usernames that get banned will appear here.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")
                    ]])
                )
                return
            
            text = "🚫 *YOUR BANLIST*\n"
            text += "━━━━━━━━━━━━━━━━━━━━━\n"
            
            for i, username in enumerate(banlist, 1):
                text += f"{i}. `@{username}`\n"
            
            text += f"\n📊 *Total Banned*: {len(banlist)}\n"
            text += "━━━━━━━━━━━━━━━━━━━━━"
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")
                ]])
            )
        
        elif data == "status":
            watchlist = db.get_user_watchlist(user.id)
            banlist = db.get_user_banlist(user.id)
            
            text = f"""
📊 *ACCOUNT STATUS*
━━━━━━━━━━━━━━━━━━━━━
👤 *User*: {user.mention_html()}
🆔 *ID*: `{user.id}`

📋 *Watchlist*: {len(watchlist)}/{MAX_WATCHLIST_PER_USER}
🚫 *Banlist*: {len(banlist)}

💎 *Subscription*
• Status: {'✅ Active' if db.has_active_subscription(user.id) else '❌ Inactive'}
• Expiry: {db.get_user(user.id).get('expiry', 'Not Set')}

━━━━━━━━━━━━━━━━━━━━━
            """
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")
                ]])
            )
        
        elif data == "settings":
            text = f"""
⚙️ *SETTINGS*
━━━━━━━━━━━━━━━━━━━━━
🔔 *Notifications*: Enabled
⏱️ *Check Interval*: 5 Minutes
✅ *Confirmation Steps*: {CONFIRMATION_THRESHOLD}
📊 *Max Watchlist*: {MAX_WATCHLIST_PER_USER}

━━━━━━━━━━━━━━━━━━━━━
📢 Channel: {CHANNEL_USERNAME}
📞 Contact: {CONTACT_USERNAME}
━━━━━━━━━━━━━━━━━━━━━
            """
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")
                ]])
            )
        
        elif data == "admin_panel":
            if not db.is_admin(user.id):
                await query.edit_message_text("⛔ Access Denied")
                return
            
            text = f"""
👑 *ADMIN PANEL*
━━━━━━━━━━━━━━━━━━━━━
👤 *Admin*: {user.mention_html()}
🆔 *ID*: `{user.id}`

📊 *System Stats*
• Total Users: {len(db.get_all_users())}
• Watchlist Size: {len(db.data['watchlist'])}
• Banlist Size: {len(db.data['banlist'])}
• Total Checks: {db.data['stats']['total_checks']}
• Total Alerts: {db.data['stats']['total_alerts']}

━━━━━━━━━━━━━━━━━━━━━
            """
            
            keyboard = [
                [InlineKeyboardButton("✅ Approve User", callback_data="admin_approve")],
                [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
                [InlineKeyboardButton("👥 User List", callback_data="admin_users")],
                [InlineKeyboardButton("➕ Add Admin", callback_data="admin_add")],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
            ]
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data == "main_menu":
            await self.start(update, context)
        
        elif data == "admin_approve":
            if not db.is_admin(user.id):
                return
            await query.edit_message_text(
                "✅ *Approve User*\n\n"
                "Send me the User ID to approve and days to add.\n"
                "Format: `USER_ID DAYS`\n"
                "Example: `123456789 30`",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['awaiting'] = 'admin_approve'
        
        elif data == "admin_broadcast":
            if not db.is_admin(user.id):
                return
            await query.edit_message_text(
                "📢 *Broadcast Message*\n\n"
                "Send me the message you want to broadcast to all users.\n\n"
                "_Supported formatting: HTML_",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['awaiting'] = 'admin_broadcast'
        
        elif data == "admin_add":
            if not db.is_owner(user.id):
                await query.edit_message_text("⛔ Only Owner can add admins")
                return
            await query.edit_message_text(
                "👑 *Add Admin*\n\n"
                "Send me the User ID to make admin:\n"
                "Example: `123456789`",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['awaiting'] = 'admin_add'
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages"""
        user = update.effective_user
        text = update.message.text.strip()
        
        if 'awaiting' not in context.user_data:
            await update.message.reply_text("Please use the buttons to navigate.")
            return
        
        action = context.user_data['awaiting']
        
        if action == 'watch_username':
            # Clean username
            username = text.lower().replace('@', '').strip()
            
            if not username:
                await update.message.reply_text("❌ Invalid username!")
                return
            
            # Check subscription
            if not db.has_active_subscription(user.id) and not db.is_admin(user.id):
                await update.message.reply_text(
                    "❌ *Subscription Required*\n\n"
                    "Your subscription has expired. Please contact an admin to renew.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Check limit
            watchlist = db.get_user_watchlist(user.id)
            if len(watchlist) >= MAX_WATCHLIST_PER_USER and not db.is_admin(user.id):
                await update.message.reply_text(
                    f"❌ *Limit Reached*\n\n"
                    f"You've reached the maximum of {MAX_WATCHLIST_PER_USER} usernames.\n"
                    f"Remove some to add more.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Add to watchlist
            if db.add_to_watchlist(username, user.id):
                await update.message.reply_text(
                    f"✅ *Username Added*\n\n"
                    f"`@{username}` has been added to your watchlist.\n"
                    f"I'll monitor it every 5 minutes!",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("📋 View Watchlist", callback_data="view_watch")
                    ]])
                )
            else:
                await update.message.reply_text(
                    f"❌ *Failed to Add*\n\n"
                    f"`@{username}` is already in your watchlist or banlist.",
                    parse_mode=ParseMode.MARKDOWN
                )
            
            context.user_data.pop('awaiting')
        
        elif action == 'admin_approve':
            if not db.is_admin(user.id):
                return
            
            try:
                target_id, days = text.split()
                target_id = int(target_id)
                days = int(days)
                
                expiry = datetime.now() + timedelta(days=days)
                user_data = db.get_user(target_id)
                user_data['expiry'] = expiry.isoformat()
                db.save_data()
                
                await update.message.reply_text(
                    f"✅ *User Approved*\n\n"
                    f"User ID: `{target_id}`\n"
                    f"Days Added: {days}\n"
                    f"Expiry: {expiry.strftime('%Y-%m-%d')}",
                    parse_mode=ParseMode.MARKDOWN
                )
                
                # Notify user
                try:
                    await context.bot.send_message(
                        target_id,
                        f"✅ *Subscription Activated*\n\n"
                        f"Your subscription has been approved for {days} days!\n"
                        f"Expires: {expiry.strftime('%Y-%m-%d')}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
                
            except Exception as e:
                await update.message.reply_text(
                    "❌ Invalid format. Use: `USER_ID DAYS`",
                    parse_mode=ParseMode.MARKDOWN
                )
            
            context.user_data.pop('awaiting')
        
        elif action == 'admin_broadcast':
            if not db.is_admin(user.id):
                return
            
            await update.message.reply_text(
                "📢 *Broadcasting...*\n\n"
                "Sending message to all users. This may take a while.",
                parse_mode=ParseMode.MARKDOWN
            )
            
            users = db.get_all_users()
            success = 0
            failed = 0
            
            for target_id in users:
                try:
                    await context.bot.send_message(
                        target_id,
                        f"📢 *BROADCAST MESSAGE*\n\n{text}",
                        parse_mode=ParseMode.HTML
                    )
                    success += 1
                    await asyncio.sleep(0.05)  # Small delay to avoid flooding
                except:
                    failed += 1
            
            await update.message.reply_text(
                f"📊 *Broadcast Complete*\n\n"
                f"✅ Success: {success}\n"
                f"❌ Failed: {failed}\n"
                f"📊 Total: {len(users)}",
                parse_mode=ParseMode.MARKDOWN
            )
            
            context.user_data.pop('awaiting')
        
        elif action == 'admin_add':
            if not db.is_owner(user.id):
                return
            
            try:
                target_id = int(text)
                if target_id not in db.data['admins']:
                    db.data['admins'].append(target_id)
                    db.save_data()
                    
                    await update.message.reply_text(
                        f"✅ *Admin Added*\n\n"
                        f"User ID: `{target_id}` is now an admin.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    
                    # Notify new admin
                    try:
                        await context.bot.send_message(
                            target_id,
                            f"👑 *You are now an Admin!*\n\n"
                            f"You have access to admin features.",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except:
                        pass
                else:
                    await update.message.reply_text("❌ User is already an admin.")
                    
            except:
                await update.message.reply_text("❌ Invalid User ID")
            
            context.user_data.pop('awaiting')

# ==================== MONITORING ENGINE ====================
class MonitoringEngine:
    def __init__(self, application: Application):
        self.application = application
        self.checker = InstagramChecker()
        self.is_running = False
    
    async def monitor_loop(self):
        """Main monitoring loop - runs every 5 minutes"""
        while True:
            try:
                if not self.is_running:
                    self.is_running = True
                
                logger.info("Starting monitoring cycle...")
                
                # Check all usernames in watchlist
                watchlist = db.data['watchlist'].copy()
                
                for username, data in watchlist.items():
                    try:
                        # Check Instagram status
                        status, details = await self.checker.check_username(username)
                        
                        # Update stats
                        db.data['stats']['total_checks'] += 1
                        
                        # Update confirmation counter
                        confirmed, count = db.update_confirmation(username, status)
                        
                        if confirmed:
                            # Status confirmed - take action
                            if status == "BANNED":
                                # Move to banlist and notify
                                moved_data = db.move_to_banlist(username)
                                if moved_data:
                                    await self.send_ban_alert(
                                        moved_data['user_id'],
                                        username,
                                        details
                                    )
                                    db.data['stats']['total_alerts'] += 1
                            
                            elif status == "ACTIVE":
                                # Check if in banlist and move back
                                if username in db.data['banlist']:
                                    moved_data = db.move_to_watchlist(username)
                                    if moved_data:
                                        await self.send_unban_alert(
                                            moved_data['user_id'],
                                            username,
                                            details
                                        )
                                        db.data['stats']['total_alerts'] += 1
                        
                        # Small delay between checks
                        await asyncio.sleep(0.5)
                        
                    except Exception as e:
                        logger.error(f"Error checking {username}: {e}")
                        continue
                
                # Check banlist for unbans
                banlist = db.data['banlist'].copy()
                
                for username, data in banlist.items():
                    try:
                        status, details = await self.checker.check_username(username)
                        
                        if status == "ACTIVE":
                            # Check confirmation
                            confirmed, count = db.update_confirmation(username, status)
                            
                            if confirmed:
                                moved_data = db.move_to_watchlist(username)
                                if moved_data:
                                    await self.send_unban_alert(
                                        moved_data['user_id'],
                                        username,
                                        details
                                    )
                                    db.data['stats']['total_alerts'] += 1
                        
                        await asyncio.sleep(0.5)
                        
                    except Exception as e:
                        logger.error(f"Error checking banned {username}: {e}")
                        continue
                
                # Save database after cycle
                db.save_data()
                
                logger.info("Monitoring cycle completed. Next check in 5 minutes.")
                
                # Wait for next cycle
                await asyncio.sleep(MONITOR_INTERVAL)
                
            except Exception as e:
                logger.error(f"Critical error in monitor loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error
    
    async def send_ban_alert(self, user_id: int, username: str, details: dict):
        """Send ban alert with professional formatting"""
        try:
            alert_text = f"""
🚫 *ACCOUNT BANNED* 🚫
━━━━━━━━━━━━━━━━━━━━━
👤 *Username*: `@{username}`

📊 *ACCOUNT DETAILS*
━━━━━━━━━━━━━━━━━━━━━
👤 Name: {details.get('name', 'N/A')}
👥 Followers: {details.get('followers', 'N/A')}
👤 Following: {details.get('following', 'N/A')}
📸 Posts: {details.get('posts', 'N/A')}
🔐 Private: {'Yes' if details.get('private') else 'No'}

━━━━━━━━━━━━━━━━━━━━━
⚠️ *STATUS: BANNED SUCCESSFULLY*
━━━━━━━━━━━━━━━━━━━━━
⏱️ Detected: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📢 Channel: {CHANNEL_USERNAME}
📞 Contact: {CONTACT_USERNAME}
━━━━━━━━━━━━━━━━━━━━━
            """
            
            await self.application.bot.send_message(
                user_id,
                alert_text,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Failed to send ban alert to {user_id}: {e}")
    
    async def send_unban_alert(self, user_id: int, username: str, details: dict):
        """Send unban alert with professional formatting"""
        try:
            alert_text = f"""
✅ *ACCOUNT UNBANNED* ✅
━━━━━━━━━━━━━━━━━━━━━
👤 *Username*: `@{username}`

📊 *ACCOUNT DETAILS*
━━━━━━━━━━━━━━━━━━━━━
👤 Name: {details.get('name', 'N/A')}
👥 Followers: {details.get('followers', 'N/A')}
👤 Following: {details.get('following', 'N/A')}
📸 Posts: {details.get('posts', 'N/A')}
🔐 Private: {'Yes' if details.get('private') else 'No'}

━━━━━━━━━━━━━━━━━━━━━
✨ *STATUS: UNBANNED SUCCESSFULLY*
━━━━━━━━━━━━━━━━━━━━━
⏱️ Detected: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📢 Channel: {CHANNEL_USERNAME}
📞 Contact: {CONTACT_USERNAME}
━━━━━━━━━━━━━━━━━━━━━
            """
            
            await self.application.bot.send_message(
                user_id,
                alert_text,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Failed to send unban alert to {user_id}: {e}")
    
    async def start_monitoring(self):
        """Start the monitoring loop"""
        asyncio.create_task(self.monitor_loop())
        logger.info("Monitoring engine started")

# ==================== MAIN APPLICATION ====================
async def post_init(application: Application):
    """Post initialization hook"""
    # Set bot commands
    commands = [
        ("start", "🚀 Start the bot"),
        ("watch", "➕ Add to watchlist"),
        ("banlist", "🚫 View banlist"),
        ("status", "📊 Your status"),
        ("help", "❓ Help menu")
    ]
    
    if db.is_admin(OWNER_ID):
        commands.extend([
            ("admin", "👑 Admin panel"),
            ("broadcast", "📢 Broadcast message")
        ])
    
    await application.bot.set_my_commands(commands)
    logger.info("Bot initialized successfully")

def main():
    """Main entry point"""
    # Start Flask server in separate thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask keep-alive server started")
    
    # Create application
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    
    # Initialize handlers
    handlers = BotHandlers()
    
    # Add handlers
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CallbackQueryHandler(handlers.button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))
    
    # Start monitoring engine
    monitoring = MonitoringEngine(application)
    
    # Schedule monitoring start after application starts
    async def start_monitoring():
        await monitoring.start_monitoring()
    
    application.job_queue.run_once(start_monitoring, when=5)  # Start after 5 seconds
    
    # Start bot
    logger.info("Starting bot...")
    
    # For Render, use webhook if URL provided, else polling
    if WEBHOOK_URL:
        # Webhook mode for Render
        port = int(os.environ.get("PORT", 8080))
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
    else:
        # Polling mode for development
        application.run_polling()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")

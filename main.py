#!/usr/bin/env python3
"""
Instagram Username Monitor Bot - Enterprise SaaS Solution
Professional Telegram bot for monitoring Instagram usernames status
with anti-false-alert system, subscription management, and role-based access.
"""

import os
import json
import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from threading import Thread
from contextlib import asynccontextmanager

from flask import Flask, jsonify
import requests
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.constants import ParseMode

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== Configuration ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))  # Set your Telegram ID as owner
MONITOR_INTERVAL = 300  # 5 minutes in seconds
MAX_WATCH_PER_USER = 20  # Default limit for normal users
DATA_FILE = "data.json"

# Instagram API simulation (replace with actual API)
INSTAGRAM_API_URL = "https://i.instagram.com/api/v1/users/web_profile_info/?username={}"

# Flask app for keep-alive
app = Flask(__name__)

# ==================== Data Management ====================
class DataManager:
    """Handles all persistent data operations"""
    
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.data = self.load_data()
        self.lock = asyncio.Lock()
    
    def load_data(self) -> Dict:
        """Load data from JSON file"""
        try:
            if os.path.exists(self.filepath):
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return self.get_default_structure()
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            return self.get_default_structure()
    
    def get_default_structure(self) -> Dict:
        """Return default data structure"""
        return {
            "users": {},  # Telegram user data: role, expiry, etc.
            "watchlist": {},  # username -> {user_id, status, confirm_count, last_check}
            "banlist": {},  # username -> {user_id, status, confirm_count, last_check}
            "pending_confirmations": {},  # username -> {status, count, first_detected}
            "admins": [OWNER_ID] if OWNER_ID else [],
            "stats": {
                "total_checks": 0,
                "alerts_sent": 0,
                "created_at": datetime.now().isoformat()
            }
        }
    
    async def save_data(self):
        """Save data to JSON file"""
        async with self.lock:
            try:
                with open(self.filepath, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Error saving data: {e}")
    
    async def get_user(self, user_id: int) -> Dict:
        """Get user data, create if not exists"""
        user_id = str(user_id)
        if user_id not in self.data["users"]:
            self.data["users"][user_id] = {
                "role": "user",
                "subscription_expiry": None,
                "joined_at": datetime.now().isoformat(),
                "username": None,
                "first_name": None,
                "total_monitored": 0
            }
            await self.save_data()
        return self.data["users"][user_id]
    
    async def update_user(self, user_id: int, **kwargs):
        """Update user data"""
        user_id = str(user_id)
        if user_id in self.data["users"]:
            self.data["users"][user_id].update(kwargs)
            await self.save_data()
    
    async def is_admin(self, user_id: int) -> bool:
        """Check if user is admin or owner"""
        user_id = int(user_id)
        if user_id == OWNER_ID:
            return True
        return user_id in self.data.get("admins", [])
    
    async def is_owner(self, user_id: int) -> bool:
        """Check if user is owner"""
        return int(user_id) == OWNER_ID
    
    async def has_active_subscription(self, user_id: int) -> bool:
        """Check if user has active subscription"""
        user = await self.get_user(user_id)
        if await self.is_admin(user_id):
            return True  # Admins and owner have unlimited access
        
        expiry = user.get("subscription_expiry")
        if not expiry:
            return False
        
        try:
            expiry_date = datetime.fromisoformat(expiry)
            return expiry_date > datetime.now()
        except:
            return False
    
    async def add_to_watchlist(self, username: str, user_id: int) -> bool:
        """Add username to watchlist"""
        username = username.lower().strip()
        user_id = str(user_id)
        
        # Check if already in watchlist
        if username in self.data["watchlist"]:
            return False
        
        # Check if in banlist
        if username in self.data["banlist"]:
            # Remove from banlist first
            del self.data["banlist"][username]
        
        self.data["watchlist"][username] = {
            "user_id": user_id,
            "added_at": datetime.now().isoformat(),
            "status": "unknown",
            "confirm_count": 0,
            "last_check": None,
            "last_details": None
        }
        
        # Update user stats
        user = await self.get_user(int(user_id))
        user["total_monitored"] = user.get("total_monitored", 0) + 1
        await self.save_data()
        return True
    
    async def add_to_banlist(self, username: str, user_id: int) -> bool:
        """Add username directly to banlist"""
        username = username.lower().strip()
        user_id = str(user_id)
        
        # Check if already in banlist
        if username in self.data["banlist"]:
            return False
        
        # Check if in watchlist
        if username in self.data["watchlist"]:
            del self.data["watchlist"][username]
        
        self.data["banlist"][username] = {
            "user_id": user_id,
            "added_at": datetime.now().isoformat(),
            "status": "banned",
            "confirm_count": 3,  # Pre-confirmed
            "last_check": None,
            "last_details": None
        }
        
        await self.save_data()
        return True
    
    async def move_to_banlist(self, username: str, details: Dict = None):
        """Move username from watchlist to banlist"""
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
        """Move username from banlist to watchlist"""
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
        """
        Update confirmation counter for username
        Returns: (should_alert, confirmation_count)
        """
        username = username.lower().strip()
        
        # Initialize pending confirmation if not exists
        if username not in self.data["pending_confirmations"]:
            self.data["pending_confirmations"][username] = {
                "status": status,
                "count": 1,
                "first_detected": datetime.now().isoformat()
            }
            await self.save_data()
            return False, 1
        
        pending = self.data["pending_confirmations"][username]
        
        # If status changed, reset counter
        if pending["status"] != status:
            pending["status"] = status
            pending["count"] = 1
            pending["first_detected"] = datetime.now().isoformat()
            await self.save_data()
            return False, 1
        
        # Same status, increment counter
        pending["count"] += 1
        
        # Check if reached threshold (3 confirmations)
        should_alert = pending["count"] >= 3
        
        if should_alert:
            # Clear pending confirmation
            del self.data["pending_confirmations"][username]
        else:
            await self.save_data()
        
        return should_alert, pending["count"]

# ==================== Instagram Monitor ====================
class InstagramMonitor:
    """Handles Instagram username checking"""
    
    def __init__(self, data_manager: DataManager):
        self.data_manager = data_manager
        self.session = None
        self.user_agent = "Instagram 219.0.0.12.117 Android"
    
    async def ensure_session(self):
        """Ensure aiohttp session exists"""
        if self.session is None or self.session.closed:
            import aiohttp
            self.session = aiohttp.ClientSession()
    
    async def close_session(self):
        """Close aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def check_username(self, username: str) -> Tuple[str, Optional[Dict]]:
        """
        Check Instagram username status
        Returns: (status, details_dict)
        Status: "active", "banned", "unknown", "error"
        """
        try:
            await self.ensure_session()
            
            # Simulate Instagram API call
            # In production, replace with actual Instagram API
            url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Host": "www.instagram.com",
                "Origin": "https://www.instagram.com",
                "Referer": f"https://www.instagram.com/{username}/",
                "X-IG-App-ID": "936619743392459",  # Instagram web app ID
                "X-Requested-With": "XMLHttpRequest"
            }
            
            # For demo purposes, simulate different responses
            # Replace with actual API call in production
            import random
            rand = random.random()
            
            if rand < 0.7:  # 70% chance active
                details = {
                    "name": f"User {username}",
                    "followers": random.randint(100, 50000),
                    "following": random.randint(50, 2000),
                    "posts": random.randint(1, 500),
                    "private": random.random() < 0.3,
                    "is_banned": False
                }
                return "active", details
            elif rand < 0.85:  # 15% chance banned
                return "banned", None
            elif rand < 0.95:  # 10% chance unknown/private
                return "unknown", None
            else:  # 5% chance error
                return "error", None
                
        except Exception as e:
            logger.error(f"Error checking username {username}: {e}")
            return "error", None
    
    def format_account_details(self, details: Dict) -> str:
        """Format account details for display"""
        if not details:
            return "❌ Details unavailable"
        
        private_status = "✅ No" if not details.get("private", True) else "🔒 Yes"
        
        return f"""
👤 *Name:* {details.get('name', 'Unknown')}
👥 *Followers:* {details.get('followers', 0):,}
👤 *Following:* {details.get('following', 0):,}
📸 *Posts:* {details.get('posts', 0)}
🔐 *Private:* {private_status}
"""
    
    async def check_all_usernames(self, context: ContextTypes.DEFAULT_TYPE):
        """
        Main monitoring function - checks all usernames
        Runs every MONITOR_INTERVAL seconds
        """
        logger.info("Starting monitoring cycle...")
        
        data_manager = self.data_manager
        
        # Check watchlist
        watchlist = dict(data_manager.data.get("watchlist", {}))
        for username, entry in watchlist.items():
            try:
                status, details = await self.check_username(username)
                
                # Update stats
                data_manager.data["stats"]["total_checks"] += 1
                
                # Get confirmation status
                should_alert, confirm_count = await data_manager.update_confirmation(username, status)
                
                if should_alert:
                    user_id = int(entry["user_id"])
                    
                    if status == "banned":
                        # Move to banlist and alert
                        await data_manager.move_to_banlist(username, details)
                        await self.send_ban_alert(context, user_id, username, details)
                        data_manager.data["stats"]["alerts_sent"] += 1
                    
                    elif status == "active":
                        # Already in watchlist, just update status
                        entry["status"] = "active"
                        entry["last_details"] = details
                        # Could send "still active" notification if needed
                    
                # Update last check time
                if username in data_manager.data["watchlist"]:
                    data_manager.data["watchlist"][username]["last_check"] = datetime.now().isoformat()
                
            except Exception as e:
                logger.error(f"Error processing {username}: {e}")
                continue
        
        # Check banlist for reactivations
        banlist = dict(data_manager.data.get("banlist", {}))
        for username, entry in banlist.items():
            try:
                status, details = await self.check_username(username)
                
                if status == "active":  # Previously banned account is now active
                    should_alert, confirm_count = await data_manager.update_confirmation(username, status)
                    
                    if should_alert:
                        user_id = int(entry["user_id"])
                        await data_manager.move_to_watchlist(username, details)
                        await self.send_unban_alert(context, user_id, username, details)
                        data_manager.data["stats"]["alerts_sent"] += 1
                
            except Exception as e:
                logger.error(f"Error processing banned {username}: {e}")
                continue
        
        await data_manager.save_data()
        logger.info("Monitoring cycle completed")
    
    async def send_ban_alert(self, context: ContextTypes.DEFAULT_TYPE, user_id: int, username: str, details: Dict = None):
        """Send ban alert to user"""
        try:
            details_text = self.format_account_details(details) if details else "❌ Details unavailable"
            
            message = f"""
🚫 *ACCOUNT BANNED DETECTED* 🚫

📛 *Username:* @{username}
⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📊 *Account Details:*
{details_text}

❌ *Status:* BANNED SUCCESSFULLY

━━━━━━━━━━━━━━━━
⚠️ This account has been moved to your Ban List
🔄 You will be notified if it becomes active again
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
            await context.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send ban alert to {user_id}: {e}")
    
    async def send_unban_alert(self, context: ContextTypes.DEFAULT_TYPE, user_id: int, username: str, details: Dict = None):
        """Send unban alert to user"""
        try:
            details_text = self.format_account_details(details) if details else "❌ Details unavailable"
            
            message = f"""
✅ *ACCOUNT UNBANNED DETECTED* ✅

📛 *Username:* @{username}
⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📊 *Account Details:*
{details_text}

✨ *Status:* UNBANNED SUCCESSFULLY

━━━━━━━━━━━━━━━━
🎉 This account has been moved back to your Watch List
🔍 Monitoring will continue automatically
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
            await context.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send unban alert to {user_id}: {e}")

# ==================== Bot Handlers ====================
class BotHandlers:
    """All bot command handlers"""
    
    def __init__(self, data_manager: DataManager, monitor: InstagramMonitor):
        self.data_manager = data_manager
        self.monitor = monitor
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command handler"""
        user = update.effective_user
        await self.data_manager.get_user(user.id)
        
        # Update user info
        await self.data_manager.update_user(
            user.id,
            username=user.username,
            first_name=user.first_name
        )
        
        welcome_msg = f"""
🌟 *Welcome to Instagram Monitor Bot, {user.first_name}!* 🌟

🔍 *Professional Username Monitoring Service*
Track Instagram usernames for bans and reactivations with enterprise-grade reliability.

━━━━━━━━━━━━━━━━
📋 *Your Plan:* {'👑 Owner' if user.id == OWNER_ID else '👥 Admin' if await self.data_manager.is_admin(user.id) else '👤 Free User'}

✨ *Features:*
• 🔍 Monitor up to {MAX_WATCH_PER_USER} usernames
• 🚫 Automatic ban detection with 3x confirmation
• ✅ Instant unban notifications
• 📊 Detailed account insights
• 🔒 100% Private & Secure

━━━━━━━━━━━━━━━━
📌 *Quick Commands:*
/watch - Add username to watch list
/ban - Add directly to ban list
/status - View your lists
/help - Show all commands

━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
        
        # Create keyboard
        keyboard = [
            [InlineKeyboardButton("🔍 Add to Watch", callback_data="add_watch"),
             InlineKeyboardButton("🚫 Add to Ban", callback_data="add_ban")],
            [InlineKeyboardButton("📊 My Status", callback_data="my_status"),
             InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        
        if await self.data_manager.is_admin(user.id):
            keyboard.append([
                InlineKeyboardButton("👥 Admin Panel", callback_data="admin_panel")
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Help command handler"""
        user = update.effective_user
        is_admin = await self.data_manager.is_admin(user.id)
        
        help_text = """
📚 *Instagram Monitor Bot - Commands*

━━━━━━━━━━━━━━━━
*👤 User Commands:*
/start - Welcome & main menu
/help - Show this help
/watch - Add username to watch
/ban - Add username to ban list
/status - View your monitoring lists
/check [username] - Quick status check
/subscribe - Check subscription status

"""
        
        if is_admin:
            help_text += """
━━━━━━━━━━━━━━━━
*👑 Admin Commands:*
/approve [user_id] [days] - Approve subscription
/addadmin [user_id] - Add admin (Owner only)
/broadcast [message] - Send to all users
/stats - View system statistics
/listusers - Show all users

"""
        
        help_text += """
━━━━━━━━━━━━━━━━
*📝 Examples:*
• `/watch instagram`
• `/ban suspicious_account`
• `/status`
• `/check username`

━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
        
        await update.message.reply_text(
            help_text,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def watch_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add username to watch list"""
        user = update.effective_user
        
        # Check subscription
        if not await self.data_manager.has_active_subscription(user.id):
            await update.message.reply_text(
                "❌ *No Active Subscription*\n\n"
                "Your subscription has expired or you don't have one.\n"
                "Contact an admin to get access.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Check if username provided
        if not context.args:
            await update.message.reply_text(
                "⚠️ *Usage:* `/watch username`\n"
                "Example: `/watch instagram`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        username = context.args[0].lower().strip()
        
        # Check current count
        watchlist = self.data_manager.data.get("watchlist", {})
        user_watch_count = sum(1 for u in watchlist.values() if u["user_id"] == str(user.id))
        
        if user_watch_count >= MAX_WATCH_PER_USER and not await self.data_manager.is_admin(user.id):
            await update.message.reply_text(
                f"❌ *Limit Reached*\n\n"
                f"You can only monitor {MAX_WATCH_PER_USER} usernames.\n"
                f"Remove some from your watch list first.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Add to watchlist
        success = await self.data_manager.add_to_watchlist(username, user.id)
        
        if success:
            # Do initial check
            status, details = await self.monitor.check_username(username)
            
            msg = f"""
✅ *Username Added to Watch List*

📛 *Username:* @{username}
📊 *Current Status:* {'🟢 ACTIVE' if status == 'active' else '🔴 BANNED' if status == 'banned' else '⚪ UNKNOWN'}

"""
            if details:
                msg += f"\n📋 *Details:*\n{self.monitor.format_account_details(details)}"
            
            msg += """
━━━━━━━━━━━━━━━━
🔍 Monitoring every 5 minutes
✅ 3x confirmation required for alerts
🔄 Automatic status updates
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
            
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                f"❌ *Failed to add*\n\n"
                f"Username @{username} is already in your watch list or ban list.",
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add username directly to ban list"""
        user = update.effective_user
        
        # Check subscription
        if not await self.data_manager.has_active_subscription(user.id):
            await update.message.reply_text(
                "❌ *No Active Subscription*",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        if not context.args:
            await update.message.reply_text(
                "⚠️ *Usage:* `/ban username`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        username = context.args[0].lower().strip()
        
        # Add to banlist
        success = await self.data_manager.add_to_banlist(username, user.id)
        
        if success:
            msg = f"""
🚫 *Username Added to Ban List*

📛 *Username:* @{username}

━━━━━━━━━━━━━━━━
🔍 Monitoring for reactivation
✅ You'll be notified when unbanned
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                f"❌ Username @{username} is already in your ban list.",
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user's watch and ban lists"""
        user = update.effective_user
        user_id = str(user.id)
        
        watchlist = self.data_manager.data.get("watchlist", {})
        banlist = self.data_manager.data.get("banlist", {})
        
        user_watch = [u for u, data in watchlist.items() if data["user_id"] == user_id]
        user_ban = [u for u, data in banlist.items() if data["user_id"] == user_id]
        
        expiry = (await self.data_manager.get_user(user.id)).get("subscription_expiry")
        expiry_text = f"📅 Expires: {datetime.fromisoformat(expiry).strftime('%Y-%m-%d')}" if expiry else "❌ No active subscription"
        
        if await self.data_manager.is_admin(user.id):
            expiry_text = "👑 Admin (Unlimited)"
        
        msg = f"""
📊 *Your Monitoring Status*

━━━━━━━━━━━━━━━━
*👤 User:* {user.first_name}
*🆔 ID:* `{user.id}`
*💳 Status:* {expiry_text}

━━━━━━━━━━━━━━━━
*🔍 Watch List* ({len(user_watch)}/{MAX_WATCH_PER_USER if not await self.data_manager.is_admin(user.id) else '∞'})
"""
        
        if user_watch:
            for i, username in enumerate(user_watch[:10], 1):
                status = watchlist[username].get("status", "unknown")
                status_emoji = "🟢" if status == "active" else "🔴" if status == "banned" else "⚪"
                msg += f"{i}. {status_emoji} @{username}\n"
            if len(user_watch) > 10:
                msg += f"... and {len(user_watch) - 10} more\n"
        else:
            msg += "No usernames in watch list\n"
        
        msg += f"\n*🚫 Ban List* ({len(user_ban)})\n"
        
        if user_ban:
            for i, username in enumerate(user_ban[:10], 1):
                msg += f"{i}. 🔴 @{username}\n"
            if len(user_ban) > 10:
                msg += f"... and {len(user_ban) - 10} more\n"
        else:
            msg += "No usernames in ban list\n"
        
        msg += """
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
        
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def approve_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Approve user subscription (Admin only)"""
        user = update.effective_user
        
        if not await self.data_manager.is_admin(user.id):
            await update.message.reply_text("❌ Admin only command.")
            return
        
        if len(context.args) < 2:
            await update.message.reply_text(
                "⚠️ *Usage:* `/approve user_id days`\n"
                "Example: `/approve 123456789 30`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        try:
            target_id = int(context.args[0])
            days = int(context.args[1])
            
            # Calculate expiry
            expiry = datetime.now() + timedelta(days=days)
            
            # Update user
            await self.data_manager.update_user(
                target_id,
                subscription_expiry=expiry.isoformat()
            )
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"""
✅ *Subscription Approved!*

━━━━━━━━━━━━━━━━
📅 *Duration:* {days} days
📊 *Expiry:* {expiry.strftime('%Y-%m-%d %H:%M')}
👤 *Approved by:* {user.first_name}

━━━━━━━━━━━━━━━━
You can now add usernames to monitor.
Use /watch to get started.
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
""",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
            
            await update.message.reply_text(
                f"✅ User {target_id} approved for {days} days.",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID or days.")
    
    async def addadmin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add admin (Owner only)"""
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
                await self.data_manager.save_data()
                
                # Update user role
                await self.data_manager.update_user(target_id, role="admin")
                
                await update.message.reply_text(f"✅ User {target_id} is now an admin.")
            else:
                await update.message.reply_text("❌ User is already an admin.")
                
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
    
    async def broadcast_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Broadcast message to all users (Admin only)"""
        user = update.effective_user
        
        if not await self.data_manager.is_admin(user.id):
            await update.message.reply_text("❌ Admin only command.")
            return
        
        if not context.args:
            await update.message.reply_text(
                "⚠️ *Usage:* `/broadcast your message here`\n"
                "Example: `/broadcast System maintenance tonight`",
                parseMode=ParseMode.MARKDOWN
            )
            return
        
        message = " ".join(context.args)
        
        # Add broadcast header
        broadcast_msg = f"""
📢 *Broadcast Message*

{message}

━━━━━━━━━━━━━━━━
👤 *From:* {user.first_name}
⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
        
        # Send to all users
        users = self.data_manager.data.get("users", {})
        sent = 0
        failed = 0
        
        status_msg = await update.message.reply_text("📤 Broadcasting message...")
        
        for user_id in users:
            try:
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=broadcast_msg,
                    parse_mode=ParseMode.MARKDOWN
                )
                sent += 1
                await asyncio.sleep(0.05)  # Small delay to avoid flooding
            except Exception as e:
                failed += 1
                logger.error(f"Broadcast failed to {user_id}: {e}")
        
        await status_msg.edit_text(
            f"✅ *Broadcast Complete*\n\n"
            f"📨 Sent: {sent}\n"
            f"❌ Failed: {failed}",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show system statistics (Admin only)"""
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
• Expired: {len(users) - active_users}

━━━━━━━━━━━━━━━━
*📋 Monitoring:*
• Watch List: {len(watchlist)}
• Ban List: {len(banlist)}
• Total Tracked: {len(watchlist) + len(banlist)}

━━━━━━━━━━━━━━━━
*📈 Performance:*
• Total Checks: {stats.get('total_checks', 0)}
• Alerts Sent: {stats.get('alerts_sent', 0)}
• Success Rate: {stats.get('total_checks', 0) > 0 and f"{(stats.get('alerts_sent', 0) / max(stats.get('total_checks', 0), 1) * 100):.1f}%" or "N/A"}

━━━━━━━━━━━━━━━━
*⚙️ System:*
• Monitor Interval: 5 minutes
• Confirmation: 3x required
• Version: 1.0.0

━━━━━━━━━━━━━━━━
*Powered by @proxyfxc*
"""
        
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks"""
        query = update.callback_query
        await query.answer()
        
        user = update.effective_user
        data = query.data
        
        if data == "add_watch":
            await query.edit_message_text(
                "🔍 *Add to Watch List*\n\n"
                "Please send the username you want to monitor:\n"
                "Example: `instagram`",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data["awaiting"] = "watch_username"
            
        elif data == "add_ban":
            await query.edit_message_text(
                "🚫 *Add to Ban List*\n\n"
                "Please send the username to add to ban list:\n"
                "Example: `banned_account`",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data["awaiting"] = "ban_username"
            
        elif data == "my_status":
            # Create status message
            user_id = str

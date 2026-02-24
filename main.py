#!/usr/bin/env python3
"""
Professional Instagram Username Monitor Bot
Enterprise-grade Telegram bot for monitoring Instagram account status
Powered by @proxyfxc | Channel: @proxydominates
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import random
import string

from flask import Flask, jsonify
import threading
import requests
from dotenv import load_dotenv

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

# Load environment variables
load_dotenv()

# ==================== Configuration ====================
class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    OWNER_ID = int(os.getenv('OWNER_ID', '0'))
    CHANNEL_USERNAME = '@proxydominates'
    CONTACT_USERNAME = '@proxyfxc'
    DATA_FILE = 'data.json'
    CHECK_INTERVAL = 300  # 5 minutes in seconds
    MAX_WATCH_PER_USER = 20
    FLASK_PORT = int(os.getenv('PORT', 8080))
    WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')  # For Render health checks

# ==================== Logging Setup ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== Data Models ====================
class UserRole(Enum):
    OWNER = "owner"
    ADMIN = "admin"
    USER = "user"

class AccountStatus(Enum):
    ACTIVE = "active"
    BANNED = "banned"
    UNKNOWN = "unknown"
    CHECKING = "checking"

class ConfirmationStatus(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    RESET = "reset"

# ==================== Database Manager ====================
class DatabaseManager:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.data = self._load_data()
        self._lock = asyncio.Lock()
    
    def _load_data(self) -> Dict:
        """Load data from JSON file with proper structure"""
        default_data = {
            "users": {},  # user_id: {"role": "user", "subscription_expiry": None, "username": "first_name"}
            "watch_list": {},  # username: {"user_id": int, "status": "active/banned", "confirmed_count": 0}
            "ban_list": {},  # username: {"user_id": int, "status": "banned", "confirmed_count": 0}
            "confirmation_counter": {},  # username: {"status": "active/banned", "count": 0, "last_check": "timestamp"}
            "pending_approvals": []  # List of user_ids waiting for admin approval
        }
        
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r') as f:
                    loaded_data = json.load(f)
                    # Merge with default structure to ensure all keys exist
                    for key in default_data:
                        if key not in loaded_data:
                            loaded_data[key] = default_data[key]
                    return loaded_data
        except Exception as e:
            logger.error(f"Error loading data: {e}")
        
        return default_data
    
    async def save_data(self):
        """Save data to JSON file with lock to prevent corruption"""
        async with self._lock:
            try:
                # Create backup before saving
                if os.path.exists(self.file_path):
                    backup_file = f"{self.file_path}.backup"
                    try:
                        with open(self.file_path, 'r') as src:
                            with open(backup_file, 'w') as dst:
                                dst.write(src.read())
                    except:
                        pass
                
                # Save current data
                with open(self.file_path, 'w') as f:
                    json.dump(self.data, f, indent=2, default=str)
                logger.info("Data saved successfully")
            except Exception as e:
                logger.error(f"Error saving data: {e}")
    
    async def get_user_role(self, user_id: int) -> UserRole:
        """Get user role"""
        user_id = str(user_id)
        if user_id == str(Config.OWNER_ID):
            return UserRole.OWNER
        
        user_data = self.data["users"].get(user_id, {})
        role = user_data.get("role", "user")
        return UserRole(role)
    
    async def is_admin_or_owner(self, user_id: int) -> bool:
        """Check if user is admin or owner"""
        role = await self.get_user_role(user_id)
        return role in [UserRole.OWNER, UserRole.ADMIN]
    
    async def add_admin(self, user_id: int, username: str = "") -> bool:
        """Add admin (owner only)"""
        user_id = str(user_id)
        if user_id not in self.data["users"]:
            self.data["users"][user_id] = {}
        
        self.data["users"][user_id]["role"] = "admin"
        self.data["users"][user_id]["username"] = username
        await self.save_data()
        return True
    
    async def remove_admin(self, user_id: int) -> bool:
        """Remove admin (owner only)"""
        user_id = str(user_id)
        if user_id in self.data["users"]:
            self.data["users"][user_id]["role"] = "user"
            await self.save_data()
            return True
        return False
    
    async def add_user(self, user_id: int, username: str, first_name: str):
        """Add or update user"""
        user_id = str(user_id)
        if user_id not in self.data["users"]:
            self.data["users"][user_id] = {
                "role": "user",
                "subscription_expiry": None,
                "username": username,
                "first_name": first_name,
                "joined_date": datetime.now().isoformat()
            }
        else:
            self.data["users"][user_id]["username"] = username
            self.data["users"][user_id]["first_name"] = first_name
        
        await self.save_data()
    
    async def approve_subscription(self, user_id: int, days: int, admin_id: int) -> bool:
        """Approve user subscription"""
        user_id = str(user_id)
        if user_id not in self.data["users"]:
            return False
        
        expiry_date = datetime.now() + timedelta(days=days)
        self.data["users"][user_id]["subscription_expiry"] = expiry_date.isoformat()
        self.data["users"][user_id]["approved_by"] = admin_id
        self.data["users"][user_id]["approved_date"] = datetime.now().isoformat()
        
        # Remove from pending approvals
        if int(user_id) in self.data["pending_approvals"]:
            self.data["pending_approvals"].remove(int(user_id))
        
        await self.save_data()
        return True
    
    async def has_active_subscription(self, user_id: int) -> bool:
        """Check if user has active subscription"""
        user_id = str(user_id)
        role = await self.get_user_role(int(user_id))
        
        # Owners and admins have unlimited access
        if role in [UserRole.OWNER, UserRole.ADMIN]:
            return True
        
        if user_id not in self.data["users"]:
            return False
        
        expiry = self.data["users"][user_id].get("subscription_expiry")
        if not expiry:
            return False
        
        try:
            expiry_date = datetime.fromisoformat(expiry)
            return expiry_date > datetime.now()
        except:
            return False
    
    async def add_to_watch(self, username: str, user_id: int) -> Tuple[bool, str]:
        """Add username to watch list"""
        username = username.lower().strip('@')
        user_id = str(user_id)
        
        # Check subscription
        if not await self.has_active_subscription(int(user_id)):
            return False, "❌ Your subscription has expired. Please contact an admin."
        
        # Check if already in watch list
        if username in self.data["watch_list"]:
            return False, f"❌ @{username} is already in your watch list."
        
        # Check if in ban list
        if username in self.data["ban_list"]:
            # Move from ban to watch
            self.data["ban_list"].pop(username)
        
        # Check user's watch count
        user_watch_count = sum(1 for u in self.data["watch_list"].values() if u.get("user_id") == user_id)
        if user_watch_count >= Config.MAX_WATCH_PER_USER:
            return False, f"❌ You've reached the maximum limit of {Config.MAX_WATCH_PER_USER} usernames."
        
        # Add to watch list
        self.data["watch_list"][username] = {
            "user_id": user_id,
            "status": "unknown",
            "added_date": datetime.now().isoformat(),
            "confirmed_count": 0
        }
        
        # Initialize confirmation counter
        self.data["confirmation_counter"][username] = {
            "status": "unknown",
            "count": 0,
            "last_check": datetime.now().isoformat()
        }
        
        await self.save_data()
        return True, f"✅ @{username} added to watch list successfully!"
    
    async def add_to_ban(self, username: str, user_id: int) -> Tuple[bool, str]:
        """Add username directly to ban list"""
        username = username.lower().strip('@')
        user_id = str(user_id)
        
        # Check subscription
        if not await self.has_active_subscription(int(user_id)):
            return False, "❌ Your subscription has expired. Please contact an admin."
        
        # Check if already in ban list
        if username in self.data["ban_list"]:
            return False, f"❌ @{username} is already in your ban list."
        
        # Remove from watch if present
        if username in self.data["watch_list"]:
            self.data["watch_list"].pop(username)
        
        self.data["ban_list"][username] = {
            "user_id": user_id,
            "status": "banned",
            "added_date": datetime.now().isoformat(),
            "confirmed_count": 0
        }
        
        # Initialize confirmation counter
        self.data["confirmation_counter"][username] = {
            "status": "banned",
            "count": 3,  # Pre-confirmed for manual ban
            "last_check": datetime.now().isoformat()
        }
        
        await self.save_data()
        return True, f"✅ @{username} added to ban list successfully!"
    
    async def remove_from_watch(self, username: str, user_id: int) -> Tuple[bool, str]:
        """Remove username from watch list"""
        username = username.lower().strip('@')
        user_id = str(user_id)
        
        if username not in self.data["watch_list"]:
            return False, f"❌ @{username} is not in your watch list."
        
        if self.data["watch_list"][username]["user_id"] != user_id:
            return False, "❌ You don't have permission to remove this username."
        
        self.data["watch_list"].pop(username)
        if username in self.data["confirmation_counter"]:
            self.data["confirmation_counter"].pop(username)
        
        await self.save_data()
        return True, f"✅ @{username} removed from watch list."
    
    async def remove_from_ban(self, username: str, user_id: int) -> Tuple[bool, str]:
        """Remove username from ban list"""
        username = username.lower().strip('@')
        user_id = str(user_id)
        
        if username not in self.data["ban_list"]:
            return False, f"❌ @{username} is not in your ban list."
        
        if self.data["ban_list"][username]["user_id"] != user_id:
            return False, "❌ You don't have permission to remove this username."
        
        self.data["ban_list"].pop(username)
        if username in self.data["confirmation_counter"]:
            self.data["confirmation_counter"].pop(username)
        
        await self.save_data()
        return True, f"✅ @{username} removed from ban list."
    
    async def get_user_stats(self, user_id: int) -> Dict:
        """Get user statistics"""
        user_id = str(user_id)
        watch_count = sum(1 for u in self.data["watch_list"].values() if u.get("user_id") == user_id)
        ban_count = sum(1 for u in self.data["ban_list"].values() if u.get("user_id") == user_id)
        
        user_data = self.data["users"].get(user_id, {})
        expiry = user_data.get("subscription_expiry")
        expiry_str = "Lifetime (Admin)" if expiry is None and await self.is_admin_or_owner(int(user_id)) else \
                    (datetime.fromisoformat(expiry).strftime("%Y-%m-%d") if expiry else "No subscription")
        
        return {
            "watch_count": watch_count,
            "ban_count": ban_count,
            "total": watch_count + ban_count,
            "limit": "Unlimited" if await self.is_admin_or_owner(int(user_id)) else Config.MAX_WATCH_PER_USER,
            "subscription_expiry": expiry_str,
            "role": (await self.get_user_role(int(user_id))).value
        }
    
    async def update_confirmation_counter(self, username: str, detected_status: str) -> Tuple[bool, str]:
        """Update confirmation counter for username"""
        username = username.lower().strip('@')
        
        if username not in self.data["confirmation_counter"]:
            self.data["confirmation_counter"][username] = {
                "status": detected_status,
                "count": 1,
                "last_check": datetime.now().isoformat()
            }
            return False, detected_status
        
        counter = self.data["confirmation_counter"][username]
        
        # Reset if status changed
        if counter["status"] != detected_status:
            counter["status"] = detected_status
            counter["count"] = 1
            counter["last_check"] = datetime.now().isoformat()
            return False, detected_status
        
        # Increment counter
        counter["count"] += 1
        counter["last_check"] = datetime.now().isoformat()
        
        # Check if confirmed (3 times)
        if counter["count"] >= 3:
            counter["count"] = 0  # Reset counter
            return True, detected_status
        
        return False, detected_status
    
    async def move_to_ban(self, username: str):
        """Move username from watch to ban list"""
        username = username.lower().strip('@')
        
        if username in self.data["watch_list"]:
            user_id = self.data["watch_list"][username]["user_id"]
            self.data["ban_list"][username] = {
                "user_id": user_id,
                "status": "banned",
                "added_date": datetime.now().isoformat(),
                "moved_from_watch": True,
                "moved_date": datetime.now().isoformat()
            }
            self.data["watch_list"].pop(username)
            await self.save_data()
    
    async def move_to_watch(self, username: str):
        """Move username from ban to watch list"""
        username = username.lower().strip('@')
        
        if username in self.data["ban_list"]:
            user_id = self.data["ban_list"][username]["user_id"]
            self.data["watch_list"][username] = {
                "user_id": user_id,
                "status": "active",
                "added_date": datetime.now().isoformat(),
                "moved_from_ban": True,
                "moved_date": datetime.now().isoformat()
            }
            self.data["ban_list"].pop(username)
            await self.save_data()
    
    async def get_all_users(self) -> List[int]:
        """Get all user IDs"""
        return [int(uid) for uid in self.data["users"].keys()]

# ==================== Instagram Mock API (Replace with actual API) ====================
class InstagramChecker:
    """Mock Instagram checker - Replace with actual Instagram API"""
    
    @staticmethod
    async def check_username(username: str) -> Tuple[str, Dict]:
        """
        Check Instagram username status
        Returns: (status, details)
        status: 'active', 'banned', 'unknown'
        """
        # This is a mock implementation
        # Replace with actual Instagram API calls
        
        # Simulate API call delay
        await asyncio.sleep(1)
        
        # Mock data for demonstration
        mock_profiles = {
            "testuser": {
                "status": "active",
                "full_name": "Test User",
                "followers": 1500,
                "following": 500,
                "posts": 45,
                "is_private": False
            },
            "privateuser": {
                "status": "active",
                "full_name": "Private User",
                "followers": 500,
                "following": 200,
                "posts": 12,
                "is_private": True
            },
            "banneduser": {
                "status": "banned",
                "full_name": None,
                "followers": 0,
                "following": 0,
                "posts": 0,
                "is_private": False
            }
        }
        
        # 90% chance of active, 10% chance of banned for random usernames
        if username not in mock_profiles:
            import random
            if random.random() < 0.1:  # 10% chance of banned
                status = "banned"
                details = {
                    "full_name": None,
                    "followers": 0,
                    "following": 0,
                    "posts": 0,
                    "is_private": False
                }
            else:
                status = "active"
                details = {
                    "full_name": f"User {username.title()}",
                    "followers": random.randint(100, 10000),
                    "following": random.randint(50, 2000),
                    "posts": random.randint(1, 500),
                    "is_private": random.choice([True, False])
                }
            return status, details
        
        profile = mock_profiles[username]
        return profile["status"], {
            "full_name": profile.get("full_name"),
            "followers": profile.get("followers", 0),
            "following": profile.get("following", 0),
            "posts": profile.get("posts", 0),
            "is_private": profile.get("is_private", False)
        }

# ==================== Flask Keep-Alive Server ====================
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({
        "status": "active",
        "bot": "Instagram Monitor Bot",
        "channel": Config.CHANNEL_USERNAME,
        "contact": Config.CONTACT_USERNAME,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

def run_flask():
    app.run(host='0.0.0.0', port=Config.FLASK_PORT)

# ==================== Telegram Bot UI Components ====================
class BotUI:
    """Professional UI components for the bot"""
    
    @staticmethod
    def get_welcome_message(user_first_name: str) -> str:
        return f"""
🚀 **Welcome to Instagram Monitor Pro** 🚀

Hello **{user_first_name}**! Welcome to the most advanced Instagram username monitoring system.

🔍 **What I can do:**
• Monitor Instagram usernames 24/7
• Detect BANNED/ACTIVE status changes
• 3x confirmation system - zero false alerts
• Professional alerts with account details

📊 **Your Dashboard:**
Use /watch to start monitoring
Use /status to check your stats
Use /ban to manually add banned accounts

⚡ **Powered by:** {Config.CHANNEL_USERNAME}
📞 **Contact:** {Config.CONTACT_USERNAME}

_Professional Instagram Monitoring Solution_
        """
    
    @staticmethod
    def get_account_details_card(username: str, details: Dict, status: str) -> str:
        """Generate professional account details card"""
        status_emoji = "🔴" if status == "banned" else "🟢" if status == "active" else "⚪"
        status_text = "BANNED" if status == "banned" else "ACTIVE" if status == "active" else "UNKNOWN"
        
        # Handle None values
        full_name = details.get('full_name') or 'N/A'
        followers = details.get('followers', 0)
        following = details.get('following', 0)
        posts = details.get('posts', 0)
        is_private = details.get('is_private', False)
        private_text = "Yes 🔒" if is_private else "No 🔓"
        
        card = f"""
📱 **ACCOUNT DETAILS**
━━━━━━━━━━━━━━━━━━━━━
👤 **Username:** @{username}
📝 **Name:** {full_name}
📊 **Status:** {status_emoji} {status_text}
━━━━━━━━━━━━━━━━━━━━━
👥 **Followers:** {followers:,}
👤 **Following:** {following:,}
📸 **Posts:** {posts:,}
🔐 **Private:** {private_text}
━━━━━━━━━━━━━━━━━━━━━
⏱ **Checked:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⚡ {Config.CHANNEL_USERNAME}
        """
        return card
    
    @staticmethod
    def get_alert_message(username: str, old_status: str, new_status: str, details: Dict) -> str:
        """Generate professional alert message"""
        if new_status == "banned":
            alert_type = "🔴 **BANNED DETECTED**"
            emoji = "🚫"
        elif new_status == "active":
            alert_type = "🟢 **UNBANNED DETECTED**"
            emoji = "✅"
        else:
            alert_type = "⚪ **STATUS UPDATE**"
            emoji = "ℹ️"
        
        # Account details
        full_name = details.get('full_name') or 'N/A'
        followers = details.get('followers', 0)
        following = details.get('following', 0)
        posts = details.get('posts', 0)
        is_private = details.get('is_private', False)
        private_text = "Yes 🔒" if is_private else "No 🔓"
        
        message = f"""
{emoji} **ACCOUNT STATUS ALERT** {emoji}
━━━━━━━━━━━━━━━━━━━━━
{alert_type}
━━━━━━━━━━━━━━━━━━━━━
👤 **Username:** @{username}
📝 **Name:** {full_name}
📊 **Previous:** {old_status.upper()}
📊 **Current:** {new_status.upper()}
━━━━━━━━━━━━━━━━━━━━━
👥 **Followers:** {followers:,}
👤 **Following:** {following:,}
📸 **Posts:** {posts:,}
🔐 **Private:** {private_text}
━━━━━━━━━━━━━━━━━━━━━
⏱ **Detected:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
💡 **Action Required:** {'None - Automatic tracking updated' if new_status == 'active' else 'Account is banned'}
━━━━━━━━━━━━━━━━━━━━━
⚡ {Config.CHANNEL_USERNAME}
📞 {Config.CONTACT_USERNAME}
        """
        return message
    
    @staticmethod
    def get_main_menu_keyboard(user_role: UserRole) -> InlineKeyboardMarkup:
        """Get main menu keyboard based on user role"""
        keyboard = [
            [
                InlineKeyboardButton("📋 Watch List", callback_data="menu_watch"),
                InlineKeyboardButton("🚫 Ban List", callback_data="menu_ban")
            ],
            [
                InlineKeyboardButton("📊 Status", callback_data="menu_status"),
                InlineKeyboardButton("➕ Add to Watch", callback_data="menu_add_watch")
            ],
            [
                InlineKeyboardButton("🔨 Add to Ban", callback_data="menu_add_ban"),
                InlineKeyboardButton("❓ Help", callback_data="menu_help")
            ]
        ]
        
        # Admin buttons
        if user_role in [UserRole.OWNER, UserRole.ADMIN]:
            keyboard.append([
                InlineKeyboardButton("👥 Approve Users", callback_data="admin_approve_list"),
                InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")
            ])
        
        # Owner only buttons
        if user_role == UserRole.OWNER:
            keyboard.append([
                InlineKeyboardButton("👑 Add Admin", callback_data="owner_add_admin"),
                InlineKeyboardButton("🗑 Remove Admin", callback_data="owner_remove_admin")
            ])
        
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_username_action_keyboard(username: str, list_type: str) -> InlineKeyboardMarkup:
        """Get keyboard for username actions"""
        keyboard = [
            [
                InlineKeyboardButton("🔄 Check Now", callback_data=f"check_{username}"),
                InlineKeyboardButton("❌ Remove", callback_data=f"remove_{list_type}_{username}")
            ],
            [
                InlineKeyboardButton("🔙 Back", callback_data=f"back_to_{list_type}")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_cancel_keyboard() -> InlineKeyboardMarkup:
        """Get cancel keyboard"""
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        return InlineKeyboardMarkup(keyboard)

# ==================== Telegram Bot Handlers ====================
class BotHandlers:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.ui = BotUI()
        self.checker = InstagramChecker()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command handler"""
        user = update.effective_user
        await self.db.add_user(user.id, user.username or "NoUsername", user.first_name)
        
        welcome_msg = self.ui.get_welcome_message(user.first_name)
        user_role = await self.db.get_user_role(user.id)
        
        await update.message.reply_text(
            welcome_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.ui.get_main_menu_keyboard(user_role)
        )
    
    async def menu_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle menu callbacks"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        user_role = await self.db.get_user_role(user_id)
        data = query.data
        
        if data == "menu_watch":
            await self.show_watch_list(update, context)
        elif data == "menu_ban":
            await self.show_ban_list(update, context)
        elif data == "menu_status":
            await self.show_status(update, context)
        elif data == "menu_add_watch":
            await query.edit_message_text(
                "📝 **Add to Watch List**\n\n"
                "Please send the Instagram username you want to monitor:\n"
                "Example: `username` or `@username`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.ui.get_cancel_keyboard()
            )
            context.user_data['awaiting'] = 'watch_username'
        elif data == "menu_add_ban":
            await query.edit_message_text(
                "📝 **Add to Ban List**\n\n"
                "Please send the Instagram username to mark as banned:\n"
                "Example: `username` or `@username`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.ui.get_cancel_keyboard()
            )
            context.user_data['awaiting'] = 'ban_username'
        elif data == "menu_help":
            await self.show_help(update, context)
        elif data == "admin_approve_list" and user_role in [UserRole.OWNER, UserRole.ADMIN]:
            await self.show_approval_list(update, context)
        elif data == "admin_broadcast" and user_role in [UserRole.OWNER, UserRole.ADMIN]:
            await query.edit_message_text(
                "📢 **Broadcast Message**\n\n"
                "Please send the message you want to broadcast to all users:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.ui.get_cancel_keyboard()
            )
            context.user_data['awaiting'] = 'broadcast_message'
        elif data == "owner_add_admin" and user_role == UserRole.OWNER:
            await query.edit_message_text(
                "👑 **Add Admin**\n\n"
                "Please send the user ID of the person you want to make admin:\n"
                "Tip: Users can get their ID with /id",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.ui.get_cancel_keyboard()
            )
            context.user_data['awaiting'] = 'add_admin'
        elif data == "owner_remove_admin" and user_role == UserRole.OWNER:
            await query.edit_message_text(
                "👑 **Remove Admin**\n\n"
                "Please send the user ID of the admin you want to remove:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.ui.get_cancel_keyboard()
            )
            context.user_data['awaiting'] = 'remove_admin'
        elif data == "cancel":
            await query.edit_message_text(
                "❌ Action cancelled.",
                reply_markup=self.ui.get_main_menu_keyboard(user_role)
            )
            context.user_data.clear()
    
    async def show_watch_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user's watch list"""
        query = update.callback_query
        user_id = str(update.effective_user.id)
        
        watch_items = [(un, data) for un, data in self.db.data["watch_list"].items() 
                      if data.get("user_id") == user_id]
        
        if not watch_items:
            await query.edit_message_text(
                "📋 **Your Watch List**\n\n"
                "Your watch list is empty.\n"
                "Use /watch to add usernames to monitor.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.ui.get_main_menu_keyboard(await self.db.get_user_role(int(user_id)))
            )
            return
        
        message = "📋 **YOUR WATCH LIST**\n━━━━━━━━━━━━━━━━━━━━━\n"
        for username, data in watch_items:
            status_emoji = "🟢" if data.get("status") == "active" else "🔴" if data.get("status") == "banned" else "⚪"
            message += f"{status_emoji} @{username}\n"
        
        message += f"\n━━━━━━━━━━━━━━━━━━━━━\n📊 **Total:** {len(watch_items)}"
        
        # Create keyboard with first 5 usernames (Telegram limit)
        keyboard = []
        for username, _ in watch_items[:5]:
            keyboard.append([InlineKeyboardButton(f"👤 @{username}", callback_data=f"view_watch_{username}")])
        keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
        
        await query.edit_message_text(
            message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def show_ban_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user's ban list"""
        query = update.callback_query
        user_id = str(update.effective_user.id)
        
        ban_items = [(un, data) for un, data in self.db.data["ban_list"].items() 
                    if data.get("user_id") == user_id]
        
        if not ban_items:
            await query.edit_message_text(
                "🚫 **Your Ban List**\n\n"
                "Your ban list is empty.\n"
                "Use /ban to add banned usernames.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.ui.get_main_menu_keyboard(await self.db.get_user_role(int(user_id)))
            )
            return
        
        message = "🚫 **YOUR BAN LIST**\n━━━━━━━━━━━━━━━━━━━━━\n"
        for username, data in ban_items:
            message += f"🔴 @{username}\n"
        
        message += f"\n━━━━━━━━━━━━━━━━━━━━━\n📊 **Total:** {len(ban_items)}"
        
        # Create keyboard with first 5 usernames
        keyboard = []
        for username, _ in ban_items[:5]:
            keyboard.append([InlineKeyboardButton(f"👤 @{username}", callback_data=f"view_ban_{username}")])
        keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
        
        await query.edit_message_text(
            message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user status"""
        query = update.callback_query
        user_id = update.effective_user.id
        
        stats = await self.db.get_user_stats(user_id)
        
        message = f"""
📊 **YOUR DASHBOARD**
━━━━━━━━━━━━━━━━━━━━━
👤 **User ID:** `{user_id}`
👑 **Role:** {stats['role'].upper()}
━━━━━━━━━━━━━━━━━━━━━
📋 **Watch List:** {stats['watch_count']}/{stats['limit']}
🚫 **Ban List:** {stats['ban_count']}
📈 **Total:** {stats['total']}
━━━━━━━━━━━━━━━━━━━━━
⏱ **Subscription:** {stats['subscription_expiry']}
━━━━━━━━━━━━━━━━━━━━━
⚡ {Config.CHANNEL_USERNAME}
        """
        
        await query.edit_message_text(
            message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.ui.get_main_menu_keyboard(await self.db.get_user_role(user_id))
        )
    
    async def show_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help message"""
        query = update.callback_query
        
        message = f"""
❓ **HELP & COMMANDS**
━━━━━━━━━━━━━━━━━━━━━

**Basic Commands:**
/start - Start the bot
/watch - Add to watch list
/ban - Add to ban list
/status - Your dashboard
/list - View your lists

**How It Works:**
1️⃣ Add usernames to Watch List
2️⃣ Bot checks every 5 minutes
3️⃣ 3x confirmation before alerts
4️⃣ Automatic status updates

**Features:**
✅ 3x Confirmation System
✅ Zero False Alerts
✅ Professional Formatting
✅ Account Details Included

**Support:**
📢 Channel: {Config.CHANNEL_USERNAME}
📞 Contact: {Config.CONTACT_USERNAME}
━━━━━━━━━━━━━━━━━━━━━
⚡ Professional Instagram Monitor
        """
        
        await query.edit_message_text(
            message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.ui.get_main_menu_keyboard(await self.db.get_user_role(update.effective_user.id))
        )
    
    async def show_approval_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show pending approvals for admin"""
        query = update.callback_query
        
        pending = self.db.data["pending_approvals"]
        
        if not pending:
            await query.edit_message_text(
                "✅ No pending approvals.",
                reply_markup=self.ui.get_main_menu_keyboard(await self.db.get_user_role(update.effective_user.id))
            )
            return
        
        message = "👥 **PENDING APPROVALS**\n━━━━━━━━━━━━━━━━━━━━━\n"
        keyboard = []
        
        for user_id in pending[:5]:  # Show first 5
            user_data = self.db.data["users"].get(str(user_id), {})
            username = user_data.get("username", "NoUsername")
            message += f"👤 **User ID:** `{user_id}`\n📝 **Username:** @{username}\n\n"
            keyboard.append([
                InlineKeyboardButton(f"✅ Approve {user_id}", callback_data=f"approve_{user_id}")
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
        
        await query.edit_message_text(
            message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages"""
        if 'awaiting' not in context.user_data:
            return
        
        user_id = update.effective_user.id
        text = update.message.text.strip()
        action = context.user_data['awaiting']
        
        if action == 'watch_username':
            success, msg = await self.db.add_to_watch(text, user_id)
            await update.message.reply_text(msg)
            if success:
                # Initial check
                status, details = await self.checker.check_username(text)
                await update.message.reply_text(
                    self.ui.get_account_details_card(text, details, status),
                    parse_mode=ParseMode.MARKDOWN
                )
        
        elif action == 'ban_username':
            success, msg = await self.db.add_to_ban(text, user_id)
            await update.message.reply_text(msg)
        
        elif action == 'broadcast_message' and await self.db.is_admin_or_owner(user_id):
            await update.message.reply_text("📢 Broadcasting message to all users...")
            await self.broadcast_message(update, context, text)
        
        elif action == 'add_admin' and user_id == Config.OWNER_ID:
            try:
                target_id = int(text)
                await self.db.add_admin(target_id, update.message.from_user.username or "")
                await update.message.reply_text(f"✅ User `{target_id}` is now an admin.", parse_mode=ParseMode.MARKDOWN)
            except ValueError:
                await update.message.reply_text("❌ Invalid user ID. Please send a number.")
        
        elif action == 'remove_admin' and user_id == Config.OWNER_ID:
            try:
                target_id = int(text)
                if target_id == Config.OWNER_ID:
                    await update.message.reply_text("❌ Cannot remove owner.")
                else:
                    await self.db.remove_admin(target_id)
                    await update.message.reply_text(f"✅ Admin privileges removed from `{target_id}`.", parse_mode=ParseMode.MARKDOWN)
            except ValueError:
                await update.message.reply_text("❌ Invalid user ID. Please send a number.")
        
        context.user_data.clear()
    
    async def broadcast_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, message: str):
        """Broadcast message to all users"""
        users = await self.db.get_all_users()
        success = 0
        failed = 0
        
        status_msg = await update.message.reply_text(f"📢 Broadcasting... 0/{len(users)}")
        
        for i, uid in enumerate(users):
            try:
                await context.bot.send_message(
                    uid,
                    f"📢 **BROADCAST MESSAGE**\n━━━━━━━━━━━━━━━━━━━━━\n\n{message}\n\n━━━━━━━━━━━━━━━━━━━━━\n⚡ {Config.CHANNEL_USERNAME}",
                    parse_mode=ParseMode.MARKDOWN
                )
                success += 1
            except Exception as e:
                failed += 1
                logger.error(f"Failed to send broadcast to {uid}: {e}")
            
            if (i + 1) % 10 == 0:
                await status_msg.edit_text(f"📢 Broadcasting... {i + 1}/{len(users)}")
        
        await status_msg.edit_text(
            f"📢 **Broadcast Complete**\n\n✅ Success: {success}\n❌ Failed: {failed}",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def view_username(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View username details"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        if data.startswith("view_watch_"):
            username = data.replace("view_watch_", "")
            list_type = "watch"
        elif data.startswith("view_ban_"):
            username = data.replace("view_ban_", "")
            list_type = "ban"
        else:
            return
        
        # Get current status
        status, details = await self.checker.check_username(username)
        
        await query.edit_message_text(
            self.ui.get_account_details_card(username, details, status),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.ui.get_username_action_keyboard(username, list_type)
        )
    
    async def handle_username_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle username actions"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = update.effective_user.id
        
        if data.startswith("check_"):
            username = data.replace("check_", "")
            status, details = await self.checker.check_username(username)
            await query.edit_message_text(
                self.ui.get_account_details_card(username, details, status),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.ui.get_username_action_keyboard(username, "watch" if username in self.db.data["watch_list"] else "ban")
            )
        
        elif data.startswith("remove_watch_"):
            username = data.replace("remove_watch_", "")
            success, msg = await self.db.remove_from_watch(username, user_id)
            await query.edit_message_text(msg)
            await self.show_watch_list(update, context)
        
        elif data.startswith("remove_ban_"):
            username = data.replace("remove_ban_", "")
            success, msg = await self.db.remove_from_ban(username, user_id)
            await query.edit_message_text(msg)
            await self.show_ban_list(update, context)
    
    async def handle_approval(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle user approval"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        if data.startswith("approve_"):
            user_id = int(data.replace("approve_", ""))
            
            await query.edit_message_text(
                f"📝 **Approve User**\n\nUser ID: `{user_id}`\n\nHow many days of access?",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.ui.get_cancel_keyboard()
            )
            context.user_data['awaiting'] = f'approve_days_{user_id}'
    
    async def handle_approval_days(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle approval days input"""
        if 'awaiting' not in context.user_data or not context.user_data['awaiting'].startswith('approve_days_'):
            return
        
        user_id = int(context.user_data['awaiting'].replace('approve_days_', ''))
        admin_id = update.effective_user.id
        
        try:
            days = int(update.message.text)
            if days <= 0 or days > 365:
                await update.message.reply_text("❌ Please enter a number between 1 and 365.")
                return
            
            success = await self.db.approve_subscription(user_id, days, admin_id)
            if success:
                await update.message.reply_text(f"✅ User `{user_id}` approved for {days} days.", parse_mode=ParseMode.MARKDOWN)
                
                # Notify user
                try:
                    await context.bot.send_message(
                        user_id,
                        f"✅ **Subscription Approved!**\n\nYour subscription has been approved for **{days} days**.\n\nYou can now use all features of the bot.\n\n⚡ {Config.CHANNEL_USERNAME}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
            else:
                await update.message.reply_text("❌ Failed to approve user.")
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number.")
        
        context.user_data.clear()
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors gracefully"""
        logger.error(f"Update {update} caused error {context.error}")

# ==================== Background Monitor ====================
class BackgroundMonitor:
    def __init__(self, db: DatabaseManager, application: Application):
        self.db = db
        self.application = application
        self.checker = InstagramChecker()
        self.is_running = False
    
    async def start_monitoring(self):
        """Start the background monitoring loop"""
        self.is_running = True
        asyncio.create_task(self._monitor_loop())
        logger.info("Background monitor started")
    
    async def _monitor_loop(self):
        """Main monitoring loop"""
        while self.is_running:
            try:
                await self._check_all_usernames()
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
            
            await asyncio.sleep(Config.CHECK_INTERVAL)
    
    async def _check_all_usernames(self):
        """Check all usernames in watch and ban lists"""
        logger.info("Starting periodic username check")
        
        # Check watch list
        watch_copy = list(self.db.data["watch_list"].items())
        for username, data in watch_copy:
            try:
                await self._check_username(username, data, "watch")
            except Exception as e:
                logger.error(f"Error checking watch username {username}: {e}")
        
        # Check ban list
        ban_copy = list(self.db.data["ban_list"].items())
        for username, data in ban_copy:
            try:
                await self._check_username(username, data, "ban")
            except Exception as e:
                logger.error(f"Error checking ban username {username}: {e}")
    
    async def _check_username(self, username: str, data: Dict, list_type: str):
        """Check individual username"""
        user_id = int(data["user_id"])
        status, details = await self.checker.check_username(username)
        
        # Update confirmation counter
        confirmed, detected_status = await self.db.update_confirmation_counter(username, status)
        
        if confirmed:
            # Status confirmed 3 times
            if list_type == "watch" and detected_status == "banned":
                # Watch list username got banned
                await self.db.move_to_ban(username)
                await self._send_alert(user_id, username, "active", "banned", details)
                logger.info(f"Username {username} moved from watch to ban (confirmed banned)")
            
            elif list_type == "ban" and detected_status == "active":
                # Ban list username got unbanned
                await self.db.move_to_watch(username)
                await self._send_alert(user_id, username, "banned", "active", details)
                logger.info(f"Username {username} moved from ban to watch (confirmed active)")
    
    async def _send_alert(self, user_id: int, username: str, old_status: str, new_status: str, details: Dict):
        """Send alert to user"""
        try:
            alert_msg = BotUI.get_alert_message(username, old_status, new_status, details)
            await self.application.bot.send_message(
                user_id,
                alert_msg,
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info(f"Alert sent to {user_id} for {username}: {old_status} -> {new_status}")
        except Exception as e:
            logger.error(f"Failed to send alert to {user_id}: {e}")

# ==================== Main Application ====================
async def post_init(application: Application):
    """Post initialization hook"""
    logger.info("Bot started successfully!")
    logger.info(f"Owner ID: {Config.OWNER_ID}")
    logger.info(f"Channel: {Config.CHANNEL_USERNAME}")

async def shutdown(application: Application):
    """Shutdown hook"""
    logger.info("Shutting down bot...")
    if hasattr(application, 'monitor'):
        application.monitor.is_running = False

def main():
    """Main entry point"""
    # Check for bot token
    if not Config.BOT_TOKEN:
        logger.error("BOT_TOKEN not set in environment variables!")
        return
    
    # Initialize database
    db = DatabaseManager(Config.DATA_FILE)
    
    # Initialize handlers
    handlers = BotHandlers(db)
    
    # Build application
    application = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    
    # Add command handlers
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("watch", handlers.start))  # Redirect to menu
    application.add_handler(CommandHandler("ban", handlers.start))    # Redirect to menu
    application.add_handler(CommandHandler("status", handlers.start)) # Redirect to menu
    application.add_handler(CommandHandler("list", handlers.start))   # Redirect to menu
    
    # Add callback handlers
    application.add_handler(CallbackQueryHandler(handlers.menu_callback, pattern="^menu_"))
    application.add_handler(CallbackQueryHandler(handlers.view_username, pattern="^view_"))
    application.add_handler(CallbackQueryHandler(handlers.handle_username_action, pattern="^(check_|remove_)"))
    application.add_handler(CallbackQueryHandler(handlers.handle_approval, pattern="^approve_"))
    application.add_handler(CallbackQueryHandler(handlers.menu_callback, pattern="^admin_"))
    application.add_handler(CallbackQueryHandler(handlers.menu_callback, pattern="^owner_"))
    application.add_handler(CallbackQueryHandler(handlers.menu_callback, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(handlers.menu_callback, pattern="^cancel$"))
    
    # Add message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))
    application.add_handler(MessageHandler(filters.TEXT & filters.COMMAND, handlers.handle_approval_days))
    
    # Add error handler
    application.add_error_handler(handlers.error_handler)
    
    # Start background monitor
    monitor = BackgroundMonitor(db, application)
    application.monitor = monitor
    asyncio.create_task(monitor.start_monitoring())
    
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Start bot
    logger.info("Starting bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
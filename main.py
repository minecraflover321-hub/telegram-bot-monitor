#!/usr/bin/env python3
"""
Instagram Username Monitor Bot - Enterprise Grade SaaS Solution
Developed by @proxyfxc | Channel: @proxydominates
Professional Instagram username monitoring with anti-false-alert system
"""

import os
import json
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
from contextlib import asynccontextmanager

from flask import Flask, jsonify
import requests
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

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))  # Set your Telegram ID as Owner
ADMIN_IDS = [int(id) for id in os.environ.get('ADMIN_IDS', '').split(',') if id]
MAX_WATCH_PER_USER = 20  # Normal users limit
CHECK_INTERVAL = 300  # 5 minutes in seconds
CONFIRMATION_THRESHOLD = 3  # Need 3 consecutive confirmations

# ==================== DATA MANAGEMENT ====================
DATA_FILE = 'data.json'

class DataManager:
    """Handles all persistent data operations with JSON"""
    
    def __init__(self):
        self.data = self.load_data()
        self.lock = asyncio.Lock()
    
    def load_data(self) -> Dict:
        """Load data from JSON file"""
        default_data = {
            'users': {},  # user_id: {'role': 'user', 'expiry': None, 'joined': str}
            'watch_list': {},  # username: {'user_id': int, 'status': str, 'confirm_count': int, 'details': dict}
            'ban_list': {},  # username: {'user_id': int, 'status': str, 'confirm_count': int, 'details': dict}
            'pending_confirmations': {},  # username: {'status': str, 'count': int, 'last_check': str}
            'admins': ADMIN_IDS.copy(),
            'owner': OWNER_ID,
            'stats': {
                'total_checks': 0,
                'alerts_sent': 0,
                'users_registered': 0
            }
        }
        
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r') as f:
                    loaded_data = json.load(f)
                    # Merge with default to ensure all keys exist
                    for key in default_data:
                        if key not in loaded_data:
                            loaded_data[key] = default_data[key]
                    return loaded_data
            return default_data
        except Exception as e:
            logging.error(f"Error loading data: {e}")
            return default_data
    
    async def save_data(self):
        """Save data to JSON file"""
        async with self.lock:
            try:
                with open(DATA_FILE, 'w') as f:
                    json.dump(self.data, f, indent=2, default=str)
            except Exception as e:
                logging.error(f"Error saving data: {e}")
    
    def get_user(self, user_id: int) -> Dict:
        """Get user data"""
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return {'role': 'user', 'expiry': None, 'joined': None}
        return self.data['users'][user_id]
    
    async def update_user(self, user_id: int, **kwargs):
        """Update user data"""
        user_id = str(user_id)
        if user_id not in self.data['users']:
            self.data['users'][user_id] = {'joined': datetime.now().isoformat()}
        self.data['users'][user_id].update(kwargs)
        await self.save_data()
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin or owner"""
        return user_id in self.data['admins'] or user_id == self.data['owner']
    
    def is_owner(self, user_id: int) -> bool:
        """Check if user is owner"""
        return user_id == self.data['owner']
    
    def can_monitor(self, user_id: int) -> Tuple[bool, str]:
        """Check if user can monitor (has active subscription or is admin/owner)"""
        user_id = str(user_id)
        
        # Admins and owner have unlimited access
        if int(user_id) in self.data['admins'] or int(user_id) == self.data['owner']:
            return True, "unlimited"
        
        user = self.get_user(int(user_id))
        if not user.get('expiry'):
            return False, "No active subscription"
        
        expiry = datetime.fromisoformat(user['expiry'])
        if expiry < datetime.now():
            return False, "Subscription expired"
        
        return True, "active"
    
    def get_user_watch_count(self, user_id: int) -> int:
        """Get number of usernames user is watching"""
        user_id = str(user_id)
        count = 0
        for username, data in self.data['watch_list'].items():
            if str(data.get('user_id')) == user_id:
                count += 1
        return count

# Initialize data manager
db = DataManager()

# ==================== FLASK KEEP-ALIVE SERVER ====================
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({
        'status': 'active',
        'service': 'Instagram Monitor Bot',
        'developer': '@proxyfxc',
        'channel': '@proxydominates',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'checks': db.data['stats']['total_checks']})

def run_flask():
    """Run Flask in a separate thread"""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ==================== INSTAGRAM API SIMULATOR ====================
class InstagramChecker:
    """Simulates Instagram profile checking - Replace with actual API"""
    
    @staticmethod
    async def check_username(username: str) -> Tuple[str, Dict]:
        """
        Check Instagram username status
        Returns: (status, profile_details)
        Status can be: 'ACTIVE', 'BANNED', 'UNKNOWN'
        """
        # Simulate API call - Replace with actual Instagram API
        await asyncio.sleep(1)  # Simulate network delay
        
        # This is a simulation - In production, use actual Instagram API
        # For demo purposes, we'll simulate different responses
        import random
        
        # Mock profile details
        details = {
            'name': f"{username.capitalize()} Profile",
            'followers': random.randint(100, 50000),
            'following': random.randint(50, 2000),
            'posts': random.randint(1, 500),
            'private': random.choice([True, False]),
            'verified': random.choice([True, False]),
            'business': random.choice([True, False]),
            'last_active': datetime.now().strftime('%Y-%m-%d')
        }
        
        # Simulate status (replace with actual API logic)
        status = random.choice(['ACTIVE', 'BANNED', 'ACTIVE', 'ACTIVE'])
        
        return status, details

# ==================== MONITORING ENGINE ====================
class MonitoringEngine:
    """Background monitoring engine with anti-false-alert system"""
    
    def __init__(self, application: Application):
        self.application = application
        self.is_running = False
        self.task = None
    
    async def check_single_username(self, username: str, user_id: int, is_ban_list: bool = False) -> Optional[str]:
        """Check a single username and handle status changes"""
        try:
            # Get current status
            status, details = await InstagramChecker.check_username(username)
            
            # Update confirmation counter
            pending_key = f"{username}_ban" if is_ban_list else username
            if pending_key not in db.data['pending_confirmations']:
                db.data['pending_confirmations'][pending_key] = {
                    'status': status,
                    'count': 1,
                    'last_check': datetime.now().isoformat(),
                    'details': details
                }
            else:
                pending = db.data['pending_confirmations'][pending_key]
                
                # If status matches, increment counter
                if pending['status'] == status:
                    pending['count'] += 1
                else:
                    # Reset counter on status change
                    pending['status'] = status
                    pending['count'] = 1
                    pending['details'] = details
                
                pending['last_check'] = datetime.now().isoformat()
            
            # Check if confirmation threshold reached
            if db.data['pending_confirmations'][pending_key]['count'] >= CONFIRMATION_THRESHOLD:
                await self.handle_status_change(
                    username, 
                    user_id, 
                    status, 
                    is_ban_list, 
                    db.data['pending_confirmations'][pending_key]['details']
                )
                # Reset counter after handling
                db.data['pending_confirmations'][pending_key]['count'] = 0
            
            await db.save_data()
            return status
            
        except Exception as e:
            logging.error(f"Error checking {username}: {e}")
            return None
    
    async def handle_status_change(self, username: str, user_id: int, new_status: str, 
                                   was_in_ban_list: bool, details: Dict):
        """Handle status change with alert"""
        try:
            if new_status == 'BANNED':
                # Move from watch to ban list
                if username in db.data['watch_list']:
                    db.data['ban_list'][username] = {
                        'user_id': user_id,
                        'status': new_status,
                        'details': details,
                        'banned_at': datetime.now().isoformat()
                    }
                    del db.data['watch_list'][username]
                    
                    # Send alert
                    await self.send_alert(user_id, username, 'BANNED', details)
                    
            elif new_status == 'ACTIVE':
                # Move from ban to watch list
                if username in db.data['ban_list']:
                    db.data['watch_list'][username] = {
                        'user_id': user_id,
                        'status': new_status,
                        'details': details,
                        'unbanned_at': datetime.now().isoformat()
                    }
                    del db.data['ban_list'][username]
                    
                    # Send alert
                    await self.send_alert(user_id, username, 'UNBANNED', details)
            
            db.data['stats']['alerts_sent'] += 1
            await db.save_data()
            
        except Exception as e:
            logging.error(f"Error handling status change: {e}")
    
    async def send_alert(self, user_id: int, username: str, status: str, details: Dict):
        """Send professional alert message to user"""
        try:
            # Format profile details
            profile_text = f"""
📊 **ACCOUNT DETAILS**
━━━━━━━━━━━━━━━━━━━━━
👤 **Name:** {details.get('name', 'N/A')}
👥 **Followers:** {details.get('followers', 0):,}
👤 **Following:** {details.get('following', 0):,}
📸 **Posts:** {details.get('posts', 0):,}
🔐 **Private:** {'Yes' if details.get('private') else 'No'}
⭐ **Verified:** {'Yes' if details.get('verified') else 'No'}
💼 **Business:** {'Yes' if details.get('business') else 'No'}
📅 **Last Active:** {details.get('last_active', 'N/A')}
━━━━━━━━━━━━━━━━━━━━━
"""
            
            status_emoji = {
                'BANNED': '🔴',
                'UNBANNED': '🟢',
                'ACTIVE': '✅'
            }.get(status, 'ℹ️')
            
            status_text = {
                'BANNED': '🚫 **BANNED DETECTED**',
                'UNBANNED': '🎉 **UNBANNED SUCCESSFULLY**',
                'ACTIVE': '✅ **STATUS: ACTIVE**'
            }.get(status, f'**STATUS: {status}**')
            
            alert_message = f"""
{status_emoji} {status_text}
━━━━━━━━━━━━━━━━━━━━━
📌 **Username:** @{username}
{profile_text}
⏰ **Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
━━━━━━━━━━━━━━━━━━━━━
⚡️ **Powered by** @proxyfxc
📢 **Channel:** @proxydominates
"""
            
            await self.application.bot.send_message(
                chat_id=user_id,
                text=alert_message,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logging.error(f"Failed to send alert to {user_id}: {e}")
    
    async def monitoring_loop(self):
        """Main monitoring loop"""
        while self.is_running:
            try:
                db.data['stats']['total_checks'] += 1
                
                # Check watch list
                watch_items = list(db.data['watch_list'].items())
                for username, data in watch_items[:]:  # Use slice copy to allow modification
                    await self.check_single_username(
                        username, 
                        data['user_id'], 
                        is_ban_list=False
                    )
                    await asyncio.sleep(2)  # Rate limiting
                
                # Check ban list
                ban_items = list(db.data['ban_list'].items())
                for username, data in ban_items[:]:
                    await self.check_single_username(
                        username, 
                        data['user_id'], 
                        is_ban_list=True
                    )
                    await asyncio.sleep(2)
                
                await db.save_data()
                
                # Wait for next check interval
                for _ in range(CHECK_INTERVAL // 10):
                    if not self.is_running:
                        break
                    await asyncio.sleep(10)
                    
            except Exception as e:
                logging.error(f"Monitoring loop error: {e}")
                await asyncio.sleep(60)
    
    async def start(self):
        """Start the monitoring engine"""
        self.is_running = True
        self.task = asyncio.create_task(self.monitoring_loop())
    
    async def stop(self):
        """Stop the monitoring engine"""
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

# ==================== TELEGRAM BOT HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with professional welcome"""
    user = update.effective_user
    
    # Register user
    await db.update_user(user.id)
    
    welcome_text = f"""
╔══════════════════════════╗
║   🚀 **INSTAGRAM MONITOR PRO**   ║
╚══════════════════════════╝

🌟 **Welcome, {user.first_name}!** 🌟

━━━━━━━━━━━━━━━━━━━━━
🔍 **Premium Instagram Username Monitoring**
✅ Real-time status tracking
🚫 Anti-false-alert system (3x confirmation)
📊 Detailed profile analytics
⚡️ Instant notifications

━━━━━━━━━━━━━━━━━━━━━
📋 **Your Status:**
├ 👤 Role: {db.data['admins'] and 'Admin' if user.id in db.data['admins'] else 'User'}
├ 📊 Watch Limit: {MAX_WATCH_PER_USER if not db.is_admin(user.id) else 'Unlimited'}
├ 🔋 Subscription: {db.get_user(user.id).get('expiry', 'Not set')[:10] if db.get_user(user.id).get('expiry') else 'Not Active'}
└ 📈 Monitored: {db.get_user_watch_count(user.id)}/{MAX_WATCH_PER_USER if not db.is_admin(user.id) else '∞'}

━━━━━━━━━━━━━━━━━━━━━
**Available Commands:**
/watch [username]  - Add to watch list
/ban [username]    - Add to ban list
/status           - View your lists
/help             - Show all commands

━━━━━━━━━━━━━━━━━━━━━
⚡️ **Powered by** @proxyfxc
📢 **Channel:** @proxydominates
"""
    
    keyboard = [
        [
            InlineKeyboardButton("📋 My Lists", callback_data="status"),
            InlineKeyboardButton("➕ Add Username", callback_data="add_menu")
        ],
        [
            InlineKeyboardButton("📊 Stats", callback_data="stats"),
            InlineKeyboardButton("💎 Subscribe", callback_data="subscribe")
        ],
        [
            InlineKeyboardButton("📢 Channel", url="https://t.me/proxydominates"),
            InlineKeyboardButton("👨‍💻 Developer", url="https://t.me/proxyfxc")
        ]
    ]
    
    if db.is_admin(user.id):
        keyboard.append([
            InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
        disable_web_page_preview=True
    )

async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /watch command to add username to watch list"""
    user_id = update.effective_user.id
    
    # Check permissions
    can_monitor, reason = db.can_monitor(user_id)
    if not can_monitor and not db.is_admin(user_id):
        await update.message.reply_text(
            "❌ **Access Denied**\n\n"
            f"Reason: {reason}\n\n"
            "Please contact an admin to get a subscription.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check if username provided
    if not context.args:
        await update.message.reply_text(
            "❌ **Usage:** `/watch username`\n\n"
            "Example: `/watch instagram`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    username = context.args[0].lower().strip('@')
    
    # Check if already in lists
    if username in db.data['watch_list']:
        await update.message.reply_text(
            f"⚠️ **Already Watching**\n\n"
            f"Username `@{username}` is already in your watch list.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if username in db.data['ban_list']:
        await update.message.reply_text(
            f"⚠️ **In Ban List**\n\n"
            f"Username `@{username}` is in your ban list. Use `/ban` to manage.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check limit for normal users
    if not db.is_admin(user_id):
        current_count = db.get_user_watch_count(user_id)
        if current_count >= MAX_WATCH_PER_USER:
            await update.message.reply_text(
                "❌ **Limit Reached**\n\n"
                f"You've reached your limit of {MAX_WATCH_PER_USER} usernames.\n"
                "Upgrade your subscription to add more.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
    
    # Add to watch list
    db.data['watch_list'][username] = {
        'user_id': user_id,
        'status': 'pending',
        'added_at': datetime.now().isoformat()
    }
    await db.save_data()
    
    await update.message.reply_text(
        f"✅ **Added to Watch List**\n\n"
        f"📌 Username: `@{username}`\n"
        f"👤 Added by: {update.effective_user.first_name}\n"
        f"📊 Position: {db.get_user_watch_count(user_id)}/{MAX_WATCH_PER_USER if not db.is_admin(user_id) else '∞'}\n\n"
        f"⏳ **Status:** Pending first check...\n"
        f"🔍 **Anti-False-Alert:** {CONFIRMATION_THRESHOLD}x confirmation required",
        parse_mode=ParseMode.MARKDOWN
    )

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ban command to add username directly to ban list"""
    user_id = update.effective_user.id
    
    # Similar permission checks as watch_command
    can_monitor, reason = db.can_monitor(user_id)
    if not can_monitor and not db.is_admin(user_id):
        await update.message.reply_text(
            "❌ **Access Denied**\n\n"
            f"Reason: {reason}",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ **Usage:** `/ban username`\n\n"
            "Example: `/ban instagram`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    username = context.args[0].lower().strip('@')
    
    # Check if already in lists
    if username in db.data['ban_list']:
        await update.message.reply_text(
            f"⚠️ **Already in Ban List**\n\n"
            f"Username `@{username}` is already in your ban list.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Add to ban list
    db.data['ban_list'][username] = {
        'user_id': user_id,
        'status': 'pending',
        'added_at': datetime.now().isoformat()
    }
    await db.save_data()
    
    await update.message.reply_text(
        f"✅ **Added to Ban List**\n\n"
        f"📌 Username: `@{username}`\n"
        f"📍 **List:** Ban List (Monitoring for UNBAN)\n"
        f"🔍 **Anti-False-Alert:** {CONFIRMATION_THRESHOLD}x confirmation required",
        parse_mode=ParseMode.MARKDOWN
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's watch and ban lists"""
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    
    # Get user's lists
    watch_items = []
    ban_items = []
    
    for username, data in db.data['watch_list'].items():
        if str(data.get('user_id')) == user_id_str:
            watch_items.append(username)
    
    for username, data in db.data['ban_list'].items():
        if str(data.get('user_id')) == user_id_str:
            ban_items.append(username)
    
    # Format lists
    watch_text = "\n".join([f"• `@{w}`" for w in watch_items]) or "• None"
    ban_text = "\n".join([f"• `@{b}`" for b in ban_items]) or "• None"
    
    # Get subscription info
    user_data = db.get_user(user_id)
    expiry = user_data.get('expiry', 'Not subscribed')
    if expiry and expiry != 'Not subscribed':
        expiry_dt = datetime.fromisoformat(expiry)
        days_left = (expiry_dt - datetime.now()).days
        expiry_text = f"{expiry[:10]} ({days_left} days left)"
    else:
        expiry_text = "Not subscribed"
    
    status_text = f"""
📊 **YOUR MONITORING STATUS**
━━━━━━━━━━━━━━━━━━━━━
👤 **User:** {update.effective_user.first_name}
🆔 **ID:** `{user_id}`
👑 **Role:** {'Admin' if db.is_admin(user_id) else 'User'}
💎 **Subscription:** {expiry_text}

━━━━━━━━━━━━━━━━━━━━━
🔍 **WATCH LIST** ({len(watch_items)}/{MAX_WATCH_PER_USER if not db.is_admin(user_id) else '∞'})
{watch_text}

━━━━━━━━━━━━━━━━━━━━━
🚫 **BAN LIST** ({len(ban_items)})
{ban_text}

━━━━━━━━━━━━━━━━━━━━━
📊 **Statistics:**
├ ✅ Active Checks: {len(watch_items) + len(ban_items)}
├ 🔄 Checks Today: {db.data['stats']['total_checks']}
└ ⚡️ Alerts Sent: {db.data['stats']['alerts_sent']}

━━━━━━━━━━━━━━━━━━━━━
⚡️ **Powered by** @proxyfxc
"""
    
    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="status"),
            InlineKeyboardButton("➕ Add More", callback_data="add_menu")
        ],
        [
            InlineKeyboardButton("❌ Remove", callback_data="remove_menu"),
            InlineKeyboardButton("📈 Upgrade", callback_data="subscribe")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        status_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )

# Admin Commands
async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to approve user subscription"""
    user_id = update.effective_user.id
    
    if not db.is_admin(user_id):
        await update.message.reply_text("❌ **Access Denied** - Admin only command.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ **Usage:** `/approve user_id days`\n\n"
            "Example: `/approve 123456789 30`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
        
        expiry_date = datetime.now() + timedelta(days=days)
        await db.update_user(target_id, expiry=expiry_date.isoformat())
        
        # Notify admin
        await update.message.reply_text(
            f"✅ **Subscription Approved**\n\n"
            f"👤 User ID: `{target_id}`\n"
            f"📅 Duration: {days} days\n"
            f"📆 Expires: {expiry_date.strftime('%Y-%m-%d')}",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"""
🎉 **SUBSCRIPTION ACTIVATED!**
━━━━━━━━━━━━━━━━━━━━━
✅ Your monitoring subscription has been approved!
📅 Duration: **{days} days**
📆 Expires: **{expiry_date.strftime('%Y-%m-%d')}**

━━━━━━━━━━━━━━━━━━━━━
🔍 You can now add usernames to monitor!
Use /watch to get started.

⚡️ **Powered by** @proxyfxc
""",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
            
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def addadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to add new admin"""
    user_id = update.effective_user.id
    
    if not db.is_owner(user_id):
        await update.message.reply_text("❌ **Access Denied** - Owner only command.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ **Usage:** `/addadmin user_id`\n\n"
            "Example: `/addadmin 123456789`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        target_id = int(context.args[0])
        
        if target_id not in db.data['admins']:
            db.data['admins'].append(target_id)
            await db.save_data()
            
            await update.message.reply_text(
                f"✅ **Admin Added Successfully**\n\n"
                f"👤 New Admin ID: `{target_id}`\n"
                f"👑 Added by: Owner",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Notify new admin
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"""
👑 **ADMIN PRIVILEGES GRANTED!**
━━━━━━━━━━━━━━━━━━━━━
🎉 Congratulations! You've been promoted to Admin.

**Your new powers:**
✅ Approve subscriptions
✅ Broadcast messages
✅ Unlimited monitoring
✅ Access admin panel

━━━━━━━━━━━━━━━━━━━━━
Use /admin to access admin panel.

⚡️ **Powered by** @proxyfxc
""",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await update.message.reply_text("⚠️ User is already an admin.")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to broadcast message to all users"""
    user_id = update.effective_user.id
    
    if not db.is_admin(user_id):
        await update.message.reply_text("❌ **Access Denied** - Admin only command.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ **Usage:** `/broadcast your message here`\n\n"
            "Example: `/broadcast Server maintenance in 1 hour`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    message = ' '.join(context.args)
    
    # Ask for confirmation
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Broadcast", callback_data=f"confirm_broadcast:{message}"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")
        ]
    ]
    
    await update.message.reply_text(
        f"📢 **Broadcast Confirmation**\n\n"
        f"**Message:**\n{message}\n\n"
        f"**Recipients:** {len(db.data['users'])} users\n\n"
        f"Are you sure you want to broadcast?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Callback Query Handler
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "status":
        # Show status
        user_id_str = str(user_id)
        
        watch_items = []
        ban_items = []
        
        for username, data in db.data['watch_list'].items():
            if str(data.get('user_id')) == user_id_str:
                watch_items.append(username)
        
        for username, data in db.data['ban_list'].items():
            if str(data.get('user_id')) == user_id_str:
                ban_items.append(username)
        
        watch_text = "\n".join([f"• `@{w}`" for w in watch_items]) or "• None"
        ban_text = "\n".join([f"• `@{b}`" for b in ban_items]) or "• None"
        
        status_text = f"""
📊 **YOUR MONITORING STATUS**
━━━━━━━━━━━━━━━━━━━━━
👤 **User:** {query.from_user.first_name}

━━━━━━━━━━━━━━━━━━━━━
🔍 **WATCH LIST** ({len(watch_items)}/{MAX_WATCH_PER_USER if not db.is_admin(user_id) else '∞'})
{watch_text}

━━━━━━━━━━━━━━━━━━━━━
🚫 **BAN LIST** ({len(ban_items)})
{ban_text}

━━━━━━━━━━━━━━━━━━━━━
⚡️ **Powered by** @proxyfxc
"""
        
        keyboard = [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="status"),
                InlineKeyboardButton("➕ Add", callback_data="add_menu")
            ]
        ]
        
        await query.edit_message_text(
            status_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == "add_menu":
        # Show add menu
        text = """
➕ **Add Username to Monitor**
━━━━━━━━━━━━━━━━━━━━━
Choose where to add the username:

🔍 **Watch List** - Monitor for BAN
🚫 **Ban List** - Monitor for UNBAN

━━━━━━━━━━━━━━━━━━━━━
Use commands:
• `/watch username` - Add to Watch List
• `/ban username` - Add to Ban List

━━━━━━━━━━━━━━━━━━━━━
⚡️ **Powered by** @proxyfxc
"""
        keyboard = [
            [
                InlineKeyboardButton("🔍 Watch List", callback_data="add_watch"),
                InlineKeyboardButton("🚫 Ban List", callback_data="add_ban")
            ],
            [InlineKeyboardButton("◀️ Back", callback_data="status")]
        ]
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == "admin_panel" and db.is_admin(user_id):
        # Show admin panel
        total_users = len(db.data['users'])
        total_watch = len(db.data['watch_list'])
        total_ban = len(db.data['ban_list'])
        active_subs = sum(1 for u in db.data['users'].values() if u.get('expiry') and datetime.fromisoformat(u['expiry']) > datetime.now())
        
        text = f"""
👑 **ADMIN CONTROL PANEL**
━━━━━━━━━━━━━━━━━━━━━
📊 **System Statistics:**
├ 👥 Total Users: {total_users}
├ 💎 Active Subs: {active_subs}
├ 🔍 Watch List: {total_watch}
└ 🚫 Ban List: {total_ban}

━━━━━━━━━━━━━━━━━━━━━
📈 **Performance:**
├ ✅ Total Checks: {db.data['stats']['total_checks']}
└ ⚡️ Alerts Sent: {db.data['stats']['alerts_sent']}

━━━━━━━━━━━━━━━━━━━━━
**Admin Commands:**
/approve user_id days
/broadcast message
/addadmin user_id (Owner only)
/stats - Detailed stats

━━━━━━━━━━━━━━━━━━━━━
⚡️ **Powered by** @proxyfxc
"""
        keyboard = [
            [
                InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
                InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")
            ],
            [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
            [InlineKeyboardButton("◀️ Back", callback_data="start")]
        ]
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data.startswith("confirm_broadcast:"):
        # Handle broadcast confirmation
        if not db.is_admin(user_id):
            await query.edit_message_text("❌ Access Denied")
            return
        
        message = data.split(":", 1)[1]
        success = 0
        failed = 0
        
        # Send to all users
        for uid in db.data['users'].keys():
            try:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=f"""
📢 **OFFICIAL BROADCAST**
━━━━━━━━━━━━━━━━━━━━━
{message}

━━━━━━━━━━━━━━━━━━━━━
📢 **Channel:** @proxydominates
⚡️ **Powered by** @proxyfxc
""",
                    parse_mode=ParseMode.MARKDOWN
                )
                success += 1
                await asyncio.sleep(0.05)  # Rate limiting
            except:
                failed += 1
        
        await query.edit_message_text(
            f"✅ **Broadcast Complete**\n\n"
            f"📨 Sent: {success}\n"
            f"❌ Failed: {failed}\n"
            f"👥 Total: {len(db.data['users'])}",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "cancel_broadcast":
        await query.edit_message_text("❌ Broadcast cancelled.")
    
    elif data == "subscribe":
        # Show subscription info
        text = f"""
💎 **PREMIUM SUBSCRIPTION**
━━━━━━━━━━━━━━━━━━━━━
**Free Tier:**
• Monitor up to {MAX_WATCH_PER_USER} usernames
• Basic alerts
• Standard support

**Premium Benefits:**
✅ Unlimited monitoring
✅ Priority checks
✅ Advanced analytics
✅ Priority support

━━━━━━━━━━━━━━━━━━━━━
**Pricing:**
• 30 days: Contact @proxyfxc
• 90 days: Contact @proxyfxc
• Lifetime: Contact @proxyfxc

━━━━━━━━━━━━━━━━━━━━━
To purchase, contact:
👨‍💻 **Developer:** @proxyfxc
📢 **Channel:** @proxydominates
"""
        keyboard = [
            [InlineKeyboardButton("👨‍💻 Contact Developer", url="https://t.me/proxyfxc")],
            [InlineKeyboardButton("◀️ Back", callback_data="start")]
        ]
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == "stats":
        # Show global stats
        text = f"""
📊 **SYSTEM STATISTICS**
━━━━━━━━━━━━━━━━━━━━━
👥 **Users:**
├ Total: {len(db.data['users'])}
├ Admins: {len(db.data['admins'])}
└ Owner: 1

━━━━━━━━━━━━━━━━━━━━━
📋 **Lists:**
├ Watch List: {len(db.data['watch_list'])}
├ Ban List: {len(db.data['ban_list'])}
└ Total Monitored: {len(db.data['watch_list']) + len(db.data['ban_list'])}

━━━━━━━━━━━━━━━━━━━━━
⚙️ **Performance:**
├ Total Checks: {db.data['stats']['total_checks']}
├ Alerts Sent: {db.data['stats']['alerts_sent']}
└ Check Interval: {CHECK_INTERVAL // 60} minutes

━━━━━━━━━━━━━━━━━━━━━
⚡️ **Powered by** @proxyfxc
"""
        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data="stats")],
            [InlineKeyboardButton("◀️ Back", callback_data="start")]
        ]
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# Error Handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler"""
    logging.error(f"Update {update} caused error {context.error}")
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ **An error occurred**\n\n"
                "Our team has been notified. Please try again later.",
                parse_mode=ParseMode.MARKDOWN
            )
    except:
        pass

# ==================== MAIN APPLICATION ====================

async def post_init(application: Application):
    """Initialize after application starts"""
    # Start monitoring engine
    monitoring_engine = MonitoringEngine(application)
    await monitoring_engine.start()
    application.monitoring_engine = monitoring_engine
    
    # Set bot commands
    commands = [
        ("start", "🚀 Start the bot"),
        ("watch", "🔍 Add username to watch list"),
        ("ban", "🚫 Add username to ban list"),
        ("status", "📊 View your lists"),
        ("help", "📚 Show all commands")
    ]
    
    if db.data['admins']:
        commands.extend([
            ("approve", "✅ Approve subscription (Admin)"),
            ("broadcast", "📢 Broadcast message (Admin)"),
            ("addadmin", "👑 Add admin (Owner only)")
        ])
    
    await application.bot.set_my_commands(commands)

async def shutdown(application: Application):
    """Clean shutdown"""
    if hasattr(application, 'monitoring_engine'):
        await application.monitoring_engine.stop()

def main():
    """Main entry point"""
    # Setup logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    
    # Start Flask in separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Create application
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("watch", watch_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("approve", approve_command))
    application.add_handler(CommandHandler("addadmin", addadmin_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)
    
    # Run bot
    print("🤖 Bot started successfully!")
    print(f"👑 Owner ID: {OWNER_ID}")
    print(f"👥 Admins: {ADMIN_IDS}")
    print("⚡️ Monitoring every 5 minutes")
    print("🔍 Anti-false-alert: 3x confirmation required")
    
    try:
        application.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    finally:
        asyncio.run(shutdown(application))

if __name__ == '__main__':
    main()

import os
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from modules.leech import register_leech_handlers, cancel_task
from modules.cookies import register_cookie_handlers
from modules.utils import ensure_dirs

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

# Environment variables
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")

if not API_ID or not API_HASH or not BOT_TOKEN:
    raise SystemExit("Please set API_ID, API_HASH, BOT_TOKEN environment variables.")

# Initialize Pyrogram Client
app = Client(
    "colab_leech_bot",  # session name
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

def home_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add cookies.txt", callback_data="cookies:add"),
            InlineKeyboardButton("🗑 Remove cookies.txt", callback_data="cookies:remove")
        ],
        [
            InlineKeyboardButton("📥 Leech/Mirror (send /leech <url>)", callback_data="noop"),
            InlineKeyboardButton("⛔ Cancel all downloads", callback_data="cancel_all")
        ]
    ])

# /start command
@app.on_message(filters.command("start"))
async def start_cmd(_, m: Message):
    ensure_dirs()
    await m.reply_text(
        "👋 **Welcome to Colab Leech Bot**\n\n"
        "✅ Features:\n"
        "   • Quality selection\n"
        "   • Progress bar\n"
        "   • Cookies management\n"
        "   • Cancel ongoing downloads\n"
        "▶ Usage: `/leech <url>`\n"
        "Use the buttons below to manage cookies or cancel downloads.",
        reply_markup=home_keyboard(),
        disable_web_page_preview=True
    )

# /cancel command to cancel all tasks
@app.on_message(filters.command("cancel"))
async def cancel_cmd(_, m: Message):
    cancel_task()  # Cancels all ongoing tasks
    await m.reply_text("⛔ All ongoing download processes have been cancelled.")

# Handle noop button
@app.on_callback_query(filters.regex("^noop$"))
async def ignore_noop(_, cq):
    await cq.answer("Use /leech <url> to start.", show_alert=False)

# Cancel all downloads via button
@app.on_callback_query(filters.regex("^cancel_all$"))
async def cancel_all_cb(_, cq):
    cancel_task()
    await cq.answer("⛔ All ongoing download processes cancelled.", show_alert=True)

# Register handlers
register_cookie_handlers(app)
register_leech_handlers(app)

if __name__ == "__main__":
    ensure_dirs()
    log.info("Starting bot…")
    app.run()

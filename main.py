import os
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

# Importing all necessary modules
from modules.leech import register_leech_handlers, ACTIVE_TASKS as LEECH_TASKS
from modules.ytdlp import register_ytdl_handlers, ACTIVE_TASKS as YTDL_TASKS
from modules.drive import register_drive_handlers, ACTIVE_TASKS as DRIVE_TASKS
from modules.utils import ensure_dirs, cancel_task
from modules.cookies import register_cookie_handlers

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

if not API_ID or not API_HASH or not BOT_TOKEN:
    raise SystemExit("Please set API_ID, API_HASH, BOT_TOKEN environment variables.")

app = Client(
    "colab_leech_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

def home_keyboard():
    """Generates the main keyboard with command buttons."""
    # Updated to include tasks from the new drive module
    tasks_count = len(LEECH_TASKS) + len(YTDL_TASKS) + len(DRIVE_TASKS)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add cookies.txt", callback_data="cookies:add"),
            InlineKeyboardButton("🗑 Remove cookies.txt", callback_data="cookies:remove")
        ],
        [
            InlineKeyboardButton("📥 Leech file (send /leech <url>)", callback_data="noop"),
            InlineKeyboardButton("📹 Download video (send /ytdl <url>)", callback_data="noop")
        ],
        [
            # New button for the drive command
            InlineKeyboardButton("📂 Drive File (send /drive <url>)", callback_data="noop")
        ],
        [
            InlineKeyboardButton(f"⛔ Cancel all ({tasks_count})", callback_data="cancel_all")
        ]
    ])

@app.on_message(filters.command("start"))
async def start_cmd(_, m: Message):
    """Handles the /start command."""
    ensure_dirs()
    await m.reply_text(
        "👋 **Welcome to Colab Leech Bot**\n\n"
        "✅ Features:\n"
        "  • Direct file download: `/leech <url>`\n"
        "  • Video download: `/ytdl <url>`\n"
        "  • Drive download: `/drive <url>`\n" # Updated message
        "  • Cookies management\n"
        "  • Cancel ongoing downloads\n"
        "Use the buttons below to manage cookies or cancel downloads.",
        reply_markup=home_keyboard(),
        disable_web_page_preview=True
    )

@app.on_message(filters.command("cancel"))
async def cancel_cmd(_, m: Message):
    """Handles the /cancel command to stop all active tasks."""
    cancel_task(LEECH_TASKS)
    cancel_task(YTDL_TASKS)
    cancel_task(DRIVE_TASKS) # Added this line for the new drive module
    await m.reply_text("⛔ All ongoing download processes have been cancelled.")

@app.on_callback_query(filters.regex("^noop$"))
async def ignore_noop(_, cq):
    """Ignores no-op callback queries."""
    await cq.answer("Use /leech <url>, /ytdl <url>, or /drive <url> to start a download.", show_alert=True)

@app.on_callback_query(filters.regex("^cancel_all$"))
async def cancel_all_cb(_, cq):
    """Handles the 'Cancel all' button to stop all tasks."""
    cancel_task(LEECH_TASKS)
    cancel_task(YTDL_TASKS)
    cancel_task(DRIVE_TASKS) # Added this line for the new drive module
    await cq.answer(f"⛔ All ongoing download processes cancelled.", show_alert=True)

@app.on_callback_query(filters.regex(r"^cookies:(add|remove)$"))
async def cookies_cb(_, cq):
    """Handles cookies-related button clicks."""
    action = cq.data.split(":")[1]
    if action == "add":
        await cq.answer("Send cookies.txt file to add.", show_alert=True)
    elif action == "remove":
        await cq.answer("Cookies removed.", show_alert=True)

# Register handlers for all modules
register_cookie_handlers(app)
register_leech_handlers(app)
register_ytdl_handlers(app)
register_drive_handlers(app) # New line to register handlers for the drive module

if __name__ == "__main__":
    ensure_dirs()
    log.info("Starting bot…")
    app.run()

import os
import logging
import threading
from flask import Flask
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

# ----------------- Flask keepalive -----------------
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "✅ Mongo Leech Bot is running!"

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# ----------------------------------------------------
# Optional ping endpoint
@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "message": "🏓 Pong! Service is online."})

if __name__ == "__main__":
    ts_ip, public_ip = start_tailscale()
    try:
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
    finally:
        cleanup_tailscale(ts_ip, public_ip)


app = Client(
    "colab_leech_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

def home_keyboard():
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
            InlineKeyboardButton("📂 Drive File (send /drive <url>)", callback_data="noop")
        ],
        [
            InlineKeyboardButton(f"⛔ Cancel all ({tasks_count})", callback_data="cancel_all")
        ]
    ])

@app.on_message(filters.command("start"))
async def start_cmd(_, m: Message):
    ensure_dirs()
    await m.reply_text(
        "👋 **Welcome to Colab Leech Bot**\n\n"
        "✅ Features:\n"
        "  • Direct file download: `/leech <url>`\n"
        "  • Video download: `/ytdl <url>`\n"
        "  • Drive download: `/drive <url>`\n"
        "  • Cookies management\n"
        "  • Cancel ongoing downloads\n",
        reply_markup=home_keyboard(),
        disable_web_page_preview=True
    )

@app.on_message(filters.command("cancel"))
async def cancel_cmd(_, m: Message):
    cancel_task(LEECH_TASKS)
    cancel_task(YTDL_TASKS)
    cancel_task(DRIVE_TASKS)
    await m.reply_text("⛔ All ongoing download processes have been cancelled.")

@app.on_callback_query(filters.regex("^noop$"))
async def ignore_noop(_, cq):
    await cq.answer("Use /leech <url>, /ytdl <url>, or /drive <url> to start a download.", show_alert=True)

@app.on_callback_query(filters.regex("^cancel_all$"))
async def cancel_all_cb(_, cq):
    cancel_task(LEECH_TASKS)
    cancel_task(YTDL_TASKS)
    cancel_task(DRIVE_TASKS)
    await cq.answer("⛔ All ongoing download processes cancelled.", show_alert=True)

@app.on_callback_query(filters.regex(r"^cookies:(add|remove)$"))
async def cookies_cb(_, cq):
    action = cq.data.split(":")[1]
    if action == "add":
        await cq.answer("Send cookies.txt file to add.", show_alert=True)
    elif action == "remove":
        await cq.answer("Cookies removed.", show_alert=True)

# Register handlers
register_cookie_handlers(app)
register_leech_handlers(app)
register_ytdl_handlers(app)
register_drive_handlers(app)

if __name__ == "__main__":
    ensure_dirs()
    log.info("Starting bot…")
    # Start Flask keepalive in a thread
    threading.Thread(target=run_flask).start()
    # Run Pyrogram bot
    app.run()




## **main.py (patched)**


import os
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from modules.leech import register_leech_handlers
from modules.cookies import register_cookie_handlers
from modules.utils import ensure_dirs

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
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add cookies.txt", callback_data="cookies:add"),
            InlineKeyboardButton("🗑 Remove cookies.txt", callback_data="cookies:remove")
        ],
        [
            InlineKeyboardButton("📥 Leech/Mirror (send /leech <url>)", callback_data="noop")
        ],
    ])

@app.on_message(filters.command("start"))
async def start_cmd(_, m: Message):
    ensure_dirs()
    await m.reply_text(
        "👋 **Welcome to Colab Leech Bot**\n\n"
        "✅ Features: quality selection, progress bar, cookies, cancel button.\n"
        "▶ Usage: `/leech <url>`",
        reply_markup=home_keyboard(),
        disable_web_page_preview=True,
    )

@app.on_callback_query(filters.regex("^noop$"))
async def ignore_noop(_, cq):
    await cq.answer("Use /leech <url> to start.", show_alert=False)

# register handlers
register_cookie_handlers(app)
register_leech_handlers(app)

if __name__ == "__main__":
    ensure_dirs()
    log.info("Starting bot…")
    app.run()

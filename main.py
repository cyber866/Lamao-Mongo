import logging
import os
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from modules.leech import register_leech_handlers, ACTIVE_TASKS, cancel_task
from modules.ytdlp import register_ytdlp_handlers
from modules.cookies import add_cookies, remove_cookies, cookies_status
from modules.utils import start_cleanup

logging.basicConfig(level=logging.INFO)

# ---------------- CONFIG ----------------
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH"))
BOT_TOKEN = os.environ.get("BOT_TOKEN")

app = Client("mongo-leech", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---------------- START ----------------
@app.on_message(filters.command("start"))
async def start_cmd(_, message):
    await message.reply_text(
        "👋 Welcome to Mongo-Leech Bot!\n\n"
        "Use /leech <link> for general files 📁\n"
        "Use /ytdlp <link> for streamable videos 🎥\n\n"
        "You can also /cancel any running task ❌"
    )

# ---------------- CANCEL ----------------
@app.on_message(filters.command("cancel"))
async def cancel_cmd(_, message):
    chat_id = message.chat.id
    if chat_id in ACTIVE_TASKS and ACTIVE_TASKS[chat_id]:
        task = ACTIVE_TASKS[chat_id]
        cancel_task(chat_id)
        await message.reply_text("✅ Current task has been cancelled successfully.")
    else:
        await message.reply_text("⚠️ No active task to cancel.")

# ---------------- COOKIES ----------------
@app.on_message(filters.command("addcookies"))
async def add_cookies_cmd(_, message):
    if not message.reply_to_message or not message.reply_to_message.document:
        return await message.reply_text("⚠️ Please reply to a cookies.txt file.")
    await add_cookies(message)

@app.on_message(filters.command("removecookies"))
async def remove_cookies_cmd(_, message):
    await remove_cookies(message)

@app.on_message(filters.command("cookies"))
async def cookies_status_cmd(_, message):
    await cookies_status(message)

# ---------------- REGISTER HANDLERS ----------------
register_leech_handlers(app)
register_ytdlp_handlers(app)

# ---------------- CLEANUP ----------------
@app.on_message(filters.command("cleanup"))
async def cleanup_cmd(_, message):
    await start_cleanup(message)

# ---------------- RUN ----------------
if __name__ == "__main__":
    logging.info("✅ Mongo-Leech Bot Started...")
    app.run()

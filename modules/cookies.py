import os
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from .utils import ensure_dirs, cookies_col

def register_cookie_handlers(app: Client):

    @app.on_callback_query(filters.regex(r"^cookies:add$"))
    async def add_cookies_cb(_, cq):
        user_id = cq.from_user.id
        ensure_dirs()
        existing = cookies_col.find_one({"user_id": user_id})
        if existing:
            await cq.answer("⚠ Cookies already exist! Remove first to add new.", show_alert=True)
        else:
            await cq.answer("📥 Send your cookies.txt file in chat.", show_alert=True)

    @app.on_callback_query(filters.regex(r"^cookies:remove$"))
    async def remove_cookies_cb(_, cq):
        user_id = cq.from_user.id
        ensure_dirs()
        cookies_col.delete_one({"user_id": user_id})
        cookie_path = os.path.join("./data/cookies", f"{user_id}_cookies.txt")
        if os.path.exists(cookie_path):
            os.remove(cookie_path)
        await cq.answer("✅ cookies.txt removed.", show_alert=True)

    @app.on_message(filters.document & filters.private)
    async def save_cookies_file(_, m):
        if not m.document.file_name.endswith(".txt"):
            return await m.reply("❌ Only .txt files are allowed for cookies.")
        user_id = m.from_user.id
        ensure_dirs()
        file_path = os.path.join("./data/cookies", f"{user_id}_cookies.txt")
        await m.download(file_path)
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        cookies_col.update_one({"user_id": user_id}, {"$set": {"content": content}}, upsert=True)
        await m.reply("✅ cookies.txt saved successfully!")

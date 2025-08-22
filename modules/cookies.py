import os
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from .utils import ensure_dirs, cookies_col, data_paths

def register_cookie_handlers(app):
    @app.on_message(filters.document & filters.private)
    async def handle_cookies_file(_, m: Message):
        """Handle user sending cookies.txt"""
        if not m.document.file_name.endswith(".txt"):
            return await m.reply("❌ Only .txt files are supported for cookies.")

        user_id = m.from_user.id
        paths = data_paths(user_id)
        ensure_dirs()

        # Download the cookies file
        file_path = paths["cookies"]
        await m.download(file_path)
        await m.reply(f"✅ cookies.txt saved for user `{user_id}`")

        # Save to MongoDB if available
        if cookies_col:
            with open(file_path, "r", encoding="utf-8") as f:
                cookies_content = f.read()
            cookies_col.update_one(
                {"user_id": user_id},
                {"$set": {"cookies": cookies_content}},
                upsert=True
            )

    @app.on_callback_query(filters.regex(r"^cookies:(add|remove)$"))
    async def cookies_cb(_, cq):
        action = cq.data.split(":")[1]
        user_id = cq.from_user.id
        paths = data_paths(user_id)
        ensure_dirs()

        if action == "add":
            await cq.answer("Send your cookies.txt file now.", show_alert=True)
        elif action == "remove":
            # Remove local file
            if os.path.exists(paths["cookies"]):
                os.remove(paths["cookies"])

            # Remove from MongoDB
            if cookies_col:
                cookies_col.delete_one({"user_id": user_id})

            await cq.answer("✅ Cookies removed.", show_alert=True)

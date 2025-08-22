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

        # Ensure parent folder exists (Colab-safe)
        os.makedirs(os.path.dirname(paths["cookies"]), exist_ok=True)

        # Download the cookies file
        file_path = paths["cookies"]
        await m.download(file_path)
        await m.reply(f"✅ cookies.txt saved for user `{user_id}`")

        # Save to MongoDB if available
        if cookies_col is not None:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    cookies_content = f.read()
                cookies_col.update_one(
                    {"user_id": user_id},
                    {"$set": {"cookies": cookies_content}},
                    upsert=True
                )
            except Exception as e:
                await m.reply(f"⚠ Warning: Could not save to MongoDB: {e}")

    @app.on_callback_query(filters.regex(r"^cookies:(add|remove)$"))
    async def cookies_cb(_, cq):
        action = cq.data.split(":")[1]
        user_id = cq.from_user.id
        paths = data_paths(user_id)
        ensure_dirs()

        if action == "add":
            await cq.answer("Send your cookies.txt file now.", show_alert=True)

        elif action == "remove":
            # Colab-safe: remove all cookies files for this user
            cookies_dir = os.path.dirname(paths["cookies"])
            removed_local = False
            if os.path.exists(cookies_dir):
                for f in os.listdir(cookies_dir):
                    if f.startswith(f"{user_id}_") and f.endswith(".txt"):
                        try:
                            os.remove(os.path.join(cookies_dir, f))
                            removed_local = True
                        except:
                            continue

            # Remove from MongoDB if available
            removed_db = False
            if cookies_col is not None:
                try:
                    cookies_col.delete_one({"user_id": user_id})
                    removed_db = True
                except:
                    removed_db = False

            # Feedback to user
            if removed_local or removed_db:
                await cq.answer("✅ Cookies removed.", show_alert=True)
            else:
                await cq.answer("❌ No cookies found.", show_alert=True)

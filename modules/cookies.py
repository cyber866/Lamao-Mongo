import os
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from .utils import ensure_dirs, cookies_col, data_paths

def register_cookie_handlers(app: Client):

    @app.on_callback_query(filters.regex(r"^cookies:add$"))
    async def add_cookie_cb(_, cq):
        user_id = cq.from_user.id
        ensure_dirs()
        path = data_paths(user_id)["cookies"]
        if os.path.exists(path):
            await cq.answer("❌ You already have a cookies.txt file.", show_alert=True)
        else:
            cookies_col.update_one(
                {"user_id": user_id},
                {"$set": {"status": "pending"}},
                upsert=True
            )
            await cq.answer("✅ Send me your cookies.txt file in private chat.", show_alert=True)

    @app.on_callback_query(filters.regex(r"^cookies:remove$"))
    async def remove_cookie_cb(_, cq):
        user_id = cq.from_user.id
        ensure_dirs()
        path = data_paths(user_id)["cookies"]
        if os.path.exists(path):
            os.remove(path)
        cookies_col.delete_one({"user_id": user_id})
        await cq.answer("🗑 Your cookies.txt file has been removed.", show_alert=True)

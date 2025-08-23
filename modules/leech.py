import asyncio
import os
import logging
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ---------------- ACTIVE TASKS ----------------
ACTIVE_TASKS = {}  # {chat_id: asyncio.Task}

# ---------------- CANCEL HANDLER ----------------
def cancel_task(chat_id: int):
    if chat_id in ACTIVE_TASKS and ACTIVE_TASKS[chat_id]:
        task = ACTIVE_TASKS[chat_id]
        if not task.done():
            task.cancel()
        ACTIVE_TASKS.pop(chat_id, None)

# ---------------- FILE DOWNLOAD ----------------
async def handle_leech(client, message):
    chat_id = message.chat.id
    if chat_id in ACTIVE_TASKS and ACTIVE_TASKS[chat_id]:
        return await message.reply_text("⚠️ A task is already running. Please /cancel first.")

    if len(message.command) < 2:
        return await message.reply_text("⚠️ Usage: `/leech <url>`", quote=True)

    url = message.command[1]

    status_msg = await message.reply_text(
        f"⬇️ Starting leech: `{url}`",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{chat_id}")]]
        )
    )

    async def leech_task():
        try:
            await asyncio.sleep(5)  # simulate download
            await status_msg.edit_text(f"✅ Download completed for:\n`{url}`")
        except asyncio.CancelledError:
            await status_msg.edit_text(f"❌ Leech cancelled for:\n`{url}`")

    task = asyncio.create_task(leech_task())
    ACTIVE_TASKS[chat_id] = task

# ---------------- CALLBACK BUTTON ----------------
async def cancel_callback(client, callback_query):
    chat_id = callback_query.message.chat.id
    if chat_id in ACTIVE_TASKS:
        cancel_task(chat_id)
        await callback_query.message.edit_text("❌ Task cancelled by user.")

# ---------------- REGISTER ----------------
def register_leech_handlers(app):
    app.add_handler(filters.command("leech")(handle_leech))
    app.add_handler(filters.callback_query("cancel")(cancel_callback))

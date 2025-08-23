from pyrogram import Client, filters
from pyrogram.types import CallbackQuery
from .utils import cancel_task


# ------------------------- Cancel button -------------------------
@Client.on_callback_query(filters.regex(r"^cancel:(.+)"))
async def cancel_callback(client: Client, query: CallbackQuery):
    chat_id = query.message.chat.id
    task_id = query.data.split(":", 1)[1]

    cancel_task(chat_id, task_id)

    await query.answer("🚫 Cancelled")
    try:
        await query.message.edit_text("❌ Task cancelled by user.")
    except:
        pass

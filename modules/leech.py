import os
import asyncio
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import Message

from .utils import get_cancel_button, is_cancelled, set_cancel_flag, run_with_cancel
from .file_spliter import split_file

# ------------------------- /leech command -------------------------
@Client.on_message(filters.command("leech") & filters.private)
async def leech_handler(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("⚠️ Usage: `/leech <url>`", quote=True)

    url = message.command[1]
    chat_id = message.chat.id
    task_id = f"leech_{message.id}"

    status = await message.reply_text(
        f"📥 Starting leech for:\n`{url}`",
        reply_markup=get_cancel_button(task_id),
        quote=True,
    )

    async def process():
        try:
            ydl_opts = {
                "outtmpl": "downloads/%(title)s.%(ext)s",
                "noplaylist": True,
                "quiet": True,
                "progress_hooks": [lambda d: asyncio.create_task(update_status(d))],
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = ydl.prepare_filename(info)

            if is_cancelled(chat_id, task_id):
                return await status.edit_text("🚫 Task cancelled before upload.")

            # Handle large files by splitting
            if os.path.getsize(file_path) > 1900 * 1024 * 1024:  # ~1.9 GB
                parts = split_file(file_path)
                for part in parts:
                    if is_cancelled(chat_id, task_id):
                        return await status.edit_text("🚫 Task cancelled during upload.")
                    await client.send_document(chat_id, part)
                    os.remove(part)
            else:
                await client.send_document(chat_id, file_path)

            await status.edit_text("✅ Upload completed!")

            if os.path.exists(file_path):
                os.remove(file_path)

        except Exception as e:
            await status.edit_text(f"❌ Error: `{e}`")

    async def update_status(d):
        if d["status"] == "downloading":
            text = f"⬇️ Downloading: {d.get('filename','')}\n" \
                   f"Progress: {d.get('_percent_str','')}, " \
                   f"Speed: {d.get('_speed_str','')}"
            try:
                await status.edit_text(text, reply_markup=get_cancel_button(task_id))
            except:
                pass

    # Run with cancel support
    set_cancel_flag(chat_id, task_id)  # register for cancellation
    await run_with_cancel(process(), chat_id, task_id, status)

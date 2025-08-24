#
# This module handles the /drive command for downloading direct file
# links from services like Google Drive. It uses the gdown library
# for reliable and automated Google Drive downloads.
#

import os
import uuid
import logging
import asyncio
import time
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
import gdown

# Assuming these imports are correct based on your project structure.
from .utils import data_paths, ensure_dirs, humanbytes, DownloadCancelled, safe_edit_text
from .file_splitter import split_file

log = logging.getLogger("drive")
ACTIVE_TASKS = {} # This is for drive tasks

# ---------------- Telegram-safe split size ----------------
MAX_SIZE = 1900 * 1024 * 1024 # 1900 MiB ≈ 1.86 GiB

def cancel_btn(tid):
    """
    Creates an inline keyboard with a single "Cancel" button.
    """
    return InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Cancel", callback_data=f"cancel_drive:{tid}")]])

def get_progress_bar(percentage):
    """Generates a progress bar string."""
    filled_length = int(percentage // 5)
    bar = "█" * filled_length + "░" * (20 - filled_length)
    return f"`[{bar}]`"

def register_drive_handlers(app: Client):
    """
    Registers the command and callback query handlers for the drive module.
    """
    @app.on_message(filters.command("drive") & (filters.private | filters.group))
    async def cmd_drive(_, m: Message):
        args = m.text.split(maxsplit=1)
        if len(args) < 2:
            return await m.reply("Usage: `/drive <file URL>`")
        
        url = args[1].strip()
        user_id = m.from_user.id
        paths = data_paths(user_id)
        ensure_dirs()
        
        tid = str(uuid.uuid4())[:8]
        ACTIVE_TASKS[tid] = {"user_id": user_id, "url": url, "msg_id": None, "cancel": False}
        
        msg = await m.reply("⏳ Starting download...", reply_markup=cancel_btn(tid))
        ACTIVE_TASKS[tid]["msg_id"] = msg.id
        
        asyncio.create_task(download_file(app, url, msg, paths, tid))
        
    @app.on_callback_query(filters.regex(r"^cancel_drive:(.+)$"))
    async def cancel_drive_cb(_, q):
        tid = q.data.split(":")[1]
        if tid in ACTIVE_TASKS:
            ACTIVE_TASKS[tid]["cancel"] = True
            await q.answer("⛔ Task cancelled.", show_alert=True)
        else:
            await q.answer("❌ Task not found.", show_alert=True)
            
def _gdown_progress_callback(filename, current_chunk, total_chunks, total_size, start_time, tid, msg, app):
    """
    Callback function for gdown to update download progress.
    """
    now = time.time()
    if now - start_time > 2: # Update progress every 2 seconds
        percentage = (current_chunk / total_size) * 100
        
        progress_text = f"**Downloading**:\n"
        progress_text += f"**File:** `{filename}`\n"
        progress_text += f"{get_progress_bar(percentage)} **{percentage:.1f}%**\n"
        progress_text += f"**Size:** {humanbytes(current_chunk)} / {humanbytes(total_size)}"
        
        # Using a new task to avoid blocking gdown's thread
        asyncio.create_task(safe_edit_text(msg, progress_text, reply_markup=cancel_btn(tid)))

async def download_file(app, url, msg, paths, tid):
    """
    Downloads a file from a URL with a progress bar and sends it.
    """
    try:
        # Use gdown to handle the download and all redirects
        full_path = os.path.join(paths["downloads"], os.path.basename(url)) # Initial path for gdown
        
        start_time = time.time()
        
        # We need a partial function to pass our arguments to the callback
        callback_with_args = lambda filename, current_chunk, total_chunks, total_size: _gdown_progress_callback(
            filename, current_chunk, total_chunks, total_size, start_time, tid, msg, app
        )

        await safe_edit_text(msg, "⏳ Starting download with gdown...", reply_markup=cancel_btn(tid))
        
        # Use asyncio.to_thread to run the blocking gdown call without freezing the bot
        file_path = await asyncio.to_thread(
            gdown.download, url, output=full_path, fuzzy=True, quiet=False,
            # This is a custom callback function for gdown.
            # It sends the progress updates back to the Telegram message.
            # Unfortunately, there's no native async callback, so we have to do this.
            _gdown_progress_callback=_gdown_progress_callback
        )
        
        # gdown returns the final path if it's different from the initial output.
        full_path = file_path
        
        await safe_edit_text(msg, "✅ Download complete. Uploading file...")
        
        # Check if file needs to be split
        filesize = os.path.getsize(full_path)
        if filesize > MAX_SIZE:
            await safe_edit_text(msg, f"✅ Download complete. Splitting file into parts…")
            fpaths = await asyncio.to_thread(split_file, full_path, MAX_SIZE)
            os.remove(full_path)
        else:
            fpaths = [full_path]
            
        total_parts = len(fpaths)
        for idx, fpath in enumerate(fpaths, 1):
            if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                raise DownloadCancelled()
                
            last_upload_update = 0
            async def upload_progress(cur, tot):
                nonlocal last_upload_update
                now = time.time()
                if now - last_upload_update < 2:
                    return
                last_upload_update = now
                frac = cur / tot * 100 if tot else 0
                bar = get_progress_bar(frac)
                part_name = os.path.basename(fpath)
                await safe_edit_text(
                    msg,
                    f"**Uploading part {idx}/{total_parts}**:\n"
                    f"`{part_name}`\n"
                    f"{bar} **{frac:.1f}%**\n"
                    f"**Size:** {humanbytes(cur)} / {humanbytes(tot)}",
                    reply_markup=cancel_btn(tid)
                )
                if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                    raise DownloadCancelled()
            
            await app.send_document(
                msg.chat.id,
                fpath,
                caption=f"✅ Uploaded part {idx}/{total_parts}: `{os.path.basename(fpath)}`",
                progress=upload_progress
            )
            os.remove(fpath) # Clean up the uploaded part
            
        await safe_edit_text(msg, "✅ All parts uploaded successfully!")

    except DownloadCancelled:
        await safe_edit_text(msg, "❌ Download/Upload cancelled.")
    except Exception as e:
        await safe_edit_text(msg, f"❌ Error: {e}")
    finally:
        ACTIVE_TASKS.pop(tid, None)
        if 'full_path' in locals() and os.path.exists(full_path):
            os.remove(full_path)

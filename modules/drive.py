#
# This module handles the /drive command for downloading direct file
# links from services like Google Drive. It uses the gdown library
# to get the final download URL and then handles the stream to show
# download progress with aiohttp.
#

import os
import uuid
import logging
import asyncio
import time
import requests
import aiohttp
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

async def download_file(app, url, msg, paths, tid):
    """
    Downloads a file from a URL with a progress bar and sends it.
    """
    full_path = ""
    try:
        # Step 1: Resolve the final download URL using gdown
        # We use asyncio.to_thread because gdown.download is a blocking, synchronous function.
        await safe_edit_text(msg, "🔍 Resolving Google Drive URL...", reply_markup=cancel_btn(tid))
        
        # gdown.download is a cleaner way to get the final URL
        final_url = await asyncio.to_thread(gdown.download, url, output=None, fuzzy=True, quiet=True)
        
        if not final_url:
            raise Exception("Failed to resolve the direct download link.")
            
        fname = os.path.basename(final_url.split('?')[0])
        full_path = os.path.join(paths["downloads"], fname)
        
        await safe_edit_text(msg, f"Starting download of: `{fname}`", reply_markup=cancel_btn(tid))
        
        # Step 2: Stream the download with aiohttp and a progress bar
        async with aiohttp.ClientSession() as session:
            async with session.get(final_url) as response:
                response.raise_for_status() # Raise an error for bad status codes
                
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                last_update = 0
                
                with open(full_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                            raise DownloadCancelled()
                        
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        now = time.time()
                        if now - last_update > 2: # Update progress every 2 seconds
                            last_update = now
                            percentage = (downloaded / total_size) * 100 if total_size else 0
                            
                            progress_text = f"**Downloading**:\n"
                            progress_text += f"**File:** `{fname}`\n"
                            progress_text += f"{get_progress_bar(percentage)} **{percentage:.1f}%**\n"
                            progress_text += f"**Size:** {humanbytes(downloaded)} / {humanbytes(total_size)}"
                            
                            await safe_edit_text(msg, progress_text, reply_markup=cancel_btn(tid))
        
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

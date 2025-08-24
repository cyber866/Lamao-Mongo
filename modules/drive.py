#
# This module handles the /drive command for downloading direct file
# links from services like Google Drive using the gdown library.
#

import os
import uuid
import logging
import asyncio
import re
import glob
import time
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

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
            await q.answer("❌ Task not found.")

async def download_file(app, url, msg, paths, tid):
    """
    Downloads a file from a URL using gdown and then uploads it to Telegram.
    This version includes robust retries for the upload phase and better
    error handling to prevent silent failures.
    """
    file_path = ""
    download_dir = paths["downloads"]
    
    try:
        await safe_edit_text(msg, "🔍 Starting download...", reply_markup=cancel_btn(tid))
        
        # We'll use the gdown library to handle the download.
        try:
            import gdown
        except ImportError:
            await safe_edit_text(msg, "❌ The `gdown` library is not installed. Please install it with `pip install gdown`.")
            return

        # Extract the Google Drive file ID from the URL using a regex.
        match = re.search(r'id=([a-zA-Z0-9_-]+)', url)
        if not match:
            raise ValueError("Invalid Google Drive URL. Could not find a file ID.")
        
        file_id = match.group(1)

        # Download the file to a temporary directory.
        await asyncio.to_thread(gdown.download, id=file_id, output=download_dir, fuzzy=True, quiet=True)

        # After download, we need to figure out the actual file name.
        list_of_files = glob.glob(os.path.join(download_dir, '*'))
        if not list_of_files:
            raise FileNotFoundError("Gdown finished, but no file was found in the download directory.")
        
        # The file gdown downloads might not be named what we expect. Find the newest file.
        file_path = max(list_of_files, key=os.path.getctime)
        
        # Check if the file is empty.
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            raise Exception("Downloaded file is empty or does not exist.")

        # --- Upload Logic ---
        await safe_edit_text(msg, "✅ Download complete. Preparing for upload...", reply_markup=cancel_btn(tid))
        
        filesize = os.path.getsize(file_path)
        if filesize > MAX_SIZE:
            await safe_edit_text(msg, f"✅ Download complete. Splitting file into parts…", reply_markup=cancel_btn(tid))
            fpaths = await asyncio.to_thread(split_file, file_path, MAX_SIZE)
            os.remove(file_path) # Delete the original large file
        else:
            fpaths = [file_path]
            
        total_parts = len(fpaths)
        
        for idx, fpath in enumerate(fpaths, 1):
            if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                raise DownloadCancelled()
                
            # Define the upload progress callback
            last_upload_update = 0
            async def upload_progress(current, total):
                nonlocal last_upload_update
                now = time.time()
                # Update the message only every 2 seconds to avoid flooding Telegram's API
                if now - last_upload_update < 2:
                    return
                last_upload_update = now
                
                percentage = (current / total) * 100 if total else 0
                part_name = os.path.basename(fpath)
                
                progress_text = f"**Uploading part {idx}/{total_parts}**:\n"
                progress_text += f"`{part_name}`\n"
                progress_text += f"{get_progress_bar(percentage)} **{percentage:.1f}%**\n"
                progress_text += f"**Size:** {humanbytes(current)} / {humanbytes(total)}"
                
                await safe_edit_text(msg, progress_text, reply_markup=cancel_btn(tid))
            
            # Use a retry loop for the upload
            retries = 3
            for attempt in range(retries):
                try:
                    await safe_edit_text(msg, f"**Attempt {attempt + 1}/{retries}:** Uploading part {idx}/{total_parts}...", reply_markup=cancel_btn(tid))
                    
                    # Ensure the file path still exists right before upload
                    if not os.path.exists(fpath):
                        raise FileNotFoundError(f"File to upload not found: {fpath}")
                    
                    await app.send_document(
                        msg.chat.id,
                        fpath,
                        caption=f"✅ Uploaded part {idx}/{total_parts}: `{os.path.basename(fpath)}`",
                        progress=upload_progress
                    )
                    log.info(f"Successfully uploaded part {idx}.")
                    break  # Exit the retry loop on success
                except Exception as upload_e:
                    log.error(f"Error during file upload on attempt {attempt + 1}: {upload_e}")
                    if attempt < retries - 1:
                        await asyncio.sleep(5)  # Wait 5 seconds before retrying
                    else:
                        raise  # Re-raise the exception after all retries fail

        await safe_edit_text(msg, "✅ All parts uploaded successfully!")

    except DownloadCancelled:
        await safe_edit_text(msg, "❌ Download/Upload cancelled.")
    except Exception as e:
        log.error(f"Error in drive command: {e}")
        await safe_edit_text(msg, f"❌ An unexpected error occurred: {e}")
    finally:
        ACTIVE_TASKS.pop(tid, None)
        # Clean up any remaining files from the download process
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        # Clean up any split parts
        for part_file in glob.glob(os.path.join(download_dir, 'split_part_*')):
            if os.path.exists(part_file):
                os.remove(part_file)

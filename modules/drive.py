#
# This module handles the /drive command for downloading direct file
# links from services like Google Drive using the gdown library.
#

import os
import uuid
import logging
import asyncio
import re
import time
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

# We need to import gdown here to catch its specific exceptions.
import gdown.exceptions
import gdown

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
        
        # The initial message explains the download/upload process
        msg = await m.reply("⏳ Starting download...", reply_markup=cancel_btn(tid))
        ACTIVE_TASKS[tid]["msg_id"] = msg.id
        
        asyncio.create_task(download_file(app, url, msg, paths, tid))
        
    @app.on_callback_query(filters.regex(r"^cancel_drive:(.+)$"))
    async def cancel_drive_cb(_, q):
        tid = q.data.split(":")[1]
        if tid in ACTIVE_TASKS:
            # We set the flag to True and also edit the message to provide immediate feedback
            ACTIVE_TASKS[tid]["cancel"] = True
            await q.answer("⛔ Task cancelled.", show_alert=True)
            # The message is updated to show cancellation is in progress.
            # This is important because the download might still be blocking.
            msg_id = ACTIVE_TASKS[tid]["msg_id"]
            if msg_id:
                msg = q.message.reply_to_message or q.message
                await safe_edit_text(msg, "⛔ **Cancellation requested.**\n_The download may still be in progress, but the file will not be uploaded._", reply_markup=None)
        else:
            await q.answer("❌ Task not found.")

async def download_progress_updater(msg, start_time, tid):
    """
    Updates the message with the download status every few seconds.
    This runs concurrently with the actual download.
    """
    try:
        while True:
            # Check for cancellation
            if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                break
            
            elapsed_time = time.time() - start_time
            await safe_edit_text(msg, f"⏳ **Downloading...**\n`[{int(elapsed_time)}s elapsed]`", reply_markup=cancel_btn(tid))
            await asyncio.sleep(5) # Wait 5 seconds before updating status again
    except asyncio.CancelledError:
        log.info(f"Progress updater for task {tid} was cancelled.")
    except Exception as e:
        log.error(f"Error in progress updater: {e}")

async def download_file(app, url, msg, paths, tid):
    """
    Downloads a file from a URL using gdown and then uploads it to Telegram.
    This version includes robust retries for the upload phase and better
    error handling to prevent silent failures.
    """
    file_path = ""
    fpaths = []
    download_dir = paths["downloads"]
    
    try:
        # Check for gdown installation
        try:
            import gdown
        except ImportError:
            await safe_edit_text(msg, "❌ The `gdown` library is not installed. Please install it with `pip install gdown`.")
            return

        # --- CRITICAL FIX: Use a more robust regex to find the file ID ---
        # It now looks for both ?id= and /d/.../view formats.
        match = re.search(r'(?:id=|/d/)([a-zA-Z0-9_-]+)', url)
        if not match:
            raise ValueError("Invalid Google Drive URL. Could not find a file ID.")
        
        file_id = match.group(1)
        
        # We need a filename to pass to gdown.download
        temp_filename = f"{tid}_{file_id}"
        temp_filepath = os.path.join(download_dir, temp_filename)

        # --- Download Logic with concurrent status updates ---
        start_time = time.time()
        
        # The gdown.download function is synchronous, so we run it in a separate thread.
        download_coro = asyncio.to_thread(gdown.download, id=file_id, output=temp_filepath, quiet=True, fuzzy=True)
        
        # Create tasks for both the download and the progress updater
        download_task = asyncio.create_task(download_coro)
        progress_task = asyncio.create_task(download_progress_updater(msg, start_time, tid))
        
        try:
            # Wait for the download task to complete
            downloaded_path = await download_task
            # gdown.download returns the downloaded file path
            if isinstance(downloaded_path, str) and os.path.exists(downloaded_path):
                file_path = downloaded_path
            else:
                raise Exception("Gdown did not return a valid file path.")

        except asyncio.CancelledError:
            # If the download task is cancelled, cancel the progress task as well
            progress_task.cancel()
            raise DownloadCancelled()
        # This new specific block catches gdown download errors in a general way
        except Exception as gdown_e:
            log.error(f"Gdown download error: {gdown_e}")
            await safe_edit_text(msg, f"❌ **Google Drive Download Failed**\n\nAn error occurred during the download: `{gdown_e}`. Please check the URL and try again later.", reply_markup=None)
            return # Exit the function on this specific, unrecoverable error
        finally:
            # Ensure the progress task is cancelled when the download task finishes
            if not progress_task.done():
                progress_task.cancel()
                try:
                    await progress_task # Await cancellation
                except asyncio.CancelledError:
                    pass

        # --- NEW: Check for cancellation *after* the download finishes but *before* upload starts ---
        if ACTIVE_TASKS.get(tid, {}).get("cancel"):
            raise DownloadCancelled()
            
        # Check if the file is empty.
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            raise Exception("Downloaded file is empty or does not exist.")

        # --- Upload Logic ---
        original_filename = os.path.basename(file_path)
        
        await safe_edit_text(msg, f"✅ Download complete. Preparing for upload: `{original_filename}`", reply_markup=cancel_btn(tid))
        
        filesize = os.path.getsize(file_path)
        if filesize > MAX_SIZE:
            await safe_edit_text(msg, f"✅ Download complete. Splitting file into parts…", reply_markup=cancel_btn(tid))
            fpaths = await asyncio.to_thread(split_file, file_path, MAX_SIZE)
            os.remove(file_path)
        else:
            fpaths = [file_path]
            
        total_parts = len(fpaths)
        
        for idx, fpath in enumerate(fpaths, 1):
            if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                raise DownloadCancelled()
                
            last_upload_update = 0
            async def upload_progress(current, total):
                nonlocal last_upload_update
                now = time.time()
                
                if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                    log.info(f"Cancellation requested for task {tid}. Cancelling upload.")
                    raise asyncio.CancelledError

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
            
            retries = 3
            for attempt in range(retries):
                try:
                    await safe_edit_text(msg, f"**Attempt {attempt + 1}/{retries}:** Uploading part {idx}/{total_parts}...", reply_markup=cancel_btn(tid))
                    
                    if not os.path.exists(fpath):
                        raise FileNotFoundError(f"File to upload not found: {fpath}")
                    
                    await app.send_document(
                        msg.chat.id,
                        fpath,
                        caption=f"✅ Uploaded part {idx}/{total_parts}: `{os.path.basename(fpath)}`",
                        progress=upload_progress
                    )
                    log.info(f"Successfully uploaded part {idx}.")
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as upload_e:
                    log.error(f"Error during file upload on attempt {attempt + 1}: {upload_e}")
                    if attempt < retries - 1:
                        await asyncio.sleep(5)
                    else:
                        raise

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
        # Clean up any split parts from the list of file paths
        for part_file in fpaths:
            if os.path.exists(part_file):
                os.remove(part_file)

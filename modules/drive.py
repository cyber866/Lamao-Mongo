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
            await q.answer("❌ Task not found.", show_alert=True)

async def download_file(app, url, msg, paths, tid):
    """
    Downloads a file from a URL using gdown with a progress bar and improved error handling.
    """
    file_path = ""
    download_dir = paths["downloads"]
    try:
        # Extract the Google Drive file ID from the URL using a regex.
        match = re.search(r'id=([a-zA-Z0-9_-]+)', url)
        if not match:
            raise ValueError("Invalid Google Drive URL. Could not find a file ID.")
        
        file_id = match.group(1)
        
        await safe_edit_text(msg, "🔍 Starting download with `gdown`...", reply_markup=cancel_btn(tid))
        
        # Start the gdown process. We use --output to specify the directory.
        cmd = ["gdown", "--id", file_id, "--output", download_dir]
        
        # Start the gdown process.
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Read the output from the gdown process to get the progress.
        downloaded = 0
        total_size = 0
        file_name = ""
        
        async for line in process.stdout:
            line = line.decode('utf-8').strip()
            # Look for lines containing file info and download progress.
            if "File: " in line:
                file_name = line.split("File: ", 1)[1].strip()
            elif "Total: " in line:
                total_size_str = re.search(r'Total: ([\d.]+) (K|M|G)B', line)
                if total_size_str:
                    value = float(total_size_str.group(1))
                    unit = total_size_str.group(2)
                    if unit == 'K': total_size = value * 1024
                    elif unit == 'M': total_size = value * 1024 * 1024
                    elif unit == 'G': total_size = value * 1024 * 1024 * 1024
            elif "Downloaded: " in line:
                downloaded_str = re.search(r'Downloaded: ([\d.]+) (K|M|G)B', line)
                if downloaded_str:
                    value = float(downloaded_str.group(1))
                    unit = downloaded_str.group(2)
                    if unit == 'K': downloaded = value * 1024
                    elif unit == 'M': downloaded = value * 1024 * 1024
                    elif unit == 'G': downloaded = value * 1024 * 1024 * 1024

            if not file_name:
                continue

            percentage = (downloaded / total_size) * 100 if total_size else 0
            
            progress_text = f"**Downloading**:\n"
            progress_text += f"**File:** `{file_name}`\n"
            progress_text += f"{get_progress_bar(percentage)} **{percentage:.1f}%**\n"
            progress_text += f"**Size:** {humanbytes(downloaded)} / {humanbytes(total_size)}"
            
            await safe_edit_text(msg, progress_text, reply_markup=cancel_btn(tid))
            
            # Check for cancellation
            if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                process.terminate()
                raise DownloadCancelled()
        
        await process.wait() # Wait for the process to complete.

        # New: Check for gdown process failure
        if process.returncode != 0:
            stderr = (await process.stderr.read()).decode('utf-8')
            raise Exception(f"gdown process failed with code {process.returncode}:\n{stderr}")
        
        # The gdown output isn't always reliable. Find the actual file in the directory.
        if file_name:
            file_path = os.path.join(download_dir, file_name)
        else:
            # Fallback: find the newest file in the download directory
            list_of_files = glob.glob(os.path.join(download_dir, '*'))
            if list_of_files:
                file_path = max(list_of_files, key=os.path.getctime)
            else:
                raise Exception("Could not find the downloaded file.")
        
        # Final check to ensure the file exists and is not empty.
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            raise Exception("Downloaded file is empty or does not exist.")
            
        await safe_edit_text(msg, "✅ Download complete. Uploading file...")
        
        # Corrected file splitting and upload logic
        try:
            # Check if file needs to be split
            filesize = os.path.getsize(file_path)
            if filesize > MAX_SIZE:
                await safe_edit_text(msg, f"✅ Download complete. Splitting file into parts…")
                fpaths = await asyncio.to_thread(split_file, file_path, MAX_SIZE)
                os.remove(file_path)
            else:
                fpaths = [file_path]
                
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
                
                await app.send_document(
                    msg.chat.id,
                    fpath,
                    caption=f"✅ Uploaded part {idx}/{total_parts}: `{os.path.basename(fpath)}`",
                    progress=upload_progress
                )
                os.remove(fpath) # Clean up the uploaded part
            
            await safe_edit_text(msg, "✅ All parts uploaded successfully!")

        except Exception as upload_e:
            log.error(f"Error during file upload: {upload_e}")
            await safe_edit_text(msg, f"❌ Upload failed: {upload_e}")
            if file_path and os.path.exists(file_path):
                os.remove(file_path)

    except DownloadCancelled:
        await safe_edit_text(msg, "❌ Download/Upload cancelled.")
    except Exception as e:
        log.error(f"Error in drive command: {e}")
        await safe_edit_text(msg, f"❌ Error: {e}")
    finally:
        ACTIVE_TASKS.pop(tid, None)
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

import os
import uuid
import logging
import asyncio
import time
import requests
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

from .utils import data_paths, ensure_dirs, humanbytes, DownloadCancelled, safe_edit_text

log = logging.getLogger("leech")
ACTIVE_TASKS = {}

def cancel_btn(tid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Cancel", callback_data=f"cancel:{tid}")]])

def sanitize_filename(name):
    """Remove characters Telegram cannot handle"""
    return name.strip()

def register_leech_handlers(app: Client):
    @app.on_message(filters.command("leech"))
    async def cmd_leech(_, m: Message):
        args = m.text.split(maxsplit=1)
        if len(args) < 2:
            return await m.reply("Usage: `/leech <direct file URL>`")

        url = args[1].strip()
        user_id = m.from_user.id
        paths = data_paths(user_id)
        ensure_dirs()
        tid = str(uuid.uuid4())[:8]
        
        ACTIVE_TASKS[tid] = {"user_id": user_id, "url": url, "msg_id": None, "cancel": False}

        msg = await m.reply("⏳ Starting direct file download...", reply_markup=cancel_btn(tid))
        ACTIVE_TASKS[tid]["msg_id"] = msg.id

        async def runner():
            try:
                # Use aiohttp or requests to download the file
                await download_file(url, paths["downloads"], tid, msg)

                # After download, find the file and upload
                filename = os.path.basename(url)
                download_path = os.path.join(paths["downloads"], filename)

                if not os.path.exists(download_path):
                    await msg.edit("❌ Download failed. File not found.")
                    return

                await msg.edit(f"✅ Download complete. Uploading `{filename}`...")
                
                async def upload_progress(cur, tot):
                    frac = cur / tot * 100 if tot else 0
                    bar = "█" * int(frac // 5) + "░" * (20 - int(frac // 5))
                    await safe_edit_text(msg, f"Uploading... {bar} {frac:.1f}%\n⬆ {humanbytes(cur)}/{humanbytes(tot)}", reply_markup=cancel_btn(tid))
                    if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                        raise DownloadCancelled()

                await app.send_document(m.chat.id, download_path, progress=upload_progress)
                await msg.edit(f"✅ Uploaded `{filename}` successfully!")

            except DownloadCancelled:
                await msg.edit("❌ Download/Upload cancelled.")
            except Exception as e:
                await msg.edit(f"❌ Error: {e}")
            finally:
                if tid in ACTIVE_TASKS:
                    ACTIVE_TASKS.pop(tid)
        
        asyncio.create_task(runner())

async def download_file(url, path, tid, msg):
    try:
        filename = os.path.basename(url)
        filepath = os.path.join(path, filename)

        if not url.startswith(("http://", "https://")):
            raise ValueError("URL is not valid")

        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            total_size = int(r.headers.get("content-length", 0))
            downloaded = 0
            start_time = time.time()
            
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                        raise DownloadCancelled()
                    
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if time.time() - start_time > 2:
                        start_time = time.time()
                        pct = (downloaded / total_size) * 100 if total_size > 0 else 0
                        bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
                        asyncio.create_task(
                            safe_edit_text(msg, f"Downloading... {bar} {pct:.1f}%\n⬇ {humanbytes(downloaded)}/{humanbytes(total_size)}", reply_markup=cancel_btn(tid))
                        )
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to download file: {e}")

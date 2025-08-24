import os
import uuid
import logging
import asyncio
import time
import re
import requests
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from .utils import data_paths, ensure_dirs, humanbytes, DownloadCancelled, safe_edit_text
from .file_splitter import split_file   # ✅ use file splitter

log = logging.getLogger("leech")

ACTIVE_TASKS = {}

MAX_SIZE = 1900 * 1024 * 1024  # 1.9GB safe for Telegram

def cancel_btn(tid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Cancel", callback_data=f"cancel:{tid}")]])

def sanitize_filename(name):
    """Remove characters Telegram cannot handle"""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    return name.strip()

def register_leech_handlers(app: Client):
    @app.on_message(filters.command("leech"))
    async def cmd(_, m):
        args = m.text.split(maxsplit=1)
        if len(args) < 2:
            return await m.reply("Usage: /leech <direct_file_url>")
        url = args[1].strip()
        user_id = m.from_user.id
        paths = data_paths(user_id)
        ensure_dirs()

        msg = await m.reply("⏳ Checking file info…")

        try:
            head = requests.head(url, allow_redirects=True, timeout=10)
            file_size = int(head.headers.get("Content-Length", 0))
            file_name = url.split("/")[-1].split("?")[0] or f"file_{uuid.uuid4().hex}"
        except Exception as e:
            return await msg.edit(f"❌ Failed to fetch file info: {e}")

        tid = str(uuid.uuid4())[:8]
        ACTIVE_TASKS[tid] = {"user_id": user_id, "url": url, "cancel": False}

        await msg.edit(
            f"📥 File: `{file_name}`\n"
            f"📦 Size: {humanbytes(file_size)}\n\n"
            f"▶ Ready to download…",
            reply_markup=cancel_btn(tid)
        )

        async def runner():
            try:
                local_path = os.path.join(paths["downloads"], sanitize_filename(file_name))

                # ---------- Download ----------
                with requests.get(url, stream=True) as r:
                    r.raise_for_status()
                    with open(local_path, "wb") as f:
                        downloaded = 0
                        last_update = 0
                        for chunk in r.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
                            if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                                raise DownloadCancelled()
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                            now = time.time()
                            if now - last_update > 3:
                                last_update = now
                                pct = (downloaded / file_size * 100) if file_size else 0
                                bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
                                await safe_edit_text(
                                    msg,
                                    f"⬇ Downloading… {bar} {pct:.1f}%\n"
                                    f"{humanbytes(downloaded)}/{humanbytes(file_size)}",
                                    reply_markup=cancel_btn(tid)
                                )

                # ---------- Split if too big ----------
                files_to_upload = []
                if os.path.getsize(local_path) > MAX_SIZE:
                    parts = split_file(local_path, MAX_SIZE)
                    files_to_upload.extend([os.path.join(paths["downloads"], p) for p in parts])
                    os.remove(local_path)
                else:
                    files_to_upload.append(local_path)

                # ---------- Upload ----------
                total_parts = len(files_to_upload)
                for idx, fpath in enumerate(files_to_upload, 1):
                    if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                        raise DownloadCancelled()
                    part_name = sanitize_filename(os.path.basename(fpath))
                    if len(part_name) > 150:
                        ext = os.path.splitext(part_name)[1]
                        part_name = part_name[:150] + ext

                    async def upload_progress(cur, tot):
                        pct = (cur / tot * 100) if tot else 0
                        bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
                        await safe_edit_text(
                            msg,
                            f"⬆ Uploading part {idx}/{total_parts}… {bar} {pct:.1f}%\n"
                            f"{humanbytes(cur)}/{humanbytes(tot)}",
                            reply_markup=cancel_btn(tid)
                        )

                    await m.reply_document(
                        fpath,
                        caption=f"✅ Uploaded part {idx}/{total_parts}: `{part_name}`",
                        progress=upload_progress
                    )

                await msg.edit("✅ All parts uploaded successfully!")

            except DownloadCancelled:
                await msg.edit("❌ Download/Upload cancelled.")
            except Exception as e:
                await msg.edit(f"❌ Error: {e}")
            finally:
                ACTIVE_TASKS.pop(tid, None)

        asyncio.create_task(runner())

    @app.on_callback_query(filters.regex(r"^cancel:(.+)$"))
    async def cancel_cb(_, q):
        tid = q.data.split(":")[1]
        if tid in ACTIVE_TASKS:
            ACTIVE_TASKS[tid]["cancel"] = True
            await q.answer("⛔ Task cancelled.", show_alert=True)
        else:
            await q.answer("❌ Task not found.", show_alert=True)

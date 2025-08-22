import os
import uuid
import logging
import asyncio
import time
import re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from .ytdlp import list_formats, download_media
from .utils import data_paths, ensure_dirs, humanbytes, DownloadCancelled, safe_edit_text

log = logging.getLogger("leech")
ACTIVE_TASKS = {}

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
            return await m.reply("Usage: /leech <url>")

        url = args[1].strip()
        user_id = m.from_user.id
        paths = data_paths(user_id)
        ensure_dirs()

        msg = await m.reply("🔍 Fetching formats…")

        try:
            fmts = await asyncio.to_thread(list_formats, url, paths["cookies"])
        except Exception as e:
            return await msg.edit(f"❌ Error fetching formats: {e}")

        if not fmts:
            return await msg.edit("❌ No formats found.")

        unique_fmts = {}
        for f in fmts:
            try:
                res = int(f.get("res") or 0)
                if res > 0 and f.get("id"):
                    key = f"{res}_{f.get('id')}"
                    unique_fmts[key] = f
            except ValueError:
                continue

        fmts = sorted(unique_fmts.values(), key=lambda x: int(x.get("res") or 0), reverse=True)
        if not fmts:
            return await msg.edit("❌ No valid video/audio formats found.")

        tid = str(uuid.uuid4())[:8]
        ACTIVE_TASKS[tid] = {"user_id": user_id, "url": url, "msg_id": msg.id, "cancel": False}

        kb = []
        row = []
        for i, f in enumerate(fmts[:10], 1):
            size_text = humanbytes(f.get("size", 0))
            label = f"{f.get('res')} • {size_text}"
            row.append(InlineKeyboardButton(label, callback_data=f"choose:{tid}:{f['id']}"))
            if i % 2 == 0:
                kb.append(row)
                row = []
        if row:
            kb.append(row)

        await msg.edit("🎞 Choose quality:", reply_markup=InlineKeyboardMarkup(kb))

    @app.on_callback_query(filters.regex(r"^choose:(.+?):(.+)$"))
    async def cb(_, q):
        tid, fmt = q.data.split(":")[1:]
        task_info = ACTIVE_TASKS.get(tid)
        if not task_info:
            return await q.answer("❌ Task not found or expired.", show_alert=True)

        url = task_info["url"]
        user_id = task_info["user_id"]
        paths = data_paths(user_id)

        st = await q.message.edit("⏳ Preparing download…", reply_markup=cancel_btn(tid))
        main_loop = asyncio.get_running_loop()
        last_download_update = 0
        last_upload_update = 0

        async def updater(txt):
            await safe_edit_text(st, f"{txt}\n\n`{url}`", reply_markup=cancel_btn(tid))

        def progress_hook(d):
            nonlocal last_download_update
            if d["status"] == "downloading":
                now = time.time()
                if now - last_download_update < 5:
                    return
                last_download_update = now

                pct = d.get("_percent_str", "").strip().replace("%", "")
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                try:
                    pct_float = float(pct)
                except:
                    pct_float = 0
                bar = "█" * int(pct_float // 5) + "░" * (20 - int(pct_float // 5))

                main_loop.call_soon_threadsafe(
                    asyncio.create_task,
                    updater(f"Downloading… {bar} {pct_float:.1f}%\n⬆ {humanbytes(downloaded)}/{humanbytes(total)}")
                )

        async def runner():
            nonlocal last_upload_update
            try:
                fpaths, fname = await asyncio.to_thread(
                    download_media, url, paths["downloads"], paths["cookies"], progress_hook, fmt
                )

                total_parts = len(fpaths)
                for idx, fpath in enumerate(fpaths, 1):
                    if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                        await st.edit("❌ Upload cancelled by user.")
                        return

                    if not os.path.exists(fpath):
                        await st.edit(f"❌ File not found: {fpath}")
                        continue

                    part_name = sanitize_filename(os.path.basename(fpath))
                    if len(part_name) > 150:
                        ext = os.path.splitext(part_name)[1]
                        part_name = part_name[:150] + ext

                    async def upload_progress(cur, tot):
                        nonlocal last_upload_update
                        now = time.time()
                        if now - last_upload_update < 2:
                            return
                        last_upload_update = now
                        frac = cur / tot * 100 if tot else 0
                        bar = "█" * int(frac // 5) + "░" * (20 - int(frac // 5))
                        await updater(f"Uploading part {idx}/{total_parts}… {bar} {frac:.1f}%\n⬆ {humanbytes(cur)}/{humanbytes(tot)}")

                        if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                            raise DownloadCancelled()

                    await q.message.reply_document(fpath, caption=f"✅ Uploaded part {idx}/{total_parts}: `{part_name}`", progress=upload_progress)

                await updater("✅ All parts uploaded successfully!")

            except DownloadCancelled:
                await st.edit("❌ Download/Upload cancelled.")
            except Exception as e:
                await st.edit(f"❌ Error: {e}")
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

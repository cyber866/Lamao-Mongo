import os
import uuid
import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from .ytdlp import list_formats, download_media
from .utils import data_paths, ensure_dirs, humanbytes, DownloadCancelled, safe_edit_text

log = logging.getLogger("leech")

# Active tasks storage
ACTIVE_TASKS = {}

def cancel_btn(tid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Cancel", callback_data=f"cancel:{tid}")]])

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

        # Filter video/audio with valid size
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
        ACTIVE_TASKS[tid] = {"user_id": user_id, "url": url, "msg_id": msg.id}

        # Create buttons
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

        async def updater(txt):
            # Safe edit every 1 sec to avoid Telegram rate-limit
            await safe_edit_text(st, f"{txt}\n\n`{url}`", reply_markup=cancel_btn(tid))

        last_update = 0

        def progress_hook(d):
            nonlocal last_update
            if d["status"] == "downloading":
                pct_str = d.get("_percent_str", "0").strip().replace("%", "")
                try:
                    pct_float = float(pct_str)
                except:
                    pct_float = 0
                now = asyncio.get_event_loop().time()
                # Update only every 1 second
                if now - last_update > 1:
                    last_update = now
                    downloaded = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    bar = "█" * int(pct_float // 5) + "░" * (20 - int(pct_float // 5))
                    asyncio.get_event_loop().create_task(
                        updater(f"Downloading… {bar} {pct_float:.1f}%\n⬆ {humanbytes(downloaded)}/{humanbytes(total)}")
                    )

        async def runner():
            try:
                fpath, fname = await asyncio.to_thread(
                    download_media, url, paths["downloads"], paths["cookies"], progress_hook, fmt
                )

                async def upload_progress(cur, tot):
                    frac = cur / tot * 100 if tot else 0
                    bar = "█" * int(frac // 5) + "░" * (20 - int(frac // 5))
                    await updater(f"Uploading… {bar} {frac:.1f}%\n⬆ {humanbytes(cur)}/{humanbytes(tot)}")

                await q.message.reply_document(fpath, caption=f"✅ Leech complete: `{fname}`", progress=upload_progress)
                await updater("✅ Done.")
            except DownloadCancelled:
                await st.edit("❌ Download cancelled.")
            except Exception as e:
                await st.edit(f"❌ Error: {e}")
            finally:
                ACTIVE_TASKS.pop(tid, None)

        asyncio.create_task(runner())

    @app.on_callback_query(filters.regex(r"^cancel:(.+)$"))
    async def cancel_cb(_, q):
        tid = q.data.split(":")[1]
        if tid in ACTIVE_TASKS:
            ACTIVE_TASKS.pop(tid)
            await q.answer("⛔ Task cancelled.", show_alert=True)
        else:
            await q.answer("❌ Task not found.", show_alert=True)

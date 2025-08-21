import os, asyncio, logging, uuid, re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from .ytdlp import list_formats, download_media
from .utils import (
    data_paths, ensure_dirs, register_task, cancel_task,
    cleanup_task, DownloadCancelled, safe_edit_text,
    humanbytes, text_progress, ACTIVE_TASKS
)

log = logging.getLogger("leech")
LEECH_URLS = {}

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
        msg = await m.reply("🔍 Fetching available qualities…")

        # Fetch formats in executor
        fmts = await asyncio.get_event_loop().run_in_executor(
            None, lambda: list_formats(url, paths["cookies"])
        )

        # Filter only mp4/mkv/webm formats & remove duplicates by resolution
        seen = set()
        unique_fmts = []
        for f in fmts:
            if f["res"] in seen or not f["id"] or f["size"] == 0:
                continue
            seen.add(f["res"])
            unique_fmts.append(f)
        if not unique_fmts:
            return await msg.edit("❌ No valid formats found.")

        tid = str(uuid.uuid4())[:8]
        LEECH_URLS[tid] = {"url": url, "user_id": user_id}

        # Build buttons
        kb = []
        row = []
        for i, f in enumerate(unique_fmts[:10], 1):
            size_text = humanbytes(f['size']) if f['size'] else "N/A"
            label = f"{f['res']} • {size_text}"
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
        task_info = LEECH_URLS.get(tid)
        if not task_info:
            return await q.answer("❌ URL not found!", show_alert=True)

        url = task_info["url"]
        user_id = task_info["user_id"]
        paths = data_paths(user_id)

        st = await q.message.edit("⏳ Preparing download…", reply_markup=cancel_btn(tid))
        ev = register_task(tid)

        async def updater(txt):
            await safe_edit_text(st, f"{txt}\n\n`{url}`", reply_markup=cancel_btn(tid))

        def progress_hook(d):
            if d['status'] == 'downloading':
                pct = d.get('_percent_str', '').strip()
                pct = re.sub(r'\x1b\[[0-9;]*m', '', pct)
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                bar = text_progress(float(pct.replace('%','')) if pct else 0)
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                loop.create_task(
                    updater(f"⬇ Downloading… {bar} {pct}\n⬆ {humanbytes(downloaded)}/{humanbytes(total)}")
                )

        async def runner():
            try:
                fpath, fname = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: download_media(url, paths["downloads"], paths["cookies"], tid, progress_hook, fmt)
                )
                async def up_cb(cur, tot):
                    bar = text_progress(cur/tot*100 if tot else 0)
                    await updater(f"⬆ Uploading… {bar}\n{humanbytes(cur)}/{humanbytes(tot)}")
                    if ev.is_set(): raise DownloadCancelled("Upload cancelled.")

                msg_file = await q.message.reply_document(fpath, caption=f"✅ Leech complete: `{fname}`", progress=up_cb)
                await updater("✅ Done.")
            except DownloadCancelled as e:
                await st.edit(f"❌ Cancelled: {e}")
            except Exception as e:
                await st.edit(f"❌ Error: {e}")
            finally:
                cleanup_task(tid)

        task = asyncio.create_task(runner())
        ACTIVE_TASKS[tid]['task'] = task

    @app.on_callback_query(filters.regex(r"^cancel:(.+)$"))
    async def cancel_cb(_, q):
        tid = q.data.split(":")[1]
        cancel_task(tid)
        await q.answer("⛔ Cancelling…", show_alert=True)

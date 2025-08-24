import yt_dlp
import os
import asyncio
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from .utils import data_paths, ensure_dirs, humanbytes, safe_edit_text, DownloadCancelled

# ---------------- Telegram-safe split size ----------------
MAX_SIZE = 1900 * 1024 * 1024 # 1900 MiB ≈ 1.86 GiB

def list_formats(url, cookies=None):
    ...
    # (same as your old function, no changes)

def download_media(url, path, cookies, progress_hook, fmt_id):
    ...
    # (same as your old function, no changes)


# ---------------- Pyrogram handler ----------------
ACTIVE_YTDLP = {}

def cancel_btn(tid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Cancel", callback_data=f"ytdlp_cancel:{tid}")]])

def register_ytdlp_handlers(app):
    @app.on_message(filters.command("ytdlp"))
    async def ytdlp_cmd(_, m):
        args = m.text.split(maxsplit=1)
        if len(args) < 2:
            return await m.reply("Usage: /ytdlp <video_url>")
        url = args[1].strip()
        user_id = m.from_user.id
        paths = data_paths(user_id)
        ensure_dirs()

        msg = await m.reply("🔍 Fetching video formats…")
        try:
            fmts = await asyncio.to_thread(list_formats, url, paths["cookies"])
        except Exception as e:
            return await msg.edit(f"❌ Error fetching formats: {e}")

        if not fmts:
            return await msg.edit("❌ No formats found.")

        tid = str(hash(url))[:8]
        ACTIVE_YTDLP[tid] = {"url": url, "user_id": user_id, "cancel": False}

        kb = []
        row = []
        for i, f in enumerate(fmts[:10], 1):
            size_text = humanbytes(f.get("size", 0))
            label = f"{f.get('res')}p • {size_text}"
            row.append(InlineKeyboardButton(label, callback_data=f"ytdlp_choose:{tid}:{f['id']}"))
            if i % 2 == 0:
                kb.append(row)
                row = []
        if row: kb.append(row)

        await msg.edit("🎞 Choose quality:", reply_markup=InlineKeyboardMarkup(kb))

    @app.on_callback_query(filters.regex(r"^ytdlp_choose:(.+?):(.+)$"))
    async def choose_quality(_, cq):
        tid, fmt_id = cq.data.split(":")[1:]
        task = ACTIVE_YTDLP.get(tid)
        if not task:
            return await cq.answer("❌ Task not found.", show_alert=True)

        url, user_id = task["url"], task["user_id"]
        paths = data_paths(user_id)

        st = await cq.message.edit("⏳ Preparing download…", reply_markup=cancel_btn(tid))
        loop = asyncio.get_running_loop()
        last_update = 0

        async def updater(txt):
            await safe_edit_text(st, f"{txt}\n\n`{url}`", reply_markup=cancel_btn(tid))

        def progress_hook(d):
            nonlocal last_update
            if d["status"] == "downloading":
                now = asyncio.get_event_loop().time()
                if now - last_update < 3: return
                last_update = now
                pct = d.get("_percent_str", "").strip()
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or 0
                loop.call_soon_threadsafe(
                    asyncio.create_task,
                    updater(f"⬇ Downloading {pct}\n{humanbytes(downloaded)}/{humanbytes(total)}")
                )

        async def runner():
            try:
                fpaths, fname = await asyncio.to_thread(
                    download_media, url, paths["downloads"], paths["cookies"], progress_hook, fmt_id
                )
                for idx, f in enumerate(fpaths, 1):
                    if ACTIVE_YTDLP.get(tid, {}).get("cancel"):
                        await st.edit("❌ Cancelled by user.")
                        return
                    await cq.message.reply_document(f, caption=f"✅ Uploaded part {idx}/{len(fpaths)}: `{os.path.basename(f)}`")
                await updater("✅ All parts uploaded!")
            except DownloadCancelled:
                await st.edit("❌ Cancelled.")
            except Exception as e:
                await st.edit(f"❌ Error: {e}")
            finally:
                ACTIVE_YTDLP.pop(tid, None)

        asyncio.create_task(runner())

    @app.on_callback_query(filters.regex(r"^ytdlp_cancel:(.+)$"))
    async def cancel_cb(_, cq):
        tid = cq.data.split(":")[1]
        if tid in ACTIVE_YTDLP:
            ACTIVE_YTDLP[tid]["cancel"] = True
            await cq.answer("⛔ Task cancelled.", show_alert=True)
        else:
            await cq.answer("❌ Task not found.", show_alert=True)

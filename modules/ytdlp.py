import os
import uuid
import logging
import asyncio
import time
import re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

from .utils import data_paths, ensure_dirs, humanbytes, DownloadCancelled, safe_edit_text
from .file_splitter import split_file
import yt_dlp
from yt_dlp.utils import DownloadError

log = logging.getLogger("ytdl")
ACTIVE_TASKS = {} # This is now for ytdl tasks

# ---------------- Telegram-safe split size ----------------
MAX_SIZE = 1900 * 1024 * 1024  # 1900 MiB ≈ 1.86 GiB

def cancel_btn(tid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Cancel", callback_data=f"cancel_ytdl:{tid}")]])

def sanitize_filename(name):
    """Remove characters Telegram cannot handle"""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    return name.strip()

def clean_ansi_codes(text):
    """Remove ANSI escape codes from a string."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def register_ytdl_handlers(app: Client):
    @app.on_message(filters.command("ytdl"))
    async def cmd_ytdl(_, m: Message):
        args = m.text.split(maxsplit=1)
        if len(args) < 2:
            return await m.reply("Usage: `/ytdl <video URL>`")

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

        tid = str(uuid.uuid4())[:8]
        ACTIVE_TASKS[tid] = {"user_id": user_id, "url": url, "msg_id": msg.id, "cancel": False}

        kb = []
        row = []
        for i, f in enumerate(fmts[:10], 1):
            size_text = humanbytes(f.get("size", 0))
            if f.get('res') == 0:
                label = f"🎵 Audio • {size_text}"
            else:
                label = f"{f.get('res')}p • {size_text}"
            row.append(InlineKeyboardButton(label, callback_data=f"choose_ytdl:{tid}:{f['id']}"))
            if i % 2 == 0:
                kb.append(row)
                row = []
        if row:
            kb.append(row)

        await msg.edit("🎞 Choose quality:", reply_markup=InlineKeyboardMarkup(kb))

    @app.on_callback_query(filters.regex(r"^choose_ytdl:(.+?):(.+)$"))
    async def cb_ytdl(_, q):
        tid, fmt = q.data.split(":")[1:]
        task_info = ACTIVE_TASKS.get(tid)
        if not task_info:
            return await q.answer("❌ Task not found or expired.", show_alert=True)

        url = task_info["url"]
        user_id = task_info["user_id"]
        paths = data_paths(user_id)
        
        st = await q.message.edit("⏳ Preparing download…", reply_markup=cancel_btn(tid))
        
        class ProgressUpdater:
            def __init__(self, msg, url):
                self.msg = msg
                self.url = url
                self.queue = asyncio.Queue()
                self.last_update = 0
                self.task = None

            def start(self):
                self.task = asyncio.create_task(self.updater_task())

            def stop(self):
                if self.task and not self.task.done():
                    self.task.cancel()

            async def updater_task(self):
                try:
                    while True:
                        text = await self.queue.get()
                        await safe_edit_text(self.msg, f"{text}\n\n`{self.url}`", reply_markup=cancel_btn(tid))
                        self.queue.task_done()
                except asyncio.CancelledError:
                    pass

            def progress_hook(self, d):
                if d["status"] == "downloading":
                    now = time.time()
                    if now - self.last_update < 3:
                        return
                    self.last_update = now
                    
                    pct_str = d.get("_percent_str", "").strip()
                    if not pct_str:
                         return
                    
                    pct = float(clean_ansi_codes(pct_str).replace('%', ''))
                    downloaded = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    
                    download_speed = clean_ansi_codes(d.get("_speed_str", "N/A")).strip()
                    eta = clean_ansi_codes(d.get("_eta_str", "N/A")).strip()
                    
                    progress_text = f"**Downloading**:\n"
                    progress_text += f"**File:** `{clean_ansi_codes(d.get('filename', 'Unknown File'))}`\n"
                    
                    bar = get_progress_bar(pct)
                    
                    progress_text += f"{bar} **{pct:.1f}%**\n"
                    progress_text += f"**Size:** {humanbytes(downloaded)} / {humanbytes(total)}\n"
                    progress_text += f"**Speed:** {download_speed} • **ETA:** {eta}"
                    
                    self.queue.put_nowait(progress_text)

        async def runner():
            fpaths = []
            updater = ProgressUpdater(st, url)
            updater.start()
            try:
                await st.edit("✅ Download starting...", reply_markup=cancel_btn(tid))
                full_path, fname = await asyncio.to_thread(
                    download_media, url, paths["downloads"], paths["cookies"], updater.progress_hook, fmt
                )
                
                filesize = os.path.getsize(full_path)
                
                if filesize <= MAX_SIZE:
                    fpaths = [full_path]
                else:
                    await st.edit(f"✅ Download complete. Splitting file into parts…")
                    fpaths = await asyncio.to_thread(split_file, full_path, MAX_SIZE)
                    os.remove(full_path) # Remove the large original file after splitting
                    
                total_parts = len(fpaths)
                for idx, fpath in enumerate(fpaths, 1):
                    if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                        await st.edit("❌ Upload cancelled by user.")
                        return

                    if not os.path.exists(fpath):
                        await st.edit(f"❌ File not found: {fpath}")
                        continue

                    # Determine if it should be sent as video or document
                    file_ext = os.path.splitext(fpath)[1].lower()
                    is_video = file_ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm']
                    
                    if total_parts == 1 and is_video:
                        last_upload_update = 0
                        # Send as a streamable video
                        async def upload_progress(cur, tot):
                            nonlocal last_upload_update
                            now = time.time()
                            if now - last_upload_update < 2:
                                return
                            last_upload_update = now
                            frac = cur / tot * 100 if tot else 0
                            bar = get_progress_bar(frac)
                            await updater.queue.put(f"**Uploading**:\n`{fname}`\n"
                                          f"{bar} **{frac:.1f}%**\n"
                                          f"**Size:** {humanbytes(cur)} / {humanbytes(tot)}")
                            if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                                raise DownloadCancelled()

                        await app.send_video(
                            q.message.chat.id,
                            fpath,
                            caption=f"✅ Uploaded: `{fname}`",
                            progress=upload_progress
                        )
                    else:
                        # Send as a document for multi-part files or non-video formats
                        part_name = sanitize_filename(os.path.basename(fpath))
                        if len(part_name) > 150:
                            ext = os.path.splitext(part_name)[1]
                            part_name = part_name[:150] + ext
                        
                        last_upload_update = 0
                        async def upload_progress(cur, tot):
                            nonlocal last_upload_update
                            now = time.time()
                            if now - last_upload_update < 2:
                                return
                            last_upload_update = now
                            frac = cur / tot * 100 if tot else 0
                            bar = get_progress_bar(frac)
                            await updater.queue.put(f"**Uploading part {idx}/{total_parts}**:\n"
                                          f"`{part_name}`\n"
                                          f"{bar} **{frac:.1f}%**\n"
                                          f"**Size:** {humanbytes(cur)} / {humanbytes(tot)}")
                            if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                                raise DownloadCancelled()

                        await app.send_document(q.message.chat.id, fpath, caption=f"✅ Uploaded part {idx}/{total_parts}: `{part_name}`", progress=upload_progress)

                await st.edit("✅ All parts uploaded successfully!")

            except DownloadCancelled:
                await st.edit("❌ Download/Upload cancelled.")
            except Exception as e:
                await st.edit(f"❌ Error: {e}")
            finally:
                updater.stop()
                ACTIVE_TASKS.pop(tid, None)
                # Cleanup: remove all files after a successful or failed task
                for fpath in fpaths:
                    if os.path.exists(fpath):
                        os.remove(fpath)

        asyncio.create_task(runner())

    @app.on_callback_query(filters.regex(r"^cancel_ytdl:(.+)$"))
    async def cancel_ytdl_cb(_, q):
        tid = q.data.split(":")[1]
        if tid in ACTIVE_TASKS:
            ACTIVE_TASKS[tid]["cancel"] = True
            await q.answer("⛔ Task cancelled.", show_alert=True)
        else:
            await q.answer("❌ Task not found.", show_alert=True)

def list_formats(url, cookies=None):
    """List available formats for a URL, including both video and audio."""
    opts = {
        "quiet": True,
        "skip_download": True,
        "cookiefile": cookies if cookies else None,
        "noplaylist": True, # Ensure we don't process playlists
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except DownloadError:
            return []

        formats = info.get("formats", [])
        unique_fmts = {}
        for f in formats:
            if not f.get("format_id") or (f.get("acodec") == "none" and f.get("vcodec") == "none"):
                continue

            filesize = f.get("filesize") or f.get("filesize_approx") or 0

            if f.get("height") and f.get("acodec") != "none":
                res_key = f.get("height")
                if res_key not in unique_fmts or filesize > unique_fmts[res_key]['size']:
                    unique_fmts[res_key] = {
                        "id": f.get("format_id"),
                        "res": res_key,
                        "size": filesize,
                        "ext": f.get("ext")
                    }
            elif f.get("height") and f.get("acodec") == "none":
                res_key = f.get("height")
                if res_key not in unique_fmts or filesize > unique_fmts[res_key]['size']:
                    unique_fmts[res_key] = {
                        "id": f.get("format_id"),
                        "res": res_key,
                        "size": filesize,
                        "ext": f.get("ext")
                    }
            elif f.get("acodec") != "none" and f.get("vcodec") == "none":
                res_key = f"audio_{f.get('format_id')}"
                if res_key not in unique_fmts:
                    unique_fmts[res_key] = {
                        "id": f.get("format_id"),
                        "res": 0,
                        "size": filesize,
                        "ext": f.get("ext")
                    }

        sorted_list = sorted(unique_fmts.values(), key=lambda x: (x['res'] == 0, -x['res'], x['size']), reverse=False)
        return sorted_list


def download_media(url, path, cookies, progress_hook, fmt_id):
    """Download media and return the path to the downloaded file."""
    opts = {
        "outtmpl": os.path.join(path, "%(title)s.%(ext)s"),
        "cookiefile": cookies if cookies else None,
        "progress_hooks": [progress_hook],
        "format": fmt_id
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        full_path = ydl.prepare_filename(info)
        return full_path, info.get("title")


def get_progress_bar(percentage):
    """Generates a progress bar string."""
    filled_length = int(percentage // 5)
    bar = "█" * filled_length + "░" * (20 - filled_length)
    return f"`[{bar}]`"

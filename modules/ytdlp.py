import os
import uuid
import logging
import asyncio
import time
import re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

from .utils import data_paths, ensure_dirs, humanbytes, DownloadCancelled, safe_edit_text
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
            fmts, info = await asyncio.to_thread(list_formats, url, paths["cookies"])
        except Exception as e:
            return await msg.edit(f"❌ Error fetching formats: {e}")

        if not fmts:
            return await msg.edit("❌ No formats found.")

        tid = str(uuid.uuid4())[:8]
        # Store info in ACTIVE_TASKS for later use
        ACTIVE_TASKS[tid] = {"user_id": user_id, "url": url, "msg_id": msg.id, "cancel": False, "info": info, "fmts": fmts}

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
        tid, fmt_id = q.data.split(":")[1:]
        task_info = ACTIVE_TASKS.get(tid)
        if not task_info:
            return await q.answer("❌ Task not found or expired.", show_alert=True)

        url = task_info["url"]
        user_id = task_info["user_id"]
        paths = data_paths(user_id)
        
        selected_fmt = next((f for f in task_info['fmts'] if str(f['id']) == fmt_id), None)
        if not selected_fmt:
            return await q.answer("❌ Format not found.", show_alert=True)
            
        st = await q.message.edit("⏳ Preparing download…", reply_markup=cancel_btn(tid))
        main_loop = asyncio.get_running_loop()
        last_download_update = 0
        last_upload_update = 0
        
        async def updater(txt):
            await safe_edit_text(st, f"{txt}", reply_markup=cancel_btn(tid))

        def progress_hook(d):
            nonlocal last_download_update
            if d["status"] == "downloading":
                now = time.time()
                if now - last_download_update < 3: # Reduced update frequency for smoother UI
                    return
                last_download_update = now

                pct = d.get("_percent_str", "").strip().replace("%", "")
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                
                try:
                    pct_float = float(pct)
                except ValueError:
                    pct_float = 0
                
                download_speed = d.get("_speed_str", "N/A").strip()
                eta = d.get("_eta_str", "N/A").strip()
                
                progress_text = f"**Downloading**:\n"
                progress_text += f"`{d.get('filename')[:40]}...`\n"
                
                bar = get_progress_bar(pct_float)
                
                progress_text += f"{bar} {pct_float:.1f}%\n"
                progress_text += f"**Size:** {humanbytes(downloaded)} / {humanbytes(total)}\n"
                progress_text += f"**Speed:** {download_speed} • **ETA:** {eta}"

                main_loop.call_soon_threadsafe(
                    asyncio.create_task,
                    updater(progress_text)
                )

        async def runner():
            nonlocal last_upload_update
            try:
                fpaths, fname, thumb_path = await asyncio.to_thread(
                    download_media, url, paths["downloads"], paths["cookies"], progress_hook, fmt_id
                )

                total_parts = len(fpaths)
                
                # Get resolution and source URL
                resolution = f"{selected_fmt.get('res')}p" if selected_fmt.get('res') > 0 else "Audio"
                source_url = task_info['info'].get('webpage_url', url)
                
                # Build the caption
                caption = f"**Title:** `{fname}`\n"
                caption += f"**Resolution:** `{resolution}`\n"
                caption += f"**Source:** [Link]({source_url})"

                # Check if it's a single file and a video format for streaming
                file_ext = os.path.splitext(fpaths[0])[1].lower()
                is_video = file_ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm']
                
                if total_parts == 1 and is_video:
                    async def upload_progress(cur, tot):
                        nonlocal last_upload_update
                        now = time.time()
                        if now - last_upload_update < 2:
                            return
                        last_upload_update = now
                        frac = cur / tot * 100 if tot else 0
                        bar = get_progress_bar(frac)
                        await updater(f"**Uploading**:\n`{fname}`\n"
                                      f"{bar} {frac:.1f}%\n"
                                      f"**Size:** {humanbytes(cur)} / {humanbytes(tot)}")
                        if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                            raise DownloadCancelled()

                    await app.send_video(
                        q.message.chat.id,
                        fpaths[0],
                        caption=caption,
                        thumb=thumb_path, # Pass the thumbnail path here
                        progress=upload_progress
                    )
                else:
                    # Send as a document for multi-part files or non-video formats
                    await st.edit("⚠️ File too large for streaming or not a video. Sending as document(s)...")
                    for idx, fpath in enumerate(fpaths, 1):
                        # ... (rest of document upload logic remains the same)
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
                            bar = get_progress_bar(frac)
                            await updater(f"**Uploading part {idx}/{total_parts}**:\n"
                                          f"`{part_name}`\n"
                                          f"{bar} {frac:.1f}%\n"
                                          f"**Size:** {humanbytes(cur)} / {humanbytes(tot)}")
                            if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                                raise DownloadCancelled()

                        await app.send_document(q.message.chat.id, fpath, caption=f"✅ Uploaded part {idx}/{total_parts}: `{part_name}`", progress=upload_progress)

                await updater("✅ All parts uploaded successfully!")

            except DownloadCancelled:
                await st.edit("❌ Download/Upload cancelled.")
            except Exception as e:
                await st.edit(f"❌ Error: {e}")
            finally:
                ACTIVE_TASKS.pop(tid, None)

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
            return [], {}

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
        return sorted_list, info

def download_media(url, path, cookies, progress_hook, fmt_id):
    """Download media and split into Telegram-safe chunks if needed, also downloads thumbnail."""
    
    # Define paths for video and thumbnail
    video_path = os.path.join(path, "%(title)s.%(ext)s")
    thumb_path = os.path.join(path, "%(title)s.%(ext)s.thumb")

    opts = {
        "outtmpl": video_path,
        "cookiefile": cookies if cookies else None,
        "progress_hooks": [progress_hook],
        "format": fmt_id,
        "writethumbnail": True,  # Enable thumbnail download
        "postprocessors": [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'} if fmt_id.startswith('audio') else {},
            {'key': 'SponsorBlock'}
        ]
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        full_path = ydl.prepare_filename(info)
        
        # Check if a thumbnail was downloaded
        thumbnail_path = ydl.prepare_filename(info, 'jpg')
        
        filesize = os.path.getsize(full_path)

        if filesize <= MAX_SIZE:
            return [full_path], info.get("title"), thumbnail_path

        part_paths = []
        with open(full_path, "rb") as f:
            idx = 1
            while True:
                chunk = f.read(MAX_SIZE)
                if not chunk:
                    break
                base, ext = os.path.splitext(full_path)
                part_file = f"{base}.part{idx}{ext}"
                with open(part_file, "wb") as pf:
                    pf.write(chunk)
                part_paths.append(part_file)
                idx += 1

        os.remove(full_path)
        return part_paths, info.get("title"), thumbnail_path

def get_progress_bar(percentage):
    """Generates a progress bar string."""
    filled_length = int(percentage // 5)
    bar = "█" * filled_length + "░" * (20 - filled_length)
    return f"`[{bar}]`"

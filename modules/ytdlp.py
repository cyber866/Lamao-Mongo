#
# This module handles the /ytdl command for downloading and sending
# files from various supported websites using yt-dlp.
#

import os
import uuid
import logging
import asyncio
import time
import re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import FloodWait, RPCError

# Assuming these imports are correct based on your project structure.
from .utils import data_paths, ensure_dirs, humanbytes, DownloadCancelled, safe_edit_text
from .file_splitter import split_file
import yt_dlp
from yt_dlp.utils import DownloadError

log = logging.getLogger("ytdl")
ACTIVE_TASKS = {} # This is now for ytdl tasks

# ---------------- Telegram-safe split size ----------------
MAX_SIZE = 1900 * 1024 * 1024 # 1900 MiB ≈ 1.86 GiB

def cancel_btn(tid):
    """
    Creates an inline keyboard markup with a single "Cancel" button.
    The callback data includes the task ID (tid) for easy identification.
    """
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
    # This function now correctly registers the command handlers.
    # The filter is changed to allow commands in both private and group chats.
    @app.on_message(filters.command("ytdl") & (filters.private | filters.group))
    async def cmd_ytdl(_, m: Message):
        """
        Handles the /ytdl command by fetching available video/audio formats.
        """
        args = m.text.split(maxsplit=1)
        if len(args) < 2:
            return await m.reply("Usage: `/ytdl <video URL>`")

        url = args[1].strip()
        user_id = m.from_user.id
        paths = data_paths(user_id)
        ensure_dirs()

        msg = await m.reply("🔍 Fetching formats…")

        try:
            # Use asyncio.to_thread to run the blocking list_formats function
            fmts = await asyncio.to_thread(list_formats, url, paths["cookies"])
        except Exception as e:
            return await msg.edit(f"❌ Error fetching formats: {e}")

        if not fmts:
            return await msg.edit("❌ No formats found.")

        # --- Find highest resolution and check for specific resolutions ---
        max_res_fmt = None
        has_360p = False
        has_480p = False
        has_720p = False
        has_1080p = False
        for f in fmts:
            if f.get('res') > 0:
                if max_res_fmt is None or f.get('res') > max_res_fmt.get('res', 0):
                    max_res_fmt = f
                if f.get('res') == 360:
                    has_360p = True
                if f.get('res') == 480:
                    has_480p = True
                if f.get('res') == 720:
                    has_720p = True
                if f.get('res') == 1080:
                    has_1080p = True
        # --------------------------------------------------------------------------

        # Create a unique ID for the task and store it
        tid = str(uuid.uuid4())[:8]
        ACTIVE_TASKS[tid] = {"user_id": user_id, "url": url, "msg_id": msg.id, "cancel": False}

        kb = []
        row = []
        # Create an inline keyboard with format options, limited to the first 10
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
        
        # --- Add new custom quality buttons based on availability ---
        if has_360p:
            kb.append([InlineKeyboardButton("🎬 Low Quality (360p + audio)", callback_data=f"choose_ytdl:{tid}:merged_360p")])
        if has_480p:
            kb.append([InlineKeyboardButton("🎬 Low Quality (480p + audio)", callback_data=f"choose_ytdl:{tid}:merged_480p")])
        if has_720p:
            kb.append([InlineKeyboardButton("🎬 Normal Quality (720p + audio)", callback_data=f"choose_ytdl:{tid}:merged_720p")])
        if has_1080p:
            kb.append([InlineKeyboardButton("🎬 Best Quality (1080p + audio)", callback_data=f"choose_ytdl:{tid}:merged_1080p")])
        if max_res_fmt:
            kb.append([InlineKeyboardButton(f"🎬 Highest Quality ({max_res_fmt.get('res')}p + audio)", callback_data=f"choose_ytdl:{tid}:merged_max")])
        # --------------------------------------------------------------------

        await msg.edit("🎞 Choose quality:", reply_markup=InlineKeyboardMarkup(kb))

    @app.on_callback_query(filters.regex(r"^choose_ytdl:(.+?):(.+)$"))
    async def cb_ytdl(_, q):
        """
        Handles the callback query when a user chooses a format.
        """
        tid, fmt = q.data.split(":")[1:]
        task_info = ACTIVE_TASKS.get(tid)
        if not task_info:
            return await q.answer("❌ Task not found or expired.", show_alert=True)

        url = task_info["url"]
        user_id = task_info["user_id"]
        paths = data_paths(user_id)

        st = await q.message.edit("⏳ Preparing download…", reply_markup=cancel_btn(tid))

        class ProgressUpdater:
            """
            Manages the queue-based progress bar updates for the message.
            This avoids flooding the Telegram API with too many edits.
            """
            def __init__(self, msg, url):
                self.msg = msg
                self.url = url
                self.queue = asyncio.Queue()
                self.last_update = 0
                self.last_uploaded_bytes = 0
                self.last_downloaded_bytes = 0
                self.start_time = time.time()
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
                """
                A hook function for yt-dlp to send progress updates.
                """
                # Check for cancellation before processing
                if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                    log.info(f"Cancellation detected during download for task {tid}. Raising exception.")
                    raise DownloadCancelled() # Re-raise our custom exception

                if d["status"] == "downloading":
                    now = time.time()
                    # Only update every 3 seconds to avoid FloodWait errors
                    if now - self.last_update < 3:
                        return

                    pct_str = d.get("_percent_str", "").strip()
                    if not pct_str:
                          return

                    pct = float(clean_ansi_codes(pct_str).replace('%', ''))
                    downloaded = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0

                    download_speed = clean_ansi_codes(d.get("_speed_str", "N/A")).strip()
                    eta = clean_ansi_codes(d.get("_eta_str", "N/A")).strip()

                    # Construct the progress message using the custom progress bar
                    progress_text = f"**Downloading**:\n"
                    progress_text += f"**File:** `{clean_ansi_codes(d.get('filename', 'Unknown File'))}`\n"

                    bar = get_progress_bar(pct)

                    progress_text += f"{bar} **{pct:.1f}%**\n"
                    progress_text += f"**Size:** {humanbytes(downloaded)} / {humanbytes(total)}\n"
                    progress_text += f"**Speed:** {download_speed} • **ETA:** {eta}"

                    self.queue.put_nowait(progress_text)
                    self.last_update = now

        async def runner():
            """
            The main coroutine to handle the entire download and upload process.
            """
            fpaths = []
            updater = ProgressUpdater(st, url)
            updater.start()

            try:
                # Part 1: Download Media
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

                # Part 2: Upload Media
                total_parts = len(fpaths)
                for idx, fpath in enumerate(fpaths, 1):
                    # Check for cancellation before each upload
                    if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                        await st.edit("❌ Upload cancelled by user.")
                        # This return will jump to the finally block
                        return

                    if not os.path.exists(fpath):
                        await st.edit(f"❌ File not found: {fpath}")
                        continue

                    # Define retry logic
                    retries = 3
                    while retries > 0:
                        try:
                            file_ext = os.path.splitext(fpath)[1].lower()
                            is_video = file_ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm']

                            if total_parts == 1 and is_video:
                                # Send as a streamable video
                                await app.send_video(
                                    q.message.chat.id,
                                    fpath,
                                    caption=f"✅ Uploaded: `{fname}`",
                                    progress=lambda cur, tot: upload_progress(cur, tot, updater, tid, "video", fname, 1, 1)
                                )
                            else:
                                # Send as a document for multi-part files or non-video formats
                                part_name = sanitize_filename(os.path.basename(fpath))
                                if len(part_name) > 150:
                                    ext = os.path.splitext(part_name)[1]
                                    part_name = part_name[:150] + ext

                                await app.send_document(
                                    q.message.chat.id,
                                    fpath,
                                    caption=f"✅ Uploaded part {idx}/{total_parts}: `{part_name}`",
                                    progress=lambda cur, tot: upload_progress(cur, tot, updater, tid, "document", part_name, idx, total_parts)
                                )

                            # If upload is successful, break the retry loop
                            break
                        except FloodWait as e:
                            log.info(f"Flood wait. Waiting for {e.value} seconds...")
                            await asyncio.sleep(e.value)
                        except RPCError as e:
                            log.error(f"RPC Error during upload: {e}")
                            retries -= 1
                            if retries > 0:
                                log.info(f"Retrying upload... {retries} attempts left.")
                                await asyncio.sleep(5) # Wait before retrying
                            else:
                                raise e # Re-raise if all retries fail

                await st.edit("✅ All parts uploaded successfully!")

            except DownloadCancelled:
                # The progress callback raises this, so we catch it here to stop the task
                await st.edit("❌ Download/Upload cancelled.")
            except Exception as e:
                # Catch any other unexpected errors and report them
                log.error(f"An error occurred in the runner: {e}", exc_info=True)
                await st.edit(f"❌ Error: {e}")
            finally:
                updater.stop()
                ACTIVE_TASKS.pop(tid, None)
                # Cleanup: remove all files after a successful or failed task
                for fpath in fpaths:
                    if os.path.exists(fpath):
                        os.remove(fpath)

        asyncio.create_task(runner())

    def upload_progress(cur, tot, updater, tid, file_type, name, part, total_parts):
        """
        A unified progress callback for both video and document uploads.
        """
        # Check for the cancel flag. If set, we stop the upload process.
        if ACTIVE_TASKS.get(tid, {}).get("cancel"):
            raise DownloadCancelled()

        now = time.time()
        # Only update every 2 seconds to avoid FloodWait errors
        if now - updater.last_update < 2:
            return

        # Calculate speed and ETA
        elapsed = now - updater.start_time
        speed = (cur - updater.last_uploaded_bytes) / (now - updater.last_update) if now > updater.last_update else 0
        eta = (tot - cur) / speed if speed > 0 else "N/A"

        # Update the last recorded bytes and time
        updater.last_uploaded_bytes = cur
        updater.last_update = now

        frac = cur / tot * 100 if tot else 0
        bar = get_progress_bar(frac)

        if file_type == "document" and total_parts > 1:
            progress_text = f"**Uploading part {part}/{total_parts}**:\n"
            progress_text += f"`{name}`\n"
        else:
            progress_text = f"**Uploading**:\n`{name}`\n"

        progress_text += f"{bar} **{frac:.1f}%**\n"
        progress_text += f"**Size:** {humanbytes(cur)} / {humanbytes(tot)}\n"
        progress_text += f"**Speed:** {humanbytes(speed)}/s • **ETA:** {int(eta)}s"

        updater.queue.put_nowait(progress_text)


def list_formats(url, cookies=None):
    """
    Lists available formats for a given URL, including both video and audio.
    This function uses a blocking library (yt-dlp) and should be run in a thread.
    """
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

            # Prioritize formats with both video and audio streams
            if f.get("height") and f.get("acodec") != "none":
                res_key = f.get("height")
                if res_key not in unique_fmts or filesize > unique_fmts[res_key]['size']:
                    unique_fmts[res_key] = {
                        "id": f.get("format_id"),
                        "res": res_key,
                        "size": filesize,
                        "ext": f.get("ext")
                    }
            # Handle video-only formats
            elif f.get("height") and f.get("acodec") == "none":
                res_key = f.get("height")
                if res_key not in unique_fmts or filesize > unique_fmts[res_key]['size']:
                    unique_fmts[res_key] = {
                        "id": f.get("format_id"),
                        "res": res_key,
                        "size": filesize,
                        "ext": f.get("ext")
                    }
            # Handle audio-only formats
            elif f.get("acodec") != "none" and f.get("vcodec") == "none":
                res_key = f"audio_{f.get('format_id')}"
                if res_key not in unique_fmts:
                    unique_fmts[res_key] = {
                        "id": f.get("format_id"),
                        "res": 0,
                        "size": filesize,
                        "ext": f.get("ext")
                    }

        # Sort formats by resolution (descending) and audio first
        sorted_list = sorted(unique_fmts.values(), key=lambda x: (x['res'] == 0, -x['res'], x['size']), reverse=False)
        return sorted_list


def download_media(url, path, cookies, progress_hook, fmt_id):
    """
    Download media using yt-dlp and return the path to the downloaded file.
    This is a blocking function.
    """
    # --- Use a dictionary to map custom IDs to yt-dlp format strings ---
    fmt_map = {
        'merged_360p': 'bestvideo[height=360][ext=mp4]+bestaudio[ext=m4a]',
        'merged_480p': 'bestvideo[height=480][ext=mp4]+bestaudio[ext=m4a]',
        'merged_720p': 'bestvideo[height=720][ext=mp4]+bestaudio[ext=m4a]',
        'merged_1080p': 'bestvideo[height=1080][ext=mp4]+bestaudio[ext=m4a]',
        'merged_max': 'bestvideo+bestaudio/best',
    }
    format_string = fmt_map.get(fmt_id, fmt_id)
    # --------------------------------------------------------------------------

    opts = {
        "format": format_string,
        "outtmpl": os.path.join(path, "%(title)s.%(ext)s"),
        "cookiefile": cookies if cookies else None,
        "progress_hooks": [progress_hook],
    }

    # If we are merging, we need to explicitly tell yt-dlp to use FFmpeg.
    # --- Check if the format ID is in our map to determine if merging is needed ---
    if fmt_id in fmt_map:
        opts["postprocessors"] = [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }]
    # ------------------------------------------------------------------------------------

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        
        # --- The 'full_path' needs to be handled differently for merged files. ---
        # yt-dlp automatically handles the filename for merged formats.
        if fmt_id in fmt_map:
            full_path = ydl.prepare_filename(info)
        else:
            full_path = ydl.prepare_filename(info)
        # --------------------------------------------------------------------------------
        return full_path, info.get("title")


def get_progress_bar(percentage):
    """Generates a progress bar string with the specified visual style."""
    filled_length = int(percentage // 5)
    bar = "█" * filled_length + "░" * (20 - filled_length)
    return f"`[{bar}]`"

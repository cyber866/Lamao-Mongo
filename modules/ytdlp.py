#
# This module handles the /ytdl command for downloading and sending
# files from various supported websites using yt-dlp.
#
# **NEW:** This version now integrates with the cloudflare_solver module
# to handle persistent 403 errors by automatically re-trying the download
# with the unblocked URL.
#

import os
import uuid
import logging
import asyncio
import time
import re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

# We need to import yt-dlp here to catch its specific exceptions.
import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError, SameFileError

# Assuming these imports are correct based on your project structure.
from .utils import data_paths, ensure_dirs, humanbytes, DownloadCancelled, safe_edit_text
from .file_splitter import split_file

# **NEW** Import the Cloudflare solver
from .cloudflare_solver import get_redirected_url

log = logging.getLogger("ytdl")
ACTIVE_TASKS = {} # This is for ytdl tasks

# ---------------- Telegram-safe split size ----------------
MAX_SIZE = 1900 * 1024 * 1024  # 1900 MiB ≈ 1.86 GiB

# --- Helper Functions ---

def cancel_btn(tid):
    """
    Creates an inline keyboard with a single "Cancel" button.
    """
    return InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Cancel", callback_data=f"cancel_ytdl:{tid}")]])

def get_progress_bar(percentage):
    """Generates a progress bar string."""
    filled_length = int(percentage // 5)
    bar = "█" * filled_length + "░" * (20 - filled_length)
    return f"`[{bar}]`"

def sanitize_filename(name):
    """Remove characters that Telegram or file systems cannot handle."""
    # This regex is more robust for removing illegal characters.
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def clean_ansi_codes(text):
    """Remove ANSI escape codes from a string."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

# --- Format Listing and Selection ---

async def list_formats(url, cookies=None):
    """
    Fetches and lists available formats for a given URL.
    It prioritizes combined video+audio formats and then separates them.
    This now handles Cloudflare errors by re-trying with a solved URL.
    """
    opts = {
        "quiet": True,
        "skip_download": True,
        "cookiefile": cookies if cookies else None,
        "noplaylist": True, # Ensure we don't process playlists
    }
    
    # Try fetching formats normally first
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        # If it's a Cloudflare 403 error, try to solve it.
        if "HTTP Error 403" in str(e) and "Cloudflare" in str(e):
            log.warning("Detected Cloudflare challenge. Attempting to solve...")
            unblocked_url = await get_redirected_url(url)
            if unblocked_url:
                # Re-try with the unblocked URL and a different User-Agent
                log.info("Cloudflare challenge solved. Re-trying with new URL...")
                opts['http_headers'] = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36'
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(unblocked_url, download=False)
            else:
                log.error("Cloudflare challenge could not be solved.")
                return []
        else:
            log.error(f"Error extracting info for {url}: {e}")
            return []
    except ExtractorError as e:
        log.error(f"Error extracting info for {url}: {e}")
        return []

    formats = info.get("formats", [])
    unique_fmts = {}

    for f in formats:
        # Skip formats without format IDs or streams without video/audio
        if not f.get("format_id") or (f.get("acodec") == "none" and f.get("vcodec") == "none"):
            continue

        # Get relevant info
        format_id = f.get("format_id")
        filesize = f.get("filesize") or f.get("filesize_approx") or 0
        resolution = f.get("height", 0)
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        ext = f.get("ext")

        # Prioritize formats with both video and audio
        if vcodec != "none" and acodec != "none":
            key = f"combo_{resolution}"
            if key not in unique_fmts or filesize > unique_fmts[key]['size']:
                unique_fmts[key] = {
                    "id": format_id,
                    "res": resolution,
                    "size": filesize,
                    "ext": ext,
                    "vcodec": vcodec,
                    "acodec": acodec,
                    "is_combined": True
                }
        # Handle video-only formats
        elif vcodec != "none" and acodec == "none":
            key = f"video_{resolution}"
            if key not in unique_fmts or filesize > unique_fmts[key]['size']:
                unique_fmts[key] = {
                    "id": format_id,
                    "res": resolution,
                    "size": filesize,
                    "ext": ext,
                    "vcodec": vcodec,
                    "acodec": acodec,
                    "is_combined": False
                }
        # Handle audio-only formats
        elif vcodec == "none" and acodec != "none":
            key = f"audio_{acodec}_{ext}"
            if key not in unique_fmts or filesize > unique_fmts[key]['size']:
                unique_fmts[key] = {
                    "id": format_id,
                    "res": 0,
                    "size": filesize,
                    "ext": ext,
                    "vcodec": vcodec,
                    "acodec": acodec,
                    "is_combined": False
                }

    # Sort the list: combined, then video-only, then audio-only.
    # Within each category, sort by resolution (desc) and size (desc).
    sorted_list = sorted(
        unique_fmts.values(),
        key=lambda x: (x['is_combined'], -x['res'], -x['size']),
        reverse=True
    )

    return sorted_list

def get_best_audio(formats):
    """
    Finds the best available audio-only format.
    """
    best_audio = None
    for f in formats:
        if f.get('vcodec') == 'none' and f.get('acodec') != 'none':
            if not best_audio or f.get('filesize', 0) > best_audio.get('filesize', 0):
                best_audio = f
    return best_audio

# --- Download Logic ---

async def download_progress_hook(d, msg, tid, start_time):
    """
    A custom progress hook for yt-dlp to update the Telegram message.
    """
    # Check for cancellation first to stop the download process early.
    if ACTIVE_TASKS.get(tid, {}).get("cancel"):
        raise DownloadCancelled("Download cancelled by user.")
    
    # Only update on 'downloading' status
    if d['status'] == 'downloading':
        current_time = time.time()
        # Don't update too often
        if (current_time - msg.last_edit_date) < 3:
            return

        percentage = d.get('_percent_str', '').strip()
        speed = d.get('_speed_str', 'N/A').strip()
        eta = d.get('_eta_str', 'N/A').strip()

        progress_text = f"⏳ **Downloading...**\n"
        progress_text += f"{d.get('info_dict', {}).get('title', '...')}\n"
        progress_text += f"**{percentage}**\n"
        progress_text += f"**Speed:** {speed} • **ETA:** {eta}"
        
        await safe_edit_text(msg, progress_text, reply_markup=cancel_btn(tid))
        msg.last_edit_date = current_time

    if d['status'] == 'finished':
        await safe_edit_text(msg, "✅ Download complete. Preparing for upload...", reply_markup=cancel_btn(tid))

async def download_media(app, url, msg, paths, tid, fmt_id):
    """
    Downloads a video using yt-dlp and uploads it to Telegram.
    This function handles the entire download and upload lifecycle.
    """
    file_path = ""
    fpaths = []
    download_dir = paths["downloads"]
    
    try:
        ydl_opts = {
            'outtmpl': os.path.join(download_dir, f"{tid}_%(title)s.%(ext)s"),
            'format': fmt_id,
            'retries': 5,
            'fragment_retries': 5,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            # CRITICAL: This allows yt-dlp to use the progress hook correctly
            'progress_hooks': [lambda d: asyncio.run_coroutine_threadsafe(download_progress_hook(d, msg, tid, time.time()), app.loop).result()],
            # CRITICAL: Path to the ffmpeg executable.
            # You might need to specify a full path here if it's not in your system's PATH.
            'ffmpeg_location': 'ffmpeg',
        }

        # Handle potential Cloudflare issues during the download itself
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = await asyncio.to_thread(ydl.extract_info, url, download=True)
                file_path = ydl.prepare_filename(info_dict)
        except DownloadError as e:
            if "HTTP Error 403" in str(e) and "Cloudflare" in str(e):
                log.warning("Detected Cloudflare challenge during download. Attempting to solve...")
                unblocked_url = await get_redirected_url(url)
                if unblocked_url:
                    log.info("Cloudflare challenge solved. Re-trying download with new URL...")
                    ydl_opts['http_headers'] = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36'}
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl_retry:
                        info_dict = await asyncio.to_thread(ydl_retry.extract_info, unblocked_url, download=True)
                        file_path = ydl_retry.prepare_filename(info_dict)
                else:
                    raise e # Re-raise if the solver fails

        # New check for cancellation after download is finished
        if ACTIVE_TASKS.get(tid, {}).get("cancel"):
            raise DownloadCancelled()
            
        # Check if the file is empty or does not exist
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            raise Exception("Downloaded file is empty or does not exist.")

        # --- Upload Logic ---
        original_filename = sanitize_filename(os.path.basename(file_path))
        
        await safe_edit_text(msg, f"✅ Download complete. Preparing for upload: `{original_filename}`", reply_markup=cancel_btn(tid))
        
        filesize = os.path.getsize(file_path)
        if filesize > MAX_SIZE:
            await safe_edit_text(msg, f"✅ Download complete. Splitting file into parts…", reply_markup=cancel_btn(tid))
            fpaths = await asyncio.to_thread(split_file, file_path, MAX_SIZE)
            os.remove(file_path)
        else:
            fpaths = [file_path]
            
        total_parts = len(fpaths)
        
        for idx, fpath in enumerate(fpaths, 1):
            if ACTIVE_TASKS.get(tid, {}).get("cancel"):
                raise DownloadCancelled()
                
            # Use a retry loop for the upload
            retries = 3
            for attempt in range(retries):
                try:
                    await safe_edit_text(msg, f"**Attempt {attempt + 1}/{retries}:** Uploading part {idx}/{total_parts}...", reply_markup=cancel_btn(tid))
                    
                    if not os.path.exists(fpath):
                        raise FileNotFoundError(f"File to upload not found: {fpath}")
                    
                    # Use send_document for all files to be safe
                    await app.send_document(
                        msg.chat.id,
                        fpath,
                        caption=f"✅ Uploaded part {idx}/{total_parts}: `{os.path.basename(fpath)}`"
                    )
                    log.info(f"Successfully uploaded part {idx}.")
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as upload_e:
                    log.error(f"Error during file upload on attempt {attempt + 1}: {upload_e}")
                    if attempt < retries - 1:
                        await asyncio.sleep(5)
                    else:
                        raise

        await safe_edit_text(msg, "✅ All parts uploaded successfully!")

    except DownloadCancelled:
        await safe_edit_text(msg, "❌ Download/Upload cancelled.")
    except (DownloadError, ExtractorError) as e:
        log.error(f"YTDL download error: {e}")
        await safe_edit_text(msg, f"❌ **YTDL Download Failed**\n\nAn error occurred during the download: `{e}`. Please check the URL and try again later.", reply_markup=None)
    except Exception as e:
        log.error(f"An unexpected error occurred: {e}")
        await safe_edit_text(msg, f"❌ An unexpected error occurred: `{e}`")
    finally:
        ACTIVE_TASKS.pop(tid, None)
        # Clean up any remaining files from the download process
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        for part_file in fpaths:
            if os.path.exists(part_file):
                os.remove(part_file)

# --- Register Handlers ---

def register_ytdl_handlers(app: Client):
    @app.on_message(filters.command("ytdl") & (filters.private | filters.group))
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
        # We limit to top 10 formats for a clean UI
        for i, f in enumerate(fmts[:10], 1):
            size_text = humanbytes(f.get("size", 0))
            if f['res'] == 0:
                label = f"🎵 Audio • {f['ext']} • {size_text}"
            else:
                label = f"{f['res']}p • {f['ext']} • {size_text}"
            row.append(InlineKeyboardButton(label, callback_data=f"choose_ytdl:{tid}:{f['id']}"))
            if i % 2 == 0:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        
        kb.append([InlineKeyboardButton("⛔ Cancel", callback_data=f"cancel_ytdl:{tid}")])

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
        
        st = q.message
        
        # Start the download and upload process
        asyncio.create_task(download_media(app, url, st, paths, tid, fmt_id))
        
    @app.on_callback_query(filters.regex(r"^cancel_ytdl:(.+)$"))
    async def cancel_ytdl_cb(_, q):
        tid = q.data.split(":")[1]
        if tid in ACTIVE_TASKS:
            ACTIVE_TASKS[tid]["cancel"] = True
            await q.answer("⛔ Task cancelled.", show_alert=True)
            msg = q.message.reply_to_message or q.message
            await safe_edit_text(msg, "⛔ **Cancellation requested.**\n_The download may still be in progress, but the file will not be uploaded._", reply_markup=None)
        else:
            await q.answer("❌ Task not found.", show_alert=True)

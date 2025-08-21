import os
import uuid
import asyncio
from pymongo import MongoClient

# --- Directories ---
BASE_DIR = os.path.join(os.getcwd(), "data")
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
COOKIES_DIR = os.path.join(BASE_DIR, "cookies")

def ensure_dirs():
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    os.makedirs(COOKIES_DIR, exist_ok=True)

def data_paths(user_id=None):
    """
    Returns paths for downloads and cookies
    """
    return {
        "downloads": DOWNLOADS_DIR,
        "cookies": os.path.join(COOKIES_DIR, f"{user_id}.txt") if user_id else None
    }

# --- MongoDB setup ---
MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    raise SystemExit("Please set MONGO_URI in .env")

mongo_client = MongoClient(MONGO_URI)
db = mongo_client["mongo_leech_db"]

# --- Collections ---
cookies_col = db["cookies"]  # Store user cookies.txt info
tasks_col = db["tasks"]      # Store ongoing download tasks

# --- Task management ---
ACTIVE_TASKS = {}

def register_task(tid):
    evt = asyncio.Event()
    ACTIVE_TASKS[tid] = {"event": evt, "status": "running"}
    return evt

def cancel_task(tid=None):
    if tid:
        if tid in ACTIVE_TASKS:
            ACTIVE_TASKS[tid]["event"].set()
            ACTIVE_TASKS[tid]["status"] = "cancelled"
    else:
        for t in list(ACTIVE_TASKS.keys()):
            ACTIVE_TASKS[t]["event"].set()
            ACTIVE_TASKS[t]["status"] = "cancelled"

def cleanup_task(tid):
    if tid in ACTIVE_TASKS:
        del ACTIVE_TASKS[tid]

def should_update(tid):
    return tid in ACTIVE_TASKS and not ACTIVE_TASKS[tid]["event"].is_set()

class DownloadCancelled(Exception):
    pass

def humanbytes(size):
    # Converts bytes to human-readable
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}PB"

def text_progress(percent):
    # Returns a textual progress bar
    done = int(percent / 10)
    remain = 10 - done
    return f"[{'█'*done}{'░'*remain}] {percent:.1f}%"

async def safe_edit_text(msg, text, reply_markup=None):
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except Exception:
        pass

# modules/utils.py

import os
import math
import logging
import asyncio
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import Message
from pymongo import MongoClient

log = logging.getLogger("utils")

# ------------------ MongoDB cookies collection ------------------
MONGO_URI = os.environ.get("MONGO_URI", "")
if MONGO_URI:
    client = MongoClient(MONGO_URI)
    cookies_col = client["mongo_leech"]["cookies"]
else:
    cookies_col = None  # In case MongoDB URI is not set

# ------------------ Exception ------------------
class DownloadCancelled(Exception):
    """Raised when a download or upload is cancelled"""
    pass

# ------------------ Directory helpers ------------------
BASE_DIR = os.path.join(os.getcwd(), "data")
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
COOKIES_DIR = os.path.join(BASE_DIR, "cookies")

def ensure_dirs():
    """Ensure required directories exist"""
    for d in [BASE_DIR, DOWNLOADS_DIR, COOKIES_DIR]:
        os.makedirs(d, exist_ok=True)

def data_paths(user_id):
    """Return user-specific paths"""
    user_dir = os.path.join(DOWNLOADS_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    return {
        "downloads": user_dir,
        "cookies": os.path.join(COOKIES_DIR, f"{user_id}_cookies.txt")
    }

# ------------------ Helpers ------------------
def humanbytes(size):
    """Convert bytes to human-readable format"""
    if not size:
        return "0B"
    power = 2**10
    n = 0
    units = ["B", "KB", "MB", "GB", "TB"]
    while size >= power and n < len(units)-1:
        size /= power
        n += 1
    return f"{size:.2f}{units[n]}"

async def safe_edit_text(msg: Message, text: str, reply_markup=None):
    """
    Edits a message with robust error handling for API flooding and message modification.
    """
    while True:
        try:
            await msg.edit_text(text, reply_markup=reply_markup)
            break
        except FloodWait as e:
            log.warning(f"FloodWait: {e.value}s. Sleeping...")
            await asyncio.sleep(e.value)
        except MessageNotModified:
            break
        except Exception as e:
            log.error(f"Failed to edit message: {e}")
            break

# ------------------ Cancel tasks ------------------
def cancel_task(active_tasks):
    """
    Cancels all ongoing download/upload tasks.
    Sets 'cancel' flag for each active task.
    """
    for tid in list(active_tasks.keys()):
        active_tasks[tid]["cancel"] = True

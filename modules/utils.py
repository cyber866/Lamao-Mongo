import os
import asyncio
import logging
from pymongo import MongoClient

log = logging.getLogger("utils")

# ---------------- MONGO SETUP ---------------- #
MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    raise SystemExit("Please set MONGO_URI environment variable.")

client = MongoClient(MONGO_URI)
db = client["mongo_leech"]

cookies_col = db["cookies"]
tasks_col = db["tasks"]

# ---------------- PATHS ---------------- #
def ensure_dirs():
    paths = ["./data/downloads", "./data/cookies"]
    for p in paths:
        os.makedirs(p, exist_ok=True)

def data_paths(user_id: int):
    return {
        "downloads": f"./data/downloads/{user_id}",
        "cookies": f"./data/cookies/{user_id}_cookies.txt"
    }

# ---------------- HUMAN READABLE SIZE ---------------- #
def humanbytes(size: int) -> str:
    if not size:
        return "N/A"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}PB"

# ---------------- TEXT PROGRESS BAR ---------------- #
def text_progress(percentage: float, length: int = 20) -> str:
    """
    Returns a simple text progress bar for the given percentage.
    """
    filled_len = int(length * percentage / 100)
    bar = "█" * filled_len + "░" * (length - filled_len)
    return f"[{bar}] {percentage:.1f}%"

# ---------------- TASK MANAGEMENT ---------------- #
ACTIVE_TASKS = {}

class DownloadCancelled(Exception):
    pass

def register_task(task_id: int):
    event = asyncio.Event()
    ACTIVE_TASKS[task_id] = {"event": event, "task": None}
    return event

def cancel_task(task_id: int = None):
    if task_id:
        t = ACTIVE_TASKS.get(task_id)
        if t:
            t["event"].set()
    else:
        for t in ACTIVE_TASKS.values():
            t["event"].set()

def cleanup_task(task_id: int):
    if task_id in ACTIVE_TASKS:
        del ACTIVE_TASKS[task_id]

def should_update(task_id: int) -> bool:
    t = ACTIVE_TASKS.get(task_id)
    if not t:
        return False
    return not t["event"].is_set()

# ---------------- SAFE EDIT ---------------- #
async def safe_edit_text(msg, text: str, reply_markup=None):
    try:
        await msg.edit(text, reply_markup=reply_markup)
    except Exception:
        pass

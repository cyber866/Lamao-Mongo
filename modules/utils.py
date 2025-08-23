import asyncio
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Dictionary to keep track of cancel flags for each chat/task
CANCEL_FLAGS = {}

def set_cancel_flag(chat_id: int, task_id: str):
    """Mark a task as cancelled."""
    if chat_id not in CANCEL_FLAGS:
        CANCEL_FLAGS[chat_id] = set()
    CANCEL_FLAGS[chat_id].add(task_id)

def clear_cancel_flag(chat_id: int, task_id: str):
    """Remove cancel flag after task is done."""
    if chat_id in CANCEL_FLAGS and task_id in CANCEL_FLAGS[chat_id]:
        CANCEL_FLAGS[chat_id].remove(task_id)

def is_cancelled(chat_id: int, task_id: str) -> bool:
    """Check if this task has been cancelled."""
    return chat_id in CANCEL_FLAGS and task_id in CANCEL_FLAGS[chat_id]

def get_cancel_button(task_id: str):
    """Return inline keyboard with cancel button for this task."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{task_id}")]]
    )

async def run_with_cancel(task_coro, chat_id: int, task_id: str, message):
    """
    Run a coroutine with cancellation check.
    If cancelled, edit the message and stop execution.
    """
    try:
        await task_coro
    except asyncio.CancelledError:
        await message.edit_text("🚫 Task cancelled by user.")
    finally:
        clear_cancel_flag(chat_id, task_id)

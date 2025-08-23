from pyrogram import Client, filters
from pyrogram.types import Message


# ------------------------- /start command -------------------------
@Client.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        "👋 Hello! I am your private leech bot.\n\n"
        "📥 Use:\n"
        "  • `/leech <link>` – to leech files (Google Drive, direct links, etc.)\n"
        "  • `/ytdlp <link>` – to stream/download streamable media via yt-dlp\n\n"
        "❌ You can cancel any task using the cancel button."
    )

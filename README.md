# Colab Leech Bot

A Telegram bot to **download, leech, and mirror media from almost any website**, using **yt-dlp** and supporting **per-user cookies**. Supports maximum quality selection, shows file size, and allows canceling downloads.  

---

## Features

- Leech / download media from **most major websites**.
- Select **quality + file size** via inline buttons.
- Supports **per-user cookies**.
- **Cancel ongoing downloads** with a button.
- Tracks tasks in **MongoDB** (optional but recommended).
- Safe progress updates, avoids Pyrogram coroutine errors.
- Fully deployable on **Colab, VPS, or any server**.

---

## Commands

| Command         | Description |
|-----------------|-------------|
| `/start`        | Show bot welcome message and buttons |
| `/leech <url>`  | Start leeching a file from a URL |
| Add / Remove Cookies | Use the inline buttons to manage cookies.txt |

---

## Installation

1. Clone the repo:
```bash
git clone https://github.com/<username>/colab_leech_bot.git
cd colab_leech_bot

import yt_dlp
import os

# ---------------- Telegram-safe split size ----------------
MAX_SIZE = 1900 * 1024 * 1024  # 1900 MiB ≈ 1.86 GiB

def list_formats(url, cookies=None):
    """List available formats for a URL"""
    opts = {
        "quiet": True,
        "skip_download": True,
        "cookiefile": cookies if cookies else None
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        fmts = info.get("formats", [])
        result = []
        for f in fmts:
            if not f.get("format_id") or (f.get("acodec") == "none" and f.get("vcodec") == "none"):
                continue
            res = f.get("height") or 0
            result.append({
                "id": f.get("format_id"),
                "res": res,
                "size": f.get("filesize") or f.get("filesize_approx") or 0,
                "ext": f.get("ext")
            })
        # Remove duplicates based on resolution and keep largest size
        unique = {}
        for f in result:
            r = f['res']
            if r not in unique or f['size'] > unique[r]['size']:
                unique[r] = f
        return sorted(unique.values(), key=lambda x: x['res'], reverse=True)


def download_media(url, path, cookies, progress_hook, fmt_id):
    """Download media and split into Telegram-safe chunks if needed"""
    opts = {
        "outtmpl": os.path.join(path, "%(title)s.%(ext)s"),
        "cookiefile": cookies if cookies else None,
        "progress_hooks": [progress_hook],
        "format": fmt_id
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        full_path = ydl.prepare_filename(info)
        filesize = os.path.getsize(full_path)

        # If file is smaller than MAX_SIZE, return as single file
        if filesize <= MAX_SIZE:
            return [full_path], info.get("title")

        # Split large file into multiple chunks
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

        # Remove original large file
        os.remove(full_path)
        return part_paths, info.get("title")

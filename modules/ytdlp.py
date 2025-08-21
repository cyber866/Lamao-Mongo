import yt_dlp
import re
import os

def list_formats(url, cookies=None):
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
            if not f.get("format_id") or f.get("acodec") == "none" and f.get("vcodec") == "none":
                continue
            try:
                res = f.get("height") or 0
            except:
                res = 0
            result.append({
                "id": f.get("format_id"),
                "res": res,
                "size": f.get("filesize") or f.get("filesize_approx") or 0,
                "ext": f.get("ext")
            })
        # Remove duplicates based on resolution and prefer largest size
        unique = {}
        for f in result:
            r = f['res']
            if r not in unique or f['size'] > unique[r]['size']:
                unique[r] = f
        return sorted(unique.values(), key=lambda x: x['res'], reverse=True)

def download_media(url, path, cookies, progress_hook, fmt_id):
    opts = {
        "outtmpl": os.path.join(path, "%(title)s.%(ext)s"),
        "cookiefile": cookies if cookies else None,
        "progress_hooks": [progress_hook],
        "format": fmt_id
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        fname = ydl.prepare_filename(info)
        return fname, info.get("title")

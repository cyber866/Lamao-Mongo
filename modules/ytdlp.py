import yt_dlp

def list_formats(url, cookies=None, all_formats=False):
    opts = {
        "quiet": True,
        "skip_download": True,
        "cookiefile": cookies if cookies else None
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        fmts = info.get("formats", []) if all_formats else [f for f in info.get("formats", []) if f.get("format_id")]
        result = []
        for f in fmts:
            result.append({
                "id": f.get("format_id"),
                "res": f.get("format_note") or f.get("resolution") or f.get("height"),
                "size": f.get("filesize") or f.get("filesize_approx") or 0
            })
        return result

def download_media(url, path, cookies, tid, progress_hook, fmt_id):
    opts = {
        "outtmpl": f"{path}/%(title)s.%(ext)s",
        "cookiefile": cookies if cookies else None,
        "progress_hooks": [progress_hook],
        "format": fmt_id
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        fname = ydl.prepare_filename(info)
        return fname, info.get("title")

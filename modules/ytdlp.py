import yt_dlp

def list_formats(url, cookies=None):
    opts = {
        "quiet": True,
        "skip_download": True,
        "cookiefile": cookies if cookies else None
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        fmts = info.get("formats", [])
        unique = {}
        for f in fmts:
            fid = f.get("format_id")
            if fid not in unique and f.get("vcodec") != "none":
                unique[fid] = {
                    "id": fid,
                    "res": f.get("format_note") or str(f.get("height"))+"p",
                    "size": f.get("filesize") or f.get("filesize_approx") or 0
                }
        return list(unique.values())

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

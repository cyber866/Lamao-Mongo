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
        # Keep only unique resolutions with largest file size
        seen = {}
        for f in fmts:
            res = f.get("format_note") or f.get("height") or "N/A"
            size = f.get("filesize") or f.get("filesize_approx") or 0
            if res in seen:
                if size > seen[res]['size']:
                    seen[res] = {"id": f["format_id"], "res": res, "size": size}
            else:
                seen[res] = {"id": f["format_id"], "res": res, "size": size}
        return list(seen.values())

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

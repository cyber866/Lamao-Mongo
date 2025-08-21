import yt_dlp

def list_formats(url, cookies=None, all_formats=False):
    """
    Fetch available formats for a URL.
    all_formats: if True, return all available formats including audio/video mix.
    """
    opts = {
        "quiet": True,
        "skip_download": True,
    }
    if cookies:
        opts["cookiefile"] = cookies

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        fmts = info.get("formats", [])
        if not all_formats:
            # filter only formats with resolution info
            fmts = [f for f in fmts if f.get("format_id") and (f.get("height") or f.get("format_note"))]

        result = []
        for f in fmts:
            result.append({
                "id": f.get("format_id"),
                "res": f.get("format_note") or f.get("resolution") or f.get("height") or "N/A",
                "size": f.get("filesize") or f.get("filesize_approx") or 0
            })
        return result


def download_media(url, path, cookies, tid, progress_hook, fmt_id):
    """
    Download media from URL in specific format.
    """
    opts = {
        "outtmpl": f"{path}/%(title)s.%(ext)s",
        "progress_hooks": [progress_hook],
        "format": fmt_id,
    }
    if cookies:
        opts["cookiefile"] = cookies

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        fname = ydl.prepare_filename(info)
        return fname, info.get("title")

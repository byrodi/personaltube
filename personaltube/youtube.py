"""YouTube through yt-dlp: read-only import of the user's account, and anonymous search for new videos."""
import csv
import datetime
import html
import io
import json
import re
import subprocess
import sys
import urllib.parse
import zipfile
from pathlib import Path

BROWSERS = ["chrome", "firefox", "safari", "edge", "brave", "chromium", "opera", "vivaldi"]


def _ytdlp():
    try:
        import yt_dlp  # noqa: F401  (installed with the package)
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        return ["yt-dlp"]


def flat(url, limit, browser=None):
    """A channel, playlist, feed or search page as a flat list; None or {"error": ...} on failure."""
    cmd = _ytdlp() + ["--flat-playlist", "-J", "--playlist-end", str(limit),
                      "--extractor-args", "youtubetab:approximate_date", url]
    if browser:
        cmd += ["--cookies-from-browser", browser]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode:
        error = (out.stderr.strip().splitlines() or ["failed"])[-1][:160]
        print(f"  yt-dlp: {error}", flush=True)
        return {"error": error}
    return json.loads(out.stdout)


def _watched(e):
    url = e.get("url") or ""
    return {"id": e.get("id"), "title": e.get("title") or "", "channel": e.get("channel") or "",
            "short": "/shorts/" in url or 0 < (e.get("duration") or 0) <= 60}


# ---------- the user's account ----------

def import_from_browser(browser):
    """History, liked videos and subscriptions, using the YouTube login saved in a browser."""
    history = flat(":ythis", 3000, browser) or {}
    if not history.get("entries"):
        raise RuntimeError(
            f"Could not read your YouTube history with the {browser} login. Open youtube.com in "
            f"{browser}, make sure you are signed in, and try again. Or use a Google Takeout export.")
    likes = flat("https://www.youtube.com/playlist?list=LL", 1000, browser) or {}
    subs = flat("https://www.youtube.com/feed/channels", 3000, browser) or {}
    return ([_watched(e) for e in history["entries"]],
            [_watched(e) for e in likes.get("entries") or []],
            [{"name": e.get("title") or "", "handle": e.get("uploader_id") or ""}
             for e in subs.get("entries") or []])


TAKEOUT_LINK = re.compile(
    r'<a href="https://www\.youtube\.com/watch\?v=([\w-]{11})">([^<]+)</a>'
    r'(?:<br><a href="https://www\.youtube\.com/[^"]+">([^<]+)</a>)?')


def import_takeout(path):
    """History and subscriptions from a Google Takeout export (zip file or unpacked folder)."""
    path = Path(path).expanduser()
    if zipfile.is_zipfile(path):
        archive = zipfile.ZipFile(path)
        files = [(n, lambda n=n: archive.read(n)) for n in archive.namelist()]
    elif path.is_dir():
        files = [(str(p), p.read_bytes) for p in path.rglob("*") if p.is_file()]
    else:
        files = [(str(path), path.read_bytes)]
    history, subs = [], []
    for name, read in files:
        suffix = name.lower().rsplit(".", 1)[-1]
        rows = []
        if suffix == "json":
            try:
                data = json.loads(read())
            except ValueError:
                continue
            if isinstance(data, list):
                for item in data:
                    m = re.search(r"watch\?v=([\w-]{11})", str(item.get("titleUrl", "")) if isinstance(item, dict) else "")
                    if m:
                        channel = (item.get("subtitles") or [{}])[0].get("name", "")
                        title = item.get("title", "").removeprefix("Watched ")  # Takeout writes "Watched <title>"
                        rows.append({"id": m[1], "title": title, "channel": channel, "short": False})
        elif suffix == "html":
            text = read().decode("utf-8", "replace")
            rows = [{"id": m[1], "title": html.unescape(m[2]), "channel": html.unescape(m[3] or ""), "short": False}
                    for m in TAKEOUT_LINK.finditer(text)]
        elif suffix == "csv":
            table = csv.reader(io.StringIO(read().decode("utf-8-sig", "replace")))
            found = [{"name": r[2], "handle": r[1]} for r in table
                     if len(r) >= 3 and re.fullmatch(r"UC[\w-]{22}", r[0])]
            subs = found or subs
        if len(rows) > len(history):  # the biggest list of watched videos is the watch history
            history = rows
    if not history:
        raise RuntimeError("No YouTube watch history found in this Takeout export.")
    return history, [], subs


# ---------- finding new videos (no login) ----------

def search_url(query):
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)


def channel_url(handle):
    return f"https://www.youtube.com/{handle}/videos"


def videos(url, limit):
    """Long videos from a search or channel page, without Shorts and live streams.
    None means the page does not exist (wrong handle, no videos tab)."""
    data = flat(url, limit) or {}
    if "404" in data.get("error", "") or "does not have a videos tab" in data.get("error", ""):
        return None
    result = []
    for e in data.get("entries") or []:
        dur = e.get("duration") or 0
        if dur < 70 or "/shorts/" in (e.get("url") or "") or e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        date = e.get("upload_date") or ""
        if not date and e.get("timestamp"):
            date = datetime.datetime.fromtimestamp(e["timestamp"]).strftime("%Y%m%d")
        result.append({
            "id": e["id"], "title": e.get("title") or "",
            "channel": e.get("channel") or e.get("uploader") or data.get("channel") or "",
            "handle": e.get("uploader_id") or data.get("uploader_id") or e.get("channel_id") or "",
            "dur": dur, "views": e.get("view_count") or 0, "date": date,
        })
    return result

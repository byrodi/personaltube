"""The local website: static page, the feed, and what the viewer clicks."""
import http.server
import json
import threading
import time
import urllib.parse
import webbrowser

from . import engine, store

REFRESH_HOURS = 20  # look for new videos when the feed is older than this


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(store.SITE), **kwargs)

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/state":
            with engine.state_lock:
                state = engine.load_state()
            return self.send_json({**state, "updating": engine.is_updating()})
        if path == "/feed.json":
            return self.send_json(store.read("feed.json") or {"generated": "", "interests": [], "videos": []})
        super().do_GET()

    def do_POST(self):
        # Only this page may report events, not other sites open in the same browser.
        origin = self.headers.get("Origin", "")
        if self.path != "/api/event" or (origin and urllib.parse.urlparse(origin).hostname not in ("localhost", "127.0.0.1")):
            return self.send_json({"ok": False}, 403)
        try:
            event = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self.send_json({"ok": False}, 400)
        self.send_json({"ok": engine.apply_event(event)})


def _auto_update():
    while True:
        feed = store.HOME / "feed.json"
        age = time.time() - feed.stat().st_mtime if feed.exists() else float("inf")
        if age > REFRESH_HOURS * 3600 and store.read("profile.json"):
            try:
                engine.update()
            except Exception as e:  # keep the site running even if one update fails
                print(f"Update failed: {e}", flush=True)
        time.sleep(3600)


def serve(open_browser=True):
    port = store.config()["port"]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=_auto_update, daemon=True).start()
    url = f"http://localhost:{port}/"
    print(f"PersonalTube is running on {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

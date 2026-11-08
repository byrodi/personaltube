"""The local website: setup, the feed, and what the viewer clicks."""
import http.server
import json
import os
import threading
import time
import urllib.parse
import webbrowser

from . import ai, engine, store, youtube

REFRESH_HOURS = 20  # look for new videos when the feed is older than this


# ---------- setup steps, called from the page ----------

def setup_state():
    cfg = store.config()
    history = store.read("history.json")
    profile = store.read("profile.json")
    state = {
        "ai_ok": (store.HOME / "config.json").exists(),  # config is saved only after the AI answered
        "language": cfg["language"],
        "provider": cfg["ai"]["provider"],
        "has_key": bool(cfg["ai"]["api_key"]),
        "base_url": cfg["ai"]["base_url"],
        "model": cfg["ai"]["model"],
        "providers": [{"id": k, "label": v[0], "installed": ai.installed(k)} for k, v in ai.PROVIDERS.items()],
        "browsers": youtube.BROWSERS,
        "browser": cfg["youtube"]["browser"],
        "youtube": {"history": len(history), "likes": len(store.read("likes.json", [])),
                    "subs": len(store.read("subscriptions.json", []))} if history else None,
        "interests": [i["name"] for i in profile["interests"]] if profile else [],
    }
    state["ready"] = bool(state["ai_ok"] and state["youtube"] and state["interests"])
    return state


def setup_ai(body):
    provider = body.get("provider")
    if provider not in ai.PROVIDERS:
        raise RuntimeError("Choose an AI provider.")
    old = store.config()
    ai_cfg = {**store.DEFAULT_CONFIG["ai"], "provider": provider}
    if provider in ("openrouter", "openai"):
        same = old["ai"]["provider"] == provider
        ai_cfg["api_key"] = body.get("api_key", "").strip() or (old["ai"]["api_key"] if same else "")
        if provider == "openrouter" and not (ai_cfg["api_key"] or os.environ.get("OPENROUTER_API_KEY")):
            raise RuntimeError("Paste your OpenRouter API key.")
    if provider == "openai":
        ai_cfg["base_url"] = body.get("base_url", "").strip()
        ai_cfg["model"] = body.get("model", "").strip()
        if not ai_cfg["base_url"] or not ai_cfg["model"]:
            raise RuntimeError("Enter the API base URL and the model name.")
    error = ai.check(ai_cfg)
    if error:
        raise RuntimeError(f"The AI did not answer: {error}")
    store.save_config({**old, "language": body.get("language", "").strip() or "English", "ai": ai_cfg})


def setup_youtube(body):
    if body.get("browser") not in youtube.BROWSERS:
        raise RuntimeError("Choose a browser.")
    engine.connect(browser=body["browser"])


def setup_takeout(data, filename):
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".zip"
    path = store.HOME / f"takeout-upload{suffix}"
    store.HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        engine.connect(takeout=path)
    finally:
        path.unlink(missing_ok=True)


def setup_start(_body):
    threading.Thread(target=_safe_update, daemon=True).start()


SETUP_ACTIONS = {
    "/api/setup/ai": setup_ai,
    "/api/setup/youtube": setup_youtube,
    "/api/setup/profile": lambda _body: engine.build_profile(),
    "/api/setup/start": setup_start,
}


# ---------- HTTP ----------

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
            feed = store.read("feed.json") or {}
            return self.send_json({**state, "updating": engine.is_updating(),
                                   "ready": setup_state()["ready"], "feed": len(feed.get("videos", []))})
        if path == "/api/setup":
            return self.send_json(setup_state())
        if path == "/feed.json":
            return self.send_json(store.read("feed.json") or {"generated": "", "interests": [], "videos": []})
        super().do_GET()

    def do_POST(self):
        # Only this page may call the API, not other sites open in the same browser.
        origin = self.headers.get("Origin", "")
        if origin and urllib.parse.urlparse(origin).hostname not in ("localhost", "127.0.0.1"):
            return self.send_json({"ok": False}, 403)
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            if self.path == "/api/setup/takeout":
                setup_takeout(data, self.headers.get("X-Filename", "takeout.zip"))
                return self.send_json({"ok": True, **setup_state()})
            body = json.loads(data or b"{}")
            if self.path == "/api/event":
                return self.send_json({"ok": engine.apply_event(body)})
            if self.path in SETUP_ACTIONS:
                SETUP_ACTIONS[self.path](body)
                return self.send_json({"ok": True, **setup_state()})
        except Exception as e:  # show any setup failure on the page instead of dropping the request
            return self.send_json({"ok": False, "error": str(e) or e.__class__.__name__})
        self.send_json({"ok": False}, 404)


def _safe_update():
    try:
        engine.update()
    except Exception as e:  # keep the site running even if one update fails
        print(f"Update failed: {e}", flush=True)


def _auto_update():
    while True:
        feed = store.HOME / "feed.json"
        age = time.time() - feed.stat().st_mtime if feed.exists() else float("inf")
        if age > REFRESH_HOURS * 3600 and setup_state()["ready"]:
            _safe_update()
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

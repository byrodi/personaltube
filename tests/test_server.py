import http.server
import json
import os
import threading
import urllib.error
import urllib.request
from unittest import mock

from common import TempHome
from personaltube import engine, server, store

LOCAL = "http://localhost:8777"


class ServerTest(TempHome):
    def setUp(self):
        super().setUp()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def request(self, path, body=None, origin=LOCAL, raw=None, headers=None):
        data = raw if raw is not None else None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        if origin:
            req.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def json(self, path, body=None, **kwargs):
        status, _, data = self.request(path, body, **kwargs)
        return status, json.loads(data)

    def test_page_and_files_are_served_without_cache(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="gear"', body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        for name in ("/app.js", "/style.css"):
            self.assertEqual(self.request(name)[0], 200, name)

    def test_files_outside_the_site_are_not_served(self):
        for path in ("/../engine.py", "/%2e%2e/engine.py", "/..%2fengine.py"):
            status, _, body = self.request(path)
            self.assertNotIn(b"def update", body, path)

    def test_empty_feed_and_state_before_setup(self):
        self.assertEqual(self.json("/feed.json"), (200, {"generated": "", "interests": [], "videos": []}))
        status, state = self.json("/api/state")
        self.assertEqual((state["updating"], state["ready"], state["feed"]), (False, False, 0))
        self.assertEqual(state["liked"], {})

    def test_progress(self):
        self.assertEqual(self.json("/api/progress"), (200, {"updating": False}))
        store.write(engine.LOCK, {"done": 2, "total": 9, "feed": 40})
        status, p = self.json("/api/progress")
        self.assertEqual((p["updating"], p["done"], p["feed"]), (True, 2, 40))

    def test_events_from_the_page_are_saved(self):
        status, res = self.json("/api/event", {"type": "like", "id": "abc", "video": {"title": "T", "handle": "@h"}})
        self.assertEqual(res, {"ok": True})
        self.assertIn("abc", store.read("state.json")["liked"])

    def test_other_sites_cannot_call_the_api(self):
        status, res = self.json("/api/event", {"type": "hide", "id": "abc"}, origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertIsNone(store.read("state.json"))

    def test_bad_requests_get_an_error_not_a_crash(self):
        status, _, body = self.request("/api/event", raw=b"{not json")
        self.assertEqual(json.loads(body)["ok"], False)
        self.assertEqual(self.json("/api/nothing", {})[0], 404)

    def test_setup_explains_what_is_missing(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            cases = [("/api/setup/ai", {"provider": "openrouter"}, "Paste your OpenRouter API key."),
                     ("/api/setup/ai", {"provider": "nope"}, "Choose an AI provider."),
                     ("/api/setup/ai", {"provider": "openai"}, "Enter the API base URL and the model name."),
                     ("/api/setup/youtube", {"browser": "netscape"}, "Choose a browser."),
                     ("/api/setup/profile", {}, "No YouTube history yet. Connect your YouTube account first.")]
            for path, body, error in cases:
                self.assertEqual(self.json(path, body)[1], {"ok": False, "error": error}, path)
        self.assertFalse((self.home / "config.json").exists())

    def test_ai_setup_saves_the_config_only_after_the_ai_answers(self):
        with mock.patch("personaltube.ai.check", return_value="HTTP 401: bad key"):
            status, res = self.json("/api/setup/ai", {"provider": "openrouter", "api_key": "sk-or-bad"})
        self.assertEqual(res["error"], "The AI did not answer: HTTP 401: bad key")
        self.assertFalse((self.home / "config.json").exists())
        with mock.patch("personaltube.ai.check", return_value=None):
            status, res = self.json("/api/setup/ai", {"provider": "openrouter", "api_key": "sk-or-good"})
        self.assertTrue(res["ok"] and res["ai_ok"] and res["has_key"])
        self.assertEqual(oct((self.home / "config.json").stat().st_mode & 0o777), "0o600")
        with mock.patch("personaltube.ai.check", return_value=None):  # an empty key keeps the saved one
            self.json("/api/setup/ai", {"provider": "openrouter", "api_key": ""})
        self.assertEqual(store.config()["ai"]["api_key"], "sk-or-good")

    def test_takeout_upload(self):
        takeout = json.dumps([{"title": "Watched Cool video", "titleUrl": "https://www.youtube.com/watch?v=abcdefghijk",
                               "subtitles": [{"name": "Cool channel"}]}]).encode()
        status, res = self.json("/api/setup/takeout", raw=takeout, headers={"X-Filename": "watch-history.json"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["youtube"], {"history": 1, "likes": 0, "subs": 0})
        self.assertEqual(list(self.home.glob("takeout-upload*")), [])  # the upload is not kept

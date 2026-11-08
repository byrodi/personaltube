import os
import re
import time
import urllib.parse
from unittest import mock

from common import TempHome, video
from personaltube import engine, store

PROFILE = {
    "rules": ["English only"],
    "interests": [{"id": "games", "name": "Games", "weight": 8, "about": "Game videos",
                   "queries": ["q1", "q2", "q3"], "channels": ["@good", "@gone"]}],
    "avoid": ["kids"],
    "discovered": [],
}


def fake_videos(url, limit):
    """Search pages return two good and two bad videos; @good has good ones; @gone does not exist."""
    if "@gone" in url:
        return None
    if "@good" in url:
        return [video(f"good{i}", "good stream", "@good") for i in range(3)]
    if "/results" not in url:
        return []  # other channels, like the ones learned in an earlier update
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["search_query"][0]
    return [video(f"{q}-{i}", "good one" if i % 2 == 0 else "bad one", f"@ch{i}") for i in range(4)] + [
        video("watched1", "good but watched"), video("hidden1", "good but hidden")]


def fake_ask(system, user, **kwargs):
    """Scores 9 for titles with "good", 2 for the rest, like a strict AI."""
    rows = []
    for line in user.splitlines():
        n, title = re.match(r"(\d+)\. (.*?) \|", line).groups()
        rows.append([int(n), 9 if "good" in title else 2, "games"])
    return {"r": rows}


class UpdateTest(TempHome):
    def setUp(self):
        super().setUp()
        store.write("profile.json", PROFILE)
        store.write("state.json", {"watched": {"watched1": {"title": "x", "channel": "y", "handle": "@y"}},
                                   "hidden": {"hidden1": {"title": "x", "channel": "y", "handle": "@y"}},
                                   "liked": {}, "seen": {}})
        for patcher in [mock.patch("personaltube.engine.time.sleep"),
                        mock.patch("personaltube.engine.youtube.videos", fake_videos),
                        mock.patch("personaltube.engine.ai.parallel", lambda: (2, 5)),
                        mock.patch("personaltube.engine.FIRST_BATCH", 3)]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.writes = []
        real_write = store.write

        def spy(name, data, pretty=False):
            if name == "feed.json":
                self.writes.append((name, len(data["videos"])))
            elif name == engine.LOCK:
                self.writes.append((name, dict(data)))
            real_write(name, data, pretty)

        patcher = mock.patch("personaltube.store.write", spy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def feed_ids(self):
        return {v["id"] for v in store.read("feed.json")["videos"]}

    def test_feed_gets_good_videos_and_skips_watched_and_hidden(self):
        with mock.patch("personaltube.engine.ai.ask", side_effect=fake_ask):
            engine.update()
        self.assertEqual(self.feed_ids(),
                         {"q1-0", "q1-2", "q2-0", "q2-2", "q3-0", "q3-2", "good0", "good1", "good2"})
        self.assertFalse((self.home / engine.LOCK).exists())
        self.assertFalse(engine.is_updating())

    def test_feed_is_written_while_the_search_runs(self):
        with mock.patch("personaltube.engine.ai.ask", side_effect=fake_ask):
            engine.update()
        sizes = [n for name, n in self.writes if name == "feed.json"]
        self.assertGreater(len(set(sizes)), 2, sizes)  # several growing versions, not one at the end
        self.assertEqual(sizes, sorted(sizes))

    def test_progress_ends_with_everything_checked(self):
        with mock.patch("personaltube.engine.ai.ask", side_effect=fake_ask):
            engine.update()
        last = [r for name, r in self.writes if name == engine.LOCK][-1]
        self.assertEqual((last["done"], last["total"]), (5, 5))
        self.assertEqual(last["checked"], last["found"])
        self.assertEqual(last["feed"], 9)

    def test_cached_scores_need_no_ai(self):
        with mock.patch("personaltube.engine.ai.ask", side_effect=fake_ask):
            engine.update()
        store.write("feed.json", {"generated": "", "interests": [], "videos": []})
        with mock.patch("personaltube.engine.ai.ask") as ask:
            engine.update()
        ask.assert_not_called()
        self.assertEqual(len(self.feed_ids()), 9)
        last = [r for name, r in self.writes if name == engine.LOCK][-1]
        self.assertEqual(last["checked"], last["found"])  # cached scores count as checked

    def test_good_channels_are_learned_and_broken_ones_dropped(self):
        with mock.patch("personaltube.engine.ai.ask", side_effect=fake_ask):
            engine.update()
        profile = store.read("profile.json")
        self.assertIn("@good", profile["discovered"])
        self.assertEqual(profile["interests"][0]["channels"], ["@good"])

    def test_a_failed_ai_batch_is_skipped(self):
        with mock.patch("personaltube.engine.ai.ask", side_effect=RuntimeError("AI is down")):
            engine.update()
        self.assertEqual(self.feed_ids(), set())
        self.assertFalse(engine.is_updating())

    def test_a_crash_in_scoring_stops_the_update_and_frees_the_lock(self):
        with mock.patch("personaltube.engine.ai.ask", side_effect=ValueError("bug")):
            with self.assertRaises(ValueError):
                engine.update()
        self.assertFalse(engine.is_updating())
        self.assertFalse((self.home / engine.LOCK).exists())

    def test_old_videos_stay_for_a_while(self):
        now = time.time()
        store.write("feed.json", {"generated": "", "interests": [], "videos": [
            {**video("recent"), "score": 8, "interest": "games", "found": now - 10 * 86400},
            {**video("ancient"), "score": 8, "interest": "games", "found": now - 60 * 86400},
            {**video("hidden1"), "score": 8, "interest": "games", "found": now},
        ]})
        with mock.patch("personaltube.engine.ai.ask", side_effect=fake_ask):
            engine.update()
        feed = {v["id"]: v for v in store.read("feed.json")["videos"]}
        self.assertIn("recent", feed)
        self.assertEqual(feed["recent"]["found"], now - 10 * 86400)
        self.assertNotIn("ancient", feed)
        self.assertNotIn("hidden1", feed)

    def test_a_second_update_does_not_start(self):
        store.write(engine.LOCK, {"done": 1})
        with mock.patch("personaltube.engine.youtube.videos") as videos:
            engine.update()
        videos.assert_not_called()


class ProgressTest(TempHome):
    def test_no_lock_means_no_update(self):
        self.assertFalse(engine.is_updating())
        self.assertIsNone(engine.progress())

    def test_fresh_lock_reports_progress(self):
        store.write(engine.LOCK, {"done": 3, "total": 10})
        self.assertEqual(engine.progress(), {"done": 3, "total": 10})

    def test_lock_from_an_older_version_still_counts(self):
        (self.home / engine.LOCK).write_text("12345")  # 0.1.0 wrote the process id
        self.assertEqual(engine.progress(), {})

    def test_stale_lock_from_a_dead_update_is_ignored(self):
        store.write(engine.LOCK, {"done": 3})
        old = time.time() - engine.STALE_LOCK - 60
        os.utime(self.home / engine.LOCK, (old, old))
        self.assertFalse(engine.is_updating())
        self.assertIsNone(engine.progress())

    def test_lock_removed_while_reading(self):
        store.write(engine.LOCK, {"done": 3})
        with mock.patch("personaltube.engine.store.read", side_effect=FileNotFoundError):
            self.assertEqual(engine.progress(), {})


class EventTest(TempHome):
    def test_events_change_the_state(self):
        v = video("abc", "t", "@c")
        self.assertTrue(engine.apply_event({"type": "seen", "ids": ["abc", "abc", "def"]}))
        self.assertTrue(engine.apply_event({"type": "like", "id": "abc", "video": v}))
        self.assertTrue(engine.apply_event({"type": "hide", "id": "def", "video": v}))
        state = engine.load_state()
        self.assertEqual(state["seen"], {"abc": 2, "def": 1})
        self.assertEqual(state["liked"]["abc"]["handle"], "@c")
        self.assertIn("def", state["hidden"])
        engine.apply_event({"type": "unlike", "id": "abc"})
        engine.apply_event({"type": "unhide", "id": "def"})
        state = engine.load_state()
        self.assertEqual((state["liked"], state["hidden"]), ({}, {}))

    def test_unknown_event_is_refused(self):
        self.assertFalse(engine.apply_event({"type": "delete-everything"}))

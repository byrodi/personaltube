import json
import zipfile
from unittest import mock

from common import TempHome
from personaltube import youtube


def entry(vid, duration=600, **extra):
    return {"id": vid, "title": f"title {vid}", "duration": duration, "url": f"https://www.youtube.com/watch?v={vid}",
            "channel": "Chan", "uploader_id": "@chan", "view_count": 5, "upload_date": "20261001", **extra}


class VideosTest(TempHome):
    def test_only_long_videos_that_are_not_live(self):
        page = {"entries": [
            entry("long"), entry("short", 50), entry("shortsurl", 300, url="https://www.youtube.com/shorts/x"),
            entry("live", live_status="is_live"), entry("soon", live_status="is_upcoming"),
            entry("nodate", upload_date=None, timestamp=1790000000)]}
        with mock.patch.object(youtube, "flat", return_value=page):
            result = youtube.videos("url", 10)
        self.assertEqual([v["id"] for v in result], ["long", "nodate"])
        self.assertEqual(result[0], {"id": "long", "title": "title long", "channel": "Chan", "handle": "@chan",
                                     "dur": 600, "views": 5, "date": "20261001"})
        self.assertRegex(result[1]["date"], r"^\d{8}$")

    def test_missing_channel_means_none_and_other_errors_mean_empty(self):
        cases = [({"error": "ERROR: HTTP Error 404: Not Found"}, None),
                 ({"error": "ERROR: This channel does not have a videos tab"}, None),
                 ({"error": "ERROR: Unable to download webpage: timed out"}, []),
                 (None, [])]  # yt-dlp timed out
        for page, expected in cases:
            with mock.patch.object(youtube, "flat", return_value=page):
                self.assertEqual(youtube.videos("url", 10), expected, page)

    def test_search_url_encodes_the_query(self):
        self.assertEqual(youtube.search_url("F1 & Формула"),
                         "https://www.youtube.com/results?search_query=F1+%26+%D0%A4%D0%BE%D1%80%D0%BC%D1%83%D0%BB%D0%B0")

    def test_browser_import_explains_a_missing_login(self):
        with mock.patch.object(youtube, "flat", return_value={"error": "no cookies"}):
            with self.assertRaisesRegex(RuntimeError, "signed in"):
                youtube.import_from_browser("chrome")


class TakeoutTest(TempHome):
    HISTORY = [
        {"title": "Watched First video", "titleUrl": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
         "subtitles": [{"name": "Chan A"}]},
        {"title": "Watched a video that has been removed"},  # no link: skipped
        {"title": "Watched Second video", "titleUrl": "https://www.youtube.com/watch?v=bbbbbbbbbbb"},
    ]
    HTML = ('Watched <a href="https://www.youtube.com/watch?v=ccccccccccc">Third &amp; last</a>'
            '<br><a href="https://www.youtube.com/channel/UCxxx">Chan C</a>')
    CSV = "Channel Id,Channel Url,Channel Title\nUC1234567890123456789012,http://www.youtube.com/channel/UC1,My channel\n"

    def test_json_history(self):
        path = self.home / "watch-history.json"
        path.write_text(json.dumps(self.HISTORY))
        history, likes, subs = youtube.import_takeout(path)
        self.assertEqual(history, [
            {"id": "aaaaaaaaaaa", "title": "First video", "channel": "Chan A", "short": False},
            {"id": "bbbbbbbbbbb", "title": "Second video", "channel": "", "short": False}])

    def test_zip_with_html_history_and_subscriptions(self):
        path = self.home / "takeout.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("Takeout/YouTube/history/watch-history.html", self.HTML)
            z.writestr("Takeout/YouTube/subscriptions/subscriptions.csv", self.CSV)
        history, likes, subs = youtube.import_takeout(path)
        self.assertEqual(history, [{"id": "ccccccccccc", "title": "Third & last", "channel": "Chan C", "short": False}])
        self.assertEqual(subs, [{"name": "My channel", "handle": "http://www.youtube.com/channel/UC1"}])

    def test_no_history_is_an_error(self):
        path = self.home / "empty.json"
        path.write_text("[]")
        with self.assertRaisesRegex(RuntimeError, "No YouTube watch history"):
            youtube.import_takeout(path)

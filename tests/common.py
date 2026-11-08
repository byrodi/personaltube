"""Shared test helpers: an empty data folder per test and fake YouTube videos."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personaltube import store


class TempHome(unittest.TestCase):
    """Every test gets its own empty ~/.personaltube."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="personaltube-test-"))
        patcher = mock.patch.object(store, "HOME", self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)


def video(vid, title="a video", handle="@someone", **extra):
    return {"id": vid, "title": title, "channel": handle.lstrip("@").title(), "handle": handle,
            "dur": 600, "views": 1000, "date": "20261101", **extra}

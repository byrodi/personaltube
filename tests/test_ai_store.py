from unittest import mock

from common import TempHome
from personaltube import ai, store


class AskTest(TempHome):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("personaltube.ai.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_json_is_found_inside_extra_text(self):
        with mock.patch.object(ai, "_http", return_value='Sure! ```json\n{"r": [[0, 9, "x"]]}\n``` Done.'):
            self.assertEqual(ai.ask("sys", "user"), {"r": [[0, 9, "x"]]})

    def test_a_failed_call_is_tried_again(self):
        with mock.patch.object(ai, "_http", side_effect=[OSError("reset"), "not json", '{"ok": true}']) as call:
            self.assertEqual(ai.ask("sys", "user"), {"ok": True})
        self.assertEqual(call.call_count, 3)

    def test_three_failures_give_a_readable_error(self):
        with mock.patch.object(ai, "_http", side_effect=RuntimeError("HTTP 402: no credits")):
            with self.assertRaisesRegex(RuntimeError, "AI request failed: HTTP 402: no credits"):
                ai.ask("sys", "user")

    def test_models_by_provider(self):
        s = ai.settings({**store.DEFAULT_CONFIG["ai"], "provider": "claude"})
        self.assertEqual((s["model"], s["profile_model"]), ("haiku", "sonnet"))
        s = ai.settings({**store.DEFAULT_CONFIG["ai"], "provider": "openai", "model": "llama3.1"})
        self.assertEqual((s["model"], s["profile_model"]), ("llama3.1", "llama3.1"))

    def test_missing_cli_is_reported(self):
        with mock.patch("personaltube.ai.shutil.which", return_value=None):
            error = ai.check({**store.DEFAULT_CONFIG["ai"], "provider": "codex"})
        self.assertEqual(error, "The `codex` command is not installed on this computer.")


class StoreTest(TempHome):
    def test_files_are_private_and_read_back(self):
        store.write("x.json", {"a": [1, "ё"]})
        self.assertEqual(store.read("x.json"), {"a": [1, "ё"]})
        self.assertEqual((self.home / "x.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(store.read("missing.json", []), [])

    def test_config_fills_in_defaults(self):
        store.write("config.json", {"ai": {"provider": "claude"}})
        cfg = store.config()
        self.assertEqual(cfg["ai"]["provider"], "claude")
        self.assertEqual(cfg["ai"]["api_key"], "")
        self.assertEqual(cfg["youtube"]["browser"], "chrome")
        self.assertEqual(cfg["port"], 8777)

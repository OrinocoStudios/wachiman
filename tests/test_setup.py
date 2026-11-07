import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wachiman import cli, paths, setup

TOKEN = "123456:SECRET-token_value"


class Script:
    """Scripted answers; records every prompt and every line shown to the user."""

    def __init__(self, answers, secrets=()):
        self.answers, self.secrets = list(answers), list(secrets)
        self.said, self.prompts = [], []
        self.gets, self.posts = [], []
        self.get_result = {"ok": True, "result": {"username": "my_bot"}}

    def ask(self, prompt=""):
        self.prompts.append(prompt)
        return self.answers.pop(0)

    def ask_secret(self, prompt=""):
        self.prompts.append(prompt)
        return self.secrets.pop(0)

    def say(self, *parts):
        self.said.append(" ".join(str(p) for p in parts))

    def get(self, url):
        self.gets.append(url)
        if isinstance(self.get_result, Exception):
            raise self.get_result
        return self.get_result

    def post(self, url, data):
        self.posts.append((url, data))
        return {"ok": True}

    def run(self, env, **kw):
        return setup.run(env, ask=self.ask, ask_secret=self.ask_secret, say=self.say, get=self.get,
                         post=self.post, **kw)

    @property
    def screen(self):
        return "\n".join(self.said + self.prompts)


class WriteConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "wm" / "config.env"

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_file_600_in_folder_700(self):
        setup.write_config(self.path, {"WACHIMAN_LANG": "es"})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(paths.load_config(self.path), {"WACHIMAN_LANG": "es"})

    def test_keeps_unknown_keys_and_comments_and_replaces_known(self):
        self.path.parent.mkdir()
        self.path.write_text("# mine\nFOO=bar\nWACHIMAN_LANG=en\nWACHIMAN_UPLOAD_ALERT_MB=5\n")
        os.chmod(self.path, 0o644)
        setup.write_config(self.path, {"WACHIMAN_LANG": "es", "WACHIMAN_TELEGRAM_CHAT_ID": "42"})
        text = self.path.read_text()
        self.assertIn("# mine", text)
        self.assertEqual(paths.load_config(self.path), {
            "FOO": "bar", "WACHIMAN_LANG": "es", "WACHIMAN_UPLOAD_ALERT_MB": "5", "WACHIMAN_TELEGRAM_CHAT_ID": "42"})
        self.assertEqual(text.count("WACHIMAN_LANG"), 1)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_rejects_values_with_newlines(self):
        with self.assertRaises(ValueError):
            setup.write_config(self.path, {"WACHIMAN_LANG": "es\nEVIL=1"})


class ValidateTokenTest(unittest.TestCase):
    def test_returns_bot_name_when_telegram_accepts(self):
        s = Script([])
        self.assertEqual(setup.check_token(TOKEN, s.get), "my_bot")
        self.assertEqual(s.gets, ["https://api.telegram.org/bot%s/getMe" % TOKEN])

    def test_none_when_rejected_or_network_fails(self):
        s = Script([])
        s.get_result = {"ok": False}
        self.assertIsNone(setup.check_token(TOKEN, s.get))
        s.get_result = OSError("boom")
        self.assertIsNone(setup.check_token(TOKEN, s.get))


class WizardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"HOME": self.tmp.name}
        self.cfg = paths.config_path(self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def test_language_only_no_telegram(self):
        s = Script(["es", "n"])
        self.assertEqual(s.run(self.env), 0)
        self.assertEqual(paths.load_config(self.cfg), {"WACHIMAN_LANG": "es"})
        self.assertEqual(s.gets, [])
        self.assertEqual(s.posts, [])

    def test_unknown_language_falls_back_to_english(self):
        s = Script(["klingon", "n"])
        s.run(self.env)
        self.assertEqual(paths.load_config(self.cfg)["WACHIMAN_LANG"], "en")

    def test_explains_where_to_get_token_and_chat_id(self):
        s = Script(["en", "y", "", "n"], secrets=[""])
        s.run(self.env)
        self.assertIn("@BotFather", s.screen)
        self.assertIn("@userinfobot", s.screen)

    def test_full_flow_saves_token_never_echoes_it_and_tests_only_on_yes(self):
        s = Script(["en", "y", "987654", "y"], secrets=[TOKEN])
        self.assertEqual(s.run(self.env), 0)
        cfg = paths.load_config(self.cfg)
        self.assertEqual(cfg["WACHIMAN_TELEGRAM_BOT_TOKEN"], TOKEN)
        self.assertEqual(cfg["WACHIMAN_TELEGRAM_CHAT_ID"], "987654")
        self.assertEqual(stat.S_IMODE(self.cfg.stat().st_mode), 0o600)
        self.assertNotIn(TOKEN, s.screen)
        self.assertNotIn("SECRET-token", s.screen)
        self.assertIn("my_bot", s.screen)
        self.assertEqual(len(s.posts), 1)
        url, data = s.posts[0]
        self.assertTrue(url.endswith("/sendMessage"))
        self.assertEqual(data["chat_id"], "987654")

    def test_declining_the_test_message_sends_nothing(self):
        s = Script(["en", "y", "987654", "n"], secrets=[TOKEN])
        s.run(self.env)
        self.assertEqual(s.posts, [])
        self.assertEqual(paths.load_config(self.cfg)["WACHIMAN_TELEGRAM_CHAT_ID"], "987654")

    def test_rejected_token_is_not_saved(self):
        s = Script(["en", "y"], secrets=[TOKEN])
        s.get_result = {"ok": False}
        self.assertEqual(s.run(self.env), 0)
        self.assertNotIn("WACHIMAN_TELEGRAM_BOT_TOKEN", paths.load_config(self.cfg))
        self.assertNotIn(TOKEN, s.screen)
        self.assertEqual(s.posts, [])

    def test_network_error_message_never_contains_token(self):
        s = Script(["en", "y"], secrets=[TOKEN])
        s.get_result = OSError("failed https://api.telegram.org/bot%s/getMe" % TOKEN)
        s.run(self.env)
        self.assertNotIn(TOKEN, s.screen)

    def test_existing_telegram_settings_can_be_kept_and_unknown_keys_survive(self):
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text("FOO=bar\nWACHIMAN_TELEGRAM_BOT_TOKEN=%s\nWACHIMAN_TELEGRAM_CHAT_ID=1\n" % TOKEN)
        s = Script(["es", "y"])  # language, keep telegram
        s.run(self.env)
        cfg = paths.load_config(self.cfg)
        self.assertEqual(cfg["FOO"], "bar")
        self.assertEqual(cfg["WACHIMAN_TELEGRAM_BOT_TOKEN"], TOKEN)
        self.assertEqual(cfg["WACHIMAN_LANG"], "es")
        self.assertNotIn(TOKEN, s.screen)

    def test_wizard_talks_in_the_chosen_language(self):
        s = Script(["es", "n"])
        s.run(self.env)
        self.assertIn("Telegram", s.screen)
        self.assertRegex(s.screen, "(?i)avisos|alertas")


class NonInteractiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / ".wachiman" / "config.env"

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_values_from_env_and_never_asks_or_calls_network(self):
        s = Script([])
        env = {"HOME": self.tmp.name, "WACHIMAN_LANG": "es", "WACHIMAN_TELEGRAM_BOT_TOKEN": TOKEN,
               "WACHIMAN_TELEGRAM_CHAT_ID": "77", "PATH": "/usr/bin"}
        self.assertEqual(s.run(env, non_interactive=True), 0)
        self.assertEqual(paths.load_config(self.cfg), {
            "WACHIMAN_LANG": "es", "WACHIMAN_TELEGRAM_BOT_TOKEN": TOKEN, "WACHIMAN_TELEGRAM_CHAT_ID": "77"})
        self.assertEqual((s.prompts, s.gets, s.posts), ([], [], []))
        self.assertNotIn(TOKEN, s.screen)
        self.assertEqual(stat.S_IMODE(self.cfg.stat().st_mode), 0o600)

    def test_bad_language_is_an_error(self):
        s = Script([])
        self.assertEqual(s.run({"HOME": self.tmp.name, "WACHIMAN_LANG": "xx"}, non_interactive=True), 2)

    def test_nothing_given_changes_nothing(self):
        s = Script([])
        self.assertEqual(s.run({"HOME": self.tmp.name}, non_interactive=True), 0)
        self.assertFalse(self.cfg.exists())


class CliWiringTest(unittest.TestCase):
    def test_setup_command_calls_wizard(self):
        with mock.patch.object(setup, "run", return_value=0) as run:
            self.assertEqual(cli.main(["setup"], env={"HOME": "/x"}), 0)
        self.assertFalse(run.call_args.kwargs.get("non_interactive"))

    def test_setup_non_interactive_flag(self):
        with mock.patch.object(setup, "run", return_value=0) as run:
            cli.main(["setup", "--non-interactive"], env={"HOME": "/x"})
        self.assertTrue(run.call_args.kwargs["non_interactive"])

    def test_uninstall_stops_service_and_deletes_nothing(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as t:
            home = Path(t) / ".wachiman"
            home.mkdir()
            buf = io.StringIO()
            with mock.patch("wachiman.service.uninstall", return_value=0) as un, redirect_stdout(buf):
                self.assertEqual(cli.main(["uninstall"], env={"HOME": t}), 0)
            un.assert_called_once()
            self.assertTrue(home.exists())
            self.assertIn(str(home), buf.getvalue())
            self.assertIn("rm -rf", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

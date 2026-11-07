import json
import tempfile
import unittest
from pathlib import Path

from tests.shell_helpers import FakeTelegram, TOKEN
from wachiman import gateway

CHAT = "42"


class FakeTransport:
    def __init__(self, updates=None):
        self.calls = []
        self.photos = []
        self.updates = list(updates or [])

    def call(self, method, data, timeout=70):
        self.calls.append((method, data))
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return {"ok": True, "result": out}
        return {"ok": True}

    def send_photo(self, path, caption=""):
        self.photos.append((Path(path).name, caption))

    def texts(self):
        return [d["text"] for m, d in self.calls if m == "sendMessage"]

    def chats(self):
        return [d["chat_id"] for m, d in self.calls if m == "sendMessage"]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / "guard").mkdir()
        self.runs = []
        self.out = "fake output"
        self.now = [1000.0]
        self.t = FakeTransport()
        self.settings = {"WACHIMAN_TELEGRAM_BOT_TOKEN": TOKEN, "WACHIMAN_TELEGRAM_CHAT_ID": CHAT}

    def make(self, **settings):
        s = dict(self.settings)
        s.update(settings)

        def runner(argv, timeout=60):
            self.runs.append(list(argv))
            return self.out

        return gateway.Gateway(s, home=self.home, repo=Path("/repo"), transport=self.t, runner=runner,
                               clock=lambda: self.now[0], sleep=lambda s: None)

    def photo_files(self, *names):
        for n in names:
            (self.home / "guard" / n).write_bytes(b"jpg")

    def jpgs(self):
        return list((self.home / "guard").glob("*.jpg"))


class CommandsTest(Case):
    def test_guard_commands_call_the_scripts_with_fixed_arguments(self):
        g = self.make()
        for text, tail in [("/on", ["start"]), ("/test", ["start-nolock"]), ("/off", ["stop"]), ("/status", ["status"])]:
            self.runs.clear()
            g.handle(text, CHAT)
            self.assertEqual(self.runs[0][0], "/bin/bash", text)
            self.assertTrue(self.runs[0][1].endswith("modules/guard/guardctl.sh"), self.runs)
            self.assertEqual(self.runs[0][2:], tail)

    def test_user_text_never_reaches_argv(self):
        g = self.make()
        g.handle("/status ; rm -rf ~ $(whoami)", CHAT)
        self.assertEqual(self.runs[0][2:], ["status"])
        self.assertFalse(any("rm" in a or "whoami" in a for r in self.runs for a in r))

    def test_lock_runs_lock_script_and_quotes_the_shield_state_when_available(self):
        (self.home / "guard" / "guard.debug").write_text("10:00:00 locked=1 idle=3 prev_idle=2 pending=0\n")
        g = self.make()
        self.out = "LOCKED"
        g.handle("/lock", CHAT)
        self.assertTrue(self.runs[0][1].endswith("modules/guard/lock.sh"))
        self.assertIn("shield: locked", self.t.texts()[-1])

    def test_lock_without_a_fresh_poll_says_the_answer_is_weaker(self):
        g = self.make()
        self.out = "LOCKED"
        g.handle("/lock", CHAT)
        self.assertIn("guard loop is not running", self.t.texts()[-1])

    def test_memory_commands(self):
        g = self.make()
        self.out = "6958 1500 31 12\n• Browser — 900 MB\n• Editor — 400 MB"
        g.handle("/mem", CHAT)
        self.assertTrue(self.runs[-1][1].endswith("modules/memwatch/memwatch.sh"))
        self.assertEqual(self.runs[-1][2:], ["--dry"])
        txt = self.t.texts()[-1]
        self.assertIn("31%", txt)
        self.assertIn("high-water mark", txt)
        self.assertIn("Browser", txt)
        g.handle("/top", CHAT)
        self.assertIn("Editor", self.t.texts()[-1])
        self.assertNotIn("6958", self.t.texts()[-1])

    def test_mem_verdict_thresholds(self):
        g = self.make()
        for press, word in [("40", "healthy"), ("20", "tight"), ("5", "struggling")]:
            self.out = "1 2 %s 4\n• a — 1 MB" % press
            g.handle("/mem", CHAT)
            self.assertIn(word, self.t.texts()[-1])

    def test_stealer_scan_command(self):
        g = self.make()
        self.out = "== full scan =="
        g.handle("/stealer", CHAT)
        self.assertTrue(self.runs[-1][1].endswith("modules/stealerwatch/stealerwatch.sh"))
        self.assertEqual(self.runs[-1][2:], ["--dry"])

    def test_output_is_html_escaped_and_truncated(self):
        g = self.make()
        self.out = "<script>&" + "x" * 9000
        g.handle("/status", CHAT)
        txt = self.t.texts()[-1]
        self.assertNotIn("<script>", txt)
        self.assertLess(len(txt), 4096)

    def test_log_is_read_in_python_and_shows_the_last_lines(self):
        (self.home / "guard" / "guard.log").write_text("\n".join("line%d" % i for i in range(50)) + "\n")
        g = self.make()
        g.handle("/log", CHAT)
        self.assertIn("line49", self.t.texts()[-1])
        self.assertNotIn("line10\n", self.t.texts()[-1])
        self.assertEqual(self.runs, [])

    def test_unknown_command_gets_help(self):
        g = self.make()
        g.handle("/nope", CHAT)
        self.assertIn("Unknown command", self.t.texts()[-1])
        self.assertIn("/status", self.t.texts()[-1])
        self.assertEqual(self.runs, [])

    def test_plain_text_and_start_print_help(self):
        g = self.make()
        g.handle("/start", CHAT)
        g.handle("hello?", CHAT)
        self.assertEqual(len(self.t.texts()), 2)
        self.assertTrue(all("/photos" in t for t in self.t.texts()))

    def test_command_with_bot_suffix_and_case(self):
        g = self.make()
        g.handle("/STATUS@MyWachimanBot", CHAT)
        self.assertEqual(self.runs[0][2:], ["status"])

    def test_handler_crash_is_reported_not_fatal(self):
        g = self.make()

        def boom(argv, timeout=60):
            raise RuntimeError("boom")

        g.runner = boom
        g.process_update({"update_id": 1, "message": {"chat": {"id": 42}, "text": "/status"}})
        self.assertIn("internal error", self.t.texts()[-1])


class SpanishAliasTest(Case):
    def test_spanish_aliases_work_whatever_the_language(self):
        g = self.make()
        for alias, tail in [("/estado", ["status"]), ("/prueba", ["start-nolock"])]:
            self.runs.clear()
            g.handle(alias, CHAT)
            self.assertEqual(self.runs[0][2:], tail, alias)
        self.runs.clear()
        g.handle("/bloquea", CHAT)
        self.assertTrue(self.runs[0][1].endswith("modules/guard/lock.sh"))
        g.handle("/ayuda", CHAT)
        self.assertIn("/photos", self.t.texts()[-1])
        g.handle("/ultimas", CHAT)
        self.assertEqual(self.t.texts()[-1], "📷 No photos saved.")

    def test_aliases_are_not_in_the_telegram_menu(self):
        names = [c["command"] for c in self.make().menu()]
        self.assertNotIn("estado", names)
        self.assertNotIn("ayuda", names)

    def test_help_language_follows_the_setting(self):
        es = self.make(WACHIMAN_LANG="es")
        es.handle("/help", CHAT)
        self.assertIn("bloquea la pantalla", self.t.texts()[-1])
        en = self.make()
        en.handle("/help", CHAT)
        self.assertIn("lock the screen", self.t.texts()[-1])


class MenuTest(Case):
    def test_menu_and_help_come_from_the_same_table(self):
        g = self.make()
        menu = g.menu()
        g.handle("/help", CHAT)
        help_text = self.t.texts()[-1]
        self.assertGreaterEqual(len(menu), 10)
        for c in menu:
            self.assertIn("/" + c["command"], help_text)
            self.assertIn(c["description"], help_text)
            self.assertLessEqual(len(c["description"]), 60)
            self.assertRegex(c["command"], r"^[a-z0-9_]{1,32}$")

    def test_set_my_commands_is_sent_on_start(self):
        g = self.make()
        g.run(max_polls=1)
        self.assertEqual(self.t.calls[0][0], "setMyCommands")
        self.assertEqual(json.loads(self.t.calls[0][1]["commands"]), g.menu())


class SecurityTest(Case):
    def test_other_chats_are_ignored_logged_and_never_answered(self):
        self.t = FakeTransport([
            {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/off"}},
            {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/status"}}])
        g = self.make()
        g.run(max_polls=1)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(set(self.t.chats()), {CHAT})
        self.assertIn("ignored foreign chat 999", (self.home / "gateway.log").read_text())

    def test_offset_advances_past_processed_updates(self):
        self.t = FakeTransport([{"update_id": 7, "message": {"chat": {"id": 42}, "text": "/status"}}])
        g = self.make()
        g.run(max_polls=2)
        polls = [d for m, d in self.t.calls if m == "getUpdates"]
        self.assertEqual(polls[0]["offset"], 0)
        self.assertEqual(polls[1]["offset"], 8)

    def test_refuses_to_run_without_token_or_chat(self):
        for missing in ("WACHIMAN_TELEGRAM_BOT_TOKEN", "WACHIMAN_TELEGRAM_CHAT_ID"):
            s = dict(self.settings)
            s[missing] = ""
            with self.assertRaises(gateway.ConfigError):
                gateway.from_settings(s, home=self.home, repo=Path("/repo"))

    def test_token_never_logged(self):
        self.t = FakeTransport([{"update_id": 1, "message": {"chat": {"id": 1}, "text": "/status"}}])
        g = self.make()
        g.run(max_polls=1)
        self.assertNotIn(TOKEN, (self.home / "gateway.log").read_text())


class DeletePhotosTest(Case):
    def test_needs_confirmation_twice_within_two_minutes(self):
        self.photo_files("2026-01-01_10-00-00_1.jpg", "2026-01-01_10-00-00_2.jpg")
        g = self.make()
        g.handle("/deletephotos", CHAT)
        self.assertEqual(len(self.jpgs()), 2)
        self.assertIn("2 photos", self.t.texts()[-1])
        self.now[0] += 60
        g.handle("/deletephotos", CHAT)
        self.assertEqual(self.jpgs(), [])
        self.assertIn("Deleted 2", self.t.texts()[-1])

    def test_confirmation_expires(self):
        self.photo_files("2026-01-01_10-00-00_1.jpg")
        g = self.make()
        g.handle("/deletephotos", CHAT)
        self.now[0] += 121
        g.handle("/deletephotos", CHAT)
        self.assertEqual(len(self.jpgs()), 1)

    def test_spanish_alias_shares_the_same_confirmation(self):
        self.photo_files("2026-01-01_10-00-00_1.jpg")
        g = self.make()
        g.handle("/borrafotos", CHAT)
        g.handle("/deletephotos", CHAT)
        self.assertEqual(self.jpgs(), [])

    def test_a_gateway_restart_cancels_the_pending_confirmation(self):
        self.photo_files("2026-01-01_10-00-00_1.jpg")
        self.make().handle("/deletephotos", CHAT)
        self.make().handle("/deletephotos", CHAT)
        self.assertEqual(len(self.jpgs()), 1)

    def test_only_burst_photos_are_deleted(self):
        self.photo_files("2026-01-01_10-00-00_1.jpg", "holiday.jpg")
        g = self.make()
        g.handle("/deletephotos", CHAT)
        g.handle("/deletephotos", CHAT)
        self.assertEqual([p.name for p in self.jpgs()], ["holiday.jpg"])

    def test_nothing_to_delete(self):
        g = self.make()
        g.handle("/deletephotos", CHAT)
        self.assertIn("Nothing to delete", self.t.texts()[-1])


class PhotosTest(Case):
    def test_list_bursts_and_send_the_latest(self):
        self.photo_files("2026-01-01_10-00-00_1.jpg", "2026-01-01_10-00-00_2.jpg", "2026-02-02_09-00-00_1.jpg")
        g = self.make()
        g.handle("/photos", CHAT)
        self.assertIn("3 photos", self.t.texts()[-1])
        self.assertIn("2 bursts", self.t.texts()[-1])
        g.handle("/last", CHAT)
        self.assertEqual([n for n, c in self.t.photos], ["2026-02-02_09-00-00_1.jpg"])

    def test_no_photos(self):
        g = self.make()
        g.handle("/photos", CHAT)
        g.handle("/last", CHAT)
        self.assertEqual(self.t.texts().count("📷 No photos saved."), 2)


class OnceTest(Case):
    def test_once_runs_a_single_command_and_returns(self):
        g = self.make()
        g.handle("/status", CHAT)
        self.assertEqual(len(self.runs), 1)


class TransportTest(unittest.TestCase):
    def test_real_transport_talks_http_and_does_not_expose_the_token(self):
        tg = FakeTelegram()
        self.addCleanup(tg.close)
        tr = gateway.Transport(TOKEN, "1", base=tg.url)
        self.assertEqual(tr.call("sendMessage", {"chat_id": "1", "text": "hi"})["ok"], True)
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            f.write(b"jpgdata")
            f.flush()
            tr.send_photo(f.name, "cap")
        self.assertEqual([p.split("/")[-1] for p, b in tg.requests], ["sendMessage", "sendPhoto"])
        self.assertIn("jpgdata", tg.requests[1][1])
        self.assertNotIn(TOKEN, repr(tr))


if __name__ == "__main__":
    unittest.main()

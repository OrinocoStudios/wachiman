import unittest

from tests.shell_helpers import ShellCase, MODULES

SW = MODULES / "stealerwatch" / "stealerwatch.sh"


class StealerCase(ShellCase):
    def setUp(self):
        super().setUp()
        self.agents = self.home / "Library" / "LaunchAgents"
        self.agents.mkdir(parents=True)
        self.tmpdir = self.wm.parent / "faketmp"
        self.tmpdir.mkdir()
        self.write_config("WACHIMAN_STEALER_AGENT_DIRS=%s\nWACHIMAN_STEALER_TMP_DIRS=%s\n" % (self.agents, self.tmpdir))
        self.fake("osascript", 'cat "%s/loginitems" 2>/dev/null' % self.seq)
        self.fake("dig", 'true')
        self.fake("lsof", 'true')
        self.fake("xattr", 'true')
        self.fake("codesign", 'exit 1')
        (self.seq / "loginitems").write_text("")

    def plist(self, name, body="x"):
        (self.agents / name).write_text(body)

    def once(self, *args, **env):
        return self.sh(SW, ["--once"] + list(args), **env)

    def state(self):
        return self.read("stealerwatch", "stealerwatch.state")


class ScanTest(StealerCase):
    def test_baseline_learns_what_exists_and_first_pass_is_silent(self):
        tg = self.telegram()
        self.plist("com.good.plist")
        p = self.sh(SW, ["--baseline"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("com.good.plist", self.read("stealerwatch", "baseline"))
        self.once()
        self.assertEqual(tg.texts(), [])

    def test_first_run_without_baseline_creates_one_instead_of_alerting(self):
        tg = self.telegram()
        self.plist("com.good.plist")
        p = self.once()
        self.assertEqual(p.returncode, 0)
        self.assertEqual(tg.texts(), [])
        self.assertTrue((self.wm / "stealerwatch" / "baseline").exists())

    def test_new_launch_agent_is_reported(self):
        tg = self.telegram()
        self.sh(SW, ["--baseline"])
        self.plist("com.evil.plist")
        self.once()
        t = tg.texts()
        self.assertEqual(len(t), 1)
        self.assertIn("com.evil.plist", t[0])
        self.assertIn("persistence", t[0])
        self.assertIn("wachiman stealerwatch baseline", t[0])

    def test_modified_existing_plist_is_reported(self):
        tg = self.telegram()
        self.plist("com.good.plist", "one")
        self.sh(SW, ["--baseline"])
        self.plist("com.good.plist", "two")
        self.once()
        self.assertEqual(len(tg.texts()), 1)

    def test_known_items_do_not_repeat_and_baseline_accepts_a_change(self):
        tg = self.telegram()
        self.sh(SW, ["--baseline"])
        self.plist("com.evil.plist")
        self.sh(SW, ["--baseline"])
        self.once()
        self.assertEqual(tg.texts(), [])

    def test_cooldown_per_finding(self):
        tg = self.telegram()
        self.sh(SW, ["--baseline"])
        self.plist("com.evil.plist")
        self.once()
        self.once()
        self.assertEqual(len(tg.texts()), 1)
        self.once(WACHIMAN_STEALER_COOLDOWN="0")
        self.assertEqual(len(tg.texts()), 2)

    def test_new_login_item(self):
        tg = self.telegram()
        self.sh(SW, ["--baseline"])
        (self.seq / "loginitems").write_text("Dropbox, Sneaky Helper\n")
        self.once()
        self.assertIn("Sneaky Helper", tg.texts()[0])

    def test_stealer_artefact_in_tmp(self):
        tg = self.telegram()
        self.sh(SW, ["--baseline"])
        (self.tmpdir / "osalogging.zip").write_text("loot")
        self.once()
        self.assertIn("osalogging.zip", tg.texts()[0])

    def test_collection_folder_in_home(self):
        tg = self.telegram()
        self.sh(SW, ["--baseline"])
        (self.home / "FileGrabber").mkdir()
        self.once()
        self.assertIn("FileGrabber", tg.texts()[0])

    def test_active_connection_to_known_c2(self):
        tg = self.telegram()
        self.write_config("WACHIMAN_STEALER_AGENT_DIRS=%s\nWACHIMAN_STEALER_TMP_DIRS=%s\n"
                          "WACHIMAN_STEALER_C2_DOMAINS=bad.example\n" % (self.agents, self.tmpdir))
        self.fake("dig", 'echo 203.0.113.7')
        self.sh(SW, ["--baseline"])  # nothing connected yet
        self.fake("lsof", 'echo "curl 1 u 3u IPv4 TCP 10.0.0.2:5000->203.0.113.7:443 (ESTABLISHED)"')
        self.once()
        self.assertIn("bad.example", tg.texts()[0])
        self.assertIn("203.0.113.7", tg.texts()[0])

    def test_unsigned_quarantined_executable_in_app_support(self):
        tg = self.telegram()
        d = self.home / "Library" / "Application Support" / "Thing"
        d.mkdir(parents=True)
        self.sh(SW, ["--baseline"])
        f = d / "dropper"
        f.write_text("#!/bin/sh\n")
        f.chmod(0o755)
        self.fake("xattr", 'echo com.apple.quarantine')
        self.once()
        self.assertIn("dropper", tg.texts()[0])
        # signed binaries are the normal case and stay quiet
        tg.requests.clear()
        self.sh(SW, ["--baseline"])
        self.fake("codesign", "exit 0")
        (d / "other").write_text("x")
        (d / "other").chmod(0o755)
        self.once(WACHIMAN_STEALER_COOLDOWN="0")
        self.assertEqual(tg.texts(), [])

    def test_spanish_alert(self):
        tg = self.telegram()
        self.write_config("WACHIMAN_STEALER_AGENT_DIRS=%s\nWACHIMAN_STEALER_TMP_DIRS=%s\nWACHIMAN_LANG=es\n"
                          % (self.agents, self.tmpdir))
        self.sh(SW, ["--baseline"])
        self.plist("com.evil.plist")
        self.once()
        self.assertIn("persistencia", tg.texts()[0])

    def test_html_in_findings_is_escaped(self):
        tg = self.telegram()
        self.sh(SW, ["--baseline"])
        self.plist("a&b<i>.plist")
        self.once()
        self.assertIn("a&amp;b&lt;i&gt;.plist", tg.texts()[0])

    def test_dry_prints_findings_and_sends_nothing(self):
        tg = self.telegram()
        self.plist("com.good.plist")
        p = self.sh(SW, ["--dry"])
        self.assertEqual(p.returncode, 0)
        self.assertIn("com.good.plist", p.stdout)
        self.assertEqual(tg.requests, [])
        self.assertFalse((self.wm / "stealerwatch" / "baseline").exists())

    def test_test_mode_sends_one_message_and_never_prints_the_token(self):
        tg = self.telegram()
        p = self.sh(SW, ["--test"])
        self.assertEqual(p.returncode, 0)
        self.assertEqual(len(tg.texts()), 1)
        self.assertNoToken(p.stdout, p.stderr)

    def test_test_mode_without_credentials_fails_clearly(self):
        p = self.sh(SW, ["--test"])
        self.assertNotEqual(p.returncode, 0)

    def test_scan_only_touches_stealerwatch_folder(self):
        self.sh(SW, ["--baseline"])
        self.assertEqual(sorted(x.name for x in self.wm.iterdir() if x.name != "config.env"), ["stealerwatch"])


if __name__ == "__main__":
    unittest.main()

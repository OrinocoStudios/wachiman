import unittest

from tests.shell_helpers import ShellCase, MODULES, POP

MEM = MODULES / "memwatch" / "memwatch.sh"

VM_STAT = r'''
outs=$(pop "%(seq)s/outs")
echo "Mach Virtual Memory Statistics: (page size of 16384 bytes)"
echo "Pages free:                               1000."
echo "Pages speculative:                          24."
echo "Swapouts:                                 ${outs}."
'''


class MemCase(ShellCase):
    def setUp(self):
        super().setUp()
        self.write_config("WACHIMAN_MEM_POLL=0\n")
        self.fake("sysctl", 'echo "total = 8192.00M  used = 6958.00M  free = 1234.00M  (encrypted)"')
        self.popper("memory_pressure", "press", 'echo "System-wide memory free percentage: $(pop {SEQ})%"')
        self.fake("vm_stat", POP + VM_STAT % {"seq": self.seq})
        self.fake("ps", 'printf "%s\\n" "300000 /Applications/Big Browser.app/Contents/MacOS/Big" '
                        '"200000 /Applications/Big Browser.app/Contents/Helper" "100000 /usr/bin/evil<b>" "50000 /bin/zsh"')

    def run_loop(self, press, outs, **env):
        """press/outs include the priming sample, so len == polls + 1."""
        self.sequence("press", press)
        self.sequence("outs", outs)
        return self.sh(MEM, ["--polls", str(len(press) - 1)], **env)


class MemSampleTest(MemCase):
    def test_dry_prints_four_fields_then_top5_grouped_by_app(self):
        self.sequence("press", [45])
        self.sequence("outs", [777])
        p = self.sh(MEM, ["--dry"])
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.splitlines()
        self.assertEqual(lines[0].split(), ["6958", "16", "45", "777"])
        self.assertIn("Big Browser — 488 MB", p.stdout)  # 300000+200000 KB grouped under the app
        self.assertEqual(sum(1 for l in lines[1:] if l.startswith("•")), 3)

    def test_dry_sends_nothing_and_writes_nothing(self):
        tg = self.telegram()
        self.sequence("press", [5])
        self.sequence("outs", [0])
        self.sh(MEM, ["--dry"])
        self.assertEqual(tg.requests, [])

    def test_test_mode_sends_one_message_with_the_pressure(self):
        tg = self.telegram()
        self.sequence("press", [45])
        self.sequence("outs", [0])
        p = self.sh(MEM, ["--test"])
        self.assertEqual(p.returncode, 0)
        self.assertEqual(len(tg.texts()), 1)
        self.assertIn("45%", tg.texts()[0])
        self.assertNoToken(p.stdout, p.stderr)


class MemLevelTest(MemCase):
    def alerts(self):
        return self.tg.texts() if self.tg else []

    def test_swap_used_never_alerts(self):
        self.telegram()
        self.run_loop([50, 50, 50], [0, 0, 0])   # swap used is 6958 MB the whole time
        self.assertEqual(self.alerts(), [])

    def test_pressure_warn_and_crit(self):
        self.telegram()
        self.run_loop([50, 20, 10], [0, 0, 0])
        a = self.alerts()
        self.assertEqual(len(a), 2, a)
        self.assertIn("WARN", a[0])
        self.assertIn("CRIT", a[1])
        self.assertIn("20%", a[0])

    def test_alert_names_the_top_processes_and_escapes_html(self):
        self.telegram()
        self.run_loop([50, 15], [0, 0])
        a = self.alerts()[0]
        self.assertIn("Big Browser", a)
        self.assertIn("evil&lt;b&gt;", a)
        self.assertIn("high-water mark", a)

    def test_swapout_rate_is_gated_on_pressure(self):
        self.telegram()
        # 3200 pages * 16 KB = 50 MB in ~1 s: a burst, but 40% free is macOS working, not struggling
        self.run_loop([40, 40], [0, 3200])
        self.assertEqual(self.alerts(), [])
        self.run_loop([30, 30], [0, 3200], WACHIMAN_MEM_COOLDOWN="0")
        a = self.alerts()
        self.assertEqual(len(a), 1)
        self.assertIn("WARN", a[0])

    def test_counter_reset_does_not_produce_a_negative_rate(self):
        self.telegram()
        self.run_loop([30, 30], [90000, 5])
        self.assertEqual(self.alerts(), [])

    def test_same_level_respects_cooldown_but_escalation_does_not(self):
        self.telegram()
        self.run_loop([50, 18, 17, 8], [0, 0, 0, 0])
        a = self.alerts()
        self.assertEqual([("CRIT" in x) for x in a], [False, True])
        self.assertIn("cooldown", self.read("memwatch", "memwatch.log"))

    def test_recovery_needs_hysteresis(self):
        self.telegram()
        self.run_loop([50, 15, 22, 24], [0, 0, 0, 0])      # 22 and 24 are still <= 20 + 5
        self.assertEqual(len(self.alerts()), 1)
        self.run_loop([50, 15, 30], [0, 0, 0], WACHIMAN_MEM_COOLDOWN="0")
        rec = [t for t in self.alerts() if "recovered" in t]
        self.assertEqual(len(rec), 1)
        self.assertEqual(len(self.read("memwatch", "memwatch.state").split()), 1)  # level cleared, only the timestamp

    def test_thresholds_come_from_settings(self):
        self.telegram()
        self.write_config("WACHIMAN_MEM_POLL=0\nWACHIMAN_MEM_PRESSURE_WARN=40\nWACHIMAN_MEM_PRESSURE_CRIT=30\n")
        self.run_loop([60, 38], [0, 0])
        self.assertIn("WARN", self.alerts()[0])

    def test_spanish(self):
        self.telegram()
        self.write_config("WACHIMAN_MEM_POLL=0\nWACHIMAN_LANG=es\n")
        self.run_loop([50, 15], [0, 0])
        self.assertIn("Memoria WARN", self.alerts()[0])
        self.assertIn("marca de agua", self.alerts()[0])

    def test_no_credentials_still_runs_and_logs(self):
        p = self.run_loop([50, 15], [0, 0])
        self.assertEqual(p.returncode, 0)
        self.assertIn("press=15%", self.read("memwatch", "memwatch.log"))

    def test_log_and_stdout_never_contain_the_token(self):
        self.telegram()
        p = self.run_loop([50, 15], [0, 0])
        self.assertNoToken(p.stdout, p.stderr, self.read("memwatch", "memwatch.log"))


if __name__ == "__main__":
    unittest.main()

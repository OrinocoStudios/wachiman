import os
import signal
import unittest

from tests.shell_helpers import ShellCase, MODULES, TOKEN

GUARD = MODULES / "guard" / "guard.sh"
CTL = MODULES / "guard" / "guardctl.sh"
LOCK = MODULES / "guard" / "lock.sh"

FFMPEG = r'''
echo "$@" >> "%(seq)s/ffmpeg.calls"
case "$*" in
  *list_devices*)
    echo "[AVFoundation indev @ 0x1] AVFoundation video devices:" >&2
    echo "[AVFoundation indev @ 0x1] [0] Other Camera" >&2
    echo "[AVFoundation indev @ 0x1] [3] Test Cam" >&2
    echo "[AVFoundation indev @ 0x1] AVFoundation audio devices:" >&2
    exit 0;;
esac
for a; do last="$a"; done
echo jpegdata > "$last"
'''


class GuardCase(ShellCase):
    def setUp(self):
        super().setUp()
        self.gd = self.wm / "guard"
        self.write_config("WACHIMAN_GUARD_POLL=0\nWACHIMAN_GUARD_PHOTO_GAP=0\n")
        self.fake("ffmpeg", FFMPEG % {"seq": self.seq})
        self.popper("ioreg", "idle", 'case "$*" in *IOHIDSystem*) v=$(pop {SEQ}); echo "  | \\"HIDIdleTime\\" = ${v}000000000";; esac')
        self.popper("log", "shield", 'pop {SEQ}')

    def loop(self, idle, shield, polls=None, **env):
        self.sequence("idle", idle)
        self.sequence("shield", shield)
        return self.sh(GUARD, ["--polls", str(polls or len(idle))], **env)

    def photos(self):
        return sorted(p.name for p in self.gd.glob("*.jpg"))


class GuardLoopTest(GuardCase):
    def test_activity_while_locked_alerts_and_shoots_three_photos(self):
        tg = self.telegram()
        p = self.loop([5], ["shieldWindowShowing:1", "shieldWindowShowing:1"])
        self.assertEqual(p.returncode, 0, p.stderr)
        texts = tg.texts()
        self.assertTrue(any("Activity on the Mac" in t and "locked" in t for t in texts), texts)
        self.assertEqual(len(tg.photos()), 3)
        self.assertEqual(len(self.photos()), 3)
        self.assertRegex(self.photos()[0], r"^\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d_1\.jpg$")

    def test_webcam_black_frames_are_skipped_and_camera_index_is_used(self):
        self.telegram()
        self.write_config("WACHIMAN_GUARD_POLL=0\nWACHIMAN_GUARD_PHOTO_GAP=0\nWACHIMAN_CAM=2\n")
        self.loop([5], ["shieldWindowShowing:1"] * 2)
        shots = [l for l in (self.seq / "ffmpeg.calls").read_text().splitlines() if "-frames:v" in l]
        self.assertEqual(len(shots), 3)
        self.assertIn("select=gte(n\\,12)", shots[0])
        self.assertIn("-i 2", shots[0])

    def test_camera_is_found_by_name_not_by_index(self):
        self.telegram()
        self.write_config("WACHIMAN_GUARD_POLL=0\nWACHIMAN_GUARD_PHOTO_GAP=0\nWACHIMAN_CAM=0\n"
                          "WACHIMAN_CAM_NAME=Test Cam\n")
        self.loop([5], ["shieldWindowShowing:1"] * 2)
        shots = [l for l in (self.seq / "ffmpeg.calls").read_text().splitlines() if "-frames:v" in l]
        self.assertIn("-i 3", shots[0])

    def test_unlocked_screen_never_triggers(self):
        tg = self.telegram()
        self.loop([5], ["shieldWindowShowing:0", "shieldWindowShowing:0"])
        self.assertFalse(any("Activity" in t for t in tg.texts()))
        self.assertEqual(self.photos(), [])

    def test_activity_must_be_an_edge_after_a_quiet_minute(self):
        tg = self.telegram()
        # initial prev_idle is 999; 30 s of idle then 20 s then 5 s: 20 -> 5 is not an edge from >=60
        self.loop([30, 20, 5], ["shieldWindowShowing:1"] * 4)
        self.assertEqual(self.photos(), [])
        self.assertFalse(any("Activity" in t for t in tg.texts()))

    def test_one_burst_per_cooldown(self):
        tg = self.telegram()
        self.loop([5, 100, 5], ["shieldWindowShowing:1"] * 4)
        self.assertEqual(len(tg.photos()), 3)
        self.assertIn("ignored", self.read("guard", "guard.log"))

    def test_unlock_inside_grace_keeps_photos_by_default(self):
        tg = self.telegram()
        self.loop([5, 5], ["shieldWindowShowing:1", "shieldWindowShowing:1", "shieldWindowShowing:0"])
        self.assertEqual(len(self.photos()), 3)
        self.assertTrue(any("does NOT verify" in t for t in tg.texts()), tg.texts())
        self.assertTrue(any("paranoid" in t for t in tg.texts()), tg.texts())

    def test_unlock_inside_grace_deletes_photos_only_when_password_is_confirmed(self):
        tg = self.telegram()
        self.write_config("WACHIMAN_GUARD_POLL=0\nWACHIMAN_GUARD_PHOTO_GAP=0\nWACHIMAN_GUARD_PARANOID=0\n")
        self.loop([5, 5], ["shieldWindowShowing:1", "shieldWindowShowing:1", "shieldWindowShowing:0"])
        self.assertEqual(self.photos(), [])
        self.assertTrue(any("photos deleted" in t for t in tg.texts()), tg.texts())

    def test_lock_and_unlock_transitions_are_reported(self):
        tg = self.telegram()
        self.loop([100, 100], ["shieldWindowShowing:0", "shieldWindowShowing:1", "shieldWindowShowing:0"])
        texts = " | ".join(tg.texts())
        self.assertIn("LOCKED", texts)
        self.assertIn("UNLOCKED", texts)

    def test_lock_state_comes_from_the_shield_and_is_persisted(self):
        self.loop([100], ["shieldWindowShowing:1", "shieldWindowShowing:1"], polls=1)
        self.assertEqual(self.read("guard", "lock.state").strip(), "1")

    def test_spanish_alerts(self):
        tg = self.telegram()
        self.write_config("WACHIMAN_GUARD_POLL=0\nWACHIMAN_GUARD_PHOTO_GAP=0\nWACHIMAN_LANG=es\n")
        self.loop([5], ["shieldWindowShowing:1"] * 2)
        self.assertTrue(any("Actividad en el Mac" in t for t in tg.texts()), tg.texts())

    def test_ffmpeg_path_can_be_overridden_and_missing_ffmpeg_does_not_kill_the_loop(self):
        tg = self.telegram()
        (self.bin / "ffmpeg").unlink()
        p = self.loop([5], ["shieldWindowShowing:1"] * 2)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(any("Activity" in t for t in tg.texts()))
        self.assertEqual(self.photos(), [])
        alt = self.fake("camtool", FFMPEG % {"seq": self.seq})
        self.loop([5], ["shieldWindowShowing:1"] * 2, WACHIMAN_FFMPEG=str(alt), WACHIMAN_GUARD_BURST_COOLDOWN="0")
        self.assertEqual(len(self.photos()), 3)

    def test_works_without_telegram_and_never_leaks_the_token(self):
        p = self.loop([5], ["shieldWindowShowing:1"] * 2)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(len(self.photos()), 3)
        self.telegram()
        p = self.loop([5], ["shieldWindowShowing:1"] * 2, WACHIMAN_GUARD_BURST_COOLDOWN="0")
        self.assertNoToken(p.stdout, p.stderr, self.read("guard", "guard.log"))

    def test_debug_line_per_poll(self):
        self.write_config("WACHIMAN_GUARD_POLL=0\nWACHIMAN_GUARD_DEBUG=1\n")
        self.loop([100], ["shieldWindowShowing:1"] * 2)
        self.assertRegex(self.read("guard", "guard.debug"), r"locked=1 idle=100 prev_idle=")


class LockTest(ShellCase):
    def setUp(self):
        super().setUp()
        self.fake("python3", 'cat > "%s/py.stdin"; echo SAC_OK' % self.seq)
        self.fake("pmset", 'echo "$@" >> "%s/pmset"' % self.seq)

    def test_locks_with_sac_lock_screen_and_reports_locked(self):
        self.fake("ioreg", 'echo "<key>CGSSessionScreenIsLocked</key>"; echo "<true/>"')
        p = self.sh(LOCK, WACHIMAN_LOCK_WAIT="0")
        self.assertEqual(p.returncode, 0)
        self.assertIn("LOCKED", p.stdout)
        self.assertNotIn("NOT_LOCKED", p.stdout)
        self.assertIn("SACLockScreenImmediate", (self.seq / "py.stdin").read_text())
        self.assertFalse((self.seq / "pmset").exists())

    def test_falls_back_to_blanking_the_display_and_says_it_did_not_lock(self):
        self.fake("ioreg", "true")
        p = self.sh(LOCK, WACHIMAN_LOCK_WAIT="0")
        self.assertIn("NOT_LOCKED", p.stdout)
        self.assertIn("displaysleepnow", (self.seq / "pmset").read_text())


class GuardCtlTest(GuardCase):
    def setUp(self):
        super().setUp()
        self.write_config("WACHIMAN_GUARD_POLL=1\nWACHIMAN_GUARD_STARTUP_WAIT=1\nWACHIMAN_GUARD_PHOTO_GAP=0\n")
        self.fake("python3", 'cat > /dev/null; echo "$0 locked" >> "%s/py.calls"; echo SAC_OK' % self.seq)
        self.fake("ioreg", "true")
        self.sequence("idle", [100])
        self.sequence("shield", ["shieldWindowShowing:0"])
        self.addCleanup(self._kill)

    def _kill(self):
        pid = self.read("guard", "guard.pid").strip()
        if pid.isdigit():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except OSError:
                pass

    def test_start_nolock_status_stop(self):
        p = self.sh(CTL, ["start-nolock"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"STARTED pid=\d+")
        self.assertFalse((self.seq / "py.calls").exists())
        s = self.sh(CTL, ["status"])
        self.assertIn("ACTIVE pid=", s.stdout)
        self.assertIn("locked=", s.stdout)
        again = self.sh(CTL, ["start-nolock"])
        self.assertIn("ALREADY_RUNNING", again.stdout)
        stop = self.sh(CTL, ["stop"])
        self.assertRegex(stop.stdout, r"STOPPED pid=\d+")
        self.assertIn("INACTIVE", self.sh(CTL, ["status"]).stdout)
        self.assertIn("NOT_RUNNING", self.sh(CTL, ["stop"]).stdout)

    def test_start_locks_the_screen_after_the_loop_is_up(self):
        p = self.sh(CTL, ["start"])
        self.assertRegex(p.stdout, r"STARTED pid=\d+")
        self.assertTrue((self.seq / "py.calls").exists())
        self.sh(CTL, ["stop"])

    def test_usage_on_bad_argument(self):
        p = self.sh(CTL, ["wat"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("usage", p.stdout.lower() + p.stderr.lower())


if __name__ == "__main__":
    unittest.main()

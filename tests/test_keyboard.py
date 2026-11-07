import tempfile
import unittest
from pathlib import Path

from wachiman.radar import keyboard, store

KEYS = (1 << 10) | (1 << 11) | (1 << 12)  # keyDown, keyUp, flagsChanged
MOUSE = (1 << 5) | (1 << 6) | (1 << 7)

CODESIGN_APPLE = """Executable=/System/Library/CoreServices/Siri.app/Contents/MacOS/Siri
Identifier=com.apple.Siri
Authority=Software Signing
Authority=Apple Code Signing Certification Authority
Authority=Apple Root CA
"""
CODESIGN_DEV = """Executable=/Applications/Raycast.app/Contents/MacOS/Raycast
Identifier=com.raycast.macos
Authority=Developer ID Application: Raycast Technologies Inc (SY64MV22J9)
Authority=Developer ID Certification Authority
Authority=Apple Root CA
TeamIdentifier=SY64MV22J9
"""
CODESIGN_ADHOC = """Executable=/tmp/x
Identifier=x
Signature=adhoc
TeamIdentifier=not set
"""
CODESIGN_NONE = "/tmp/evil: code object is not signed at all\n"


class MaskTest(unittest.TestCase):
    def test_keyboard_mask(self):
        self.assertTrue(keyboard.reads_keys(0x1C00))
        self.assertTrue(keyboard.reads_keys(1 << 10))
        self.assertFalse(keyboard.reads_keys(MOUSE))
        self.assertFalse(keyboard.reads_keys(0x204000))


class SignerTest(unittest.TestCase):
    def test_signers(self):
        self.assertEqual(keyboard.parse_signer(CODESIGN_APPLE), "Apple")
        self.assertEqual(keyboard.parse_signer(CODESIGN_DEV), "Raycast Technologies Inc (SY64MV22J9)")
        self.assertEqual(keyboard.parse_signer(CODESIGN_ADHOC), "unsigned")
        self.assertEqual(keyboard.parse_signer(CODESIGN_NONE), "unsigned")
        self.assertEqual(keyboard.parse_signer(""), "unknown")

    def test_macos_system_binaries_are_apple(self):
        out = "Identifier=com.apple.Siri\nAuthority=macOS Software Signing\nAuthority=Apple Root CA\n"
        self.assertEqual(keyboard.parse_signer(out), "Apple")


class TapsTest(unittest.TestCase):
    def test_keeps_enabled_keyboard_taps_one_per_program(self):
        raw = [
            keyboard.RawTap(pid=10, mask=KEYS, listen_only=True, enabled=True),
            keyboard.RawTap(pid=10, mask=KEYS, listen_only=False, enabled=True),   # same program twice
            keyboard.RawTap(pid=11, mask=MOUSE, listen_only=True, enabled=True),   # mouse only
            keyboard.RawTap(pid=12, mask=KEYS, listen_only=True, enabled=False),   # disabled
            keyboard.RawTap(pid=13, mask=KEYS, listen_only=False, enabled=True),
        ]
        paths = {10: "/Applications/Raycast.app/Contents/MacOS/Raycast", 13: "/tmp/evil"}
        taps = keyboard.keyboard_taps(raw, paths, signer=lambda p: "unsigned" if p == "/tmp/evil" else "Raycast")
        self.assertEqual(sorted((t.app, t.path, t.signer, t.can_modify) for t in taps), [
            ("Raycast", "/Applications/Raycast.app/Contents/MacOS/Raycast", "Raycast", True),
            ("evil", "/tmp/evil", "unsigned", True),
        ])

    def test_unknown_pid_is_kept_with_placeholder(self):
        taps = keyboard.keyboard_taps([keyboard.RawTap(99, KEYS, True, True)], {}, signer=lambda p: "unknown")
        self.assertEqual((taps[0].app, taps[0].path), ("pid 99", "pid:99"))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = store.Store(Path(self.tmp.name) / "radar.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def tap(self, app, path, signer="Apple"):
        return keyboard.Tap(app=app, path=path, signer=signer, can_modify=False)

    def test_learns_then_alerts_once_per_new_program(self):
        siri = self.tap("Siri", "/System/Siri")
        self.assertEqual(self.db.record_keyboard(1000, [siri], learning=True), [])
        ev = self.db.record_keyboard(1030, [siri, self.tap("evil", "/tmp/evil", "unsigned")], learning=False)
        self.assertEqual([(e.kind, e.app, e.signer) for e in ev], [("new_keyboard_tap", "evil", "unsigned")])
        self.assertEqual(self.db.record_keyboard(1060, [self.tap("evil", "/tmp/evil", "unsigned")], learning=False), [])
        rows = {r["app"]: (r["first_seen"], r["last_seen"], r["signer"]) for r in self.db.keyboard_taps()}
        self.assertEqual(rows, {"Siri": (1000, 1030, "Apple"), "evil": (1030, 1060, "unsigned")})
        self.assertEqual([r["kind"] for r in self.db.events()], ["new_keyboard_tap"])

    def test_pid_placeholders_never_alert(self):
        ev = self.db.record_keyboard(1, [self.tap("pid 9", "pid:9", "unknown")], learning=False)
        self.assertEqual(ev, [])


if __name__ == "__main__":
    unittest.main()

import plistlib
import unittest
from pathlib import Path

from wachiman import service


class PlistTest(unittest.TestCase):
    def setUp(self):
        self.env = {"HOME": "/Users/ana"}
        self.raw = service.plist(Path("/opt/wachiman"), "/usr/bin/python3", self.env)
        self.p = plistlib.loads(self.raw)

    def test_runs_radar_watch_from_the_checkout(self):
        self.assertEqual(self.p["Label"], "io.orinoco.wachiman.radar")
        self.assertEqual(self.p["ProgramArguments"], ["/usr/bin/python3", "-m", "wachiman", "radar", "watch"])
        self.assertEqual(self.p["WorkingDirectory"], "/opt/wachiman")
        self.assertEqual(self.p["EnvironmentVariables"]["PYTHONPATH"], "/opt/wachiman")

    def test_starts_at_login_and_restarts_if_it_dies(self):
        self.assertTrue(self.p["RunAtLoad"])
        self.assertTrue(self.p["KeepAlive"])
        self.assertGreaterEqual(self.p["ThrottleInterval"], 30)
        self.assertEqual(self.p["ProcessType"], "Background")

    def test_logs_go_to_the_data_folder_of_this_user(self):
        self.assertEqual(self.p["StandardOutPath"], "/Users/ana/.wachiman/radar.log")
        self.assertEqual(self.p["StandardErrorPath"], "/Users/ana/.wachiman/radar.log")

    def test_respects_wachiman_home(self):
        p = plistlib.loads(service.plist(Path("/x"), "python3", {"HOME": "/Users/ana", "WACHIMAN_HOME": "/srv/wm"}))
        self.assertEqual(p["StandardOutPath"], "/srv/wm/radar.log")
        self.assertEqual(p["EnvironmentVariables"]["WACHIMAN_HOME"], "/srv/wm")

    def test_secrets_never_end_up_in_the_plist(self):
        env = {"HOME": "/Users/ana", "WACHIMAN_TELEGRAM_BOT_TOKEN": "123:secret"}
        self.assertNotIn(b"secret", service.plist(Path("/x"), "python3", env))

    def test_agent_path(self):
        self.assertEqual(service.agent_path(self.env),
                         Path("/Users/ana/Library/LaunchAgents/io.orinoco.wachiman.radar.plist"))


if __name__ == "__main__":
    unittest.main()

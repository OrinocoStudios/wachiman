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
        self.assertEqual(self.p["ProgramArguments"], ["/usr/bin/python3", "-u", "-m", "wachiman", "radar", "watch"])
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


class ModuleAgentsTest(unittest.TestCase):
    def setUp(self):
        self.env = {"HOME": "/Users/ana", "WACHIMAN_TELEGRAM_BOT_TOKEN": "123:secret"}

    def load(self, name):
        return plistlib.loads(service.plist(Path("/opt/wachiman"), "/usr/bin/python3", self.env, name=name))

    def test_known_agents(self):
        self.assertEqual(sorted(service.AGENTS), ["gateway", "memwatch", "radar", "stealerwatch"])

    def test_labels_share_the_prefix(self):
        for name in service.AGENTS:
            self.assertEqual(self.load(name)["Label"], "io.orinoco.wachiman." + name)

    def test_radar_is_unchanged_and_default(self):
        self.assertEqual(service.plist(Path("/opt/wachiman"), "/usr/bin/python3", self.env),
                         service.plist(Path("/opt/wachiman"), "/usr/bin/python3", self.env, name="radar"))

    def test_gateway_runs_the_python_command(self):
        p = self.load("gateway")
        self.assertEqual(p["ProgramArguments"], ["/usr/bin/python3", "-u", "-m", "wachiman", "gateway", "run"])
        self.assertEqual(p["StandardOutPath"], "/Users/ana/.wachiman/gateway.out.log")

    def test_shell_watchers_run_their_loop_with_bash(self):
        for name in ("memwatch", "stealerwatch"):
            p = self.load(name)
            self.assertEqual(p["ProgramArguments"], ["/bin/bash", "/opt/wachiman/modules/%s/%s.sh" % (name, name)])
            self.assertTrue(p["KeepAlive"])
            self.assertEqual(p["StandardOutPath"], "/Users/ana/.wachiman/%s.out.log" % name)

    def test_no_secrets_in_any_agent(self):
        for name in service.AGENTS:
            self.assertNotIn(b"secret", service.plist(Path("/x"), "python3", self.env, name=name))

    def test_agent_path_per_name(self):
        self.assertEqual(service.agent_path({"HOME": "/h"}, "memwatch"),
                         Path("/h/Library/LaunchAgents/io.orinoco.wachiman.memwatch.plist"))

    def test_guard_is_not_a_service_because_it_is_started_on_demand(self):
        self.assertNotIn("guard", service.AGENTS)


if __name__ == "__main__":
    unittest.main()

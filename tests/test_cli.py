import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from wachiman import cli
from wachiman.radar.geo import Owner
from wachiman.radar.net import Socket


def sock(ip, port=443):
    return Socket(pid=1, command="x", fd="1", proto="TCP", local_ip="10.0.0.2", local_port=5000,
                  remote_ip=ip, remote_port=port, state="ESTABLISHED")


class FakeGeo:
    available = True

    def lookup(self, ip):
        return {"34.237.73.95": Owner(14618, "US", "Amazon.com, Inc."),
                "95.163.10.1": Owner(47764, "RU", "LLC VK")}.get(ip)

    def close(self):
        pass


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"HOME": self.tmp.name}
        self.sent = []
        self.traffic = lambda: {}
        self.sensors = lambda since: []
        self.keyboard = lambda: []
        self.devices = lambda: []
        self.samples = [
            [("Slack", sock("34.237.73.95"))],
            [("Slack", sock("34.237.73.95")), ("evil", sock("95.163.10.1"))],
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(list(argv), env=self.env,
                            sampler=lambda: self.samples.pop(0) if self.samples else [],
                            traffic_sampler=lambda: self.traffic(),
                            sensor_reader=lambda since: self.sensors(since),
                            keyboard_reader=lambda: self.keyboard(),
                            device_reader=lambda: self.devices(),
                            geo_factory=lambda path: FakeGeo(),
                            sender=lambda settings, text: self.sent.append(text) or True,
                            clock=lambda: 1_800_000_000)
        return code, buf.getvalue()

    def test_scan_twice_learns_then_alerts(self):
        code, out = self.run_cli("radar", "scan")
        self.assertEqual(code, 0)
        self.assertIn("baseline", out.lower())
        self.assertEqual(self.sent, [])
        code, out = self.run_cli("radar", "scan")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("evil", self.sent[0])
        self.assertTrue((Path(self.tmp.name) / ".wachiman" / "radar.db").exists())

    def test_net_lists_history_with_country_and_owner(self):
        self.run_cli("radar", "scan")
        self.run_cli("radar", "scan")
        code, out = self.run_cli("radar", "net")
        self.assertEqual(code, 0)
        self.assertIn("Slack", out)
        self.assertIn("Amazon.com, Inc.", out)
        self.assertIn("RU", out)

    def test_destination_shows_protocol_and_brackets_ipv6(self):
        self.samples = [[("Chrome", sock("2a00:1450:4003:80e::200e"))]]
        self.run_cli("radar", "scan")
        code, out = self.run_cli("radar", "net")
        self.assertIn("tcp [2a00:1450:4003:80e::200e]:443", out)
        self.assertNotIn("::200e:443", out.replace("]:443", ""))

    def test_net_filters_by_app(self):
        self.run_cli("radar", "scan")
        self.run_cli("radar", "scan")
        code, out = self.run_cli("radar", "net", "--app", "evil")
        self.assertIn("evil", out)
        self.assertNotIn("Slack", out)

    def test_events_lists_alerts(self):
        self.run_cli("radar", "scan")
        self.run_cli("radar", "scan")
        code, out = self.run_cli("radar", "events")
        self.assertIn("new_app", out)
        self.assertIn("evil", out)

    def test_traffic_counts_upload_between_scans(self):
        counters = [{1: ("Slack", 100, 1000)}, {1: ("Slack", 300, 6000)}]
        self.traffic = lambda: counters.pop(0) if counters else {}
        self.samples = [[], []]
        self.run_cli("radar", "scan")
        self.run_cli("radar", "scan")
        code, out = self.run_cli("radar", "traffic")
        self.assertEqual(code, 0)
        self.assertIn("Slack", out)
        self.assertIn("4.9 KB", out)  # 5000 bytes up
        self.assertIn("200 B", out)

    def test_sensors_history(self):
        mic = ("mic", "Zoom", "us.zoom.xos")
        reads = [[(1_799_999_000, {mic})], [(1_799_999_600, set())]]
        self.sensors = lambda since: reads.pop(0) if reads else []
        self.samples = [[], []]
        self.run_cli("radar", "scan")
        self.run_cli("radar", "scan")
        code, out = self.run_cli("radar", "sensors")
        self.assertEqual(code, 0)
        self.assertIn("Zoom", out)
        self.assertIn("mic", out)
        self.assertIn("10m", out)  # 600 s

    def test_keyboard_lists_programs_reading_keys(self):
        from wachiman.radar.keyboard import Tap
        self.keyboard = lambda: [Tap("Raycast", "/Applications/Raycast.app/Contents/MacOS/Raycast", "Raycast Inc", False)]
        self.samples = [[]]
        self.run_cli("radar", "scan")
        code, out = self.run_cli("radar", "keyboard")
        self.assertEqual(code, 0)
        self.assertIn("Raycast", out)
        self.assertIn("Raycast Inc", out)

    def test_a_module_added_later_learns_its_own_baseline(self):
        # history created by an older version that had no keyboard module
        self.samples = [[], [], []]
        self.run_cli("radar", "scan")
        self.run_cli("radar", "scan")
        import sqlite3
        con = sqlite3.connect(str(Path(self.tmp.name) / ".wachiman" / "radar.db"))
        con.execute("DELETE FROM meta WHERE key LIKE 'baseline_%'")  # as written by a version without modules
        con.commit()
        con.close()
        from wachiman.radar.keyboard import Tap
        self.keyboard = lambda: [Tap("Siri", "/System/Siri", "Apple", True)]
        self.run_cli("radar", "scan")  # first scan WITH the keyboard module: learn, do not alert
        self.assertEqual([m for m in self.sent if "Siri" in m], [])
        self.keyboard = lambda: [Tap("Siri", "/System/Siri", "Apple", True), Tap("evil", "/tmp/evil", "unsigned", True)]
        self.samples = [[]]
        self.run_cli("radar", "scan")
        self.assertEqual(len([m for m in self.sent if "evil" in m]), 1)

    def test_devices_lists_what_was_seen(self):
        from wachiman.radar.devices import Device
        self.devices = lambda: [Device("usb", "usb:1", "Razer Kiyo X", "Razer", True),
                                Device("bt", "bt:2", "AirPods", "Headphones", False)]
        self.samples = [[]]
        self.run_cli("radar", "scan")
        code, out = self.run_cli("radar", "devices")
        self.assertEqual(code, 0)
        self.assertIn("Razer Kiyo X", out)
        self.assertIn("AirPods", out)
        code, out = self.run_cli("radar", "devices", "--kind", "bt")
        self.assertNotIn("Razer", out)

    def test_usage_on_unknown_command(self):
        code, out = self.run_cli("nope")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

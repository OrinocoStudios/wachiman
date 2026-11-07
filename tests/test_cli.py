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

    def test_usage_on_unknown_command(self):
        code, out = self.run_cli("nope")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

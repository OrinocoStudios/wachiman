import tempfile
import unittest
from pathlib import Path

from wachiman.radar import store
from wachiman.radar.geo import Owner
from wachiman.radar.net import Socket


def sock(pid, ip, port=443, proto="TCP"):
    return Socket(pid=pid, command="x", fd="1", proto=proto, local_ip="192.168.1.2", local_port=50000,
                  remote_ip=ip, remote_port=port, state="ESTABLISHED")


OWNERS = {
    "34.237.73.95": Owner(14618, "US", "Amazon.com, Inc."),
    "34.237.73.96": Owner(14618, "US", "Amazon.com, Inc."),
    "95.163.10.1": Owner(47764, "RU", "LLC VK"),
}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = store.Store(Path(self.tmp.name) / "sub" / "radar.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def record(self, ts, items):
        return self.db.record(ts, [(app, sock(1, ip, port)) for app, ip, port in items], OWNERS.get)

    def test_first_sample_creates_history_and_learns_without_alerting(self):
        events = self.record(1000, [("Slack", "34.237.73.95", 443)])
        self.assertEqual(events, [])  # first run builds the baseline
        rows = self.db.connections()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["app"], r["remote_ip"], r["remote_port"], r["country"], r["org"], r["first_seen"],
                          r["last_seen"], r["samples"]),
                         ("Slack", "34.237.73.95", 443, "US", "Amazon.com, Inc.", 1000, 1000, 1))

    def test_repeat_sample_updates_last_seen_and_count(self):
        self.record(1000, [("Slack", "34.237.73.95", 443)])
        self.record(1030, [("Slack", "34.237.73.95", 443), ("Slack", "34.237.73.95", 443)])
        r = self.db.connections()[0]
        self.assertEqual((r["first_seen"], r["last_seen"], r["samples"]), (1000, 1030, 2))

    def test_alerts_on_new_app_and_on_known_app_reaching_new_country(self):
        self.record(1000, [("Slack", "34.237.73.95", 443)])
        events = self.record(1030, [
            ("Slack", "34.237.73.96", 443),  # same app, same company/country: no alert
            ("Slack", "95.163.10.1", 443),   # same app, NEW country
            ("evil", "34.237.73.95", 443),   # NEW app on the network
        ])
        kinds = sorted((e.kind, e.app) for e in events)
        self.assertEqual(kinds, [("new_app", "evil"), ("new_country", "Slack")])
        ru = [e for e in events if e.kind == "new_country"][0]
        self.assertEqual((ru.country, ru.org, ru.remote_ip), ("RU", "LLC VK", "95.163.10.1"))
        self.assertEqual(len(self.db.events()), 2)

    def test_same_novelty_is_reported_once(self):
        self.record(1000, [("Slack", "34.237.73.95", 443)])
        self.record(1030, [("evil", "95.163.10.1", 443)])
        again = self.record(1060, [("evil", "95.163.10.1", 443)])
        self.assertEqual(again, [])

    def test_unknown_owner_is_stored_without_country(self):
        self.record(1000, [("Slack", "34.237.73.95", 443)])
        events = self.record(1030, [("curl", "203.0.113.9", 443)])
        self.assertEqual([e.kind for e in events], ["new_app"])
        r = [c for c in self.db.connections() if c["app"] == "curl"][0]
        self.assertEqual((r["country"], r["org"], r["asn"]), (None, None, None))

    def test_queries_filter_by_time_and_app(self):
        self.record(1000, [("Slack", "34.237.73.95", 443)])
        self.record(5000, [("Mail", "34.237.73.96", 993)])
        self.assertEqual([r["app"] for r in self.db.connections(since=2000)], ["Mail"])
        self.assertEqual([r["app"] for r in self.db.connections(app="slack")], ["Slack"])

    def test_survives_reopen(self):
        self.record(1000, [("Slack", "34.237.73.95", 443)])
        path = self.db.path
        self.db.close()
        self.db = store.Store(path)
        self.assertEqual(self.record(1030, [("Slack", "34.237.73.95", 443)]), [])
        self.assertEqual(self.db.connections()[0]["samples"], 2)


if __name__ == "__main__":
    unittest.main()

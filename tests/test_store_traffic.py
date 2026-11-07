import tempfile
import unittest
from pathlib import Path

from wachiman.radar import store

MB = 1024 * 1024


class TrafficStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = store.Store(Path(self.tmp.name) / "radar.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_adds_up_per_app_and_hour(self):
        h = 1_800_000_000 - (1_800_000_000 % 3600)
        self.db.record_traffic(h + 10, {"Slack": (100, 1000)}, upload_alert=0)
        self.db.record_traffic(h + 40, {"Slack": (50, 500), "Mail": (1, 2)}, upload_alert=0)
        self.db.record_traffic(h + 3600, {"Slack": (7, 7)}, upload_alert=0)
        rows = {r["app"]: (r["bytes_in"], r["bytes_out"]) for r in self.db.traffic()}
        self.assertEqual(rows, {"Slack": (157, 1507), "Mail": (1, 2)})
        since = {r["app"]: r["bytes_out"] for r in self.db.traffic(since=h + 3600)}
        self.assertEqual(since, {"Slack": 7})

    def test_sorted_by_upload(self):
        self.db.record_traffic(1000, {"a": (0, 5), "b": (0, 50), "c": (0, 1)}, upload_alert=0)
        self.assertEqual([r["app"] for r in self.db.traffic()], ["b", "a", "c"])

    def test_alerts_once_when_an_app_uploads_more_than_the_limit_in_one_hour(self):
        h = 1_800_000_000 - (1_800_000_000 % 3600)
        ev = self.db.record_traffic(h + 1, {"evil": (0, 60 * MB)}, upload_alert=100 * MB)
        self.assertEqual(ev, [])
        ev = self.db.record_traffic(h + 31, {"evil": (0, 60 * MB)}, upload_alert=100 * MB)
        self.assertEqual([(e.kind, e.app, e.bytes_out) for e in ev], [("big_upload", "evil", 120 * MB)])
        ev = self.db.record_traffic(h + 61, {"evil": (0, 60 * MB)}, upload_alert=100 * MB)
        self.assertEqual(ev, [])  # same hour: already told
        ev = self.db.record_traffic(h + 3601, {"evil": (0, 150 * MB)}, upload_alert=100 * MB)
        self.assertEqual(len(ev), 1)  # new hour, new alert
        self.assertEqual([r["kind"] for r in self.db.events()], ["big_upload", "big_upload"])

    def test_zero_limit_disables_alerts(self):
        self.assertEqual(self.db.record_traffic(1, {"x": (0, 10 ** 12)}, upload_alert=0), [])

    def test_counters_snapshot_persists(self):
        self.assertIsNone(self.db.get_meta("nettop"))
        self.db.set_meta("nettop", "{}")
        self.assertEqual(self.db.get_meta("nettop"), "{}")


if __name__ == "__main__":
    unittest.main()

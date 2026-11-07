import tempfile
import unittest
from pathlib import Path

from wachiman.radar import store

MIC = ("mic", "Zoom", "us.zoom.xos")
CAM = ("cam", "Zoom", "us.zoom.xos")
SIRI = ("mic", "Siri", "com.apple.CoreSpeech")


class SensorStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = store.Store(Path(self.tmp.name) / "radar.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_sessions_open_and_close_with_duration(self):
        self.db.record_sensors([(1000, {MIC, CAM}), (1060, {MIC}), (1300, set())], learning=True)
        rows = [(r["kind"], r["app"], r["start"], r["end"]) for r in self.db.sensor_sessions()]
        self.assertEqual(sorted(rows), [("cam", "Zoom", 1000, 1060), ("mic", "Zoom", 1000, 1300)])

    def test_open_session_survives_between_calls(self):
        self.db.record_sensors([(1000, {MIC})], learning=True)
        self.assertEqual([r["end"] for r in self.db.sensor_sessions()], [None])
        self.db.record_sensors([(1100, set())], learning=False)
        self.assertEqual([r["end"] for r in self.db.sensor_sessions()], [1100])

    def test_current_state_is_kept(self):
        self.db.record_sensors([(1000, {MIC, CAM}), (1060, {MIC})], learning=True)
        self.assertEqual(self.db.sensors_active(), {MIC})

    def test_alerts_first_time_an_app_uses_a_sensor(self):
        ev = self.db.record_sensors([(1000, {SIRI})], learning=False)
        self.assertEqual([(e.kind, e.app, e.sensor) for e in ev], [("new_sensor_app", "Siri", "mic")])
        ev = self.db.record_sensors([(1100, set()), (1200, {SIRI})], learning=False)
        self.assertEqual(ev, [])  # Siri already known for the mic
        ev = self.db.record_sensors([(1300, {SIRI, ("cam", "Siri", "com.apple.CoreSpeech")})], learning=False)
        self.assertEqual([(e.app, e.sensor) for e in ev], [("Siri", "cam")])  # same app, new sensor
        self.assertEqual(len([r for r in self.db.events() if r["kind"] == "new_sensor_app"]), 2)

    def test_no_alerts_while_learning(self):
        self.assertEqual(self.db.record_sensors([(1000, {MIC})], learning=True), [])

    def test_query_by_time(self):
        self.db.record_sensors([(1000, {MIC}), (1100, set()), (5000, {SIRI})], learning=True)
        self.assertEqual([r["app"] for r in self.db.sensor_sessions(since=4000)], ["Siri"])
        self.assertEqual([r["app"] for r in self.db.sensor_sessions(since=1050)], ["Siri", "Zoom"])


if __name__ == "__main__":
    unittest.main()

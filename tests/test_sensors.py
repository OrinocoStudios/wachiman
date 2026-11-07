import json
import unittest

from wachiman.radar import sensors

MSG = "Sorted active attributions from SystemStatus update: %s"


def line(ts, payload):
    return json.dumps({"timestamp": ts, "eventMessage": MSG % payload})


class ParseTest(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(sensors.parse_attributions(MSG % "[]"), set())

    def test_one_and_many(self):
        self.assertEqual(sensors.parse_attributions(MSG % "[[mic] Siri & Dictation (com.apple.CoreSpeech)]"),
                         {("mic", "Siri & Dictation", "com.apple.CoreSpeech")})
        many = MSG % "[[cam] FaceTime (com.apple.FaceTime), [mic] Zoom (Meetings) (us.zoom.xos)]"
        self.assertEqual(sensors.parse_attributions(many),
                         {("cam", "FaceTime", "com.apple.FaceTime"), ("mic", "Zoom (Meetings)", "us.zoom.xos")})

    def test_not_an_attribution_message(self):
        self.assertIsNone(sensors.parse_attributions("Pausing sensor indicator popover requests"))

    def test_parse_log_keeps_order_and_skips_noise(self):
        text = "\n".join([
            line("2026-11-07 00:31:15.428000+0100", "[[mic] Siri (com.apple.CoreSpeech)]"),
            "not json",
            json.dumps({"eventMessage": "summary without timestamp"}),
            line("2026-11-07 00:31:19.871000+0100", "[]"),
        ])
        states = sensors.parse_log(text)
        self.assertEqual([round(ts) for ts, _ in states], [1794007875, 1794007880])
        self.assertEqual(states[0][1], {("mic", "Siri", "com.apple.CoreSpeech")})
        self.assertEqual(states[1][1], set())


class SessionsTest(unittest.TestCase):
    def test_opens_and_closes_sessions_from_state_changes(self):
        mic = ("mic", "Zoom", "us.zoom.xos")
        cam = ("cam", "Zoom", "us.zoom.xos")
        opened, closed = sensors.diff(set(), {mic, cam})
        self.assertEqual((opened, closed), ({mic, cam}, set()))
        opened, closed = sensors.diff({mic, cam}, {mic})
        self.assertEqual((opened, closed), (set(), {cam}))

    def test_labels(self):
        self.assertEqual(sensors.label("mic", "es"), "micrófono")
        self.assertEqual(sensors.label("cam", "en"), "camera")
        self.assertEqual(sensors.label("scr", "es"), "grabación de pantalla")
        self.assertEqual(sensors.label("xyz", "es"), "xyz")


if __name__ == "__main__":
    unittest.main()

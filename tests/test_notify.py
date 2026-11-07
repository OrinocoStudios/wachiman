import unittest

from wachiman import notify
from wachiman.radar.store import Event


class MessageTest(unittest.TestCase):
    def test_new_country_reads_like_a_person_wrote_it(self):
        e = Event(ts=0, kind="new_country", app="Slack", remote_ip="95.163.10.1", country="RU", org="LLC VK")
        msg = notify.message(e, lang="es")
        self.assertIn("Slack", msg)
        self.assertIn("Rusia", msg)
        self.assertIn("LLC VK", msg)
        self.assertIn("95.163.10.1", msg)

    def test_new_app_and_unknown_country(self):
        e = Event(ts=0, kind="new_app", app="evil", remote_ip="203.0.113.9", country=None, org=None)
        msg = notify.message(e, lang="es")
        self.assertIn("evil", msg)
        self.assertIn("desconocido", msg)

    def test_country_names_fall_back_to_code(self):
        self.assertEqual(notify.country_name("ES", "es"), "España")
        self.assertEqual(notify.country_name("ES", "en"), "Spain")
        self.assertEqual(notify.country_name("ZZ", "es"), "ZZ")
        self.assertEqual(notify.country_name(None, "es"), "país desconocido")
        self.assertEqual(notify.country_name(None, "en"), "unknown country")

    def test_english_is_the_default(self):
        e = Event(ts=0, kind="new_app", app="evil", remote_ip="203.0.113.9", country="RU", org="LLC VK")
        self.assertIn("Russia", notify.message(e))


class UploadMessageTest(unittest.TestCase):
    def test_big_upload_in_spanish_and_english(self):
        e = Event(ts=0, kind="big_upload", app="evil", remote_ip=None, country=None, org=None,
                  bytes_out=250 * 1024 * 1024)
        es = notify.message(e, lang="es")
        self.assertIn("evil", es)
        self.assertIn("250.0 MB", es)
        self.assertIn("última hora", es)
        self.assertIn("250.0 MB", notify.message(e))


class SensorMessageTest(unittest.TestCase):
    def test_new_sensor_app(self):
        e = Event(ts=0, kind="new_sensor_app", app="Zoom", remote_ip=None, country=None, org=None, sensor="cam")
        self.assertIn("Zoom", notify.message(e, lang="es"))
        self.assertIn("cámara", notify.message(e, lang="es"))
        self.assertIn("camera", notify.message(e))


class KeyboardMessageTest(unittest.TestCase):
    def test_new_keyboard_tap(self):
        e = Event(ts=0, kind="new_keyboard_tap", app="evil", remote_ip=None, country=None, org=None,
                  signer="unsigned")
        es = notify.message(e, lang="es")
        self.assertIn("evil", es)
        self.assertIn("teclado", es)
        self.assertIn("sin firmar", es)
        self.assertIn("keyboard", notify.message(e))


class DeviceMessageTest(unittest.TestCase):
    def test_new_device_by_kind(self):
        usb = Event(ts=0, kind="new_device", app="Flash Disk", remote_ip=None, country=None, org=None, sensor="usb")
        self.assertIn("USB", notify.message(usb, lang="es"))
        self.assertIn("Flash Disk", notify.message(usb, lang="es"))
        bt = Event(ts=0, kind="new_device", app="Magic Keyboard", remote_ip=None, country=None, org=None, sensor="bt")
        self.assertIn("Bluetooth", notify.message(bt, lang="es"))
        net = Event(ts=0, kind="new_device", app="Wi-Fi Cafe", remote_ip=None, country=None, org=None, sensor="net")
        self.assertIn("red", notify.message(net, lang="es"))
        self.assertIn("network", notify.message(net))


class TelegramTest(unittest.TestCase):
    def test_does_nothing_without_credentials(self):
        calls = []
        sent = notify.send({"WACHIMAN_TELEGRAM_BOT_TOKEN": "", "WACHIMAN_TELEGRAM_CHAT_ID": ""}, "hola",
                           post=lambda url, data: calls.append((url, data)))
        self.assertFalse(sent)
        self.assertEqual(calls, [])

    def test_posts_to_the_bot_api(self):
        calls = []
        sent = notify.send({"WACHIMAN_TELEGRAM_BOT_TOKEN": "123:abc", "WACHIMAN_TELEGRAM_CHAT_ID": "42"}, "hola",
                           post=lambda url, data: calls.append((url, data)))
        self.assertTrue(sent)
        url, data = calls[0]
        self.assertEqual(url, "https://api.telegram.org/bot123:abc/sendMessage")
        self.assertEqual(data["chat_id"], "42")
        self.assertEqual(data["text"], "hola")

    def test_network_errors_never_raise(self):
        def boom(url, data):
            raise OSError("offline")
        sent = notify.send({"WACHIMAN_TELEGRAM_BOT_TOKEN": "1:a", "WACHIMAN_TELEGRAM_CHAT_ID": "2"}, "x", post=boom)
        self.assertFalse(sent)


if __name__ == "__main__":
    unittest.main()

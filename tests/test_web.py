import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from wachiman import web
from wachiman.radar import devices as devmod
from wachiman.radar import keyboard as kbmod
from wachiman.radar import store
from wachiman.radar.geo import Owner
from wachiman.radar.net import Socket

NOW = 1_800_000_000
EVIL = "<img src=x onerror=alert(1)>"


def sock(ip, port=443):
    return Socket(pid=1, command="x", fd="1", proto="TCP", local_ip="10.0.0.2", local_port=5000,
                  remote_ip=ip, remote_port=port, state="ESTABLISHED")


OWNERS = {"34.237.73.95": Owner(14618, "US", "Amazon.com, Inc."), "95.163.10.1": Owner(47764, "RU", "LLC VK")}


def make_store(path: Path) -> store.Store:
    db = store.Store(path)
    old = NOW - 3 * 86400
    db.record(old, [("Slack", sock("34.237.73.95"))], OWNERS.get)  # baseline
    db.record(NOW - 3600, [("Slack", sock("34.237.73.95")), ("Slack", sock("95.163.10.1")), (EVIL, sock("34.237.73.95"))],
              OWNERS.get)
    db.record_traffic(NOW - 1800, {"Slack": (1000, 5 * 1024 * 1024), EVIL: (1, 2)}, 1024 * 1024)
    db.record_sensors([(NOW - 7200, {("mic", "Zoom", "us.zoom")}), (NOW - 7000, set())], learning=False)
    db.record_sensors([(NOW - 60, {("cam", EVIL, "x.y")})], learning=False)
    db.record_keyboard(NOW - 500, [kbmod.Tap("Karabiner", "/Applications/K", "Developer ID", True)], learning=False)
    db.record_devices(NOW - 400, [devmod.Device("usb", "usb:1", "Razer Kiyo", "Razer Inc", True)], learning=False)
    return db


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = make_store(Path(self.tmp.name) / "radar.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def call(self, path, query=None, lang="en"):
        return web.handle(self.db, path, query or {}, NOW, lang)

    def test_summary_in_plain_language(self):
        status, body = self.call("/api/summary")
        self.assertEqual(status, 200)
        n = body["alerts"]
        self.assertGreaterEqual(n, 4)
        self.assertEqual(body["headline"], "In the last 24 h: %d new things worth a look." % n)
        self.assertEqual(body["since"], "24h")
        self.assertIn("counts", body)

    def test_summary_nothing_new(self):
        status, body = web.handle(self.db, "/api/summary", {"since": "1m"}, NOW + 3600, "en")
        self.assertEqual(body["headline"], "In the last 1 min: nothing new. All quiet.")

    def test_summary_singular_and_spanish(self):
        _, body = self.call("/api/summary", {"since": "5m"}, lang="es")  # only the camera event, 60 s ago
        self.assertEqual(body["alerts"], 1)
        self.assertEqual(body["headline"], "En los últimos 5 min: 1 cosa nueva que merece una mirada.")

    def test_events_use_notify_wording_and_language(self):
        _, en = self.call("/api/events", {"since": "24h"})
        kinds = {e["kind"] for e in en["events"]}
        self.assertTrue({"new_country", "big_upload", "new_sensor_app", "new_keyboard_tap", "new_device"} <= kinds)
        up = [e for e in en["events"] if e["kind"] == "big_upload"][0]
        self.assertIn("uploaded", up["text"])
        _, es = self.call("/api/events", {"since": "24h"}, lang="es")
        up = [e for e in es["events"] if e["kind"] == "big_upload"][0]
        self.assertIn("ha subido", up["text"])
        self.assertEqual(en["events"][0]["ts"] >= en["events"][-1]["ts"], True)

    def test_connections_filters_and_country_name(self):
        _, body = self.call("/api/connections", {"since": "24h", "app": "slack"})
        self.assertEqual({c["app"] for c in body["connections"]}, {"Slack"})
        ru = [c for c in body["connections"] if c["country"] == "RU"][0]
        self.assertEqual(ru["country_name"], "Russia")
        self.assertEqual(ru["org"], "LLC VK")
        _, wide = self.call("/api/connections", {"since": "7d"})
        _, narrow = self.call("/api/connections", {"since": "30m"})
        self.assertGreater(len(wide["connections"]), len(narrow["connections"]))

    def test_connections_limit(self):
        _, body = self.call("/api/connections", {"limit": "1"})
        self.assertEqual(len(body["connections"]), 1)
        self.assertTrue(body["truncated"])

    def test_traffic(self):
        _, body = self.call("/api/traffic", {"since": "24h"})
        first = body["traffic"][0]
        self.assertEqual((first["app"], first["bytes_out"]), ("Slack", 5 * 1024 * 1024))
        self.assertEqual(first["sent"], "5.0 MB")

    def test_sensors_have_labels_duration_and_active_flag(self):
        _, body = self.call("/api/sensors", {"since": "7d"})
        by = {s["app"]: s for s in body["sensors"]}
        self.assertEqual(by["Zoom"]["label"], "microphone")
        self.assertEqual(by["Zoom"]["seconds"], 200)
        self.assertFalse(by["Zoom"]["active"])
        self.assertTrue(by[EVIL]["active"])

    def test_keyboard_and_devices(self):
        _, kb = self.call("/api/keyboard")
        self.assertEqual(kb["keyboard"][0]["app"], "Karabiner")
        self.assertEqual(kb["keyboard"][0]["signer_label"], "Developer ID")
        _, dv = self.call("/api/devices", {"kind": "usb"})
        self.assertEqual(dv["devices"][0]["name"], "Razer Kiyo")
        self.assertEqual(dv["devices"][0]["kind_label"], "USB device")
        _, none = self.call("/api/devices", {"kind": "bt"})
        self.assertEqual(none["devices"], [])

    def test_errors(self):
        self.assertEqual(self.call("/api/summary", {"since": "banana"})[0], 400)
        self.assertEqual(self.call("/api/nope")[0], 404)
        self.assertEqual(self.call("/api/devices", {"kind": "x"})[0], 400)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "radar.db"
        make_store(self.db_path).close()
        self.env = {"HOME": self.tmp.name, "WACHIMAN_DB": str(self.db_path), "WACHIMAN_LANG": "en"}
        self.server = web.make_server("127.0.0.1", 0, self.env, clock=lambda: NOW)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.tmp.cleanup()

    def fetch(self, path, method="GET", headers=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode()

    def test_summary_over_http(self):
        status, headers, body = self.fetch("/api/summary")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("alerts", json.loads(body))

    def test_page_is_self_contained_and_locked_down(self):
        status, headers, body = self.fetch("/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn("prefers-color-scheme", body)
        for bad in ("http://", "https://", "src=\"//", "<link", "@import"):
            self.assertNotIn(bad, body)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_page_never_uses_innerhtml_and_has_no_data_inlined(self):
        _, _, body = self.fetch("/")
        self.assertNotIn("innerHTML", body)
        self.assertNotIn("insertAdjacentHTML", body)
        self.assertNotIn("document.write", body)
        self.assertNotIn(EVIL, body)
        self.assertNotIn("onerror", body)

    def test_hostile_app_name_stays_data_in_json(self):
        status, headers, body = self.fetch("/api/connections?since=24h")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        apps = [c["app"] for c in json.loads(body)["connections"]]
        self.assertIn(EVIL, apps)  # data is untouched; the page renders it with textContent only

    def test_read_only(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            req = urllib.request.Request("http://127.0.0.1:%d/api/summary" % self.port, method=method, data=b"x")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(cm.exception.code, 405)

    def test_foreign_host_header_is_refused(self):
        status, _, _ = self.fetch("/api/summary", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_unknown_path(self):
        self.assertEqual(self.fetch("/etc/passwd")[0], 404)


class BindTest(unittest.TestCase):
    def test_refuses_anything_but_loopback(self):
        for host in ("0.0.0.0", "", "192.168.1.5", "::", "example.com"):
            with self.assertRaises(ValueError, msg=host):
                web.make_server(host, 0, {"HOME": "/nonexistent"})

    def test_cli_refuses_other_hosts(self):
        from wachiman import cli
        self.assertEqual(cli.main(["web", "--host", "0.0.0.0", "--port", "0"], env={"HOME": "/nonexistent"}), 2)


class CliTest(unittest.TestCase):
    def test_web_command_serves_on_requested_port(self):
        from unittest import mock
        from wachiman import cli
        with mock.patch.object(web, "serve", return_value=0) as serve:
            self.assertEqual(cli.main(["web", "--port", "9999"], env={"HOME": "/nonexistent"}), 0)
        self.assertEqual(serve.call_args.args[:2], ("127.0.0.1", 9999))


if __name__ == "__main__":
    unittest.main()

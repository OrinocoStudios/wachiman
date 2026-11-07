import json
import tempfile
import unittest
from pathlib import Path

from wachiman.radar import devices, store

USB = json.dumps({
    "SPUSBDataType": [],
    "SPUSBHostDataType": [
        {"_name": "USB 3.1 Bus", "_items": [
            {"_name": "USB2.1 Hub", "USBDeviceKeyVendorID": "0x0bda", "USBDeviceKeyProductID": "0x5411",
             "USBDeviceKeySerialNumber": "Not Provided", "USBDeviceKeyVendorName": "Generic", "_items": [
                 {"_name": "Razer Kiyo X", "USBDeviceKeyVendorID": "0x1532", "USBDeviceKeyProductID": "0x0e06",
                  "USBDeviceKeySerialNumber": "01.00.00", "USBDeviceKeyVendorName": "Razer Inc"},
                 {"_name": "Trust Xalas Numpad", "USBDeviceKeyVendorID": "0x145f", "USBDeviceKeyProductID": "0x0353",
                  "USBDeviceKeySerialNumber": "Not Provided"},
             ]},
        ]},
    ],
})

BT = json.dumps({"SPBluetoothDataType": [{
    "controller_properties": {"controller_address": "AA:AA"},
    "device_connected": [{"Magic Keyboard": {"device_address": "11:22:33:44:55:66", "device_minorType": "Keyboard"}}],
    "device_not_connected": [{"Goatsson AirPods": {"device_address": "34:0E:22:AA:02:10",
                                                   "device_minorType": "Headphones"}}],
}]})


class ParseTest(unittest.TestCase):
    def test_usb_walks_the_tree_and_skips_hubs(self):
        devs = devices.parse_usb(USB)
        self.assertEqual(sorted((d.name, d.vendor, d.id) for d in devs), [
            ("Razer Kiyo X", "Razer Inc", "usb:0x1532:0x0e06:01.00.00"),
            ("Trust Xalas Numpad", "", "usb:0x145f:0x0353:"),
        ])
        self.assertTrue(all(d.kind == "usb" for d in devs))

    def test_bluetooth_lists_paired_and_marks_connected(self):
        devs = {d.name: d for d in devices.parse_bluetooth(BT)}
        self.assertEqual(devs["Magic Keyboard"].id, "bt:11:22:33:44:55:66")
        self.assertEqual(devs["Magic Keyboard"].vendor, "Keyboard")
        self.assertTrue(devs["Magic Keyboard"].connected)
        self.assertFalse(devs["Goatsson AirPods"].connected)

    def test_network_identity(self):
        wifi = devices.network("en1", "192.168.1.1", "Casa_5G")
        self.assertEqual((wifi.kind, wifi.id, wifi.name), ("net", "net:wifi:Casa_5G", "Wi-Fi Casa_5G"))
        wired = devices.network("en0", "192.168.1.1", None)
        self.assertEqual((wired.id, wired.name), ("net:en0:192.168.1.1", "en0 → router 192.168.1.1"))
        self.assertIsNone(devices.network(None, None, None))

    def test_parse_ssid(self):
        text = "  InterfaceType : WiFi\n  SSID : Casa_5G\n  BSSID : aa:bb\n"
        self.assertEqual(devices.parse_ssid(text), "Casa_5G")
        self.assertIsNone(devices.parse_ssid("  SSID : <redacted>\n"))
        self.assertIsNone(devices.parse_ssid(""))

    def test_garbage(self):
        self.assertEqual(devices.parse_usb("nope"), [])
        self.assertEqual(devices.parse_bluetooth("{}"), [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = store.Store(Path(self.tmp.name) / "radar.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_learns_then_alerts_once_per_new_device(self):
        cam = devices.Device("usb", "usb:1:2:3", "Razer Kiyo X", "Razer", True)
        stick = devices.Device("usb", "usb:9:9:9", "Flash Disk", "Generic", True)
        self.assertEqual(self.db.record_devices(1000, [cam], learning=True), [])
        ev = self.db.record_devices(1030, [cam, stick], learning=False)
        self.assertEqual([(e.kind, e.app, e.sensor) for e in ev], [("new_device", "Flash Disk", "usb")])
        self.assertEqual(self.db.record_devices(1060, [stick], learning=False), [])
        rows = {r["name"]: (r["first_seen"], r["last_seen"]) for r in self.db.devices()}
        self.assertEqual(rows, {"Razer Kiyo X": (1000, 1030), "Flash Disk": (1030, 1060)})

    def test_filter_by_kind(self):
        self.db.record_devices(1, [devices.Device("usb", "u", "A", "", True),
                                   devices.Device("bt", "b", "B", "", False)], learning=True)
        self.assertEqual([r["name"] for r in self.db.devices(kind="bt")], ["B"])


if __name__ == "__main__":
    unittest.main()

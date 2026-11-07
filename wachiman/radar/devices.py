"""What is plugged in, paired, or what network we are on.

    usb  every USB device on the bus (hubs skipped), from `system_profiler`
    bt   every paired Bluetooth device, connected or not
    net  the network we route through: the Wi-Fi name, or interface + router

A new USB keyboard you did not plug in (a "BadUSB" stick types commands as a
keyboard), a new paired Bluetooth device, or joining an unknown Wi-Fi are all
worth one message. Known devices coming and going are not.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, List, NamedTuple, Optional

NO_SERIAL = {"", "Not Provided", "0", "000000000000"}


class Device(NamedTuple):
    kind: str  # usb | bt | net
    id: str
    name: str
    vendor: str
    connected: bool


def _load(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def parse_usb(text: str) -> List[Device]:
    data = _load(text)
    if not isinstance(data, dict):
        return []
    out: List[Device] = []

    def walk(items: Any) -> None:
        for it in items or []:
            if not isinstance(it, dict):
                continue
            vid = it.get("USBDeviceKeyVendorID") or it.get("vendor_id")
            pid = it.get("USBDeviceKeyProductID") or it.get("product_id")
            name = it.get("_name", "?")
            if vid and pid and "hub" not in name.lower():
                serial = it.get("USBDeviceKeySerialNumber") or it.get("serial_num") or ""
                serial = "" if serial in NO_SERIAL else serial
                vendor = it.get("USBDeviceKeyVendorName") or it.get("manufacturer") or ""
                out.append(Device("usb", "usb:%s:%s:%s" % (vid, pid, serial), name, vendor, True))
            walk(it.get("_items"))

    for value in data.values():
        walk(value)
    return out


def parse_bluetooth(text: str) -> List[Device]:
    data = _load(text)
    if not isinstance(data, dict):
        return []
    out: List[Device] = []
    for ctrl in data.get("SPBluetoothDataType") or []:
        for section, connected in (("device_connected", True), ("device_not_connected", False)):
            for entry in ctrl.get(section) or []:
                for name, info in (entry or {}).items():
                    addr = (info or {}).get("device_address")
                    if addr:
                        out.append(Device("bt", "bt:" + addr, name, (info or {}).get("device_minorType", ""),
                                          connected))
    return out


def parse_ssid(text: str) -> Optional[str]:
    for line in (text or "").splitlines():
        key, sep, value = line.strip().partition(" : ")
        if sep and key == "SSID":
            value = value.strip()
            return None if not value or value.startswith("<") else value
    return None


def network(iface: Optional[str], router: Optional[str], ssid: Optional[str]) -> Optional[Device]:
    if ssid:
        return Device("net", "net:wifi:" + ssid, "Wi-Fi " + ssid, "", True)
    if iface and router:
        return Device("net", "net:%s:%s" % (iface, router), "%s → router %s" % (iface, router), "", True)
    return None


def _run(cmd: List[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _route() -> tuple:
    iface = router = None
    for line in _run(["route", "-n", "get", "default"]).splitlines():
        key, _, value = line.strip().partition(": ")
        if key == "interface":
            iface = value.strip()
        elif key == "gateway":
            router = value.strip()
    return iface, router


def read() -> List[Device]:
    out = parse_usb(_run(["system_profiler", "-json", "SPUSBDataType", "SPUSBHostDataType"]))
    out += parse_bluetooth(_run(["system_profiler", "-json", "SPBluetoothDataType"]))
    iface, router = _route()
    ssid = parse_ssid(_run(["ipconfig", "getsummary", iface])) if iface else None
    net = network(iface, router, ssid)
    if net:
        out.append(net)
    return out

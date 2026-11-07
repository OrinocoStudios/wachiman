"""Which programs are reading the keyboard right now.

A keylogger on macOS has to install an *event tap* (or ask for Input
Monitoring, which also ends in a tap). CoreGraphics lists every active tap and
the process that owns it, and asking needs no permission at all. That makes
this the most direct keylogger signal available without root.

For each program we also record who signed it: Apple, a named developer
(Developer ID), or nobody. An unsigned program reading keystrokes is the
pattern of most macOS stealers.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import subprocess
from typing import Callable, Dict, List, NamedTuple

from . import net

KEY_EVENTS = (1 << 10) | (1 << 11) | (1 << 12)  # kCGEventKeyDown, KeyUp, FlagsChanged


class RawTap(NamedTuple):
    pid: int
    mask: int
    listen_only: bool
    enabled: bool


class Tap(NamedTuple):
    app: str
    path: str
    signer: str
    can_modify: bool  # active tap: can also change or swallow keystrokes


def reads_keys(mask: int) -> bool:
    return bool(mask & KEY_EVENTS)


def parse_signer(codesign_output: str) -> str:
    text = codesign_output or ""
    if not text.strip():
        return "unknown"
    if "not signed" in text or "Signature=adhoc" in text:
        return "unsigned"
    for line in text.splitlines():
        if line.startswith("Authority="):
            first = line[len("Authority="):].strip()
            if first in ("Software Signing", "macOS Software Signing", "Apple Mac OS Application Signing"):
                return "Apple"
            for prefix in ("Developer ID Application: ", "Apple Development: ", "Apple Distribution: "):
                if first.startswith(prefix):
                    return first[len(prefix):]
            return first
    return "unsigned"


def signer_of(path: str) -> str:
    if not path.startswith("/"):
        return "unknown"
    try:
        r = subprocess.run(["codesign", "-dv", "--verbose=2", path], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return parse_signer(r.stderr + r.stdout)


def keyboard_taps(raw: List[RawTap], paths: Dict[int, str], signer: Callable[[str], str] = signer_of) -> List[Tap]:
    by_path: Dict[str, Tap] = {}
    for t in raw:
        if not t.enabled or not reads_keys(t.mask):
            continue
        path = paths.get(t.pid)
        if path:
            app = net.app_name(path, path)
        else:
            app, path = "pid %d" % t.pid, "pid:%d" % t.pid
        old = by_path.get(path)
        can_modify = (not t.listen_only) or (old.can_modify if old else False)
        by_path[path] = Tap(app, path, old.signer if old else signer(path), can_modify)
    return list(by_path.values())


class _Info(ctypes.Structure):
    _fields_ = [("eventTapID", ctypes.c_uint32), ("tapPoint", ctypes.c_uint32), ("options", ctypes.c_uint32),
                ("eventsOfInterest", ctypes.c_uint64), ("tappingProcess", ctypes.c_int32),
                ("processBeingTapped", ctypes.c_int32), ("enabled", ctypes.c_bool),
                ("minUsecLatency", ctypes.c_float), ("avgUsecLatency", ctypes.c_float),
                ("maxUsecLatency", ctypes.c_float)]


def raw_taps() -> List[RawTap]:
    lib = ctypes.util.find_library("CoreGraphics") or ctypes.util.find_library("ApplicationServices")
    if not lib:
        return []
    try:
        cg = ctypes.CDLL(lib)
        fn = cg.CGGetEventTapList
    except (OSError, AttributeError):
        return []
    fn.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Info), ctypes.POINTER(ctypes.c_uint32)]
    fn.restype = ctypes.c_int32
    n = ctypes.c_uint32()
    if fn(0, None, ctypes.byref(n)) != 0 or n.value == 0:
        return []
    arr = (_Info * n.value)()
    if fn(n.value, arr, ctypes.byref(n)) != 0:
        return []
    return [RawTap(t.tappingProcess, t.eventsOfInterest, t.options == 1, t.enabled) for t in arr[: n.value]]


def read() -> List[Tap]:
    raw = raw_taps()
    if not raw:
        return []
    return keyboard_taps(raw, net.parse_ps(net._run(net.PS_CMD)))

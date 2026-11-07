"""How much each app sends and receives.

macOS `nettop` gives per-process byte counters for the flows that are open
right now. We keep the previous snapshot and count the growth between two
scans. That makes the totals a lower bound: bytes of a flow that opened and
closed entirely between two scans are not seen. A sustained upload, which is
what data theft looks like, is.
"""
from __future__ import annotations

import json
import subprocess
from typing import Dict, Optional, Tuple

from . import net

NETTOP_CMD = ["nettop", "-P", "-L", "1", "-x", "-J", "bytes_in,bytes_out"]

Counters = Dict[int, Tuple[str, int, int]]  # pid -> (name, bytes_in, bytes_out)


def parse_nettop(text: str) -> Counters:
    out: Counters = {}
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 3:
            continue
        label, bin_, bout = parts[0], parts[1], parts[2]
        name, dot, pid = label.rpartition(".")
        if not dot or not name or not pid.isdigit() or not bin_.isdigit() or not bout.isdigit():
            continue
        out[int(pid)] = (name, int(bin_), int(bout))
    return out


def delta(prev: Counters, cur: Counters) -> Dict[int, Tuple[int, int]]:
    """Bytes moved since the previous snapshot, per pid. Unknown or reused pids are skipped."""
    out: Dict[int, Tuple[int, int]] = {}
    for pid, (name, bin_, bout) in cur.items():
        old = prev.get(pid)
        if old is None or old[0] != name:
            continue
        d_in, d_out = max(0, bin_ - old[1]), max(0, bout - old[2])
        if d_in or d_out:
            out[pid] = (d_in, d_out)
    return out


def dumps(c: Counters) -> str:
    return json.dumps({str(k): list(v) for k, v in c.items()})


def loads(text: Optional[str]) -> Counters:
    if not text:
        return {}
    try:
        raw = json.loads(text)
        return {int(k): (str(v[0]), int(v[1]), int(v[2])) for k, v in raw.items()}
    except (ValueError, TypeError, IndexError, AttributeError):
        return {}


def human(n: int) -> str:
    if n < 1024:
        return "%d B" % n
    size = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024.0
        if size < 1024 or unit == "TB":
            break
    return "%.1f %s" % (size, unit)


def sample() -> Counters:
    """Counters now, with the app name resolved like the network radar does."""
    try:
        text = subprocess.run(NETTOP_CMD, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    counters = parse_nettop(text)
    if not counters:
        return {}
    paths = net.parse_ps(net._run(net.PS_CMD))
    return {pid: (net.app_name(paths.get(pid), name), bin_, bout) for pid, (name, bin_, bout) in counters.items()}

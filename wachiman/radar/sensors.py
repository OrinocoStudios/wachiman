"""Who used the microphone, the camera or screen recording.

macOS shows an orange/green dot in the menu bar whenever an app uses the mic,
the camera or records the screen. Control Center logs every change of that
indicator together with the app responsible:

    Sorted active attributions from SystemStatus update: [[mic] Zoom (us.zoom.xos)]

We read those lines from the unified log (`log show`), which needs no special
permission, and turn them into sessions: which app, which sensor, from when
to when. Nothing is recorded from the sensors themselves.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from typing import List, Optional, Set, Tuple

Attribution = Tuple[str, str, str]  # (kind: mic|cam|scr, display name, bundle id)
State = Tuple[float, Set[Attribution]]

PREFIX = "Sorted active attributions from SystemStatus update: "
PREDICATE = ('subsystem == "com.apple.controlcenter" AND category == "sensor-indicators" '
             'AND eventMessage BEGINSWITH "Sorted active attributions"')
_ITEM = re.compile(r"\[(\w+)\] (.+?) \(([^()]+)\)(?=, \[|$)")

LABELS = {
    "es": {"mic": "micrófono", "cam": "cámara", "scr": "grabación de pantalla"},
    "en": {"mic": "microphone", "cam": "camera", "scr": "screen recording"},
}


def label(kind: str, lang: str = "en") -> str:
    return LABELS.get(lang, LABELS["en"]).get(kind, kind)


def parse_attributions(message: str) -> Optional[Set[Attribution]]:
    if not message.startswith(PREFIX):
        return None
    body = message[len(PREFIX):].strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    return {(m.group(1), m.group(2), m.group(3)) for m in _ITEM.finditer(body)}


def _ts(text: str) -> Optional[float]:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f%z").timestamp()
    except (ValueError, TypeError):
        return None


def parse_log(text: str) -> List[State]:
    out: List[State] = []
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        ts = _ts(d.get("timestamp", ""))
        attrs = parse_attributions(d.get("eventMessage", ""))
        if ts is None or attrs is None:
            continue
        out.append((ts, attrs))
    out.sort(key=lambda s: s[0])
    return out


def diff(before: Set[Attribution], after: Set[Attribution]) -> Tuple[Set[Attribution], Set[Attribution]]:
    return after - before, before - after


def read(since: float) -> List[State]:
    """Indicator changes since `since` (epoch seconds)."""
    start = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since))
    try:
        text = subprocess.run(["log", "show", "--style", "ndjson", "--start", start, "--predicate", PREDICATE],
                              capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [s for s in parse_log(text) if s[0] > since]

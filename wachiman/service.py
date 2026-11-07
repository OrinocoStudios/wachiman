"""Run the radar in the background on macOS (launchd user agent).

    wachiman service install     write the agent and start it (starts at every login)
    wachiman service uninstall   stop it and remove the agent
    wachiman service status      is it loaded?

Secrets are never written into the agent: the radar reads them from
$WACHIMAN_HOME/config.env at runtime.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from typing import Mapping, Optional

from . import paths

LABEL = "io.orinoco.wachiman.radar"


def agent_path(env: Optional[Mapping[str, str]] = None) -> Path:
    e = os.environ if env is None else env
    base = e.get("HOME") or os.path.expanduser("~")
    return Path(base) / "Library" / "LaunchAgents" / (LABEL + ".plist")


def plist(repo: Path, python: str, env: Optional[Mapping[str, str]] = None) -> bytes:
    e = os.environ if env is None else env
    home = paths.home(e)
    agent_env = {"PYTHONPATH": str(repo), "WACHIMAN_HOME": str(home),
                 "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    log = str(home / "radar.log")
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [python, "-u", "-m", "wachiman", "radar", "watch"],
        "WorkingDirectory": str(repo),
        "EnvironmentVariables": agent_env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    })


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(repo: Path, python: str) -> int:
    dest = agent_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    paths.home().mkdir(parents=True, exist_ok=True)
    domain = "gui/%d" % os.getuid()
    _launchctl("bootout", domain + "/" + LABEL)  # replace an older copy; fine if absent
    dest.write_bytes(plist(repo, python))
    r = _launchctl("bootstrap", domain, str(dest))
    if r.returncode != 0:
        print("launchctl bootstrap failed: %s" % (r.stderr.strip() or r.returncode))
        return 1
    print("installed: %s\nlogs: %s" % (dest, paths.home() / "radar.log"))
    return 0


def uninstall() -> int:
    _launchctl("bootout", "gui/%d/%s" % (os.getuid(), LABEL))
    dest = agent_path()
    if dest.exists():
        dest.unlink()
    print("removed")
    return 0


def status() -> int:
    r = _launchctl("print", "gui/%d/%s" % (os.getuid(), LABEL))
    if r.returncode != 0:
        print("not running")
        return 1
    state = next((l.strip() for l in r.stdout.splitlines() if l.strip().startswith(("state =", "pid ="))), "loaded")
    print("running" if "running" in r.stdout else "loaded", "-", state)
    return 0

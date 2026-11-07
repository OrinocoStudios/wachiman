"""Who is talking to the internet right now.

Reads open sockets with `lsof` (ships with macOS and most Linux), maps each one
to an app name, and keeps only connections that leave the machine.
"""
from __future__ import annotations

import ipaddress
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

LSOF_CMD = ["lsof", "-nP", "-i", "-F", "pcPnT", "+c", "0"]
PS_CMD = ["ps", "-axo", "pid=,comm="]


@dataclass(frozen=True)
class Socket:
    pid: int
    command: str
    fd: str
    proto: str
    local_ip: Optional[str]
    local_port: Optional[int]
    remote_ip: Optional[str]
    remote_port: Optional[int]
    state: str


def _split_endpoint(text: str) -> Tuple[Optional[str], Optional[int]]:
    """'1.2.3.4:443' | '[::1]:443' | '*:*' -> (ip, port)."""
    if text.startswith("["):
        host, _, port = text[1:].partition("]:")
    else:
        host, _, port = text.rpartition(":")
    ip = None if host in ("", "*") else host
    try:
        p = int(port)
    except ValueError:
        p = None
    return ip, p


def parse_lsof(text: str) -> List[Socket]:
    out: List[Socket] = []
    pid: Optional[int] = None
    command = ""
    cur: Optional[dict] = None

    def flush() -> None:
        if cur is None or pid is None or "name" not in cur:
            return
        name = cur["name"]
        local, _, remote = name.partition("->")
        lip, lport = _split_endpoint(local)
        rip, rport = _split_endpoint(remote) if remote else (None, None)
        out.append(Socket(pid, command, cur.get("fd", ""), cur.get("proto", ""), lip, lport, rip, rport,
                          cur.get("state", "")))

    for line in text.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            flush()
            cur = None
            try:
                pid = int(value)
            except ValueError:
                pid = None
            command = ""
        elif tag == "c":
            command = value
        elif tag == "f":
            flush()
            cur = {"fd": value}
        elif cur is None:
            continue
        elif tag == "P":
            cur["proto"] = value
        elif tag == "n":
            cur["name"] = value
        elif tag == "T" and value.startswith("ST="):
            cur["state"] = value[3:]
    flush()
    return out


_EXTRA_NOT_PUBLIC = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("2001:db8::/32")]


def is_public(ip: Optional[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    if (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
            or addr.is_unspecified or addr.is_reserved):
        return False
    return not any(addr in net for net in _EXTRA_NOT_PUBLIC if net.version == addr.version)


def outbound(sockets: List[Socket]) -> List[Socket]:
    """Connections to the internet (drops listeners, loopback, LAN, multicast)."""
    seen = set()
    out = []
    for s in sockets:
        if s.remote_ip is None or not is_public(s.remote_ip):
            continue
        key = (s.pid, s.proto, s.local_port, s.remote_ip, s.remote_port)
        if key in seen:  # one socket can appear on several fds
            continue
        seen.add(key)
        out.append(s)
    return out


def app_name(path: Optional[str], command: str) -> str:
    """Human name of the app: the OUTERMOST .app bundle, else the binary name."""
    if path:
        for part in path.split("/"):
            if part.endswith(".app"):
                return part[:-4]
        base = path.rstrip("/").rsplit("/", 1)[-1]
        if base:
            return base
    return command


def parse_ps(text: str) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for line in text.splitlines():
        pid, _, path = line.strip().partition(" ")
        if pid.isdigit() and path.strip():
            out[int(pid)] = path.strip()
    return out


def _run(cmd: List[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def sample() -> List[Tuple[str, Socket]]:
    """One snapshot of outbound connections as (app, socket) pairs."""
    socks = outbound(parse_lsof(_run(LSOF_CMD)))
    paths = parse_ps(_run(PS_CMD)) if socks else {}
    return [(app_name(paths.get(s.pid), s.command), s) for s in socks]

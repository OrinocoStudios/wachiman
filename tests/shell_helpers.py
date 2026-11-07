"""Shared helpers for testing the shell modules from Python.

Each test gets a temporary WACHIMAN_HOME, a directory of fake binaries that is
put first in PATH, and (when asked) a tiny local HTTP server that plays the
Telegram API, so the real curl runs end to end without touching the network.
"""
import os
import re
import stat
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULES = REPO / "modules"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
TOKEN = "123456:SECRET-TOKEN-VALUE"
CHAT = "42"


class FakeTelegram:
    """Records every request; answers {"ok": true}."""

    def __init__(self):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                outer.requests.append((self.path, body.decode("utf-8", "replace")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true, "result": []}')

            def log_message(self, format, *args):  # noqa: A002
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def texts(self):
        """Decoded text= field of every sendMessage."""
        from urllib.parse import parse_qs
        out = []
        for path, body in self.requests:
            if path.endswith("/sendMessage"):
                out.append(parse_qs(body).get("text", [""])[0])
        return out

    def photos(self):
        return [b for p, b in self.requests if p.endswith("/sendPhoto")]


def fake_bin(directory, name, script):
    path = Path(directory) / name
    path.write_text("#!/bin/sh\n" + script + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# prints the first line of a file and drops it, but keeps the last line forever
POP = (r'''pop() { f="$1"; [ -s "$f" ] || return 0; head -1 "$f"; n=$(wc -l < "$f"); '''
       r'''if [ "$n" -gt 1 ]; then tail -n +2 "$f" > "$f.t"; mv "$f.t" "$f"; fi; return 0; }''')


class ShellCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root / "home"          # fake $HOME
        self.wm = root / "wm"              # WACHIMAN_HOME
        self.bin = root / "bin"            # fake binaries, first in PATH
        self.seq = root / "seq"            # sequence files for the fakes
        for d in (self.home, self.wm, self.bin, self.seq):
            d.mkdir()
        self.tg = None

    def telegram(self):
        self.tg = FakeTelegram()
        self.addCleanup(self.tg.close)
        return self.tg

    def write_config(self, text):
        (self.wm / "config.env").write_text(text)

    def sequence(self, name, lines):
        (self.seq / name).write_text("\n".join(str(x) for x in lines) + "\n")

    def env(self, **extra):
        e = {"HOME": str(self.home), "WACHIMAN_HOME": str(self.wm),
             "PATH": "%s:%s" % (self.bin, SYSTEM_PATH), "TMPDIR": self.tmp.name}
        if self.tg is not None:
            e.update(WACHIMAN_TELEGRAM_API=self.tg.url, WACHIMAN_TELEGRAM_BOT_TOKEN=TOKEN,
                     WACHIMAN_TELEGRAM_CHAT_ID=CHAT)
        e.update(extra)
        return e

    def sh(self, script, args=(), timeout=60, **extra_env):
        p = subprocess.run(["/bin/bash", str(script)] + list(args), capture_output=True, text=True,
                           env=self.env(**extra_env), timeout=timeout, stdin=subprocess.DEVNULL)
        self.last = p
        return p

    def fake(self, name, script):
        return fake_bin(self.bin, name, script)

    def popper(self, name, seqfile, extra=""):
        """Fake binary whose stdout comes from a sequence file (see POP)."""
        return self.fake(name, POP + '\n' + extra.replace("{SEQ}", str(self.seq / seqfile)))

    def read(self, *parts):
        p = self.wm.joinpath(*parts)
        return p.read_text() if p.exists() else ""

    def assertNoToken(self, *outputs):
        for o in outputs:
            self.assertNotIn(TOKEN, o)
            self.assertNotIn("SECRET-TOKEN", o)


def all_shell_scripts():
    return sorted(MODULES.rglob("*.sh")) if MODULES.exists() else []

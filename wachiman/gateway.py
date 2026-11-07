"""Telegram command gateway: drive the Guard, Memory watch and Stealer watch from your phone.

One bot (WACHIMAN_TELEGRAM_BOT_TOKEN), long polling, no AI. Rules that must stay:

- Commands are hard-coded in COMMANDS; the person only picks one from a fixed list.
  Text after the command is ignored, so nothing the user types ever reaches an argv
  or a shell.
- Only WACHIMAN_TELEGRAM_CHAT_ID is answered. Any other chat is logged and dropped.
  There is no other authentication, so never widen this.
- Destructive commands (/deletephotos) need to be sent twice within two minutes. The
  pending confirmation lives in memory, so restarting the gateway cancels it.
- /help and Telegram's "/" menu (setMyCommands) are built from the same table, so they
  cannot drift apart. English names; Spanish aliases (/estado, /bloquea...) always work
  and are not shown in the menu.
- The token is never logged or printed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

MAX_CHARS = 3500
CONFIRM_WINDOW = 120
BURST = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d+\.jpg$")
API = "https://api.telegram.org"


class ConfigError(Exception):
    pass


# name -> (handler, {lang: description}, aliases). Handlers are Gateway method names.
COMMANDS: Dict[str, Tuple[str, Dict[str, str], Tuple[str, ...]]] = {
    "status": ("c_status", {"en": "Guard status", "es": "estado del Guard"}, ("estado",)),
    "on": ("c_on", {"en": "start Guard and LOCK the screen", "es": "arranca el Guard y BLOQUEA la pantalla"}, ()),
    "test": ("c_test", {"en": "start Guard without locking (testing)", "es": "arranca sin bloquear (pruebas)"},
             ("prueba",)),
    "off": ("c_off", {"en": "stop Guard", "es": "para el Guard"}, ()),
    "lock": ("c_lock", {"en": "lock the screen (no Guard loop)", "es": "bloquea la pantalla (sin arrancar el Guard)"},
             ("bloquea",)),
    "log": ("c_log", {"en": "last Guard log lines", "es": "últimas líneas del log del Guard"}, ()),
    "debug": ("c_debug", {"en": "last polls (locked/idle)", "es": "últimos polls (locked/idle)"}, ()),
    "photos": ("c_photos", {"en": "saved photo bursts", "es": "ráfagas de fotos guardadas"}, ("fotos",)),
    "last": ("c_last", {"en": "send the latest photo burst", "es": "envía la última ráfaga"}, ("ultimas",)),
    "deletephotos": ("c_delete", {"en": "delete all photos (asks twice)", "es": "borra todas las fotos (pide confirmar)"},
                     ("borrafotos",)),
    "mem": ("c_mem", {"en": "memory now + top 5", "es": "memoria ahora + top 5"}, ("memoria",)),
    "top": ("c_top", {"en": "top 5 processes by RSS", "es": "top 5 procesos por RSS"}, ()),
    "thresholds": ("c_thresholds", {"en": "active memory thresholds", "es": "umbrales de memoria activos"},
                   ("umbrales",)),
    "memtest": ("c_memtest", {"en": "send a test memory alert", "es": "envía una alerta de memoria de prueba"}, ()),
    "stealer": ("c_stealer", {"en": "scan persistence points now", "es": "revisa los puntos de persistencia ahora"},
                ("robo",)),
    "help": ("c_help", {"en": "list of commands", "es": "lista de comandos"}, ("start", "ayuda")),
}

TEXT = {
    "en": {
        "no_photos": "📷 No photos saved.", "nothing": "📷 Nothing to delete.",
        "unknown": "Unknown command.", "title": "👁 Wachiman",
        "footer": "Answers only in this chat. Commands are fixed.",
        "internal": "💥 internal error: %s",
        "lock_title": "Lock the screen", "shield_locked": "shield: locked ✅", "shield_open": "shield: NOT locked ⚠️",
        "shield_none": "no fresh shield reading: the guard loop is not running (or WACHIMAN_GUARD_DEBUG is off), "
                       "so only ioreg's weaker verdict is available",
        "confirm": "⚠️ You are about to delete <b>%d photos</b>. Send /deletephotos again within 2 minutes to confirm.",
        "deleted": "🗑 Deleted %d photos.",
        "photos": "📷 <b>%d photos</b> in %d bursts:", "photos_hint": "/last sends the latest burst · /deletephotos deletes all",
        "burst": "📷 Burst %s (%d photos)", "n_photos": "%d photo(s)",
        "mem_title": "🧠 <b>Memory now</b> — %s", "healthy": "🟢 healthy", "tight": "🟡 tight", "struggling": "🔴 struggling",
        "mem_body": "pressure: <b>%s%% free</b> (the signal that matters) · free: %s MB\n"
                    "swap used: %s MB <i>(high-water mark, does not go down until reboot)</i>",
        "top": "<b>Top 5 by RSS:</b>", "nodata": "no data",
        "thresholds": "🎚 <b>Memory thresholds</b>\n%s\n<i>swap used never triggers anything: it is a high-water mark</i>",
        "started": "gateway started",
    },
    "es": {
        "no_photos": "📷 No hay fotos guardadas.", "nothing": "📷 No hay nada que borrar.",
        "unknown": "No conozco ese comando.", "title": "👁 Wachiman",
        "footer": "Solo responde en este chat. Los comandos son fijos.",
        "internal": "💥 error interno: %s",
        "lock_title": "Bloquear pantalla", "shield_locked": "escudo: bloqueada ✅", "shield_open": "escudo: NO bloqueada ⚠️",
        "shield_none": "sin lectura fresca del escudo: el lazo del Guard no corre (o WACHIMAN_GUARD_DEBUG está apagado), "
                       "solo hay el veredicto más débil de ioreg",
        "confirm": "⚠️ Vas a borrar <b>%d fotos</b>. Repite /deletephotos en los próximos 2 minutos para confirmar.",
        "deleted": "🗑 Borradas %d fotos.",
        "photos": "📷 <b>%d fotos</b> en %d ráfagas:", "photos_hint": "/last envía la última ráfaga · /deletephotos borra todas",
        "burst": "📷 Ráfaga %s (%d fotos)", "n_photos": "%d foto(s)",
        "mem_title": "🧠 <b>Memoria ahora</b> — %s", "healthy": "🟢 sana", "tight": "🟡 justa", "struggling": "🔴 en apuros",
        "mem_body": "presión: <b>%s%% libre</b> (la señal que importa) · libre: %s MB\n"
                    "swap acumulado: %s MB <i>(marca de agua, no baja hasta reiniciar)</i>",
        "top": "<b>Top 5 por RSS:</b>", "nodata": "sin datos",
        "thresholds": "🎚 <b>Umbrales de memoria</b>\n%s\n<i>el swap acumulado nunca dispara nada: es marca de agua</i>",
        "started": "gateway arrancado",
    },
}


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def pre(text: str) -> str:
    text = text if len(text) <= MAX_CHARS else "…\n" + text[-MAX_CHARS:]
    return "<pre>%s</pre>" % html_escape(text)


class Transport:
    """Telegram Bot API over urllib. The token lives only in the request URL."""

    def __init__(self, token: str, chat: str, base: str = API) -> None:
        self._url = "%s/bot%s" % (base, token)
        self.chat = str(chat)

    def __repr__(self) -> str:
        return "Transport(chat=%s)" % self.chat

    def call(self, method: str, data: Mapping[str, Any], timeout: int = 70) -> Any:
        body = urllib.parse.urlencode(data).encode()
        with urllib.request.urlopen(urllib.request.Request("%s/%s" % (self._url, method), data=body),
                                    timeout=timeout) as r:
            return json.load(r)

    def send_photo(self, path: str, caption: str = "") -> None:
        boundary = "----wachiman%d" % int(time.time() * 1000)
        blob = Path(path).read_bytes()
        parts: List[bytes] = []
        for k, v in (("chat_id", self.chat), ("caption", caption)):
            parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v)).encode())
        parts.append(('--%s\r\nContent-Disposition: form-data; name="photo"; filename="%s"\r\n'
                      'Content-Type: image/jpeg\r\n\r\n' % (boundary, os.path.basename(path))).encode())
        parts.append(blob + b"\r\n")
        parts.append(("--%s--\r\n" % boundary).encode())
        req = urllib.request.Request(self._url + "/sendPhoto", data=b"".join(parts),
                                     headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
        urllib.request.urlopen(req, timeout=90).read()


def run_command(argv: List[str], timeout: int = 60) -> str:
    """Run a fixed argv (never a shell string) and return its output."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return ((p.stdout or "") + (p.stderr or "")).strip() or "(no output, exit=%d)" % p.returncode
    except subprocess.TimeoutExpired:
        return "⏱ the command did not finish in %ds" % timeout
    except Exception as exc:  # noqa: BLE001
        return "error running command: %s" % exc


class Gateway:
    def __init__(self, settings: Mapping[str, str], home: Path, repo: Path, transport: Any,
                 runner: Callable[..., str] = run_command, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.settings = dict(settings)
        self.home, self.repo = Path(home), Path(repo)
        self.chat = str(self.settings.get("WACHIMAN_TELEGRAM_CHAT_ID", ""))
        lang = self.settings.get("WACHIMAN_LANG", "en")
        self.lang = lang if lang in TEXT else "en"
        self.t = transport
        self.runner, self.clock, self.sleep = runner, clock, sleep
        self._pending_delete: Dict[str, float] = {}
        self._lookup: Dict[str, str] = {}
        for name, (handler, _desc, aliases) in COMMANDS.items():
            for n in (name,) + tuple(aliases):
                self._lookup[n] = handler

    # ----------------------------------------------------------- plumbing
    def log(self, msg: str) -> None:
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            with open(str(self.home / "gateway.log"), "a") as fh:
                fh.write("%s %s\n" % (time.strftime("%F %T"), msg))
        except OSError:
            pass

    def tx(self, key: str) -> str:
        return TEXT[self.lang][key]

    def send(self, text: str, chat: Optional[str] = None) -> None:
        try:
            self.t.call("sendMessage", {"chat_id": chat or self.chat, "text": text, "parse_mode": "HTML",
                                        "disable_web_page_preview": "true"}, timeout=30)
        except Exception as exc:  # noqa: BLE001
            self.log("send failed: %s" % type(exc).__name__)

    def _run(self, path: str, *args: str, timeout: int = 60) -> str:
        return self.runner(["/bin/bash", str(self.repo / "modules" / path)] + list(args), timeout=timeout)

    def tail(self, path: Path, n: int) -> str:
        try:
            return "\n".join(path.read_text(errors="replace").splitlines()[-n:]) or "(empty)"
        except OSError:
            return "(no file yet)"

    def menu(self) -> List[Dict[str, str]]:
        return [{"command": k, "description": v[1][self.lang][:60]} for k, v in COMMANDS.items()]

    def help_text(self) -> str:
        lines = ["/%s — %s" % (c["command"], html_escape(c["description"])) for c in self.menu()]
        return "<b>%s</b>\n%s\n\n<i>%s</i>" % (self.tx("title"), "\n".join(lines), self.tx("footer"))

    def photos(self) -> List[Path]:
        d = self.home / "guard"
        return sorted(p for p in d.glob("*.jpg") if BURST.match(p.name)) if d.is_dir() else []

    # ----------------------------------------------------------- commands
    def c_help(self, chat: str) -> None:
        self.send(self.help_text(), chat)

    def c_status(self, chat: str) -> None:
        body = self._run("guard/guardctl.sh", "status")
        self.send("👁 <b>Guard</b>\n" + pre(body) + "\n" + pre(self.tail(self.home / "guard" / "guard.debug", 1)), chat)

    def c_on(self, chat: str) -> None:
        self.send("🔒 <b>Start and lock</b>\n" + pre(self._run("guard/guardctl.sh", "start", timeout=90)), chat)

    def c_test(self, chat: str) -> None:
        self.send("🧪 <b>Test start (no lock)</b>\n" + pre(self._run("guard/guardctl.sh", "start-nolock", timeout=90)), chat)

    def c_off(self, chat: str) -> None:
        self.send("🛑 <b>Stopping</b>\n" + pre(self._run("guard/guardctl.sh", "stop")), chat)

    def c_lock(self, chat: str) -> None:
        out = self._run("guard/lock.sh", timeout=40)
        # ioreg (what lock.sh reads) is unreliable on macOS 27; the loginwindow shield, which
        # the guard loop writes to guard.debug, is the figure to trust.
        dbg = self.home / "guard" / "guard.debug"
        real = self.tx("shield_none")
        if dbg.exists() and time.time() - dbg.stat().st_mtime < 60:
            self.sleep(4)
            m = re.search(r"locked=(\d)", self.tail(dbg, 1))
            if m:
                real = self.tx("shield_locked") if m.group(1) == "1" else self.tx("shield_open")
        icon = "🔒" if "LOCKED" in out and "NOT_LOCKED" not in out else "⚠️"
        self.send("%s <b>%s</b>\n%s\n%s" % (icon, self.tx("lock_title"), pre(out), html_escape(real)), chat)

    def c_log(self, chat: str) -> None:
        self.send("📜 <b>guard.log</b>\n" + pre(self.tail(self.home / "guard" / "guard.log", 20)), chat)

    def c_debug(self, chat: str) -> None:
        self.send("🔍 <b>guard.debug</b>\n" + pre(self.tail(self.home / "guard" / "guard.debug", 10)), chat)

    def c_photos(self, chat: str) -> None:
        ps = self.photos()
        if not ps:
            self.send(self.tx("no_photos"), chat)
            return
        bursts: Dict[str, int] = {}
        for p in ps:
            bursts[p.name[:19]] = bursts.get(p.name[:19], 0) + 1
        lines = ["• %s — %s" % (k, self.tx("n_photos") % v) for k, v in sorted(bursts.items(), reverse=True)]
        self.send(self.tx("photos") % (len(ps), len(bursts)) + "\n" + "\n".join(lines[:15]) + "\n\n" + self.tx("photos_hint"),
                  chat)

    def c_last(self, chat: str) -> None:
        ps = self.photos()
        if not ps:
            self.send(self.tx("no_photos"), chat)
            return
        stamp = ps[-1].name[:19]
        last = [p for p in ps if p.name.startswith(stamp)]
        self.send(self.tx("burst") % (stamp, len(last)), chat)
        for p in last:
            try:
                self.t.send_photo(str(p), p.name)
            except Exception as exc:  # noqa: BLE001
                self.log("sendPhoto failed: %s" % type(exc).__name__)

    def c_delete(self, chat: str) -> None:
        ps = self.photos()
        if not ps:
            self.send(self.tx("nothing"), chat)
            return
        now = self.clock()
        if now - self._pending_delete.get(chat, -1e9) > CONFIRM_WINDOW:
            self._pending_delete[chat] = now
            self.send(self.tx("confirm") % len(ps), chat)
            return
        self._pending_delete.pop(chat, None)
        for p in ps:
            try:
                p.unlink()
            except OSError:
                pass
        self.log("deleted %d photos on the order of chat %s" % (len(ps), chat))
        self.send(self.tx("deleted") % len(ps), chat)

    def _mem_lines(self) -> List[str]:
        return self._run("memwatch/memwatch.sh", "--dry").splitlines()

    def c_mem(self, chat: str) -> None:
        out = self._mem_lines()
        if not out:
            self.send(self.tx("nodata"), chat)
            return
        swap, free, press = (out[0].split() + ["?", "?", "?"])[:3]
        try:
            p = int(press)
            verdict = self.tx("healthy") if p > 25 else (self.tx("tight") if p > 10 else self.tx("struggling"))
        except ValueError:
            verdict = "?"
        self.send("%s\n%s\n%s\n%s" % (self.tx("mem_title") % verdict, self.tx("mem_body") % (press, free, swap),
                                      self.tx("top"), html_escape("\n".join(out[1:]))), chat)

    def c_top(self, chat: str) -> None:
        self.send(self.tx("top") + "\n" + html_escape("\n".join(self._mem_lines()[1:])), chat)

    def c_thresholds(self, chat: str) -> None:
        keys = ("PRESSURE_WARN", "PRESSURE_CRIT", "SWAPOUT_WARN_MBS", "SWAPOUT_GATE_PRESS", "RECOVER_MARGIN", "POLL",
                "COOLDOWN")
        defaults = dict(zip(keys, (20, 10, 25, 35, 5, 20, 900)))
        rows = ["%s = %s" % (k.lower(), self.settings.get("WACHIMAN_MEM_" + k, defaults[k])) for k in keys]
        self.send(self.tx("thresholds") % pre("\n".join(rows)), chat)

    def c_memtest(self, chat: str) -> None:
        self.send("🧪 " + pre(self._run("memwatch/memwatch.sh", "--test")), chat)

    def c_stealer(self, chat: str) -> None:
        self.send("🛡 <b>stealerwatch</b>\n" + pre(self._run("stealerwatch/stealerwatch.sh", "--dry", timeout=120)), chat)

    # ----------------------------------------------------------- dispatch
    def handle(self, text: str, chat: str) -> None:
        words = text.strip().split()
        cmd = words[0].lstrip("/").split("@")[0].lower() if words and words[0].startswith("/") else ""
        if cmd == "":
            self.c_help(chat)
            return
        handler = self._lookup.get(cmd)
        if handler is None:
            self.send(self.tx("unknown") + "\n\n" + self.help_text(), chat)
            return
        self.log("command /%s from chat %s" % (cmd, chat))
        getattr(self, handler)(chat)

    def process_update(self, upd: Mapping[str, Any]) -> None:
        msg = upd.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        text = msg.get("text") or ""
        if chat != self.chat:
            self.log("ignored foreign chat %s" % chat)
            return
        if not text:
            return
        try:
            self.handle(text, chat)
        except Exception as exc:  # noqa: BLE001
            self.log("handler error: %s" % type(exc).__name__)
            self.send(self.tx("internal") % html_escape(str(exc)), chat)

    def register_commands(self) -> None:
        try:
            self.t.call("setMyCommands", {"commands": json.dumps(self.menu())}, timeout=30)
        except Exception as exc:  # noqa: BLE001
            self.log("setMyCommands failed: %s" % type(exc).__name__)

    def run(self, max_polls: int = 0) -> None:
        offset, polls = 0, 0
        self.register_commands()
        self.log("%s pid=%d" % (self.tx("started"), os.getpid()))
        while True:
            try:
                res = self.t.call("getUpdates", {"offset": offset, "timeout": 50,
                                                 "allowed_updates": json.dumps(["message"])}, timeout=70)
            except urllib.error.HTTPError as exc:
                self.log("getUpdates HTTP %d" % exc.code)
                self.sleep(10)
                res = {}
            except Exception as exc:  # noqa: BLE001
                self.log("getUpdates %s" % type(exc).__name__)
                self.sleep(10)
                res = {}
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                self.process_update(upd)
            polls += 1
            if max_polls and polls >= max_polls:
                return


def from_settings(settings: Mapping[str, str], home: Path, repo: Path) -> Gateway:
    token = settings.get("WACHIMAN_TELEGRAM_BOT_TOKEN", "")
    chat = settings.get("WACHIMAN_TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise ConfigError("set WACHIMAN_TELEGRAM_BOT_TOKEN and WACHIMAN_TELEGRAM_CHAT_ID "
                          "(in $WACHIMAN_HOME/config.env or the environment)")
    base = settings.get("WACHIMAN_TELEGRAM_API") or API
    return Gateway(settings, home, repo, Transport(token, chat, base))


def main(settings: Mapping[str, str], home: Path, repo: Path, once: Optional[str] = None,
         env: Optional[Mapping[str, str]] = None) -> int:
    try:
        gw = from_settings(settings, home, repo)
    except ConfigError as exc:
        print("gateway: %s" % exc)
        return 1
    if once:
        gw.handle(once, gw.chat)
        return 0
    gw.run()
    return 0


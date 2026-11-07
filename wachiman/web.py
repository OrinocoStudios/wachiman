"""Read-only local page with the radar history.

    wachiman web [--port 8765]     then open http://127.0.0.1:8765

Safety by construction: it binds to loopback only (anything else is refused),
answers GET only, has no write endpoints and no CORS, rejects requests whose
Host header is not loopback (DNS rebinding), and the page loads nothing from
outside and writes data into the document with textContent only.

JSON API: /api/summary /api/events /api/connections /api/traffic /api/sensors
/api/keyboard /api/devices /api/ui, most taking ?since=24h (m, h, d).
"""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Mapping, Optional, Tuple
from urllib.parse import parse_qsl, urlsplit

from . import notify, paths
from .radar import sensors as sensors_mod
from .radar import store
from .radar.traffic import human

UNITS = {"m": 60, "h": 3600, "d": 86400}
LOOPBACK = {"127.0.0.1": "127.0.0.1", "localhost": "127.0.0.1"}
HOSTS_OK = {"127.0.0.1", "localhost", "[::1]"}
DEFAULT_LIMIT = 500

UI = {
    "en": {
        "title": "Wachiman", "subtitle": "What your computer does when you are not looking",
        "worth": "Worth a look", "nothing": "Nothing to show here.", "loading": "Loading…",
        "window": "Show", "h24": "24 hours", "d7": "7 days", "d30": "30 days",
        "tab_net": "Network", "tab_up": "Uploads", "tab_sensors": "Mic and camera",
        "tab_kb": "Keyboard readers", "tab_dev": "Devices",
        "net_help": "Which app talked to which company, and in which country that company is registered.",
        "up_help": "How much each app sent to and received from the internet (a lower bound).",
        "sensors_help": "Which app used the microphone, camera or screen recording, and for how long.",
        "kb_help": "Programs that can read what you type. Launchers and accessibility tools do this legitimately.",
        "dev_help": "USB devices, paired Bluetooth devices and networks seen.",
        "app": "App", "company": "Company", "country": "Country", "last": "Last seen", "times": "Times", "ips": "IPs",
        "sent": "Sent", "received": "Received", "when": "Started", "what": "What", "for": "For", "now": "now",
        "program": "Program", "signed": "Signed by", "can_change": "Can change keys", "yes": "yes", "no": "no",
        "kind": "Type", "name": "Name", "vendor": "Maker / type", "first": "First seen",
        "error": "Could not load data", "private": "Private: only visible on this computer.",
    },
    "es": {
        "title": "Wachiman", "subtitle": "Lo que hace tu ordenador cuando no miras",
        "worth": "Merece una mirada", "nothing": "Nada que mostrar aquí.", "loading": "Cargando…",
        "window": "Mostrar", "h24": "24 horas", "d7": "7 días", "d30": "30 días",
        "tab_net": "Red", "tab_up": "Subidas", "tab_sensors": "Micrófono y cámara",
        "tab_kb": "Lectores de teclado", "tab_dev": "Dispositivos",
        "net_help": "Qué app habló con qué empresa, y en qué país está registrada esa empresa.",
        "up_help": "Cuánto envió y recibió cada app de internet (una cifra mínima).",
        "sensors_help": "Qué app usó el micrófono, la cámara o la grabación de pantalla, y durante cuánto.",
        "kb_help": "Programas que pueden leer lo que tecleas. Lanzadores y herramientas de accesibilidad lo hacen con motivo.",
        "dev_help": "Dispositivos USB, Bluetooth emparejados y redes vistas.",
        "app": "App", "company": "Empresa", "country": "País", "last": "Última vez", "times": "Veces", "ips": "IPs",
        "sent": "Enviado", "received": "Recibido", "when": "Inicio", "what": "Qué", "for": "Durante", "now": "ahora",
        "program": "Programa", "signed": "Firmado por", "can_change": "Puede cambiar teclas", "yes": "sí", "no": "no",
        "kind": "Tipo", "name": "Nombre", "vendor": "Fabricante / tipo", "first": "Primera vez",
        "error": "No se pudieron cargar los datos", "private": "Privado: solo visible en este ordenador.",
    },
}

HEADLINE = {
    "en": {"none": "In the last {w}: nothing new. All quiet.", "one": "In the last {w}: 1 new thing worth a look.",
           "many": "In the last {w}: {n} new things worth a look."},
    "es": {"none": "En los últimos {w}: nada nuevo. Todo tranquilo.",
           "one": "En los últimos {w}: 1 cosa nueva que merece una mirada.",
           "many": "En los últimos {w}: {n} cosas nuevas que merecen una mirada."},
}
UNIT_NAMES = {"m": "min", "h": "h", "d": "d"}


def _lang(lang: str) -> str:
    return lang if lang in UI else "en"


def parse_since(text: str, now: int) -> int:
    t = (text or "").strip().lower()
    if len(t) >= 2 and t[-1] in UNITS and t[:-1].isdigit() and int(t[:-1]) > 0:
        return now - int(t[:-1]) * UNITS[t[-1]]
    raise ValueError("since: use e.g. 30m, 24h, 7d")


def _window(text: str) -> str:
    return "%s %s" % (text[:-1], UNIT_NAMES[text[-1]])


def _event(row) -> Tuple[store.Event, dict]:
    return store.Event(row["ts"], row["kind"], row["app"], row["remote_ip"], row["country"], row["org"],
                       row["bytes_out"], row["sensor"], row["signer"]), {}


def handle(db: store.Store, path: str, query: Mapping[str, str], now: int, lang: str = "en") -> Tuple[int, dict]:
    """Pure API: (status, JSON-able body). Knows nothing about HTTP."""
    lang = _lang(lang)
    name = path.rstrip("/")
    default_since = {"/api/sensors": "7d"}.get(name, "24h")
    since_text = query.get("since") or default_since
    try:
        since = parse_since(since_text, now)
        limit = max(1, min(5000, int(query.get("limit") or DEFAULT_LIMIT)))
    except ValueError:
        return 400, {"error": "bad since or limit (use e.g. since=24h)"}

    if name == "/api/ui":
        return 200, {"lang": lang, "ui": UI[lang]}

    if name == "/api/events":
        out = []
        for r in db.events(since=since):
            ev, _ = _event(r)
            out.append({"ts": r["ts"], "kind": r["kind"], "app": r["app"], "text": notify.message(ev, lang)})
        return 200, {"events": out[:limit], "truncated": len(out) > limit}

    if name == "/api/summary":
        events = db.events(since=since)
        n = len(events)
        key = "none" if n == 0 else ("one" if n == 1 else "many")
        return 200, {
            "since": since_text, "alerts": n,
            "headline": HEADLINE[lang][key].format(w=_window(since_text), n=n),
            "counts": {
                "apps": len({r["app"] for r in db.connections(since=since)}),
                "uploaded": human(sum(r["bytes_out"] for r in db.traffic(since=since))),
                "sensor_sessions": len(db.sensor_sessions(since=since)),
                "keyboard_readers": len(db.keyboard_taps()),
                "devices": len(db.devices()),
            },
        }

    if name == "/api/connections":
        rows = db.connections(since=since, app=query.get("app") or None)
        if query.get("group") == "1":
            groups = {}
            for r in rows:
                key = (r["app"], r["org"] or "", r["country"] or "")
                g = groups.setdefault(key, {"app": r["app"], "org": r["org"], "country": r["country"],
                                            "country_name": notify.country_name(r["country"], lang)
                                            if r["country"] else "",
                                            "ips": set(), "connections": 0, "samples": 0,
                                            "first_seen": r["first_seen"], "last_seen": r["last_seen"]})
                g["ips"].add(r["remote_ip"])
                g["connections"] += 1
                g["samples"] += r["samples"]
                g["first_seen"] = min(g["first_seen"], r["first_seen"])
                g["last_seen"] = max(g["last_seen"], r["last_seen"])
            out = sorted(groups.values(), key=lambda g: (-g["last_seen"], g["app"].lower(), g["org"] or ""))
            for g in out:
                g["ips"] = len(g["ips"])
            return 200, {"connections": out[:limit], "truncated": len(out) > limit}
        return 200, {"connections": [
            {"app": r["app"], "remote_ip": r["remote_ip"], "remote_port": r["remote_port"], "proto": r["proto"],
             "country": r["country"], "country_name": notify.country_name(r["country"], lang) if r["country"] else "",
             "org": r["org"], "first_seen": r["first_seen"], "last_seen": r["last_seen"], "samples": r["samples"]}
            for r in rows[:limit]], "truncated": len(rows) > limit}

    if name == "/api/traffic":
        return 200, {"traffic": [
            {"app": r["app"], "bytes_in": r["bytes_in"], "bytes_out": r["bytes_out"],
             "sent": human(r["bytes_out"]), "received": human(r["bytes_in"])} for r in db.traffic(since=since)[:limit]]}

    if name == "/api/sensors":
        out = []
        for r in db.sensor_sessions(since=since)[:limit]:
            active = r["end"] is None
            out.append({"kind": r["kind"], "label": sensors_mod.label(r["kind"], lang), "app": r["app"],
                        "bundle": r["bundle"], "start": r["start"], "end": r["end"], "active": active,
                        "seconds": max(0, (now if active else r["end"]) - r["start"])})
        return 200, {"sensors": out}

    if name == "/api/keyboard":
        return 200, {"keyboard": [
            {"app": r["app"], "path": r["path"], "signer": r["signer"],
             "signer_label": notify.signer_label(r["signer"], lang), "can_modify": bool(r["can_modify"]),
             "first_seen": r["first_seen"], "last_seen": r["last_seen"]} for r in db.keyboard_taps()]}

    if name == "/api/devices":
        kind = query.get("kind") or None
        if kind not in (None, "usb", "bt", "net"):
            return 400, {"error": "kind must be usb, bt or net"}
        return 200, {"devices": [
            {"kind": r["kind"], "kind_label": notify.DEVICES[lang].get(r["kind"], r["kind"]), "name": r["name"],
             "vendor": r["vendor"], "first_seen": r["first_seen"], "last_seen": r["last_seen"]}
            for r in db.devices(kind=kind)]}

    return 404, {"error": "not found"}


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wachiman</title>
<style>
:root{--bg:#fff;--fg:#1c1e21;--mute:#65676b;--card:#f4f5f7;--line:#dcdfe4;--accent:#0b6bcb;--warn:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e6e8eb;--mute:#9aa0a6;--card:#202327;--line:#33373d;--accent:#6cb2ff;--warn:#f0b35a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}
h1{margin:0;font-size:1.6rem}.sub{color:var(--mute);margin:0 0 20px}
#headline{font-size:1.4rem;font-weight:600;margin:16px 0}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
button{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer}
button[aria-pressed=true],button.on{background:var(--accent);color:var(--bg);border-color:var(--accent)}
.alert{background:var(--card);border-left:4px solid var(--warn);border-radius:6px;padding:10px 14px;margin:8px 0;white-space:pre-wrap}
.alert time{display:block;color:var(--mute);font-size:.85rem}
nav{display:flex;gap:6px;flex-wrap:wrap;margin:28px 0 8px}
.help{color:var(--mute);margin:4px 0 12px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mute);font-weight:600;font-size:.85rem}
.empty{color:var(--mute);padding:12px 0}
footer{color:var(--mute);margin-top:32px;font-size:.85rem}
</style></head><body><main>
<h1 id="title"></h1><p class="sub" id="subtitle"></p>
<div class="bar"><span id="lbl-window"></span><span id="windows"></span></div>
<div id="headline"></div>
<h2 id="worth"></h2><div id="alerts"></div>
<nav id="tabs"></nav><div id="help" class="help"></div><div class="scroll" id="panel"></div>
<footer id="private"></footer>
</main>
<script>
"use strict";
var UI = {}, since = "24h", tab = "net", lang = "en";
function el(tag, text, cls) {
  var n = document.createElement(tag);
  if (text !== undefined && text !== null) n.textContent = String(text);
  if (cls) n.className = cls;
  return n;
}
function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
function api(path) {
  return fetch(path, {cache: "no-store"}).then(function (r) {
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  });
}
function fmt(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString(lang, {dateStyle: "short", timeStyle: "short"});
}
function dur(s) {
  var m = Math.floor(s / 60), h = Math.floor(m / 60);
  return h ? h + " h " + (m % 60) + " min" : (m ? m + " min" : s + " s");
}
function table(cols, rows) {
  if (!rows.length) return el("div", UI.nothing, "empty");
  var t = el("table"), hr = el("tr");
  cols.forEach(function (c) { hr.appendChild(el("th", c[0])); });
  t.appendChild(hr);
  rows.forEach(function (r) {
    var tr = el("tr");
    cols.forEach(function (c) { tr.appendChild(el("td", c[1](r))); });
    t.appendChild(tr);
  });
  return t;
}
var TABS = {
  net: {label: "tab_net", help: "net_help", path: function () { return "/api/connections?group=1&since=" + since; },
    rows: function (d) { return d.connections; },
    cols: function () { return [[UI.app, function (r) { return r.app; }], [UI.company, function (r) { return r.org || "?"; }],
      [UI.country, function (r) { return r.country_name || r.country || "?"; }], [UI.last, function (r) { return fmt(r.last_seen); }],
      [UI.ips, function (r) { return r.ips; }], [UI.times, function (r) { return r.samples; }]]; }},
  up: {label: "tab_up", help: "up_help", path: function () { return "/api/traffic?since=" + since; },
    rows: function (d) { return d.traffic; },
    cols: function () { return [[UI.app, function (r) { return r.app; }], [UI.sent, function (r) { return r.sent; }],
      [UI.received, function (r) { return r.received; }]]; }},
  sensors: {label: "tab_sensors", help: "sensors_help", path: function () { return "/api/sensors?since=" + since; },
    rows: function (d) { return d.sensors; },
    cols: function () { return [[UI.when, function (r) { return fmt(r.start); }], [UI.what, function (r) { return r.label; }],
      [UI.app, function (r) { return r.app; }], [UI["for"], function (r) { return dur(r.seconds) + (r.active ? " (" + UI.now + ")" : ""); }]]; }},
  kb: {label: "tab_kb", help: "kb_help", path: function () { return "/api/keyboard"; },
    rows: function (d) { return d.keyboard; },
    cols: function () { return [[UI.program, function (r) { return r.app; }], [UI.signed, function (r) { return r.signer_label; }],
      [UI.can_change, function (r) { return r.can_modify ? UI.yes : UI.no; }], [UI.first, function (r) { return fmt(r.first_seen); }],
      [UI.last, function (r) { return fmt(r.last_seen); }]]; }},
  dev: {label: "tab_dev", help: "dev_help", path: function () { return "/api/devices"; },
    rows: function (d) { return d.devices; },
    cols: function () { return [[UI.kind, function (r) { return r.kind_label; }], [UI.name, function (r) { return r.name; }],
      [UI.vendor, function (r) { return r.vendor || "-"; }], [UI.first, function (r) { return fmt(r.first_seen); }],
      [UI.last, function (r) { return fmt(r.last_seen); }]]; }}
};
function fail(node) { clear(node); node.appendChild(el("div", UI.error, "empty")); }
function buttons(box, items, current, onPick) {
  clear(box);
  items.forEach(function (it) {
    var b = el("button", it[1]);
    b.setAttribute("aria-pressed", it[0] === current ? "true" : "false");
    b.addEventListener("click", function () { onPick(it[0]); });
    box.appendChild(b);
  });
}
function drawChrome() {
  document.title = UI.title;
  document.getElementById("title").textContent = UI.title;
  document.getElementById("subtitle").textContent = UI.subtitle;
  document.getElementById("lbl-window").textContent = UI.window;
  document.getElementById("worth").textContent = UI.worth;
  document.getElementById("private").textContent = UI.private;
  buttons(document.getElementById("windows"), [["24h", UI.h24], ["7d", UI.d7], ["30d", UI.d30]], since,
    function (v) { since = v; refresh(); });
  buttons(document.getElementById("tabs"), Object.keys(TABS).map(function (k) { return [k, UI[TABS[k].label]]; }), tab,
    function (v) { tab = v; drawChrome(); drawTab(); });
  document.getElementById("help").textContent = UI[TABS[tab].help];
}
function drawTab() {
  var t = TABS[tab], panel = document.getElementById("panel");
  clear(panel);
  panel.appendChild(el("div", UI.loading, "empty"));
  api(t.path()).then(function (d) {
    clear(panel);
    panel.appendChild(table(t.cols(), t.rows(d)));
  }).catch(function () { fail(panel); });
}
function drawTop() {
  var head = document.getElementById("headline"), box = document.getElementById("alerts");
  api("/api/summary?since=" + since).then(function (d) { head.textContent = d.headline; }).catch(function () { fail(head); });
  api("/api/events?since=" + since).then(function (d) {
    clear(box);
    if (!d.events.length) box.appendChild(el("div", UI.nothing, "empty"));
    d.events.forEach(function (e) {
      var a = el("div", e.text, "alert");
      a.insertBefore(el("time", fmt(e.ts)), a.firstChild);
      box.appendChild(a);
    });
  }).catch(function () { fail(box); });
}
function refresh() { drawChrome(); drawTop(); drawTab(); }
api("/api/ui").then(function (d) { UI = d.ui; lang = d.lang; refresh(); })
  .catch(function () { document.getElementById("headline").textContent = "Wachiman: error"; });
</script></body></html>
"""

CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _host_ok(header: Optional[str]) -> bool:
    if not header:
        return False
    host = header.rsplit(":", 1)[0] if not header.endswith("]") and ":" in header else header
    return host in HOSTS_OK


def make_server(host: str, port: int, env: Optional[Mapping[str, str]] = None,
                clock: Callable[[], float] = time.time) -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise ValueError("refusing to listen on %r: the web view is private and only binds to 127.0.0.1" % host)
    import os
    env_map = os.environ if env is None else env

    class Handler(BaseHTTPRequestHandler):
        server_version = "Wachiman"
        sys_version = ""

        def log_message(self, fmt, *args):  # quiet: paths could contain app names
            pass

        def _send(self, status: int, ctype: str, body: bytes, head_only: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _json(self, status: int, data: dict, head_only: bool = False) -> None:
            self._send(status, "application/json; charset=utf-8", json.dumps(data).encode("utf-8"), head_only)

        def _get(self, head_only: bool = False) -> None:
            if not _host_ok(self.headers.get("Host")):
                return self._json(403, {"error": "forbidden host"}, head_only)
            url = urlsplit(self.path)
            if url.path == "/":
                return self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"), head_only)
            if not url.path.startswith("/api/"):
                return self._json(404, {"error": "not found"}, head_only)
            query = dict(parse_qsl(url.query))
            lang = paths.settings(env_map).get("WACHIMAN_LANG", "en")
            db = store.Store(paths.db_path(env_map))
            try:
                status, body = handle(db, url.path, query, int(clock()), lang)
            except Exception:
                status, body = 500, {"error": "internal error"}
            finally:
                db.close()
            self._json(status, body, head_only)

        def do_GET(self):
            self._get()

        def do_HEAD(self):
            self._get(head_only=True)

        def _no(self):
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no

    return ThreadingHTTPServer((LOOPBACK[host], port), Handler)


def serve(host: str, port: int, env: Optional[Mapping[str, str]] = None) -> int:
    server = make_server(host, port, env)
    print("Wachiman history: http://127.0.0.1:%d   (Ctrl+C to stop)" % server.server_address[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0

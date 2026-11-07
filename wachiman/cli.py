"""wachiman command line.

    wachiman radar scan                 take one sample, store it, alert on news
    wachiman radar watch                scan forever every WACHIMAN_RADAR_INTERVAL seconds
    wachiman radar net [--since 24h] [--app NAME]
                                        history: which app reached which company/country
    wachiman radar traffic [--since 24h]
                                        bytes sent and received per app
    wachiman radar sensors [--since 7d] who used the mic, camera or screen recording
    wachiman radar keyboard             programs seen reading the keyboard, and who signed them
    wachiman radar devices [--kind usb|bt|net]
                                        USB devices, paired Bluetooth devices and networks seen
    wachiman radar events [--since 7d]  what was worth an alert
    wachiman radar geo-update           download/refresh the offline IP owner database
    wachiman service install|uninstall|status [--agent radar|gateway|memwatch|stealerwatch]
                                        run a watcher in the background (macOS launchd)
    wachiman guard start|start-nolock|stop|status
                                        screen-lock Guard: alert + webcam photos on activity while locked
    wachiman stealerwatch scan|baseline|watch|test
                                        persistence points, stealer artefacts, known C2 domains
    wachiman memwatch now|watch|test    memory pressure and swapout rate alerts
    wachiman gateway run [--once /CMD]  Telegram command bot for all of the above
    wachiman setup [--non-interactive]  first-run wizard: language, optional Telegram alerts
    wachiman web [--port 8765]          read-only history page on http://127.0.0.1:8765
    wachiman uninstall                  stop the background service and show how to remove the rest
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Mapping, Optional

from . import gateway as gateway_mod
from . import notify, paths, service, setup, web
from .radar import devices, geo, keyboard, net, sensors, store, traffic

UNITS = {"m": 60, "h": 3600, "d": 86400}


def parse_since(text: Optional[str], now: int) -> Optional[int]:
    if not text:
        return None
    text = text.strip().lower()
    if text[-1:] in UNITS and text[:-1].isdigit():
        return now - int(text[:-1]) * UNITS[text[-1]]
    raise ValueError("--since: use e.g. 30m, 24h, 7d")


def _dest(proto: str, ip: str, port: int) -> str:
    host = "[%s]" % ip if ":" in ip else ip
    return "%s %s:%s" % (proto.lower(), host, port)


def _dur(seconds: int) -> str:
    m, s = divmod(max(0, seconds), 60)
    h, m = divmod(m, 60)
    return "%dh%02dm" % (h, m) if h else ("%dm" % m if m else "%ds" % s)


def _fmt_ts(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _table(rows: List[List[str]], headers: List[str]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)] if rows else [len(h) for h in headers]
    line = lambda r: "  ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip()
    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(r) for r in rows])


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wachiman", description="See what your computer does when you are not looking.")
    sub = p.add_subparsers(dest="module")
    radar = sub.add_parser("radar", help="network history per app")
    rsub = radar.add_subparsers(dest="cmd")
    rsub.add_parser("scan", help="one sample now")
    rsub.add_parser("watch", help="scan forever")
    n = rsub.add_parser("net", help="connection history")
    n.add_argument("--since", default="24h")
    n.add_argument("--app")
    t = rsub.add_parser("traffic", help="bytes sent/received per app")
    t.add_argument("--since", default="24h")
    se = rsub.add_parser("sensors", help="mic / camera / screen recording history")
    se.add_argument("--since", default="7d")
    rsub.add_parser("keyboard", help="programs reading the keyboard")
    dv = rsub.add_parser("devices", help="USB / Bluetooth / networks seen")
    dv.add_argument("--kind", choices=["usb", "bt", "net"])
    e = rsub.add_parser("events", help="alerts history")
    e.add_argument("--since", default="7d")
    rsub.add_parser("geo-update", help="download the offline IP owner database")
    svc = sub.add_parser("service", help="run the radar in the background (macOS)")
    svc.add_argument("action", choices=["install", "uninstall", "status"])
    svc.add_argument("--agent", choices=sorted(service.AGENTS), default="radar")
    gd = sub.add_parser("guard", help="alert + webcam photos on activity while the screen is locked")
    gd.add_argument("action", choices=["start", "start-nolock", "stop", "status"])
    sw = sub.add_parser("stealerwatch", help="persistence and stealer traces")
    sw.add_argument("action", choices=["scan", "baseline", "watch", "once", "test"])
    mw = sub.add_parser("memwatch", help="memory pressure alerts")
    mw.add_argument("action", choices=["now", "watch", "test"])
    gw = sub.add_parser("gateway", help="Telegram command bot")
    gw.add_argument("action", choices=["run"])
    gw.add_argument("--once", metavar="CMD", help="run one command (e.g. /status) and exit")
    st = sub.add_parser("setup", help="first-run wizard (language, Telegram)")
    st.add_argument("--non-interactive", action="store_true", help="read values from WACHIMAN_* environment variables")
    wb = sub.add_parser("web", help="local read-only page with the history (127.0.0.1 only)")
    wb.add_argument("--port", type=int, default=8765)
    wb.add_argument("--host", default="127.0.0.1", help=argparse.SUPPRESS)
    sub.add_parser("uninstall", help="stop the background service and explain how to remove the rest")
    return p


REPO = Path(__file__).resolve().parent.parent

SCRIPTS = {  # (module, action) -> (script, fixed argv). Never built from user text.
    ("guard", "start"): ("guard/guardctl.sh", ["start"]),
    ("guard", "start-nolock"): ("guard/guardctl.sh", ["start-nolock"]),
    ("guard", "stop"): ("guard/guardctl.sh", ["stop"]),
    ("guard", "status"): ("guard/guardctl.sh", ["status"]),
    ("stealerwatch", "scan"): ("stealerwatch/stealerwatch.sh", ["--dry"]),
    ("stealerwatch", "baseline"): ("stealerwatch/stealerwatch.sh", ["--baseline"]),
    ("stealerwatch", "watch"): ("stealerwatch/stealerwatch.sh", []),
    ("stealerwatch", "once"): ("stealerwatch/stealerwatch.sh", ["--once"]),
    ("stealerwatch", "test"): ("stealerwatch/stealerwatch.sh", ["--test"]),
    ("memwatch", "now"): ("memwatch/memwatch.sh", ["--dry"]),
    ("memwatch", "watch"): ("memwatch/memwatch.sh", []),
    ("memwatch", "test"): ("memwatch/memwatch.sh", ["--test"]),
}


def _run_script(argv: List[str], env: Mapping[str, str]) -> int:
    try:
        return subprocess.call(argv, env=dict(env))
    except KeyboardInterrupt:
        return 130


def main(argv: Optional[List[str]] = None, env: Optional[Mapping[str, str]] = None,
         sampler: Callable = net.sample, traffic_sampler: Callable = traffic.sample,
         sensor_reader: Callable = sensors.read, keyboard_reader: Callable = keyboard.read,
         device_reader: Callable = devices.read, geo_factory: Callable = geo.Geo, sender: Callable = notify.send,
         clock: Callable[[], float] = time.time, script_runner: Callable = _run_script,
         gateway_runner: Callable = gateway_mod.main) -> int:
    env = os.environ if env is None else env
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if args.module == "service":
        if args.action == "install":
            return service.install(REPO, sys.executable, args.agent)
        return service.uninstall(args.agent) if args.action == "uninstall" else service.status(args.agent)
    if args.module in ("guard", "stealerwatch", "memwatch"):
        script, fixed = SCRIPTS[(args.module, args.action)]
        return int(script_runner(["/bin/bash", str(REPO / "modules" / script)] + fixed, env))
    if args.module == "gateway":
        return int(gateway_runner(paths.settings(env), paths.home(env), REPO, args.once, env))
    if args.module == "setup":
        return setup.run(env, non_interactive=args.non_interactive)
    if args.module == "web":
        try:
            return web.serve(args.host, args.port, env)
        except ValueError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
    if args.module == "uninstall":
        code = 0
        for agent in sorted(service.AGENTS):
            code = service.uninstall(agent) or code
        home = paths.home(env)
        print("Your history and settings are still in %s" % home)
        print("To remove everything: rm -rf '%s'" % home)
        print("Also remove the command link if present: rm -f ~/.local/bin/wachiman")
        return code
    if args.module != "radar" or not args.cmd:
        parser.print_help()
        return 2

    settings = paths.settings(env)
    lang = settings.get("WACHIMAN_LANG", "en")
    now = int(clock())

    if args.cmd == "geo-update":
        dest = paths.geo_db_path(env)
        tmp = dest.with_name("ip2asn.tsv.gz")
        print("downloading %s ..." % geo.SOURCE_URL)
        geo.download(tmp)
        n = geo.build(tmp, dest)
        tmp.unlink()
        print("ok: %d ranges in %s" % (n, dest))
        return 0

    db = store.Store(paths.db_path(env))
    try:
        if args.cmd in ("scan", "watch"):
            g = geo_factory(paths.geo_db_path(env))
            if not g.available:
                print("note: no IP owner database yet, run `wachiman radar geo-update`", file=sys.stderr)
            try:
                interval = max(5, int(settings.get("WACHIMAN_RADAR_INTERVAL", "30") or 30))
                upload_limit = int(float(settings.get("WACHIMAN_UPLOAD_ALERT_MB", "100") or 0) * 1024 * 1024)
                while True:
                    first = not db._baseline_done()
                    sample = sampler()
                    ts = int(clock())
                    events = db.record(ts, sample, g.lookup)
                    counters = traffic_sampler()
                    moved = traffic.delta(traffic.loads(db.get_meta("nettop")), counters)
                    per_app: dict = {}
                    for pid, (b_in, b_out) in moved.items():
                        name = counters[pid][0]
                        old = per_app.get(name, (0, 0))
                        per_app[name] = (old[0] + b_in, old[1] + b_out)
                    events += db.record_traffic(ts, per_app, upload_limit)
                    db.set_meta("nettop", traffic.dumps(counters))
                    last = float(db.get_meta("sensors_until") or ts - 60)
                    states = sensor_reader(last)
                    events += db.record_sensors(states, learning=db.get_meta("baseline_sensors") is None)
                    db.set_meta("baseline_sensors", str(ts))
                    if states:
                        db.set_meta("sensors_until", repr(states[-1][0]))
                    events += db.record_keyboard(ts, keyboard_reader(),
                                                 learning=db.get_meta("baseline_keyboard") is None)
                    db.set_meta("baseline_keyboard", str(ts))
                    events += db.record_devices(ts, device_reader(), learning=db.get_meta("baseline_devices") is None)
                    db.set_meta("baseline_devices", str(ts))
                    if first:
                        print("baseline learned: %d connections. From now on only news is reported." % len(sample))
                    else:
                        print("%s  %d connections, %d new" % (_fmt_ts(int(clock())), len(sample), len(events)))
                    for ev in events:
                        sender(settings, notify.message(ev, lang))
                    if args.cmd == "scan":
                        return 0
                    time.sleep(interval)
            finally:
                g.close()

        if args.cmd == "net":
            rows = db.connections(since=parse_since(args.since, now), app=args.app)
            print(_table([[r["app"], r["country"] or "?", r["org"] or "?", _dest(r["proto"], r["remote_ip"], r["remote_port"]),
                           _fmt_ts(r["first_seen"]), _fmt_ts(r["last_seen"]), str(r["samples"])] for r in rows],
                         ["APP", "CC", "OWNER", "DESTINATION", "FIRST SEEN", "LAST SEEN", "SEEN"]))
            return 0

        if args.cmd == "traffic":
            rows = db.traffic(since=parse_since(args.since, now))
            print(_table([[r["app"], traffic.human(r["bytes_out"]), traffic.human(r["bytes_in"])] for r in rows],
                         ["APP", "SENT", "RECEIVED"]))
            return 0

        if args.cmd == "sensors":
            rows = db.sensor_sessions(since=parse_since(args.since, now))
            print(_table([[_fmt_ts(r["start"]), r["kind"], r["app"],
                           _dur((r["end"] or now) - r["start"]) + ("" if r["end"] else " (now)"), r["bundle"]]
                          for r in rows],
                         ["STARTED", "SENSOR", "APP", "FOR", "BUNDLE"]))
            return 0

        if args.cmd == "keyboard":
            rows = db.keyboard_taps()
            print(_table([[r["app"], r["signer"], "yes" if r["can_modify"] else "no", _fmt_ts(r["first_seen"]),
                           _fmt_ts(r["last_seen"]), r["path"]] for r in rows],
                         ["PROGRAM", "SIGNED BY", "CAN MODIFY", "FIRST SEEN", "LAST SEEN", "PATH"]))
            return 0

        if args.cmd == "devices":
            rows = db.devices(kind=args.kind)
            print(_table([[r["kind"], r["name"], r["vendor"] or "-", _fmt_ts(r["first_seen"]), _fmt_ts(r["last_seen"])]
                          for r in rows],
                         ["KIND", "NAME", "VENDOR/TYPE", "FIRST SEEN", "LAST SEEN"]))
            return 0

        if args.cmd == "events":
            rows = db.events(since=parse_since(args.since, now))
            print(_table([[_fmt_ts(r["ts"]), r["kind"], r["app"], r["country"] or "-", r["org"] or "-",
                           r["remote_ip"] or r["sensor"] or r["signer"] or "-",
                           traffic.human(r["bytes_out"]) if r["bytes_out"] else "-"]
                          for r in rows],
                         ["WHEN", "KIND", "APP", "CC", "OWNER", "DETAIL", "SENT"]))
            return 0
    finally:
        db.close()
    return 2


if __name__ == "__main__":
    sys.exit(main())

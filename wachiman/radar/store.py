"""The radar history: one SQLite file, metadata only.

Table `connections` keeps one row per (app, destination ip, port, protocol)
with first/last seen and how many samples saw it. Table `events` keeps what
was worth telling the user.

What counts as news (deliberately few, to avoid alert fatigue):
  new_app      an app that never used the network before does so now
  new_country  a known app reaches a country it never reached before
  big_upload   one app sent more than the configured limit within one clock hour
  new_sensor_app  an app uses the mic, camera or screen recording for the first time
  new_keyboard_tap  a program never seen before starts reading the keyboard
  new_device   a USB device, paired Bluetooth device or network never seen before
The very first sample only learns the baseline and reports nothing.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from .geo import Owner
from .net import Socket

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
  app TEXT NOT NULL, remote_ip TEXT NOT NULL, remote_port INTEGER NOT NULL, proto TEXT NOT NULL,
  asn INTEGER, country TEXT, org TEXT,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, samples INTEGER NOT NULL,
  PRIMARY KEY (app, remote_ip, remote_port, proto)
);
CREATE INDEX IF NOT EXISTS connections_last_seen ON connections (last_seen);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, kind TEXT NOT NULL, app TEXT NOT NULL,
  remote_ip TEXT, country TEXT, org TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sensor_sessions (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, app TEXT NOT NULL, bundle TEXT NOT NULL,
  start INTEGER NOT NULL, "end" INTEGER
);
CREATE INDEX IF NOT EXISTS sensor_sessions_start ON sensor_sessions (start);
CREATE TABLE IF NOT EXISTS keyboard_taps (
  path TEXT PRIMARY KEY, app TEXT NOT NULL, signer TEXT NOT NULL, can_modify INTEGER NOT NULL,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, vendor TEXT NOT NULL,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS traffic (
  app TEXT NOT NULL, hour INTEGER NOT NULL, bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  alerted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (app, hour)
);
"""


@dataclass(frozen=True)
class Event:
    ts: int
    kind: str
    app: str
    remote_ip: Optional[str]
    country: Optional[str]
    org: Optional[str]
    bytes_out: int = 0
    sensor: Optional[str] = None
    signer: Optional[str] = None


Lookup = Callable[[str], Optional[Owner]]


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(events)")}
        if "bytes_out" not in cols:  # databases created by older versions
            self.con.execute("ALTER TABLE events ADD COLUMN bytes_out INTEGER NOT NULL DEFAULT 0")
        if "sensor" not in cols:
            self.con.execute("ALTER TABLE events ADD COLUMN sensor TEXT")
        if "signer" not in cols:
            self.con.execute("ALTER TABLE events ADD COLUMN signer TEXT")

    def close(self) -> None:
        self.con.close()

    def get_meta(self, key: str) -> Optional[str]:
        row = self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def record_traffic(self, ts: int, per_app: Dict[str, Tuple[int, int]], upload_alert: int) -> List[Event]:
        """Add bytes to each app's hourly bucket; alert once per app and hour over `upload_alert` bytes."""
        hour = ts - ts % 3600
        events: List[Event] = []
        with self.con:
            for app, (b_in, b_out) in per_app.items():
                self.con.execute(
                    "INSERT INTO traffic (app, hour, bytes_in, bytes_out) VALUES (?,?,?,?)"
                    " ON CONFLICT(app, hour) DO UPDATE SET bytes_in=bytes_in+excluded.bytes_in,"
                    " bytes_out=bytes_out+excluded.bytes_out",
                    (app, hour, b_in, b_out),
                )
                if upload_alert <= 0:
                    continue
                total, alerted = self.con.execute(
                    "SELECT bytes_out, alerted FROM traffic WHERE app=? AND hour=?", (app, hour)).fetchone()
                if total >= upload_alert and not alerted:
                    self.con.execute("UPDATE traffic SET alerted=1 WHERE app=? AND hour=?", (app, hour))
                    e = Event(ts, "big_upload", app, None, None, None, total)
                    self.con.execute(
                        "INSERT INTO events (ts, kind, app, remote_ip, country, org, bytes_out) VALUES (?,?,?,?,?,?,?)",
                        (e.ts, e.kind, e.app, None, None, None, total))
                    events.append(e)
        return events

    def traffic(self, since: Optional[int] = None) -> List[sqlite3.Row]:
        q, args = "SELECT app, SUM(bytes_in) AS bytes_in, SUM(bytes_out) AS bytes_out FROM traffic", []
        if since is not None:
            q += " WHERE hour >= ?"
            args.append(since - since % 3600)
        return self.con.execute(q + " GROUP BY app ORDER BY bytes_out DESC, app", args).fetchall()

    def record_keyboard(self, ts: int, taps: Iterable, learning: bool) -> List[Event]:
        """Remember every program seen reading keys; alert on programs never seen before."""
        events: List[Event] = []
        known = {r[0] for r in self.con.execute("SELECT path FROM keyboard_taps")}
        with self.con:
            for t in taps:
                self.con.execute(
                    "INSERT INTO keyboard_taps (path, app, signer, can_modify, first_seen, last_seen)"
                    " VALUES (?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET last_seen=excluded.last_seen,"
                    " signer=excluded.signer, can_modify=max(can_modify, excluded.can_modify)",
                    (t.path, t.app, t.signer, int(t.can_modify), ts, ts))
                if learning or t.path in known or t.path.startswith("pid:"):
                    continue
                known.add(t.path)
                e = Event(ts, "new_keyboard_tap", t.app, None, None, None, 0, None, t.signer)
                self.con.execute("INSERT INTO events (ts, kind, app, signer) VALUES (?,?,?,?)",
                                 (ts, e.kind, t.app, t.signer))
                events.append(e)
        return events

    def record_devices(self, ts: int, devs: Iterable, learning: bool) -> List[Event]:
        events: List[Event] = []
        known = {r[0] for r in self.con.execute("SELECT id FROM devices")}
        with self.con:
            for d in devs:
                self.con.execute(
                    "INSERT INTO devices (id, kind, name, vendor, first_seen, last_seen) VALUES (?,?,?,?,?,?)"
                    " ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, name=excluded.name",
                    (d.id, d.kind, d.name, d.vendor, ts, ts))
                if learning or d.id in known:
                    continue
                known.add(d.id)
                e = Event(ts, "new_device", d.name, None, None, None, 0, d.kind)
                self.con.execute("INSERT INTO events (ts, kind, app, sensor) VALUES (?,?,?,?)",
                                 (ts, e.kind, d.name, d.kind))
                events.append(e)
        return events

    def devices(self, kind: Optional[str] = None) -> List[sqlite3.Row]:
        q, args = "SELECT * FROM devices", []
        if kind:
            q += " WHERE kind = ?"
            args.append(kind)
        return self.con.execute(q + " ORDER BY kind, last_seen DESC, name", args).fetchall()

    def keyboard_taps(self) -> List[sqlite3.Row]:
        return self.con.execute("SELECT * FROM keyboard_taps ORDER BY last_seen DESC, app").fetchall()

    def sensors_active(self) -> Set[Tuple[str, str, str]]:
        rows = self.con.execute('SELECT kind, app, bundle FROM sensor_sessions WHERE "end" IS NULL')
        return {(r[0], r[1], r[2]) for r in rows}

    def record_sensors(self, states: Iterable[Tuple[float, Set[Tuple[str, str, str]]]],
                       learning: bool) -> List[Event]:
        """Apply indicator changes in order; alert the first time an app uses a given sensor."""
        events: List[Event] = []
        active = self.sensors_active()
        known = {(r[0], r[1]) for r in self.con.execute("SELECT DISTINCT kind, bundle FROM sensor_sessions")}
        with self.con:
            for ts_f, now in states:
                ts = int(ts_f)
                for kind, app, bundle in now - active:
                    self.con.execute('INSERT INTO sensor_sessions (kind, app, bundle, start) VALUES (?,?,?,?)',
                                     (kind, app, bundle, ts))
                    if not learning and (kind, bundle) not in known:
                        e = Event(ts, "new_sensor_app", app, None, None, None, 0, kind)
                        self.con.execute("INSERT INTO events (ts, kind, app, sensor) VALUES (?,?,?,?)",
                                         (ts, e.kind, app, kind))
                        events.append(e)
                    known.add((kind, bundle))
                for kind, app, bundle in active - now:
                    self.con.execute('UPDATE sensor_sessions SET "end"=? WHERE kind=? AND bundle=? AND app=?'
                                     ' AND "end" IS NULL', (ts, kind, bundle, app))
                active = set(now)
        return events

    def sensor_sessions(self, since: Optional[int] = None) -> List[sqlite3.Row]:
        q, args = "SELECT * FROM sensor_sessions", []
        if since is not None:
            q += ' WHERE "end" IS NULL OR "end" >= ?'
            args.append(since)
        return self.con.execute(q + " ORDER BY start DESC, id DESC", args).fetchall()

    def _baseline_done(self) -> bool:
        return self.con.execute("SELECT 1 FROM meta WHERE key='baseline'").fetchone() is not None

    def record(self, ts: int, sample: Iterable[Tuple[str, Socket]], lookup: Lookup) -> List[Event]:
        learning = not self._baseline_done()
        known_apps = {r[0] for r in self.con.execute("SELECT DISTINCT app FROM connections")}
        known_countries = {(r[0], r[1]) for r in self.con.execute(
            "SELECT DISTINCT app, country FROM connections WHERE country IS NOT NULL")}
        events: List[Event] = []
        counted = set()
        with self.con:
            for app, s in sample:
                key = (app, s.remote_ip, s.remote_port, s.proto)
                owner = lookup(s.remote_ip) if s.remote_ip else None
                country = owner.country if owner else None
                org = owner.org if owner else None
                if not learning:
                    if app not in known_apps:
                        events.append(Event(ts, "new_app", app, s.remote_ip, country, org))
                    elif country and (app, country) not in known_countries:
                        events.append(Event(ts, "new_country", app, s.remote_ip, country, org))
                known_apps.add(app)
                if country:
                    known_countries.add((app, country))
                bump = 0 if key in counted else 1
                counted.add(key)
                self.con.execute(
                    "INSERT INTO connections (app, remote_ip, remote_port, proto, asn, country, org,"
                    " first_seen, last_seen, samples) VALUES (?,?,?,?,?,?,?,?,?,1)"
                    " ON CONFLICT(app, remote_ip, remote_port, proto) DO UPDATE SET"
                    " last_seen=excluded.last_seen, samples=samples+?",
                    (app, s.remote_ip, s.remote_port, s.proto, owner.asn if owner else None, country,
                     owner.org if owner else None, ts, ts, bump),
                )
            for e in events:
                self.con.execute("INSERT INTO events (ts, kind, app, remote_ip, country, org) VALUES (?,?,?,?,?,?)",
                                 (e.ts, e.kind, e.app, e.remote_ip, e.country, e.org))
            if learning:
                self.con.execute("INSERT OR REPLACE INTO meta VALUES ('baseline', ?)", (str(ts),))
        return events

    def connections(self, since: Optional[int] = None, app: Optional[str] = None) -> List[sqlite3.Row]:
        q = "SELECT * FROM connections WHERE 1=1"
        args: list = []
        if since is not None:
            q += " AND last_seen >= ?"
            args.append(since)
        if app:
            q += " AND lower(app) = lower(?)"
            args.append(app)
        q += " ORDER BY last_seen DESC, app"
        return self.con.execute(q, args).fetchall()

    def events(self, since: Optional[int] = None) -> List[sqlite3.Row]:
        q, args = "SELECT * FROM events", []
        if since is not None:
            q += " WHERE ts >= ?"
            args.append(since)
        return self.con.execute(q + " ORDER BY ts DESC, id DESC", args).fetchall()

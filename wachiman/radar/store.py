"""The radar history: one SQLite file, metadata only.

Table `connections` keeps one row per (app, destination ip, port, protocol)
with first/last seen and how many samples saw it. Table `events` keeps what
was worth telling the user.

What counts as news (deliberately few, to avoid alert fatigue):
  new_app      an app that never used the network before does so now
  new_country  a known app reaches a country it never reached before
  big_upload   one app sent more than the configured limit within one clock hour
The very first sample only learns the baseline and reports nothing.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

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

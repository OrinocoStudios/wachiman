"""The radar history: one SQLite file, metadata only.

Table `connections` keeps one row per (app, destination ip, port, protocol)
with first/last seen and how many samples saw it. Table `events` keeps what
was worth telling the user.

What counts as news (deliberately few, to avoid alert fatigue):
  new_app      an app that never used the network before does so now
  new_country  a known app reaches a country it never reached before
The very first sample only learns the baseline and reports nothing.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

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
"""


@dataclass(frozen=True)
class Event:
    ts: int
    kind: str
    app: str
    remote_ip: Optional[str]
    country: Optional[str]
    org: Optional[str]


Lookup = Callable[[str], Optional[Owner]]


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)

    def close(self) -> None:
        self.con.close()

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

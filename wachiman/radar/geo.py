"""Who owns an IP address: company (autonomous system) and country.

Uses the public-domain IPtoASN dataset (https://iptoasn.com), turned into a
small local SQLite file. Lookups never touch the network, so asking "who is
this?" does not itself leak what your computer is talking to.
"""
from __future__ import annotations

import gzip
import ipaddress
import sqlite3
import urllib.request
from pathlib import Path
from typing import NamedTuple, Optional

SOURCE_URL = "https://iptoasn.com/data/ip2asn-combined.tsv.gz"


class Owner(NamedTuple):
    asn: int
    country: str
    org: str


def clean_org(raw: str) -> str:
    """'GOOGLE - Google LLC' -> 'Google LLC'."""
    handle, sep, name = raw.partition(" - ")
    return name.strip() if sep and name.strip() else raw.strip()


def _key(ip: str) -> Optional[bytes]:
    """IPs as 16-byte big-endian keys, so v4 (mapped) and v6 sort in one table."""
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    if addr.version == 4:
        addr = ipaddress.IPv6Address("::ffff:" + str(addr))
    return addr.packed


def build(src: Path, db: Path) -> int:
    """Turn the IPtoASN .tsv.gz into the lookup db. Returns ranges stored."""
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    tmp = db.with_suffix(db.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(str(tmp))
    con.execute("CREATE TABLE ranges (start BLOB PRIMARY KEY, stop BLOB, asn INTEGER, country TEXT, org TEXT)"
                " WITHOUT ROWID")
    n = 0
    rows = []
    with gzip.open(src, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            start, stop, asn, country, org = parts[:5]
            if asn == "0" or country == "None":
                continue
            a, b = _key(start), _key(stop)
            if a is None or b is None:
                continue
            try:
                asn_i = int(asn)
            except ValueError:
                continue
            rows.append((a, b, asn_i, country, clean_org(org)))
            if len(rows) >= 50000:
                con.executemany("INSERT OR REPLACE INTO ranges VALUES (?,?,?,?,?)", rows)
                n += len(rows)
                rows = []
    con.executemany("INSERT OR REPLACE INTO ranges VALUES (?,?,?,?,?)", rows)
    n += len(rows)
    con.commit()
    con.close()
    tmp.replace(db)
    return n


def download(dest: Path, url: str = SOURCE_URL) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "wachiman"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as out:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    return dest


class Geo:
    def __init__(self, db: Path):
        self.available = Path(db).exists()
        self._con = sqlite3.connect("file:%s?mode=ro" % db, uri=True) if self.available else None

    def lookup(self, ip: str) -> Optional[Owner]:
        if self._con is None:
            return None
        k = _key(ip)
        if k is None:
            return None
        row = self._con.execute(
            "SELECT stop, asn, country, org FROM ranges WHERE start <= ? ORDER BY start DESC LIMIT 1", (k,)
        ).fetchone()
        if row is None or row[0] < k:
            return None
        return Owner(row[1], row[2], row[3])

    def close(self) -> None:
        if self._con is not None:
            self._con.close()

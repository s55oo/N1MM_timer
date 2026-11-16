"""SQLite ledger of QSOs received from N1MM (never N1MM's own database)."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Iterable, Optional

from .n1mm import Contact
from .rules import utcnow as rules_utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS qsos (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, band TEXT, mode TEXT, call TEXT,
  station TEXT, radionr TEXT, run1run2 TEXT, is_run INTEGER, is_mult INTEGER,
  contest TEXT, source TEXT NOT NULL DEFAULT 'udp', tx TEXT,
  deleted INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS qsos_ts ON qsos(ts);
CREATE TABLE IF NOT EXISTS acks (fault_id TEXT PRIMARY KEY, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

FMT = "%Y-%m-%d %H:%M:%S"


def _b(v: Optional[bool]):
    return None if v is None else int(bool(v))


class Store:
    def __init__(self, path: str = "timer.db"):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---- QSOs -----------------------------------------------------------
    def upsert(self, c: Contact, source: str = "udp", tx: Optional[str] = None, commit: bool = True):
        now = rules_utcnow().strftime(FMT)
        with self.lock:
            self.db.execute(
                """INSERT INTO qsos(id, ts, band, mode, call, station, radionr, run1run2, is_run,
                                    is_mult, contest, source, tx, deleted, updated)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                   ON CONFLICT(id) DO UPDATE SET ts=excluded.ts, band=excluded.band,
                     mode=excluded.mode, call=excluded.call, station=excluded.station,
                     radionr=excluded.radionr, run1run2=excluded.run1run2, is_run=excluded.is_run,
                     is_mult=excluded.is_mult, contest=excluded.contest, source=excluded.source,
                     tx=excluded.tx, deleted=0, updated=excluded.updated""",
                (c.id, c.ts.strftime(FMT), c.band, c.mode, c.call, c.station, c.radionr, c.run1run2,
                 _b(c.is_run), _b(c.is_mult), c.contest, source, tx, now))
            if commit:
                self.db.commit()

    def delete(self, qso_id: str, commit: bool = True) -> bool:
        """Mark deleted; True only if a live QSO was actually removed."""
        with self.lock:
            cur = self.db.execute("UPDATE qsos SET deleted=1, updated=? WHERE id=? AND deleted=0",
                                  (rules_utcnow().strftime(FMT), qso_id))
            if commit:
                self.db.commit()
            return cur.rowcount > 0

    def commit(self):
        with self.lock:
            self.db.commit()

    def rows(self, contest: Optional[str] = None) -> list:
        with self.lock:
            if contest:
                cur = self.db.execute(
                    "SELECT * FROM qsos WHERE deleted=0 AND (contest=? OR contest='' OR contest IS NULL "
                    "OR source='cabrillo')", (contest,))
            else:
                cur = self.db.execute("SELECT * FROM qsos WHERE deleted=0")
            return cur.fetchall()

    def has_near(self, call: str, band: str, ts: datetime, minutes: int = 2) -> bool:
        lo = (ts - timedelta(minutes=minutes)).strftime(FMT)
        hi = (ts + timedelta(minutes=minutes)).strftime(FMT)
        with self.lock:
            r = self.db.execute(
                "SELECT 1 FROM qsos WHERE deleted=0 AND call=? AND band=? AND ts BETWEEN ? AND ? LIMIT 1",
                (call, band, lo, hi)).fetchone()
            return r is not None

    def clear_qsos(self):
        with self.lock:
            self.db.execute("DELETE FROM qsos")
            self.db.commit()

    def clear(self):
        with self.lock:
            self.db.executescript("DELETE FROM qsos; DELETE FROM acks;")
            self.db.commit()

    # ---- acks / meta ------------------------------------------------------
    def ack(self, fault_ids: Iterable[str]):
        now = rules_utcnow().strftime(FMT)
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO acks(fault_id, ts) VALUES(?,?)",
                                [(f, now) for f in fault_ids])
            self.db.commit()

    def acked(self) -> set:
        with self.lock:
            return {r[0] for r in self.db.execute("SELECT fault_id FROM acks")}

    def get_meta(self, k: str, default: str = "") -> str:
        with self.lock:
            r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return r[0] if r else default

    def set_meta(self, k: str, v: str):
        with self.lock:
            self.db.execute("INSERT INTO meta(k, v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                            (k, v))
            self.db.commit()


def row_ts(row) -> datetime:
    return datetime.strptime(row["ts"], FMT)

"""Read-only access to an N1MM Logger+ contest database (*.s3db, SQLite).

Layout (checked against N1MM Logger+ 1.0.111xx databases):
  ContestInstance(ContestID, ContestName, StartDate, TransmitterCategory ONE/TWO/..., ContestNR, ...)
  DXLOG(TS 'YYYY-MM-DD HH:MM:SS' UTC, Call, Freq kHz, Band MHz (21.0), Mode, ContestNR,
        IsRunQSO 0/1, Run1Run2 1/2, RadioNR, NetBiosName (PC), IsMultiplier1/2, isMultiplier3,
        ContactType, ID GUID, ...)
  Deleted QSOs are moved to the 'DELETEDQS' instance (ContestNR = -1).

The database is opened with mode=ro and never written. N1MM keeps it in WAL mode, so
read it on a PC where N1MM runs (not over a network share – SQLite WAL does not work over SMB).
On an N1MM network every PC holds the full, synchronised log.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .n1mm import Contact, band_from_khz, band_from_n1mm

CATEGORY = {"ONE": "MS", "TWO": "M2"}


@dataclass
class ContestInfo:
    nr: int
    name: str
    start: Optional[datetime]
    transmitter: str          # ONE / TWO / UNLIMITED / ...
    operator: str
    qsos: int

    @property
    def category(self) -> Optional[str]:
        return CATEGORY.get((self.transmitter or "").upper())

    def label(self) -> str:
        st = f"{self.start:%Y-%m-%d}" if self.start else "?"
        return f"#{self.nr} {self.name} {st} {self.operator}/{self.transmitter} – {self.qsos} QSOs"


def connect(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=2.0, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def _dt(v) -> Optional[datetime]:
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v)[:19], fmt)
        except ValueError:
            pass
    return None


def contests(db: sqlite3.Connection) -> list:
    counts = {r[0]: r[1] for r in db.execute("SELECT ContestNR, COUNT(*) FROM DXLOG GROUP BY ContestNR")}
    out = []
    for r in db.execute("SELECT * FROM ContestInstance ORDER BY ContestID"):
        nr = r["ContestNR"]
        if nr is None or nr < 1 or r["ContestName"] in ("DELETEDQS", "DX"):
            continue
        keys = r.keys()
        out.append(ContestInfo(nr, r["ContestName"] or "", _dt(r["StartDate"]),
                               r["TransmitterCategory"] if "TransmitterCategory" in keys else "",
                               r["OperatorCategory"] or "", counts.get(nr, 0)))
    return out


def pick_contest(db: sqlite3.Connection, nr: Optional[int] = None) -> Optional[ContestInfo]:
    """Given ContestNR, or the most recent contest instance that has QSOs."""
    cs = contests(db)
    if nr is not None:
        return next((c for c in cs if c.nr == nr), None)
    with_qsos = [c for c in cs if c.qsos]
    return with_qsos[-1] if with_qsos else (cs[-1] if cs else None)


def _flag(v) -> Optional[bool]:
    if v is None or v == "":
        return None
    try:
        return int(v) != 0
    except (TypeError, ValueError):
        return str(v).strip().lower() in ("true", "yes")


def read_qsos(db: sqlite3.Connection, info: ContestInfo, skip_before_start: bool = True):
    """Return (contacts, deleted_ids, skipped_pre_start)."""
    rows = db.execute("SELECT * FROM DXLOG WHERE ContestNR=?", (info.nr,)).fetchall()
    deleted = [r["ID"] for r in db.execute("SELECT ID FROM DXLOG WHERE ContestNR=-1") if r["ID"]]
    out, skipped = [], 0
    start = info.start if (skip_before_start and info.start and info.start.year > 1900) else None
    end = start + timedelta(hours=48) if start else None
    for r in rows:
        ts = _dt(r["TS"])
        if ts is None or not r["ID"]:
            continue
        if start and not (start <= ts < end):
            skipped += 1
            continue
        b = r["Band"]
        band = band_from_n1mm(f"{b:g}" if isinstance(b, (int, float)) else str(b or ""))
        if band is None:
            try:
                band = band_from_khz(float(r["Freq"]))
            except (TypeError, ValueError):
                band = None
        keys = r.keys()
        mflags = [_flag(r[k]) for k in ("IsMultiplier1", "IsMultiplier2", "isMultiplier3") if k in keys]
        known = [m for m in mflags if m is not None]
        out.append(Contact(
            id=r["ID"], ts=ts, band=band, mode=r["Mode"] or "", call=(r["Call"] or "").upper(),
            station=r["NetBiosName"] or "", radionr=str(r["RadioNR"] if r["RadioNR"] is not None else ""),
            run1run2=str(r["Run1Run2"] if r["Run1Run2"] is not None else ""),
            is_run=_flag(r["IsRunQSO"]), is_mult=(any(known) if known else None),
            contest=info.name, operator=r["Operator"] or ""))
    return out, deleted, skipped

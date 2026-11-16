"""Watcher: glue between N1MM packets, the ledger and the rule engine (no GUI)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import cabrillo, n1mm, n1mmdb, rules
from .n1mm import Contact
from .store import Store, row_ts
from .rules import utcnow as rules_utcnow


@dataclass
class Settings:
    profile: str = "auto"          # profile key or "auto" (from N1MM contest name)
    category: str = "MS"           # MS or M2 (UDP has no category; the N1MM database has)
    m2_signal_source: str = "run1run2"  # run1run2 | station | radionr
    db_path: str = ""              # N1MM .s3db to poll (empty = UDP only)
    db_contest_nr: Optional[int] = None   # ContestNR in that database (None = latest with QSOs)
    db_poll_s: float = 5.0
    skip_before_start: bool = True # ignore DB QSOs outside StartDate .. +48 h
    allowed_ips: list = field(default_factory=list)
    station_names: list = field(default_factory=list)   # optional StationName allow list


@dataclass
class Snapshot:
    profile: Optional[rules.Profile]
    contest_name: str
    now: datetime
    result: rules.Result
    faults: list            # list[(Fault, acked: bool)]
    new_faults: list        # faults never seen before (trigger alarm)
    warnings: list          # list[(station, signal, text)]
    packets: int
    last_packet_age: Optional[float]
    qso_count: int
    errors: list


class Watcher:
    RADIO_STALE_S = 30

    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings
        self.lock = threading.RLock()
        self.radios: dict = {}          # (station, radionr) -> (fields, monotonic time)
        self.packets = 0
        self.last_packet: Optional[float] = None
        self.seen_faults: set = set(store.acked())
        self.errors: list = []
        self.dirty = threading.Event()
        self.contest_name = store.get_meta("contest_name")
        self.db_info: Optional[n1mmdb.ContestInfo] = None
        self.db_status = ""
        self._db = None
        self._db_seen: dict = {}        # id -> row signature from last sync
        self._db_version = None

    # ---- configuration --------------------------------------------------
    def profile(self) -> Optional[rules.Profile]:
        s = self.settings
        if s.profile and s.profile != "auto":
            return rules.PROFILES.get(s.profile)
        cat = (self.db_info.category if self.db_info and self.db_info.category else None) or s.category
        return rules.profile_for_contest(self.contest_name, cat)

    def signal_of(self, row_or_contact, profile: rules.Profile) -> str:
        g = (lambda k: row_or_contact[k]) if not isinstance(row_or_contact, Contact) else \
            (lambda k: getattr(row_or_contact, k))
        tx = g("tx") if not isinstance(row_or_contact, Contact) else None
        if profile.category == "MS":
            if tx is not None and tx != "":
                return rules.RUN if tx == "0" else rules.MULT
            is_run = g("is_run")
            return rules.MULT if is_run in (0, False) else rules.RUN
        # M2
        if tx is not None and tx != "":
            return f"TX{int(tx) + 1}"
        src = self.settings.m2_signal_source
        if src == "radionr":
            return f"TX{g('radionr') or '?'}"
        if src == "run1run2":
            return f"TX{g('run1run2') or '?'}"
        return (g("station") or "?").upper()

    # ---- packets ----------------------------------------------------------
    def handle_packet(self, data: bytes, addr: tuple = ("", 0)):
        if self.settings.allowed_ips and addr[0] not in self.settings.allowed_ips:
            return
        msg = n1mm.parse(data)
        if msg is None:
            return
        if self.settings.station_names and msg.station and \
                msg.station.upper() not in {s.upper() for s in self.settings.station_names}:
            return
        with self.lock:
            self.packets += 1
            self.last_packet = time.monotonic()
            if msg.kind in ("contact", "replace") and msg.contact:
                self._set_contest(msg.contact.contest)
                self.store.upsert(msg.contact)
                self.dirty.set()
            elif msg.kind == "delete" and msg.delete_id:
                if self.store.delete(msg.delete_id):
                    self.dirty.set()
            elif msg.kind == "radio":
                key = (msg.station, msg.fields.get("radionr", ""))
                self.radios[key] = (msg.fields, time.monotonic())
                self.dirty.set()
            elif msg.kind == "app":
                self._set_contest(msg.fields.get("contestname", ""))

    def _set_contest(self, name: str):
        if name and name != self.contest_name:
            self.contest_name = name
            self.store.set_meta("contest_name", name)
            self.dirty.set()

    # ---- evaluation -------------------------------------------------------
    def qsos(self, profile: rules.Profile) -> list:
        out = []
        for r in self.store.rows(self.contest_name or None):
            is_mult = None if r["is_mult"] is None else bool(r["is_mult"])
            out.append(rules.Qso(r["id"], row_ts(r), r["band"], self.signal_of(r, profile),
                                 r["call"] or "", r["station"] or "", is_mult))
        return out

    def snapshot(self, now: Optional[datetime] = None, consume: bool = True) -> Snapshot:
        """Evaluate now. With consume=True, faults reported in `new_faults` are marked as seen."""
        now = now or rules_utcnow()
        with self.lock:
            prof = self.profile()
            if prof is None:
                res = rules.Result()
                qcount = len(self.store.rows())
            else:
                q = self.qsos(prof)
                qcount = len(q)
                res = rules.evaluate(prof, q, now=now)
            acked = self.store.acked()
            faults = [(f, f.id in acked) for f in res.faults]
            new = [f for f in res.faults if f.id not in self.seen_faults]
            if consume:
                self.seen_faults.update(f.id for f in new)
            warnings = []
            if prof is not None:
                for (station, radionr), (fields, t) in list(self.radios.items()):
                    if time.monotonic() - t > self.RADIO_STALE_S:
                        continue
                    sig = self._radio_signal(prof, station, radionr, fields, q)
                    w = rules.live_warning(prof, res, sig, n1mm.radio_band(fields), now)
                    if w:
                        warnings.append((f"{station} R{radionr}", sig, w))
            age = None if self.last_packet is None else time.monotonic() - self.last_packet
            return Snapshot(prof, self.contest_name, now, res, faults, new, warnings,
                            self.packets, age, qcount, list(self.errors))

    def _radio_signal(self, prof, station, radionr, fields, qsos) -> str:
        """Which signal is this radio? MS: its last logged QSO's signal, else N1MM IsRunning."""
        if prof.category == "M2":
            src = self.settings.m2_signal_source
            if src == "radionr":
                return f"TX{radionr or '?'}"
            if src == "run1run2":
                return f"TX{fields.get('run1run2', '') or radionr or '?'}"
            return (station or "?").upper()
        mine = [q for q in qsos if q.station == station]
        if mine:
            return max(mine, key=lambda q: q.ts).signal
        running = n1mm._bool(fields.get("isrunning"))
        return rules.MULT if running is False else rules.RUN

    # ---- N1MM database ------------------------------------------------------
    def sync_db(self) -> str:
        """Bring the ledger in line with the N1MM database (adds, edits, deletes)."""
        s = self.settings
        if not s.db_path:
            return ""
        try:
            if self._db is None:
                self._db = n1mmdb.connect(s.db_path)
                self._db_version = None
            ver = self._db.execute("PRAGMA data_version").fetchone()[0]
            if ver == self._db_version and self.db_info is not None:
                return self.db_status          # N1MM wrote nothing since last poll
            info = n1mmdb.pick_contest(self._db, s.db_contest_nr)
            if info is None:
                self.db_status = "no contest in database"
                return self.db_status
            contacts, deleted, skipped = n1mmdb.read_qsos(self._db, info, s.skip_before_start)
        except Exception as e:          # locked, missing, wrong file ...
            self._db = None
            self.db_status = f"database error: {e}"
            return self.db_status
        changed = False
        with self.lock:
            if self.db_info is None or self.db_info.nr != info.nr or self.db_info.category != info.category:
                changed = True
            self.db_info = info
            self._set_contest(info.name)
            now_ids = set()
            for c in contacts:
                now_ids.add(c.id)
                sig = (c.ts, c.band, c.call, c.station, c.run1run2, c.radionr, c.is_run, c.is_mult)
                if self._db_seen.get(c.id) != sig:
                    self.store.upsert(c, source="db", commit=False)
                    self._db_seen[c.id] = sig
                    changed = True
            gone = (set(self._db_seen) - now_ids) | (set(deleted) & set(self._db_seen))
            for qid in gone:
                self.store.delete(qid, commit=False)
                self._db_seen.pop(qid, None)
                changed = True
            for qid in deleted:          # deleted in N1MM before we saw it via UDP
                if self.store.delete(qid, commit=False):
                    changed = True
            self.store.commit()
        self._db_version = ver
        if changed:
            self.dirty.set()
        extra = f", {skipped} outside contest period ignored" if skipped else ""
        self.db_status = f"DB {info.label()}{extra}"
        return self.db_status

    def start_db_poller(self) -> Optional[threading.Thread]:
        if not self.settings.db_path:
            return None

        def loop():
            while True:
                self.sync_db()
                time.sleep(max(1.0, self.settings.db_poll_s))
        t = threading.Thread(target=loop, daemon=True, name="n1mm-db")
        t.start()
        return t

    # ---- actions ----------------------------------------------------------
    def ack(self, fault_ids):
        self.store.ack(fault_ids)
        self.dirty.set()

    def ack_all(self):
        snap = self.snapshot(consume=False)
        self.ack([f.id for f, a in snap.faults if not a])

    def import_cabrillo(self, path: str) -> str:
        log = cabrillo.read(path)
        added = dup = 0
        for i, q in enumerate(log.qsos):
            if self.store.has_near(q.call, q.band, q.ts):
                dup += 1
                continue
            cid = f"cbr:{q.ts:%Y%m%d%H%M}:{q.call}:{q.band}:{i}"
            c = Contact(cid, q.ts, q.band, q.mode, q.call, "", "", "", None, None, "")
            self.store.upsert(c, source="cabrillo", tx=q.tx)
            added += 1
        self.dirty.set()
        return f"Imported {added} QSOs from Cabrillo ({dup} already in ledger, {log.skipped} unreadable lines)"

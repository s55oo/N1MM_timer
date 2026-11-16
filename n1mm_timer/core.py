"""Watcher: glue between N1MM packets, the ledger and the rule engine (no GUI)."""
from __future__ import annotations

import os
import socket
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
    signal_source: str = "run1run2"  # run1run2 | radionr | station  (MS: 1=RUN 2=MULT, M2: TX1/TX2)
    signal_map: dict = field(default_factory=dict)  # PC (StationName) -> RUN/MULT/TX1/TX2 override
    db_path: str = ""              # N1MM .s3db to poll (empty = UDP only, unless db_follow)
    db_follow: bool = False        # follow the log N1MM has open on this PC (AppInfo / newest)
    db_dirs: list = field(default_factory=list)   # extra folders with N1MM databases
    db_contest_nr: Optional[int] = None   # ContestNR in that database (None = latest with QSOs)
    db_poll_s: float = 5.0
    skip_before_start: bool = True # ignore DB QSOs outside StartDate .. +48 h
    allowed_ips: list = field(default_factory=list)
    station_names: list = field(default_factory=list)   # optional StationName allow list


@dataclass
class StationRow:
    """One line of the Stations overview: an N1MM radio (RadioInfo) and/or a PC seen in the log."""
    station: str
    radionr: str = ""
    op: str = ""
    band: Optional[str] = None
    mode: str = ""
    freq_khz: Optional[float] = None
    running: Optional[bool] = None       # N1MM Run (True) / S&P (False)
    transmitting: bool = False
    tx_caption: str = ""
    signal: str = ""
    rule_text: str = ""
    rule_level: str = "none"             # ok | hold | last | full | none
    warning: str = ""
    qsos: int = 0
    last_qso: Optional[datetime] = None
    last_call: str = ""
    seen_s: Optional[float] = None       # seconds since last RadioInfo; None = log only
    stale: bool = False


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
    stations: list = field(default_factory=list)   # list[StationRow]


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
        self.follow_source = ""          # "N1MM App Info" | "guessed: newest log" | ""
        self._local_ips = self._my_ips()
        self._local_ips_t = time.monotonic()
        if settings.db_follow and not settings.db_path:
            last = store.get_meta("followed_db")
            if last and os.path.isfile(last):
                settings.db_path = last
                nr = store.get_meta("followed_contestnr")
                settings.db_contest_nr = int(nr) if nr.isdigit() else None
                self.follow_source = "guessed: last session"

    # ---- configuration --------------------------------------------------
    def profile(self) -> Optional[rules.Profile]:
        s = self.settings
        if s.profile and s.profile != "auto":
            return rules.PROFILES.get(s.profile)
        cat = (self.db_info.category if self.db_info and self.db_info.category else None) or s.category
        return rules.profile_for_contest(self.contest_name, cat)

    def signal_of(self, row_or_contact, profile: rules.Profile) -> str:
        """RUN/MULT (MS) or TX1/TX2 (M2) of a logged QSO.

        Default source is N1MM's Run1Run2 field – what N1MM writes as the Cabrillo
        transmitter id (verified on real logs: 9A7A CQ WW SSB MS, CR3A CQ WPX SSB M2).
        N1MM's IsRunQSO is Run vs S&P and is NOT used: a run station's S&P QSO is still RUN.
        A per-PC entry in signal_map overrides everything except a Cabrillo TX column."""
        g = (lambda k: row_or_contact[k]) if not isinstance(row_or_contact, Contact) else \
            (lambda k: getattr(row_or_contact, k))
        tx = g("tx") if not isinstance(row_or_contact, Contact) else None
        if tx is not None and tx != "":                       # Cabrillo transmitter column
            if profile.category == "MS":
                return rules.RUN if tx == "0" else rules.MULT
            return f"TX{int(tx) + 1}"
        station = (g("station") or "").upper()
        mapped = self.settings.signal_map.get(station)
        if mapped:
            return mapped
        return self._signal_from_fields(profile, station, g("radionr"), g("run1run2"))

    def _signal_from_fields(self, profile, station, radionr, run1run2) -> str:
        src = self.settings.signal_source
        raw = {"radionr": radionr, "run1run2": run1run2}.get(src)
        if profile.category == "MS":
            if src == "station":
                return rules.RUN                     # unmapped PC: assume RUN
            return rules.MULT if str(raw or "").strip() == "2" else rules.RUN
        if src == "station":
            return station or "?"
        return f"TX{str(raw or '').strip() or '?'}"

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
                if self.settings.db_follow and self._is_local(addr[0]):
                    self._follow(msg.fields.get("dbname", ""), msg.fields.get("contestnr", ""),
                                 "N1MM App Info")

    # ---- following the open N1MM log -----------------------------------------
    @staticmethod
    def _my_ips() -> set:
        ips = {"127.0.0.1", "::1", ""}
        try:
            ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
        except OSError:
            pass
        return ips

    def _is_local(self, ip: str) -> bool:
        if ip.startswith("127.") or ip in self._local_ips:
            return True
        if time.monotonic() - self._local_ips_t > 60:     # DHCP / VPN changes
            self._local_ips, self._local_ips_t = self._my_ips(), time.monotonic()
        return ip in self._local_ips

    def _follow(self, dbname: str, contestnr: str, source: str) -> bool:
        path = n1mmdb.resolve_dbname(dbname, self.settings.db_dirs)
        if path is None:
            if dbname:
                self.db_status = f"N1MM has {dbname!r} open but it is not found on this PC"
            return False
        nr = int(contestnr) if str(contestnr).strip().isdigit() else None
        s = self.settings
        same = os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(s.db_path or "")) \
            and (nr is None or nr == s.db_contest_nr)
        if same:
            if source != self.follow_source:
                self.follow_source = source
                self.store.set_meta("followed_source", source)
            return False
        with self.lock:
            if s.db_path:                      # another log: start a fresh ledger
                self.store.clear_qsos()
                self.seen_faults = set(self.store.acked())
            s.db_path, s.db_contest_nr = path, nr
            self._db, self.db_info, self._db_seen, self._db_version = None, None, {}, None
            self.follow_source = source
            self.store.set_meta("followed_db", path)
            self.store.set_meta("followed_contestnr", "" if nr is None else str(nr))
            self.store.set_meta("followed_source", source)
            self.dirty.set()
        return True

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
                    w = rules.live_warning(prof, res, sig, n1mm.radio_band(fields), now,
                                           bool(n1mm._bool(fields.get("istransmitting"))))
                    if w:
                        warnings.append((f"{station} R{radionr}", sig, w))
            age = None if self.last_packet is None else time.monotonic() - self.last_packet
            stations = self._stations(prof, res, q if prof is not None else [], warnings, now)
            return Snapshot(prof, self.contest_name, now, res, faults, new, warnings,
                            self.packets, age, qcount, list(self.errors), stations)

    def _radio_signal(self, prof, station, radionr, fields, qsos) -> str:
        """Which signal is this radio? The signal of this PC's latest logged QSO (N1MM's
        RadioInfo carries no Run1/Run2), else MS: N1MM IsRunning; M2: the configured source."""
        mapped = self.settings.signal_map.get((station or "").upper())
        if mapped:
            return mapped
        mine = [q for q in qsos if q.station == station]
        if mine:
            return max(mine, key=lambda q: q.ts).signal
        return self._signal_from_fields(prof, (station or "").upper(), radionr, "")

    def _rule_text(self, prof, res, signal, now):
        key = rules.ALL if prof.entry_wide else signal
        s = res.status.get(key)
        if s is None or s.band is None:
            return "no QSO yet", "none"
        if prof.rule == rules.TEN_MIN:
            if s.free_at and now < s.free_at:
                left = int((s.free_at - now).total_seconds())
                return f"held on {s.band} m – {left // 60}:{left % 60:02d}", "hold"
            return f"free (on {s.band} m)", "ok"
        rem = s.remaining()
        lvl = "ok" if rem > 1 else "last" if rem == 1 else "full"
        who = "entry" if prof.entry_wide else key
        return f"{s.used_this_hour}/{s.limit} changes {now:%H}z ({who}) – {rem} left", lvl

    def _stations(self, prof, res, qsos, warnings, now) -> list:
        mono = time.monotonic()
        rows = {}
        by_pc = {}
        for q in qsos:
            by_pc.setdefault(q.station or "?", []).append(q)
        warn_by = {st: text for st, _sig, text in warnings}
        for (station, radionr), (f, t) in list(self.radios.items()):
            try:
                freq = float(f.get("txfreq") or f.get("freq") or 0) / 100.0 or None
            except ValueError:
                freq = None
            r = StationRow(station or "?", radionr, f.get("opcall", ""), n1mm.radio_band(f),
                           f.get("mode", ""), freq, n1mm._bool(f.get("isrunning")),
                           bool(n1mm._bool(f.get("istransmitting"))), f.get("functionkeycaption", ""),
                           seen_s=mono - t, stale=(mono - t) > self.RADIO_STALE_S)
            r.warning = warn_by.get(f"{station} R{radionr}", "")
            rows[(station, radionr)] = r
        for pc in by_pc:
            if not any(k[0] == pc for k in rows):
                rows[(pc, "")] = StationRow(pc)
        for (station, radionr), r in rows.items():
            mine = by_pc.get(station, [])
            r.qsos = len(mine)
            if mine:
                last = max(mine, key=lambda q: q.ts)
                r.last_qso, r.last_call = last.ts, last.call
            if prof is not None:
                if r.seen_s is not None:
                    r.signal = self._radio_signal(prof, station, radionr, self.radios[(station, radionr)][0], qsos)
                elif mine:
                    r.signal = max(mine, key=lambda q: q.ts).signal
                if r.signal:
                    r.rule_text, r.rule_level = self._rule_text(prof, res, r.signal, now)
        return sorted(rows.values(), key=lambda r: (r.signal or "~", r.station, r.radionr))

    # ---- N1MM database ------------------------------------------------------
    def sync_db(self) -> str:
        """Bring the ledger in line with the N1MM database (adds, edits, deletes)."""
        s = self.settings
        if s.db_follow and (not s.db_path or self.follow_source.startswith("guessed")):
            guess = n1mmdb.newest_log(s.db_dirs)          # until N1MM's App Info tells us
            if guess:
                self._follow(guess, "", "guessed: newest log")
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
        how = f" – following N1MM ({self.follow_source})" if s.db_follow and self.follow_source else ""
        self.db_status = f"DB {os.path.basename(s.db_path)} {info.label()}{extra}{how}"
        return self.db_status

    def start_db_poller(self) -> Optional[threading.Thread]:
        if not (self.settings.db_path or self.settings.db_follow):
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

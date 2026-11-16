"""N1MM_timer – band-change watcher for N1MM Logger+ multi-op stations.

  python -m n1mm_timer                      GUI (default)
  python -m n1mm_timer --console            no GUI, print faults/warnings to the console
  python -m n1mm_timer --check LOG.cbr [--profile CQWW-CW-MS]   check a Cabrillo log and exit
  python -m n1mm_timer --db FILE.s3db [--contest-nr N]          check an N1MM database and exit
  python -m n1mm_timer --db FILE.s3db --list                    list contests in the database
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

from . import __version__, cabrillo, config, rules
from .core import Watcher
from .n1mm import Listeners, parse_ports
from .store import Store
from .rules import utcnow as rules_utcnow


def _utf8_console():
    """Windows consoles may use cp1252/cp852 – never crash on band arrows or call signs."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def check_cabrillo(path: str, profile_key: str = None) -> int:
    _utf8_console()
    log = cabrillo.read(path)
    key = profile_key or cabrillo.guess_profile_key(log)
    prof = rules.PROFILES.get(key or "")
    if prof is None:
        print(f"Cannot tell the profile (CONTEST: {log.contest!r}, CATEGORY-TRANSMITTER: "
              f"{log.category_transmitter!r}). Use --profile, one of:\n  " + " ".join(rules.PROFILES))
        return 2
    qsos = []
    no_tx = 0
    for i, q in enumerate(log.qsos):
        if q.tx is None:
            no_tx += 1
        if prof.category == "MS":
            sig = rules.MULT if q.tx == "1" else rules.RUN
        else:
            sig = f"TX{int(q.tx) + 1}" if q.tx is not None else "TX1"
        qsos.append(rules.Qso(f"{i + 1}", q.ts, q.band, sig, q.call))
    res = rules.evaluate(prof, qsos)
    print(f"{prof.title}: {len(qsos)} QSOs, {len(res.faults)} fault(s)")
    if no_tx and (prof.run_mult or prof.category == "M2"):
        print(f"  warning: {no_tx} QSO lines have no transmitter-id column; treated as RUN/TX1")
    for f in res.faults:
        print(f"  line QSO #{f.qso.id:>5}  {f.qso.ts:%Y-%m-%d %H:%M}  {f.kind:<15} {f.text}")
    return 1 if res.faults else 0


def check_db(path: str, contest_nr=None, profile_key=None, m2_source=None, list_only=False,
             all_qsos=False) -> int:
    _utf8_console()
    from . import n1mmdb
    from .core import Settings
    db = n1mmdb.connect(path)
    if list_only:
        for c in n1mmdb.contests(db):
            print(" ", c.label())
        return 0
    st = Settings(profile=profile_key or "auto", db_path=path, db_contest_nr=contest_nr,
                  skip_before_start=not all_qsos)
    if m2_source:
        st.m2_signal_source = m2_source
    w = Watcher(Store(":memory:"), st)
    print(w.sync_db())
    prof = w.profile()
    if prof is None:
        print(f"No rule profile for contest {w.contest_name!r} / {w.db_info and w.db_info.transmitter!r}. "
              "Use --profile, one of:\n  " + " ".join(rules.PROFILES))
        return 2
    snap = w.snapshot(now=None)
    by = {}
    for q in w.qsos(prof):
        by[q.signal] = by.get(q.signal, 0) + 1
    tx = f", M2 TX from {st.m2_signal_source}" if prof.category == "M2" else ""
    print(f"{prof.title}{tx}: {snap.qso_count} QSOs {by}, {len(snap.faults)} fault(s)")
    for f, _ in sorted(snap.faults, key=lambda x: x[0].qso.ts):
        print(f"  {f.qso.ts:%Y-%m-%d %H:%M:%S}  {f.qso.station:<8} {f.kind:<15} {f.text}")
    return 1 if snap.faults else 0


def _no_countdown(text: str) -> str:
    import re
    return re.sub(r"\(\d+:\d+ left\)", "", text)


def console(cp) -> int:
    _utf8_console()
    store = Store(os.path.join(config.app_dir(), "timer.db"))
    w = Watcher(store, config.settings(cp))
    lst = Listeners(parse_ports(cp.get("udp", "port")), w.handle_packet, cp.get("udp", "bind")).start()
    w.start_db_poller()
    sender = None
    if cp.getboolean("pingpong", "enabled"):
        from .pingpong import Sender
        sender = Sender(cp.get("pingpong", "host"), cp.getint("pingpong", "port"))
    print(f"N1MM_timer {__version__} listening on UDP {lst.describe()} (Ctrl+C to stop)")
    for e in lst.errors():
        print("ERROR:", e)
    shown_errors = set(lst.errors())
    last_warn = {}
    last_db = ""
    try:
        while True:
            w.dirty.wait(2.0)
            w.dirty.clear()
            for e in lst.errors():
                if e not in shown_errors:
                    print("ERROR:", e)
                    shown_errors.add(e)
            snap = w.snapshot()
            for f in snap.new_faults:
                print(f"\a[{rules_utcnow():%H:%M:%S}z] FAULT {f.kind}: {f.text}")
            if w.db_status and w.db_status != last_db:
                print("  ", w.db_status)
                last_db = w.db_status
            for st, sig, text in snap.warnings:
                if last_warn.get(st) != _no_countdown(text):
                    print(f"[{rules_utcnow():%H:%M:%S}z] warning {st}: {text}")
            last_warn = {st: _no_countdown(t) for st, _, t in snap.warnings}
            if sender:
                sender.send(snap)
    except KeyboardInterrupt:
        lst.stop()
    return 0


def main(argv=None) -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(prog="n1mm_timer", description="Band-change watcher for N1MM Logger+")
    ap.add_argument("--check", metavar="CABRILLO", help="check a Cabrillo log and exit")
    ap.add_argument("--profile", help="profile key (see n1mm_timer.ini)")
    ap.add_argument("--db", metavar="S3DB", help="check an N1MM Logger+ database (read-only) and exit")
    ap.add_argument("--contest-nr", type=int, help="ContestNR inside the database (default: latest)")
    ap.add_argument("--list", action="store_true", help="with --db: list contests in the database")
    ap.add_argument("--m2-source", choices=["run1run2", "station", "radionr"], help="M2: how to tell TX1/TX2")
    ap.add_argument("--all-qsos", action="store_true", help="with --db: include QSOs outside the contest period")
    ap.add_argument("--console", action="store_true", help="run without GUI")
    ap.add_argument("--ini", help="path to n1mm_timer.ini")
    a = ap.parse_args(argv)
    if a.check:
        return check_cabrillo(a.check, a.profile)
    if a.db:
        return check_db(a.db, a.contest_nr, a.profile, a.m2_source, a.list, a.all_qsos)
    cp = config.load(a.ini)
    if a.profile:
        cp.set("contest", "profile", a.profile)
    if a.console:
        return console(cp)
    from .app import run
    return run(cp)


if __name__ == "__main__":
    sys.exit(main())

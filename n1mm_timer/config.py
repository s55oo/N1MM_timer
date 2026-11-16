"""n1mm_timer.ini handling."""
from __future__ import annotations

import configparser
import os
import sys

from .core import Settings

DEFAULT_INI = """\
; N1MM_timer configuration
[udp]
; UDP port(s) N1MM sends to, e.g. 12063 or 12063, 12064 (12060 is N1MM's usual port for
; other programs, 12062 is the PingPong lamp's).
; On EVERY N1MM PC: Config > Configure Ports > Broadcast Data – add <this-pc-ip>:<port>
; (or your subnet broadcast, e.g. 192.168.1.255:12063) to the Radio, Contacts and
; Application Info lines. 127.0.0.1 reaches only programs on that same PC.
; The port must not be used by another program on this PC.
port = 12063
bind = 0.0.0.0
; optional comma-separated source IP allow list (empty = accept all)
allowed_ips =
; optional comma-separated N1MM StationName allow list (empty = accept all)
station_names =

[contest]
; auto = take contest from N1MM (CQWWCW, CQWPXSSB, ...) or one of:
; CQWW-CW-MS CQWW-SSB-MS CQWW-RTTY-MS CQWW-CW-M2 CQWW-SSB-M2 CQWW-RTTY-M2
; CQWPX-CW-MS CQWPX-SSB-MS CQWPX-RTTY-MS CQWPX-CW-M2 CQWPX-SSB-M2 CQWPX-RTTY-M2
profile = auto
; MS or M2 (used with profile = auto when no N1MM database is set –
; with a database, N1MM's own TransmitterCategory ONE/TWO is used)
category = MS
; Which signal a QSO belongs to (MS: RUN/MULT, M2: TX1/TX2):
;   run1run2 = N1MM's Run1/Run2 field = the Cabrillo transmitter id (MS: 1=RUN 2=MULT,
;              M2: 1=TX1 2=TX2); several PCs may share one signal. Recommended.
;   radionr  = N1MM RadioNr (1/2)
;   station  = by PC name (M2: one PC per TX; MS: use signal_map)
; N1MM's Run/S&P flag (IsRunQSO) is never used: a run station's S&P QSO is still RUN.
signal_source = run1run2
; optional per-PC override, StationName=SIGNAL, e.g.  PCE=RUN, MULT2=MULT  or  PC1=TX1, PC2=TX2
signal_map =

[n1mm_db]
; auto  = follow the log N1MM has open on THIS PC (from N1MM's Application Info packet –
;         add 127.0.0.1:<udp port> to N1MM's Broadcast Data > Application Info line;
;         until it arrives, the most recently written log database is used)
; empty = don't read the database (UDP only)
; or a fixed database file, e.g.
; C:\\Users\\<you>\\Documents\\N1MM Logger+\\Databases\\S50A_CQWWCW_2026.s3db
; Read-only. Use it on a PC where N1MM runs (not over a network share).
path = auto
; extra folders that hold N1MM databases (comma separated), if not in Documents\\N1MM Logger+\\Databases
folders =
; ContestNR inside the database (empty = most recent contest that has QSOs; with auto, N1MM's own)
contest_nr =
poll_seconds = 5
; ignore QSOs outside the contest's StartDate .. +48 h (test QSOs before the start)
skip_before_start = true

[alerts]
sound = true

[pingpong_alert]
; BAND VIOLATION on every PingPong screen (PingPong v1.117+, interface v1):
; unacknowledged faults go to the PingPong lamp on THIS PC, which passes them on to all
; lamps and the Overview. Nothing listening there is harmless.
enabled = true
host = 127.0.0.1
port = 46071
ttl_s = 10
"""


def _writable(d: str) -> bool:
    try:
        probe = os.path.join(d, ".n1mm_timer_write_test")
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


def app_dir() -> str:
    """Where n1mm_timer.ini and timer.db live: next to the exe (portable use), or
    %LOCALAPPDATA%\\N1MM_timer when that folder is read-only (e.g. under Program Files),
    so the program never needs administrator rights."""
    if getattr(sys, "frozen", False):
        d = os.path.dirname(sys.executable)
    else:
        d = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _writable(d):
        return d
    alt = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "N1MM_timer")
    os.makedirs(alt, exist_ok=True)
    return alt


def load(path: str = None) -> configparser.ConfigParser:
    path = path or os.path.join(app_dir(), "n1mm_timer.ini")
    cp = configparser.ConfigParser()
    cp.read_string(DEFAULT_INI)
    if os.path.exists(path):
        cp.read(path, encoding="utf-8")
    else:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(DEFAULT_INI)
        except OSError:
            pass
    cp.path = path
    return cp


def _map(v: str) -> dict:
    """'PCE=RUN, MULT2=MULT' -> {'PCE': 'RUN', 'MULT2': 'MULT'}"""
    out = {}
    for item in _list(v):
        if "=" in item:
            k, val = item.split("=", 1)
            if k.strip() and val.strip():
                out[k.strip().upper()] = val.strip().upper()
    return out


def _list(v: str) -> list:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def settings(cp) -> Settings:
    return Settings(
        profile=cp.get("contest", "profile").strip() or "auto",
        category=cp.get("contest", "category").strip().upper() or "MS",
        signal_source=(cp.get("contest", "signal_source", fallback="") or
                       cp.get("contest", "m2_signal_source", fallback="")).strip().lower() or "run1run2",
        signal_map=_map(cp.get("contest", "signal_map", fallback="")),
        db_path="" if cp.get("n1mm_db", "path").strip().lower() in ("", "auto") else
        cp.get("n1mm_db", "path").strip().strip('"'),
        db_follow=cp.get("n1mm_db", "path").strip().lower() == "auto",
        db_dirs=_list(cp.get("n1mm_db", "folders")),
        db_contest_nr=int(cp.get("n1mm_db", "contest_nr")) if cp.get("n1mm_db", "contest_nr").strip() else None,
        db_poll_s=cp.getfloat("n1mm_db", "poll_seconds"),
        skip_before_start=cp.getboolean("n1mm_db", "skip_before_start"),
        allowed_ips=_list(cp.get("udp", "allowed_ips")),
        station_names=_list(cp.get("udp", "station_names")),
    )


def save(cp):
    with open(cp.path, "w", encoding="utf-8") as fh:
        cp.write(fh)

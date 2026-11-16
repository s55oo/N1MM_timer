"""BAND VIOLATION alerts to PingPong (interface v1, PingPong v1.117 and newer).

N1MM_timer sends one small JSON datagram to the PingPong lamp on the same PC
(UDP 127.0.0.1:46071) every 2 s as a heartbeat and at once when something
changes. The lamp passes it on to every lamp and the Overview over PingPong's
own signed network, and every screen shows "BAND VIOLATION: ..." in its footer.
Only faults that are not acknowledged in N1MM_timer are sent; acknowledging one
here removes it from every PingPong screen. See docs/PINGPONG_INTERFACE.md.
"""
from __future__ import annotations

import json
import socket
import uuid
from datetime import timedelta
from typing import Optional

from . import rules

TYPE, VERSION = "n1mm_timer.alert", 1
DEFAULT_PORT = 46071
MAX_SIZE = 1400          # bytes (PingPong drops bigger datagrams)
MAX_VIOLATIONS = 5
MAX_TEXT = 60            # characters of "text"
MAX_FIELD = 40           # signal, station, bands, call
MAX_ID = 120
HEARTBEAT_S = 2.0


def _utc(t) -> Optional[str]:
    return None if t is None else t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cut(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def violation_text(f) -> str:
    """Short footer text, at most 60 characters, e.g. 'RUN 20→15 m 14:29 – 3 QSOs'."""
    q, n = f.qso, len(f.affected) or 1
    qsos = f"{n} QSO" + ("s" if n != 1 else "")
    if f.kind == rules.MULT_SAME_BAND:
        return _cut(f"MULT on RUN band {q.band} m {q.ts:%H:%M} {q.call}", MAX_TEXT)
    frm = getattr(f, "_from_band", "") or "?"
    if f.kind == rules.HOURLY:
        return _cut(f"{f.signal} too many changes {frm}→{q.band} m {q.ts:%H:%M} – {qsos}", MAX_TEXT)
    return _cut(f"{f.signal} {frm}→{q.band} m {q.ts:%H:%M} – {qsos}", MAX_TEXT)


def violation(f) -> dict:
    q = f.qso
    v = {"id": _cut(f.id, MAX_ID), "kind": f.kind, "signal": _cut(f.signal, MAX_FIELD),
         "station": _cut(q.station, MAX_FIELD), "to_band": _cut(q.band, MAX_FIELD),
         "first_utc": _utc(q.ts), "call": _cut(q.call, MAX_FIELD),
         "qsos": len(f.affected) or 1, "text": violation_text(f)}
    frm = getattr(f, "_from_band", None)
    v["from_band"] = _cut(frm if frm else q.band, MAX_FIELD)
    return v


def open_violations(snap) -> list:
    """Faults not acknowledged in N1MM_timer, newest first."""
    open_faults = [f for f, acked in snap.faults if not acked]
    return sorted(open_faults, key=lambda f: (f.qso.ts, f.id), reverse=True)


def message(snap, instance: str, seq: int, ttl_s: int = 10) -> dict:
    items = [violation(f) for f in open_violations(snap)[:MAX_VIOLATIONS]]
    msg = {"type": TYPE, "v": VERSION, "instance": instance, "seq": seq,
           "sent_utc": _utc(snap.now), "ttl_s": ttl_s,
           "status": "violation" if items else "ok",
           "profile": _cut(snap.profile.key if snap.profile else "", MAX_FIELD),
           "violations": items}
    while len(encode(msg)) > MAX_SIZE and len(msg["violations"]) > 1:
        msg["violations"] = msg["violations"][:-1]          # keep the newest ones
    return msg


def encode(msg: dict) -> bytes:
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Sender:
    """Sends interface-v1 messages; call tick(snap) about once a second."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, ttl_s: int = 10):
        self.addr = (host, port)
        self.ttl_s = ttl_s
        self.instance = uuid.uuid4().hex[:8]       # new on every start
        self.seq = 0
        self.sent = 0
        self.error: Optional[str] = None
        self._last_key = None
        self._last_sent = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def describe(self) -> str:
        state = f"error: {self.error}" if self.error else f"{self.sent} sent"
        return f"PingPong alerts → {self.addr[0]}:{self.addr[1]} ({state})"

    def tick(self, snap, force: bool = False) -> bool:
        """Send if something changed or the heartbeat is due. True if sent."""
        key = tuple((f.id, len(f.affected)) for f in open_violations(snap)[:MAX_VIOLATIONS])
        due = self._last_sent is None or (snap.now - self._last_sent) >= timedelta(seconds=HEARTBEAT_S)
        if not (force or due or key != self._last_key):
            return False
        self._last_key = key
        self._last_sent = snap.now
        return self.send(snap)

    def send(self, snap) -> bool:
        self.seq += 1
        data = encode(message(snap, self.instance, self.seq, self.ttl_s))
        try:
            self.sock.sendto(data, self.addr)
            self.sent += 1
            self.error = None
            return True
        except OSError as e:          # nothing listening is fine for UDP; real errors are shown
            self.error = str(e)
            return False

"""Phase 2: send watcher status to PingPong as JSON over UDP (see DESIGN.md)."""
from __future__ import annotations

import json
import socket
from datetime import datetime
from typing import Optional

from . import rules


def _iso(t: Optional[datetime]) -> Optional[str]:
    return None if t is None else t.strftime("%Y-%m-%dT%H:%M:%SZ")


def build(snap, seq: int) -> dict:
    prof = snap.profile
    sigs = []
    for key, s in sorted(snap.result.status.items()):
        sigs.append({
            "signal": key, "band": s.band,
            "rule": prof.rule if prof else None,
            "free_at": _iso(s.free_at) if (s.free_at and s.free_at > snap.now) else None,
            "used_this_hour": s.used_this_hour if prof and prof.rule == rules.HOURLY else None,
            "limit": s.limit or None,
        })
    return {
        "type": "n1mm_timer.status", "version": 1, "seq": seq,
        "sent_utc": _iso(snap.now),
        "profile": prof.key if prof else None,
        "signals": sigs,
        "faults": [{"id": f.id, "kind": f.kind, "signal": f.signal, "time": _iso(f.qso.ts),
                    "station": f.qso.station, "call": f.qso.call, "text": f.text, "acked": a}
                   for f, a in snap.faults],
        "warnings": [{"station": st, "signal": sig, "text": t} for st, sig, t in snap.warnings],
    }


class Sender:
    def __init__(self, host: str, port: int):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.seq = 0

    def send(self, snap):
        self.seq += 1
        payload = json.dumps(build(snap, self.seq), ensure_ascii=False).encode("utf-8")
        if len(payload) > 60000:   # keep under one datagram: drop acked faults first
            d = build(snap, self.seq)
            d["faults"] = [f for f in d["faults"] if not f["acked"]][-50:]
            payload = json.dumps(d, ensure_ascii=False).encode("utf-8")
        try:
            self.sock.sendto(payload, self.addr)
        except OSError:
            pass

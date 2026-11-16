"""Parsing of N1MM Logger+ external UDP broadcasts (XML) and a UDP listener."""
from __future__ import annotations

import socket
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

# band edges in kHz -> band label (metres)
_BANDS = [
    (1800, 2000, "160"), (3500, 4000, "80"), (5300, 5450, "60"), (7000, 7300, "40"),
    (10100, 10150, "30"), (14000, 14350, "20"), (18068, 18168, "17"), (21000, 21450, "15"),
    (24890, 24990, "12"), (28000, 29700, "10"), (50000, 54000, "6"),
]
_MHZ = {"1.8": "160", "3.5": "80", "5": "60", "7": "40", "10": "30", "14": "20", "18": "17",
        "21": "15", "24": "12", "28": "10", "50": "6"}


def band_from_khz(khz: float) -> Optional[str]:
    for lo, hi, b in _BANDS:
        if lo <= khz <= hi:
            return b
    return None


def band_from_n1mm(band_field: str, freq_10hz: str = "") -> Optional[str]:
    """N1MM <band> is in MHz ("14", "3.5"); freq fields are in units of 10 Hz."""
    b = (band_field or "").strip()
    if b in _MHZ:
        return _MHZ[b]
    try:
        f = float(b)
        for k, v in _MHZ.items():
            if abs(float(k) - f) < 0.6:
                return v
    except ValueError:
        pass
    try:
        return band_from_khz(float(freq_10hz) / 100.0)
    except (TypeError, ValueError):
        return None


def _bool(v: Optional[str]) -> Optional[bool]:
    if v is None:
        return None
    v = v.strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no", ""):
        return False
    return None


def _ts(v: str) -> Optional[datetime]:
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


@dataclass
class Contact:
    id: str
    ts: datetime
    band: Optional[str]
    mode: str
    call: str
    station: str
    radionr: str
    run1run2: str
    is_run: Optional[bool]
    is_mult: Optional[bool]
    contest: str
    mycall: str = ""
    operator: str = ""


@dataclass
class Message:
    kind: str                         # contact, replace, delete, radio, app, other
    contact: Optional[Contact] = None
    delete_id: Optional[str] = None
    station: str = ""
    fields: dict = field(default_factory=dict)


MAX_DATAGRAM = 16384   # N1MM packets are ~1–3 kB


def parse(data: bytes) -> Optional[Message]:
    """Parse one N1MM datagram. Untrusted network input: oversized packets and any XML
    with a DTD/entity declaration (entity-expansion attacks) are dropped before parsing."""
    if not data or len(data) > MAX_DATAGRAM:
        return None
    head = data[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data.upper():
        return None
    try:
        text = data.decode("utf-8", errors="replace").strip().lstrip("﻿")
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    tag = root.tag.lower()
    f = {child.tag.lower(): (child.text or "") for child in root}
    station = f.get("stationname", "")
    if tag in ("contactinfo", "contactreplace"):
        ts = _ts(f.get("timestamp", ""))
        cid = f.get("id", "").strip()
        if ts is None or not cid:
            return Message("other", fields=f, station=station)
        is_mult = None
        mflags = [_bool(f[k]) for k in ("ismultiplier1", "ismultiplier2", "ismultiplier3") if k in f]
        if mflags:
            is_mult = any(bool(x) for x in mflags)
        c = Contact(
            id=cid, ts=ts,
            band=band_from_n1mm(f.get("band", ""), f.get("txfreq") or f.get("rxfreq", "")),
            mode=f.get("mode", ""), call=f.get("call", "").upper(), station=station,
            radionr=f.get("radionr", ""), run1run2=f.get("run1run2", ""),
            is_run=_bool(f.get("isrunqso")), is_mult=is_mult,
            contest=f.get("contestname", ""), mycall=f.get("mycall", ""),
            operator=f.get("operator", ""))
        return Message("contact" if tag == "contactinfo" else "replace", contact=c, station=station, fields=f)
    if tag == "contactdelete":
        return Message("delete", delete_id=f.get("id", "").strip(), station=station, fields=f)
    if tag == "radioinfo":
        return Message("radio", station=station, fields=f)
    if tag == "appinfo":
        return Message("app", station=station, fields=f)
    return Message("other", station=station, fields=f)


def radio_band(fields: dict) -> Optional[str]:
    """Band the radio would transmit on (TXFreq, falling back to Freq), 10 Hz units."""
    for k in ("txfreq", "freq"):
        try:
            v = float(fields.get(k, "") or 0)
        except ValueError:
            continue
        if v > 0:
            return band_from_khz(v / 100.0)
    return None


class UdpListener(threading.Thread):
    """Receives datagrams and hands (bytes, (ip, port)) to a callback."""

    def __init__(self, port: int, callback: Callable[[bytes, tuple], None], bind: str = "0.0.0.0"):
        super().__init__(daemon=True, name="n1mm-udp")
        self.port, self.bind, self.callback = port, bind, callback
        self._stop = threading.Event()
        self.sock: Optional[socket.socket] = None
        self.error: Optional[str] = None

    def run(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.bind, self.port))
            s.settimeout(0.5)
            self.sock = s
        except OSError as e:
            self.error = f"Cannot listen on UDP {self.port}: {e}"
            return
        while not self._stop.is_set():
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self.callback(data, addr)
            except Exception as e:  # never let one bad packet kill the listener
                self.error = f"packet handler error: {e}"
        s.close()

    def stop(self):
        self._stop.set()

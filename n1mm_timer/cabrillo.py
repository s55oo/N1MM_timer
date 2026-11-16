"""Minimal Cabrillo 3 reader for CQ WW / WPX logs (QSO lines + CONTEST/CATEGORY headers)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .n1mm import band_from_khz


@dataclass
class CabQso:
    ts: datetime
    band: str
    mode: str
    call: str
    tx: Optional[str]      # transmitter id column (0/1) when present


@dataclass
class CabLog:
    contest: str = ""
    category_transmitter: str = ""
    category_operator: str = ""
    qsos: list = field(default_factory=list)
    skipped: int = 0


def read(path: str) -> CabLog:
    log = CabLog()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or ":" not in line:
                continue
            tag, _, rest = line.partition(":")
            tag = tag.strip().upper()
            rest = rest.strip()
            if tag == "CONTEST":
                log.contest = rest.upper()
            elif tag == "CATEGORY-TRANSMITTER":
                log.category_transmitter = rest.upper()
            elif tag == "CATEGORY-OPERATOR":
                log.category_operator = rest.upper()
            elif tag == "QSO":
                q = _qso(rest.split())
                if q:
                    log.qsos.append(q)
                else:
                    log.skipped += 1
    return log


def _qso(t: list) -> Optional[CabQso]:
    # freq mode date time mycall <sent n> call <rcvd n> [tx]
    # 5 + 2n + 1 tokens is always even, so an odd count means a trailing TX id.
    if len(t) < 8:
        return None
    try:
        khz = float(t[0])
        ts = datetime.strptime(f"{t[2]} {t[3]}", "%Y-%m-%d %H%M")
    except ValueError:
        return None
    band = band_from_khz(khz)
    if band is None:
        return None
    tx = t[-1] if len(t) % 2 == 1 and t[-1].isdigit() else None
    body = t[:-1] if tx is not None else t
    n = (len(body) - 6) // 2
    call = body[5 + n].upper() if 5 + n < len(body) else ""
    return CabQso(ts, band, t[1].upper(), call, tx)


def guess_profile_key(log: CabLog) -> Optional[str]:
    c = log.contest.replace("-", "")
    fam = "CQWPX" if "WPX" in c else "CQWW" if "CQWW" in c else None
    if fam is None:
        return None
    mode = "RTTY" if "RTTY" in c else "SSB" if "SSB" in c else "CW" if "CW" in c else None
    if mode is None:
        return None
    cat = {"ONE": "MS", "TWO": "M2"}.get(log.category_transmitter)
    return f"{fam}-{mode}-{cat}" if cat else None

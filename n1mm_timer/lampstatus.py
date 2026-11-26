"""CQ WW DX lamp snapshot from the selected, synchronized N1MM contest instance."""
import calendar
from datetime import timedelta
from itertools import groupby

from . import rules


def cqww(name):
    profile = rules.profile_for_contest(name, "MS")
    return profile is not None and profile.key in ("CQWW-CW-MS", "CQWW-SSB-MS")


def epoch(ts):
    return calendar.timegm(ts.utctimetuple())


def ambiguous_hour(qsos, hour):
    """Band at entry into the hour and band sequence within it must be known."""
    ambiguous = set()
    previous = {}
    for (signal, ts), group in groupby(sorted(qsos, key=lambda q: (q.signal, q.ts)),
                                       key=lambda q: (q.signal, q.ts)):
        bands = {q.band for q in group}
        prior = previous.get(signal, set())
        if epoch(ts) // 3600 == hour:
            if len(bands) > 1 or (len(prior) > 1 and bands & prior):
                ambiguous.add(signal)
        previous[signal] = bands
    return ambiguous | {signal for signal, bands in previous.items() if len(bands) > 1}


def derive(info, contacts, watcher, now, skipped, unreadable=0):
    if not cqww(info.name):
        return None
    category = info.cqww_category
    if unreadable:
        return {"category": "unknown", "last": {}, "signals": {}, "reason": "incomplete_log"}
    if any(c.band not in ("160", "80", "40", "20", "15", "10") for c in contacts):
        return {"category": "unknown", "last": {}, "signals": {}, "reason": "invalid_band"}
    if skipped and not contacts:
        return {"category": "unknown", "last": {}, "signals": {}, "reason": "contest_dates"}
    hour = epoch(now) // 3600
    if category == "M2":
        contacts = [c for c in contacts if epoch(c.ts) // 3600 <= hour]
    latest = {}
    for contact in contacts:
        if contact.station:
            pc = contact.station.upper()
            latest[pc] = max(latest.get(pc, 0), epoch(contact.ts))
    state = {"category": category, "last": latest, "signals": {}}
    if category not in ("MS", "M2"):
        return state

    profile = rules.profile_for_contest(info.name, category)
    names = ("RUN", "MULT") if category == "MS" else ("TX1", "TX2")
    qsos = []
    for c in contacts:
        signal = watcher.signal_of(c, profile)
        settings = watcher.settings
        mapped = settings.signal_map.get(c.station.upper())
        raw = getattr(c, settings.signal_source, None)
        if (not mapped and str(raw) not in ("1", "2")) or signal not in names or not c.band:
            state["category"] = "unknown"
            return state
        qsos.append(rules.Qso(c.id, c.ts, c.band, signal))
    result = rules.evaluate(profile, qsos, now=now)
    ambiguous = ambiguous_hour(qsos, hour) if category == "M2" else set()
    for name in names:
        if name in ambiguous:
            state["signals"][name] = {"reason": "qso_order"}
            continue
        s = result.status.get(name)
        row = None
        if s is not None:
            row = {"band": s.band}
            if category == "MS":
                start = s.band_since.replace(second=0, microsecond=0)
                row["until"] = epoch(start + timedelta(minutes=10))
            else:
                row["used"] = s.used_this_hour
        state["signals"][name] = row
    if category == "M2":
        state["hour"] = hour
    return state

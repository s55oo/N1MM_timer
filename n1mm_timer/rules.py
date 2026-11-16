"""Band-change rule profiles and the evaluation engine (pure Python, no I/O)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

TEN_MIN = "TEN_MIN"
HOURLY = "HOURLY"
MULT_SAME_BAND = "MULT_SAME_BAND"

RUN, MULT, ALL = "RUN", "MULT", "ALL"


@dataclass(frozen=True)
class Profile:
    key: str                 # e.g. "CQWW-CW-MS"
    title: str
    category: str            # "MS" or "M2"
    rule: str                # TEN_MIN or HOURLY
    hourly_limit: int = 0
    entry_wide: bool = False  # WPX Multi-One: one counter for the whole entry
    run_mult: bool = False    # WW MS: RUN/MULT signals with mult constraints


def _build_profiles() -> dict:
    p = {}
    for mode in ("CW", "SSB"):
        p[f"CQWW-{mode}-MS"] = Profile(f"CQWW-{mode}-MS", f"CQ WW {mode} Multi-Single", "MS",
                                       TEN_MIN, run_mult=True)
        p[f"CQWW-{mode}-M2"] = Profile(f"CQWW-{mode}-M2", f"CQ WW {mode} Multi-Two", "M2",
                                       HOURLY, hourly_limit=8)
    p["CQWW-RTTY-MS"] = Profile("CQWW-RTTY-MS", "CQ WW RTTY Multi-Single", "MS",
                                HOURLY, hourly_limit=8, run_mult=True)
    p["CQWW-RTTY-M2"] = Profile("CQWW-RTTY-M2", "CQ WW RTTY Multi-Two", "M2",
                                HOURLY, hourly_limit=8)
    for mode in ("CW", "SSB", "RTTY"):
        p[f"CQWPX-{mode}-MS"] = Profile(f"CQWPX-{mode}-MS", f"CQ WPX {mode} Multi-One", "MS",
                                        HOURLY, hourly_limit=10, entry_wide=True)
        # WPX RTTY M2 limit assumed 8 like CW/SSB – see DESIGN.md
        p[f"CQWPX-{mode}-M2"] = Profile(f"CQWPX-{mode}-M2", f"CQ WPX {mode} Multi-Two", "M2",
                                        HOURLY, hourly_limit=8)
    return p


PROFILES = _build_profiles()


def profile_for_contest(contest_name: str, category: str) -> Optional[Profile]:
    """Map an N1MM contest name (CQWWCW, CQWPXSSB, CQWWRTTY, ...) to a profile."""
    n = (contest_name or "").upper().replace("-", "").replace("_", "").replace(" ", "")
    if "WPX" in n:
        family = "CQWPX"
    elif "CQWW" in n or n.startswith("WW"):
        family = "CQWW"
    else:
        return None
    if "DIGI" in n:
        return None
    if "RTTY" in n or n.endswith("RY"):
        mode = "RTTY"
    elif "SSB" in n or "PH" in n:
        mode = "SSB"
    elif "CW" in n:
        mode = "CW"
    else:
        return None
    return PROFILES.get(f"{family}-{mode}-{category.upper()}")


@dataclass
class Qso:
    id: str
    ts: datetime             # UTC, naive
    band: str                # "160", "80", "40", "20", "15", "10"
    signal: str              # RUN / MULT / TX1 / TX2 / station name ...
    call: str = ""
    station: str = ""
    is_mult: Optional[bool] = None   # None = unknown (e.g. Cabrillo)


@dataclass
class Fault:
    """One out-of-compliance episode of a signal: the first QSO that broke the rule and
    every further QSO of that signal until it was back in compliance (all are removed
    by the CQ log checkers, without further penalty)."""
    id: str
    kind: str
    signal: str
    qso: Qso
    text: str
    affected: list = field(default_factory=list)   # all QSOs of the episode (incl. qso)


@dataclass
class SignalStatus:
    signal: str
    band: Optional[str] = None
    band_since: Optional[datetime] = None   # first QSO on current band (period start)
    free_at: Optional[datetime] = None      # TEN_MIN: when a change becomes legal
    used_this_hour: int = 0
    limit: int = 0
    last_qso: Optional[datetime] = None

    def remaining(self) -> Optional[int]:
        return None if not self.limit else max(0, self.limit - self.used_this_hour)

    def can_change(self, now: datetime) -> bool:
        if self.band is None:
            return True
        if self.free_at is not None:
            return now >= self.free_at
        if self.limit:
            return self.used_this_hour < self.limit
        return True


@dataclass
class Result:
    faults: list = field(default_factory=list)
    status: dict = field(default_factory=dict)   # counter key -> SignalStatus


def _minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


def _hour(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def counter_key(profile: Profile, q: Qso) -> str:
    return ALL if profile.entry_wide else q.signal


def evaluate(profile: Profile, qsos: Iterable[Qso], now: Optional[datetime] = None) -> Result:
    """Evaluate all QSOs (any order) against a profile, the way the CQ log checkers do.

    Changes are counted on the log as it is (no cascades from removed QSOs).
    10-minute rule: after an early change, every QSO of that signal on the new band before
    the allowed minute is out of compliance; going straight back to the band it left is
    back in compliance (original period restored).
    Hourly limits: after the change that exceeds the limit, every QSO of that signal is
    out of compliance until the next clock hour.
    Each such episode is one Fault listing all affected QSOs.
    CQ WW FAQ: "those contacts are in violation ... removed without penalty";
    CQ WPX FAQ: "loss of all QSOs on that transmitter until it is back in compliance".

    Returns the faults and the per-counter status as of `now` (default: last QSO time).
    """
    items = sorted((q for q in qsos if q.band), key=lambda q: (q.ts, q.id))
    res = Result()
    st: dict = {}
    episode: dict = {}         # key -> open Fault while the signal is out of compliance
    hourly_counts: dict = {}   # (key, hour) -> legal changes
    ten = timedelta(minutes=10)

    def status(key: str) -> SignalStatus:
        if key not in st:
            st[key] = SignalStatus(key, limit=profile.hourly_limit if profile.rule == HOURLY else 0)
        return st[key]

    for q in items:
        key = counter_key(profile, q)
        s = status(key)

        # --- WW Multi-Single: the two signals must be on different bands ----
        if profile.run_mult and q.signal == MULT:
            run = st.get(RUN)
            if run is not None and run.band == q.band:
                res.faults.append(Fault(f"{MULT_SAME_BAND}:{q.id}", MULT_SAME_BAND, MULT, q,
                                        f"MULT QSO with {q.call} on {q.band} m while RUN is on {q.band} m",
                                        [q]))

        # --- band change accounting ---------------------------------------
        if profile.rule == HOURLY:
            # Hourly limits are counted on the logged QSOs as they are (CQ WPX FAQ: "the band
            # change occurs at the time the first QSO on the new band is logged"). After the
            # change that exceeds the limit, all QSOs of that signal are out of compliance
            # until the next clock hour.
            f = episode.get(key)
            if f is not None and _hour(q.ts) != _hour(f.qso.ts):
                episode.pop(key, None)
                f = None
            if s.band is not None and q.band != s.band:
                hk = (key, _hour(q.ts))
                hourly_counts[hk] = hourly_counts.get(hk, 0) + 1
                if hourly_counts[hk] > profile.hourly_limit and f is None:
                    f = Fault(f"{HOURLY}:{q.id}", HOURLY, key, q, "", [])
                    f._from_band, f._since = s.band, s.band_since
                    episode[key] = f
                    res.faults.append(f)
                s.band, s.band_since = q.band, q.ts
            elif s.band is None:
                s.band, s.band_since = q.band, q.ts
            if f is not None:
                f.affected.append(q)
            s.last_qso = q.ts
            continue

        # 10-minute rule, counted on the log as it is (like the hourly limits): an early
        # change still starts the new band's period; every QSO of that signal on the new
        # band before the allowed minute is out of compliance (CQ WW FAQ: those contacts
        # "are in violation ... removed without penalty"). Going straight back to the band
        # it left early is back in compliance, with the original period restored.
        f = episode.get(key)
        if f is not None and _minute(q.ts) >= f._allowed:
            episode.pop(key, None)
            f = None
        if s.band is None:
            s.band, s.band_since = q.band, q.ts
        elif q.band != s.band:
            if f is not None and q.band == f._from_band:            # went back: compliant
                s.band, s.band_since = f._from_band, f._since
                episode.pop(key, None)
                s.last_qso = q.ts
                continue
            episode.pop(key, None)
            allowed = _minute(s.band_since) + ten                  # whole (Cabrillo) minutes
            if _minute(q.ts) < allowed:
                f = Fault(f"{TEN_MIN}:{q.id}", TEN_MIN, key, q, "", [q])
                f._from_band, f._since, f._allowed = s.band, s.band_since, allowed
                episode[key] = f
                res.faults.append(f)
            s.band, s.band_since = q.band, q.ts
        elif f is not None:
            f.affected.append(q)
        s.last_qso = q.ts

    for f in res.faults:
        if f.kind in (TEN_MIN, HOURLY):
            f.text = _episode_text(profile, f, ten)

    # --- status as of `now` ------------------------------------------------
    if now is None:
        now = items[-1].ts if items else utcnow()
    for key, s in st.items():
        if profile.rule == TEN_MIN:
            # live countdown is deliberately stricter: 10 min from the exact second
            s.free_at = s.band_since + ten if s.band_since else None
        else:
            s.used_this_hour = hourly_counts.get((key, _hour(now)), 0)
    res.status = st
    return res


def _episode_text(profile: Profile, f: Fault, ten: timedelta) -> str:
    q, n = f.qso, len(f.affected)
    bands = []
    for a in f.affected:
        if a.band not in bands:
            bands.append(a.band)
    last = f.affected[-1].ts
    span = f"{q.ts:%H:%M:%S}" if n == 1 else f"{q.ts:%H:%M:%S}–{last:%H:%M:%S}"
    lost = f"{n} QSO{'s' if n != 1 else ''} on {'/'.join(bands)} m ({span}) would be removed"
    if f.kind == TEN_MIN:
        mins = (q.ts - f._since).total_seconds() / 60
        return (f"{f.signal} left {f._from_band} m after {mins:.1f} min (on {f._from_band} m since "
                f"{f._since:%H:%M:%S}, allowed from {_minute(f._since) + ten:%H:%M}): {lost} – "
                f"first {q.call} {q.ts:%H:%M:%S}")
    return (f"{f.signal} band change #{profile.hourly_limit + 1} in the {q.ts:%H}z hour "
            f"(limit {profile.hourly_limit}): {f._from_band} → {q.band} m – {lost} "
            f"(until {_hour(q.ts) + timedelta(hours=1):%H:%M}) – first {q.call} {q.ts:%H:%M:%S}")


def live_warning(profile: Profile, result: Result, signal: str, radio_band: Optional[str],
                 now: datetime, transmitting: bool = False) -> Optional[str]:
    """Warn if a QSO logged now on `radio_band` by `signal` would be a fault.
    While N1MM reports TX there, it is a possible early transmission (not yet a logged breach)."""
    w = _live_warning(profile, result, signal, radio_band, now)
    if w and transmitting:
        return f"TX on {radio_band} m – possible early TX! " + w
    return w


def _live_warning(profile: Profile, result: Result, signal: str, radio_band: Optional[str],
                  now: datetime) -> Optional[str]:
    if not radio_band:
        return None
    key = ALL if profile.entry_wide else signal
    s = result.status.get(key)
    if profile.run_mult and signal == MULT:
        run = result.status.get(RUN)
        if run is not None and run.band == radio_band:
            return f"MULT radio is on {radio_band} m – the RUN band. A MULT QSO here is not allowed."
    if s is None or s.band is None or s.band == radio_band:
        return None
    if profile.rule == TEN_MIN and s.free_at and now < s.free_at:
        left = int((s.free_at - now).total_seconds())
        return (f"{key} radio is on {radio_band} m but {key} is held on {s.band} m until "
                f"{s.free_at:%H:%M:%S} ({left // 60}:{left % 60:02d} left). A QSO now = breach.")
    if profile.rule == HOURLY:
        # result must have been evaluated with the same `now`
        if s.used_this_hour >= profile.hourly_limit:
            return (f"{key} radio is on {radio_band} m but all {profile.hourly_limit} band changes "
                    f"for the {now:%H}z hour are used (on {s.band} m). A QSO now = breach.")
    return None


def utcnow() -> datetime:
    """Naive UTC 'now' (all timestamps in this package are naive UTC)."""
    from datetime import timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)

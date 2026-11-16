# N1MM_timer – design

A standalone band-change watcher for multi-operator contest stations running
N1MM Logger+. It is the "separate tool" asked for when PingPong PR #3 was
closed: the watcher logic lives here, and PingPong only shows the warnings.

## Phases

| Phase | What | Status |
|---|---|---|
| 1 | Listen to N1MM directly, evaluate the rules, show faults and live warnings in its own window (with sound) | this version |
| 1a | Read the N1MM contest database directly (live polling + `--db` log check) | this version |
| 1b | `--check` a Cabrillo log after the contest (or bootstrap the ledger from one) | this version |
| 1c | One PC: follow the log N1MM has open (Application Info), Stations overview, UDP 12060 | 0.2.0 |
| 2 | Push faults and live status to PingPong so the lamps can flash | JSON-over-UDP output already here (off by default); PingPong side still to do |

The tool is **advisory only**. It never keys, blocks or edits anything in N1MM
and never writes N1MM's database.

## Data flow

```
 N1MM PC #1 ─┐  contactinfo / contactreplace / contactdelete / RadioInfo / AppInfo
 N1MM PC #2 ─┼──── UDP XML ───►  N1MM_timer  (port 12060 by default)
 N1MM PC #n ─┘                       │
                                      ├─ store.py   SQLite ledger (timer.db), keyed by N1MM QSO GUID
                                      ├─ rules.py   full re-evaluation on every change → faults + status
                                      ├─ app.py     Tk window, red flash + beep on new fault, acknowledge
                                      └─ pingpong.py  (phase 2) JSON status to PingPong
```

N1MM setup on **every** networked PC: *Config → Configure Ports… → Broadcast
Data*: tick **Contacts** and **Radio** (and **App Info**) and add
`<timer-ip>:12060` (`127.0.0.1:12060` on the timer PC itself) to each address (N1MM accepts several space-separated
destinations, so PingPong's own entries stay as they are).

### N1MM database source

N1MM Logger+ keeps each contest in a SQLite file (`Documents\N1MM Logger+\Databases\*.s3db`).
The layout was checked against a real 2026 database (N1MM 1.0.111xx):

* `ContestInstance`: `ContestNR`, `ContestName` (CQWWCW, CQWPXSSB…), `StartDate`,
  `TransmitterCategory` (ONE → MS, TWO → M2). This fills in the category that UDP lacks.
* `DXLOG`: `TS` (UTC text), `Band` (MHz float), `Freq` (kHz), `IsRunQSO`, `Run1Run2`,
  `RadioNR`, `NetBiosName` (PC), `IsMultiplier1/2`, `isMultiplier3`, `ID` (same GUID as in UDP).
* Deleted QSOs move to the `DELETEDQS` instance (`ContestNR = -1`).

The watcher opens the file `mode=ro`, checks `PRAGMA data_version` every 5 s, and only re-reads
when N1MM has committed something. It then reconciles the ledger: adds, edits, and deletes
(rows gone or moved to −1). QSOs outside `StartDate … +48 h` are ignored by default (test QSOs).
It has to run on an N1MM PC, because SQLite in WAL mode can't be read safely over SMB.

### Following the open log

N1MM's *Application Info* packet carries `<dbname>` (a full path or only the file name) and
`<contestnr>`. When one arrives from this PC (a loopback or local address), the timer
resolves the name against the N1MM database folders and points the database poller at that
file and contest. Opening another log in N1MM switches the timer to it, with a fresh ledger;
acknowledgements are kept. Until such a packet arrives, the most recently written contest
database (`.s3db` or its `-wal`, never *N1MM Admin* / *Packet Spots*) is used and marked
*guessed*. The followed log is remembered for the next start.

### UDP port

Default 12060. Several ports are allowed. Each port is bound exclusively (`SO_EXCLUSIVEADDRUSE`
on Windows, no `SO_REUSEADDR`). Windows delivers a unicast datagram to only one of several
sockets sharing a port, so a shared port means silently missing packets. The timer refuses
the port and says which one instead.

### Why full re-evaluation

Each QSO add, replace or delete re-runs the rules over the whole sorted
ledger. A 10,000-QSO contest takes milliseconds. This makes edits, deletes,
late or out-of-order packets and duplicates (several PCs broadcasting the same
GUID) correct by construction, with no incremental bookkeeping to go wrong.

Each fault gets a stable id (`kind:qso_guid`), so acknowledgements survive
re-evaluation and restarts. Only a fault id that has never been seen before
makes the beep, so N1MM's delete-then-reinsert edits don't re-trigger alarms.

## Signal identity

Every QSO belongs to one signal: **RUN / MULT** (Multi-Single) or **TX1 / TX2** (Multi-Two).
By default the timer takes it from N1MM's **`Run1Run2`** field. That field is N1MM's
station/signal designation and what it writes as the Cabrillo transmitter id:

| Category | `Run1Run2 = 1` | `Run1Run2 = 2` |
|---|---|---|
| MS (CQ WW) | RUN | MULT |
| M2 | TX1 | TX2 |
| MS (CQ WPX Multi-One) | one entry-wide counter, the field is not needed | |

Several PCs may share one signal (e.g. RUN logged from 4 PCs). `signal_map` overrides it per
PC (`PCE=RUN, MULT2=MULT`), and `signal_source` can switch to `radionr` or `station`.

N1MM's **`IsRunQSO` is never used**. It records Run (CQ) vs S&P, not RUN vs MULT: a run
station's S&P QSO is still the RUN signal. This was the mistake in 0.1.0, and PR #3
pointed it out.

Verified on real 2026 databases:

* **9A7A, CQ WW SSB, Multi-Single** (6,641 QSOs, 5 PCs). With `Run1Run2` there are 0 MULT
  QSOs on the RUN band and 3 ten-minute breaches. With `IsRunQSO` there would be 2,900 faults.
* **CR3A, CQ WPX SSB, Multi-Two** (11,640 QSOs, 5 PCs on 2 TX ids). With `Run1Run2` there are
  7 hours with 9–10 changes on one TX. Counting per PC would hide all of them.

N1MM databases store **no multiplier flags** (`IsMultiplier1/2/3` are 0 for all 6,641 QSOs
in the 9A7A log), so "the MULT may only work new multipliers" is not checked.

## Rule profiles (2026 rules)

| Profile | Category | Counted per | Rule |
|---|---|---|---|
| CQ WW CW / SSB | MS | RUN, MULT independently | **10-minute rule**: after the first QSO on a band, the signal stays on that band ≥10 min. MULT must be on a different band than RUN. |
| CQ WW CW / SSB | M2 | TX1, TX2 | max **8** band changes per clock hour |
| CQ WW RTTY | MS | RUN, MULT independently | max **8** band changes per clock hour each. MULT on a different band than RUN. |
| CQ WW RTTY | M2 | TX1, TX2 | max **8** per clock hour |
| CQ WPX CW / SSB / RTTY | Multi-One | whole entry | max **10** band changes per clock hour |
| CQ WPX CW / SSB / RTTY | M2 | TX1, TX2 | max **8** per clock hour |

That's 12 profiles (WW/WPX × CW/SSB/RTTY × MS/M2), the same set as PR #3.

Counting conventions (CQ FAQs):

* A band change counts at the time of the **first QSO on the new band**.
* The clock hour is minute 00–59 UTC. The band carries over the hour boundary,
  so a 12:59 QSO on 15 m followed by a 13:01 QSO on 20 m is a 13z change.
* 20 → 40 → 20 is **two** changes.
* Reaching the limit is allowed. Only the change that goes past it is a breach.
* 10-minute rule: the period starts with the first QSO on the band. Logged QSOs are
  judged in **whole minutes**, like the CQ log checker, which only sees Cabrillo
  minutes: first QSO at 14:00:30 allows a change logged at 14:10:05. The *live*
  countdown is stricter and counts from the exact second (free at 14:10:30). In the
  9A7A log this removes 4 seconds-only "breaches" and leaves the 3 real ones.

> ⚠ The WPX RTTY Multi-Two hourly limit is assumed to be 8 (as in WPX CW/SSB).
> Check it against the 2027 WPX RTTY rules before February.

## Fault kinds

| Kind | Meaning |
|---|---|
| `TEN_MIN` | Signal changed band less than 10 min after its period started |
| `HOURLY` | Band change exceeded the hourly limit for the counter |
| `MULT_SAME_BAND` | MULT QSO on the band RUN is currently on (WW MS) |

Faults come from **logged QSOs**. While N1MM reports a transmission (`IsTransmitting`) on a
band where a QSO would break a rule, the live warning becomes **"possible early TX"**. It is
still not a logged breach, and transmissions N1MM doesn't start itself (PTT, paddle) are
not reported. Separately, **live warnings** use RadioInfo:
"RUN radio is on 20 m but RUN is locked to 40 m until 14:32:10. A QSO now
would be a breach." These clear by themselves and don't make the beep.

## Live status shown per signal

* current band and time of the first QSO there
* 10-minute rule: countdown to *free to move*
* hourly rule: `changes used / limit` this hour, and how many are left

## Storage

`timer.db` (SQLite, next to the exe): `qsos` (GUID, UTC timestamp, band, mode,
call, station, radio, flags, deleted), `acks` (fault id, time) and `meta`. A
restart keeps the ledger, so if the watcher PC reboots mid-contest, only the
QSOs logged while it was down are missing. Importing the Cabrillo file (or
N1MM re-broadcasting) fills the gap. Nothing proves that every UDP packet
arrived, so the window shows the packet count and the age of the last packet.

## Phase 2 – PingPong interface (proposal)

When `[pingpong] enabled = true`, every 2 s and on every change the watcher
sends one UDP datagram of UTF-8 JSON to `host:port`:

```json
{
  "type": "n1mm_timer.status", "version": 1, "seq": 1234,
  "sent_utc": "2026-11-28T14:21:05Z",
  "profile": "CQWW-CW-MS",
  "signals": [
    {"signal": "RUN",  "band": "40", "rule": "TEN_MIN", "free_at": "2026-11-28T14:25:00Z"},
    {"signal": "MULT", "band": "20", "rule": "TEN_MIN", "free_at": null}
  ],
  "faults": [
    {"id": "TEN_MIN:{guid}", "kind": "TEN_MIN", "signal": "RUN",
     "time": "2026-11-28T14:19:30Z", "station": "RUN-PC", "text": "…", "acked": false}
  ],
  "warnings": [{"station": "MULT-PC", "signal": "MULT", "text": "…"}]
}
```

PingPong only has to listen on that port, flash the lamps while
`faults[*].acked == false` (or only on the station named in the fault), and
show `warnings` on the matching station's lamp. Acknowledgement stays in the
watcher for now. A later version can accept an `ack` datagram back over
PingPong's existing authenticated command channel.

## Not covered (same limits as PR #3)

Multiplier *eligibility* beyond N1MM's own flag, "mult station must not CQ",
two signals on one band at the same moment (M2), RF/PTT verification, and
QSOs that never reached the watcher.

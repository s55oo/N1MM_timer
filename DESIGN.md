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
| 2 | Push faults and live status to PingPong so the lamps can flash | JSON-over-UDP output already here (off by default); PingPong side still to do |

The tool is **advisory only**. It never keys, blocks or edits anything in N1MM
and never writes N1MM's database.

## Data flow

```
 N1MM PC #1 ─┐  contactinfo / contactreplace / contactdelete / RadioInfo / AppInfo
 N1MM PC #2 ─┼──── UDP XML ───►  N1MM_timer  (port 12064 by default)
 N1MM PC #n ─┘                       │
                                      ├─ store.py   SQLite ledger (timer.db), keyed by N1MM QSO GUID
                                      ├─ rules.py   full re-evaluation on every change → faults + status
                                      ├─ app.py     Tk window, red flash + beep on new fault, acknowledge
                                      └─ pingpong.py  (phase 2) JSON status to PingPong
```

N1MM setup on **every** networked PC: *Config → Configure Ports… → Broadcast
Data*: tick **Contacts** and **Radio** (and **App Info**) and add
`<watcher-ip>:12064` to each address (N1MM accepts several space-separated
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

### Why full re-evaluation

Each QSO add, replace or delete re-runs the rules over the whole sorted
ledger. A 10,000-QSO contest takes milliseconds. This makes edits, deletes,
late or out-of-order packets and duplicates (several PCs broadcasting the same
GUID) correct by construction, with no incremental bookkeeping to go wrong.

Each fault gets a stable id (`kind:qso_guid`), so acknowledgements survive
re-evaluation and restarts. Only a fault id that has never been seen before
makes the beep, so N1MM's delete-then-reinsert edits don't re-trigger alarms.

## Signal identity

The user chose to use **N1MM's own Run/Mult flag**:

* **MS (WW)**: `IsRunQSO = True` → **RUN**, otherwise **MULT**. N1MM writes
  the same flag as the Cabrillo transmitter id (0 = run, 1 = mult).
* **MS (WPX, Multi-One)**: one entry-wide counter. The flag is shown but not
  used for the count.
* **M2**: Run/Mult doesn't say which of the two transmitters made the QSO, so
  the TX comes from a configurable source (`m2_signal_source`):
  `run1run2` (default: N1MM's Run1/Run2 = Cabrillo TX id), `station` (one PC per TX), or `radionr`.
  In the real CR3A WPX SSB 2026 database, 5 PCs shared 2 TX ids, so per-PC counting
  found 0 problems while per-TX counting found 7 hours with 9–10 changes.

> Seen in a real database: `IsRunQSO` 0/1, `Run1Run2` 1/2, `RadioNR` 1. The UDP parser
> accepts `True/False/1/0`. Still to verify on a live network: the UDP packets carry the same values.

## Rule profiles (2026 rules)

| Profile | Category | Counted per | Rule |
|---|---|---|---|
| CQ WW CW / SSB | MS | RUN, MULT independently | **10-minute rule**: after the first QSO on a band, the signal stays on that band ≥10 min. MULT must be on a different band than RUN and may only work new multipliers. |
| CQ WW CW / SSB | M2 | TX1, TX2 | max **8** band changes per clock hour |
| CQ WW RTTY | MS | RUN, MULT independently | max **8** band changes per clock hour each. MULT on a different band than RUN, new mults only. |
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
* 10-minute rule: a change is allowed at `period_start + 10:00` or later. The
  period starts with the first QSO on the band.

> ⚠ The WPX RTTY Multi-Two hourly limit is assumed to be 8 (as in WPX CW/SSB).
> Check it against the 2027 WPX RTTY rules before February.

## Fault kinds

| Kind | Meaning |
|---|---|
| `TEN_MIN` | Signal changed band less than 10 min after its period started |
| `HOURLY` | Band change exceeded the hourly limit for the counter |
| `MULT_SAME_BAND` | MULT QSO on the band RUN is currently on (WW MS) |
| `MULT_NOT_NEW` | MULT QSO that N1MM did not flag as a multiplier (WW MS, live only) |

Faults come from **logged QSOs**. Separately, **live warnings** use RadioInfo:
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

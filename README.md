# N1MM_timer

Band-change watcher for multi-operator contest stations running **N1MM Logger+**.
Run it on **one** PC. It follows the log N1MM has open on that PC (read-only) and
the N1MM network traffic (UDP, port 12060 by default). It then:

* checks every logged QSO against the 2026 CQ WW / CQ WPX band-change rules;
* shows a **Stations** overview: every N1MM radio with band, mode, frequency,
  Run/S&P, TX, which signal it is (RUN/MULT/TX1/TX2) and its band-rule status;
* warns live when a radio sits on a band where a QSO *now* would break a rule.

Advisory only: it never touches N1MM, the radios or the log.
See [DESIGN.md](DESIGN.md) for the rules, the data flow and the planned
PingPong interface.

| Contest | Multi-Single | Multi-Two |
|---|---|---|
| CQ WW CW / SSB | 10-minute rule, RUN and MULT separately. MULT on another band, new mults only | 8 band changes / clock hour per TX |
| CQ WW RTTY | 8 band changes / clock hour, RUN and MULT separately | 8 / hour per TX |
| CQ WPX CW / SSB / RTTY | 10 band changes / clock hour, whole entry | 8 / hour per TX |

## Quick start

1. Python 3.10+ on Windows (tkinter is included in the python.org installer). No extra packages.
2. `python run_timer.py` starts the window and writes `n1mm_timer.ini` next to it.
3. In N1MM on **every** networked PC: *Config → Configure Ports, Mode Control, Winkey, etc. →
   Broadcast Data*. Tick **Radio**, **Contacts** and **Application Info**, and add the timer PC
   to each of those lines (several destinations are separated by a space):
   * on the timer PC itself: `127.0.0.1:12060`
   * on the other PCs: the timer PC's address, e.g. `192.168.1.10:12060`, or your subnet
     broadcast, e.g. `192.168.1.255:12060`.

   N1MM sends only to the addresses listed. `127.0.0.1` reaches only programs on the same PC.
4. The timer follows the log N1MM has open on its PC (`[n1mm_db] path = auto`). Contest and
   category (ONE/TWO) come from that log's contest setup. Without a database, choose
   **Category** MS or M2 in the window.

**One port, one program.** Windows hands each N1MM packet on a port to only one program. If
another program on the timer PC already listens on 12060, the timer says so (red, top of the
window) instead of silently missing packets. Then give the timer another port
(`[udp] port = 12064`, several allowed: `12060, 12064`) and add that port to N1MM's lines.

Try it without N1MM:

```
python run_timer.py            # window 1
python tools/simulate_n1mm.py   # window 2 – 4 faults + 2 live warnings in ~20 s
```

Check a log (exit code 1 if faults were found):

```
python run_timer.py --db "%USERPROFILE%\Documents\N1MM Logger+\Databases\CR3A_WPCSSB_2026.s3db"
python run_timer.py --db FILE.s3db --list              # contests in the database
python run_timer.py --db FILE.s3db --contest-nr 2 --m2-source station
python run_timer.py --check S50A.cbr                    # Cabrillo instead of the database
```

## Live sources

* **The open N1MM log** (default, **Follow N1MM log** button / `[n1mm_db] path = auto`). The
  timer reads which database and contest N1MM has open from N1MM's **Application Info** packet
  (sent to `127.0.0.1:12060` on the same PC). When you open another log in N1MM, the timer
  switches to it and starts a fresh ledger. Until an Application Info packet arrives, it uses the
  log database N1MM wrote to most recently (shown as *guessed*). **N1MM database…** picks a fixed
  file instead. The database is polled every 5 s (only re-read when N1MM wrote something). It sees
  every QSO, every edit and every delete even if UDP packets were lost, and takes the
  contest and category (ONE/TWO) from N1MM's own contest setup. Opened read-only, never
  written. Run the watcher on a PC where N1MM runs: on an N1MM network every PC has the
  full synchronised log, and SQLite (WAL) must not be read over a network share.
* **UDP from every N1MM PC** (Radio, Contacts, Application Info): live radio positions for the
  Stations overview and the warnings, and the QSOs if the timer PC has no N1MM database.

Both can run together. QSOs are matched by N1MM's QSO GUID, so nothing is counted twice.

Console only (no window): `python run_timer.py --console`

## Stations overview

One line per N1MM radio (from its Radio packets) plus PCs known only from the log:

| Column | |
|---|---|
| Station, R, Op | N1MM StationName, radio number, operator |
| Band, Mode, Freq, Run/S&P | from N1MM |
| TX | `TX F1 CQ` while N1MM transmits (only transmissions N1MM starts itself; PTT/paddle aren't reported) |
| Signal | RUN / MULT (MS) or TX1 / TX2 (M2): from that PC's latest logged QSO |
| Band rule | `held on 40 m – 3:20`, `free (on 20 m)`, `7/8 changes 14z (TX1) – 1 left`, or the live warning |
| Last QSO, QSOs, Seen | from the log, and the age of the last Radio packet (`log only` = no Radio packets) |

Faults and warnings show in the timer's own window (red flash + beep for new faults).
Passing them on to the PingPong lamps comes later (see DESIGN.md, *PingPong interface*).

## How signals are identified

* **MS**: N1MM's Run/Mult flag (`IsRunQSO`) → RUN or MULT, the same as the
  Cabrillo transmitter id N1MM writes (0/1).
* **M2**: `m2_signal_source` = `run1run2` (default: N1MM's Run1/Run2 field, i.e. the
  Cabrillo TX id; several PCs may share one TX), `station` (one PC per TX) or `radionr`.
  In the CR3A WPX SSB 2026 log, five PCs (PCE, PC1, PCC, MULT2, PCB) logged on two TX ids,
  so only `run1run2` counts the changes correctly.

Check these against real N1MM packets on your network before the contest.

## Files

| File | |
|---|---|
| `n1mm_timer/rules.py` | profiles + rule engine (pure, unit-tested) |
| `n1mm_timer/core.py` | watcher: packets + open log → ledger → rules → faults, warnings, Stations |
| `n1mm_timer/n1mm.py` | N1MM UDP XML parser + listener |
| `n1mm_timer/n1mmdb.py` | read-only N1MM `.s3db` reader (DXLOG, ContestInstance) |
| `n1mm_timer/store.py` | SQLite ledger `timer.db` (survives restarts) |
| `n1mm_timer/cabrillo.py` | Cabrillo reader (`--check`, gap-filling import) |
| `n1mm_timer/app.py` | Tk window: signal tiles, warnings, faults, acknowledge, flash + beep |
| `n1mm_timer/pingpong.py` | phase 2: JSON status for PingPong (`[pingpong] enabled = true`) |
| `tools/simulate_n1mm.py` | fake N1MM packets for testing |

Tests: `python -m unittest discover -s tests -t .`

## Windows executable (Defender-friendly build)

`build.bat` (local) and `.github/workflows/build.yml` (GitHub, on every push, tag `vX.Y.Z` →
Release) produce `N1MM_timer-<version>-win64.zip` with `N1MM_timer.exe` (window) and
`N1MM_timer_console.exe` (`--console`, `--check`, `--db`). The zip comes with `SHA256SUMS.txt`.

These measures keep Microsoft Defender and other antivirus tools from flagging the build:

| Measure | Why |
|---|---|
| PyInstaller **bootloader compiled from source**, verified to differ from the prebuilt one | The prebuilt bootloader is shared by every PyInstaller app, including malware, so heuristics key on it |
| **One-folder** build, not `--onefile` | No unpacking of a payload into `%TEMP%` at every start |
| **No UPX** | Packed executables are a classic heuristic trigger |
| **VERSIONINFO** resource (company, product, version) | The exe isn't anonymous |
| **Defender scan** of the output (CI fails on a detection) | Catches a flagged build before anyone downloads it |
| **SHA-256** + GitHub **build-provenance attestation** | Anyone can check the zip came unmodified from this repo's CI: `gh attestation verify N1MM_timer-0.1.0-win64.zip --repo s55oo/N1MM_timer` |
| Optional **Authenticode signing** | The only thing that removes SmartScreen's "unknown publisher" warning |

The program itself is advisory and needs no admin rights. It opens the N1MM database
read-only, and it drops UDP packets that are oversized or contain XML DTD/entity
declarations. If its folder is read-only (e.g. Program Files), it keeps
`n1mm_timer.ini` / `timer.db` in `%LOCALAPPDATA%\N1MM_timer`.

### Signed builds

Add two repository secrets: `SIGN_PFX_BASE64` (a code-signing certificate .pfx, base64-encoded)
and `SIGN_PFX_PASSWORD`. CI then signs and verifies both exes before the Defender scan.
Locally: `set SIGN_PFX=...` and `set SIGN_PFX_PASSWORD=...` before `build.bat`, from a
Developer Command Prompt (for `signtool`). Ways to get a certificate:

* **Azure Artifact Signing** (formerly Trusted Signing): Microsoft's own service, about US$10/month.
  It needs identity validation, and eligibility depends on country and on individual vs. organization.
* **SignPath Foundation**: free signing for open-source projects that qualify.
* A commercial OV/EV code-signing certificate.

A self-signed certificate does **not** help with SmartScreen or Defender.

If a build is still flagged, submit the zip as a false positive at
https://www.microsoft.com/en-us/wdsi/filesubmission. The CI Defender scan stops a flagged
build from being published.

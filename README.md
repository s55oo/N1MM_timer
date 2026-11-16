# N1MM_timer

Band-change watcher for multi-operator contest stations running **N1MM Logger+**.
It reads the N1MM contest database directly (read-only) and/or listens to N1MM's
UDP broadcasts, and checks every logged QSO against the
2026 CQ WW / CQ WPX band-change rules. It also warns live when a radio sits on
a band where a QSO *now* would break a rule.

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
   Broadcast Data*. Tick **Contacts**, **Radio** and **App Info**, and add the watcher PC's
   `IP:12064` to each address (separate several destinations with a space).
4. In the window, choose **Category** MS or M2 (N1MM doesn't broadcast it). The contest is
   picked up from N1MM (`profile = auto`), or you can choose it by hand.

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

* **N1MM database** (recommended on an N1MM PC): *N1MM database…* button or `[n1mm_db] path`
  in `n1mm_timer.ini`. Polled every 5 s (only re-read when N1MM wrote something). It sees
  every QSO, every edit and every delete even if UDP packets were lost, and takes the
  contest and category (ONE/TWO) from N1MM's own contest setup. Opened read-only, never
  written. Run the watcher on a PC where N1MM runs: on an N1MM network every PC has the
  full synchronised log, and SQLite (WAL) must not be read over a network share.
* **UDP** (for a watcher PC without N1MM, and for live radio warnings, which need
  RadioInfo packets either way).

Both can run together. QSOs are matched by N1MM's QSO GUID, so nothing is counted twice.

Console only (no window): `python run_timer.py --console`

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
| `n1mm_timer/n1mm.py` | N1MM UDP XML parser + listener |
| `n1mm_timer/n1mmdb.py` | read-only N1MM `.s3db` reader (DXLOG, ContestInstance) |
| `n1mm_timer/store.py` | SQLite ledger `timer.db` (survives restarts) |
| `n1mm_timer/core.py` | watcher: packets → ledger → rules → faults/warnings |
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

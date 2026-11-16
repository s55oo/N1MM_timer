# N1MM_timer

Band-change watcher for multi-operator contest stations running **N1MM Logger+**.
Run it on **one** PC. It follows the log N1MM has open on that PC (read-only) and
the N1MM network traffic (UDP, port **12063** by default). It then:

* checks every logged QSO against the 2026 CQ WW / CQ WPX band-change rules;
* shows a **Stations** overview: every N1MM radio with band, mode, frequency,
  Run/S&P, TX, which signal it is (RUN/MULT/TX1/TX2) and its band-rule status;
* warns live when a radio sits on a band where a QSO *now* would break a rule.

Advisory only: it never touches N1MM, the radios or the log.
See [DESIGN.md](DESIGN.md) for the rules, the data flow and the planned
PingPong interface.

| Contest | Multi-Single | Multi-Two |
|---|---|---|
| CQ WW CW / SSB | 10-minute rule, RUN and MULT separately (judged in whole minutes, like the CQ checker). MULT on another band | 8 band changes / clock hour per TX |
| CQ WW RTTY | 8 band changes / clock hour, RUN and MULT separately | 8 / hour per TX |
| CQ WPX CW / SSB / RTTY | 10 band changes / clock hour, whole entry | 8 / hour per TX |

## Quick start

1. Python 3.10+ on Windows (tkinter is included in the python.org installer). No extra packages.
2. `python run_timer.py` starts the window and writes `n1mm_timer.ini` next to it.
3. In N1MM on **every** networked PC: *Config → Configure Ports, Mode Control, Winkey, etc. →
   Broadcast Data*. Tick **Radio**, **Contacts** and **Application Info**, and add the timer PC
   to each of those lines (several destinations are separated by a space):
   * on the timer PC itself: `127.0.0.1:12063`
   * on the other PCs: the timer PC's address, e.g. `192.168.1.10:12063`, or your subnet
     broadcast, e.g. `192.168.1.255:12063`.

   Keep the existing destinations, e.g. the PingPong lamp's `127.0.0.1:12062`. A Radio line
   then looks like `127.0.0.1:12062 127.0.0.1:12063`.

   N1MM sends only to the addresses listed. `127.0.0.1` reaches only programs on the same PC.
4. The timer follows the log N1MM has open on its PC (`[n1mm_db] path = auto`). Contest and
   category (ONE/TWO) come from that log's contest setup. Without a database, choose
   **Category** MS or M2 in the window.

**One port, one program.** Windows hands each N1MM packet on a port to only one program. If
another program on the timer PC already listens on 12063, the timer says so (red, top of the
window) instead of silently missing packets. Then give the timer another port
(`[udp] port = 12065`, several allowed: `12063, 12065`) and add that port to N1MM's lines.
Upgrading from 0.1.0? Its `n1mm_timer.ini` still says `port = 12064`; change it to 12063.

Try it without N1MM:

```
python run_timer.py            # window 1
python tools/simulate_n1mm.py   # window 2 – 3 faults + 2 live warnings in ~20 s
```

Check a log (exit code 1 if faults were found):

```
python run_timer.py --db "%USERPROFILE%\Documents\N1MM Logger+\Databases\CR3A_WPCSSB_2026.s3db"
python run_timer.py --db FILE.s3db --list              # contests in the database
python run_timer.py --db FILE.s3db --contest-nr 2 --signal-source station
python run_timer.py --check S50A.cbr                    # Cabrillo instead of the database
```

## Live sources

* **The open N1MM log** (default, **Follow N1MM log** button / `[n1mm_db] path = auto`). The
  timer reads which database and contest N1MM has open from N1MM's **Application Info** packet
  (sent to `127.0.0.1:12063` on the same PC). When you open another log in N1MM, the timer
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

## At the contest: INFO before, FAULT after

Run the timer on **one** PC. It follows the log N1MM has open there. N1MM keeps that log in
sync with every networked PC, so the timer sees each QSO from each station within seconds,
including edits and deletes.

1. **INFO, before a faulty entry.** A radio is on a band where a QSO *now* would break the rule.
   That station's row in the Stations overview turns red with the reason, and the INFO list
   below it shows it too, e.g.
   `RUN radio is on 15 m but RUN is held on 20 m until 14:32:10 (3:20 left). A QSO now = breach.`
   While N1MM transmits there, it says **possible early TX**. INFO clears on its own when the
   radio goes back or the time runs out. It makes no sound.
2. **FAULT, after a faulty entry.** The QSO that broke the rule is logged. It goes into the
   fault list with every QSO a log checker would remove, e.g.
   `11 QSOs on 20 m (09:52:39–09:58:58) would be removed`. The window flashes red and beeps
   until someone clicks **Acknowledge**.
3. **BAND VIOLATION on every PingPong screen** (PingPong v1.117 or newer). Every FAULT that is
   not acknowledged goes to the PingPong lamp on the timer PC (UDP `127.0.0.1:46071`). That lamp
   passes it on to every lamp and the Overview, which show a red footer, e.g.
   `BAND VIOLATION: RUN 20→15 m 14:29 – 3 QSOs`, with `· YOUR STATION` on the station that
   logged it. **Acknowledge** in the timer and it disappears everywhere within 2 s. Run the timer
   on the **PingPong master PC**. On by default (`[pingpong_alert] enabled = true`); with no
   PingPong running it's harmless. Details: [docs/PINGPONG_INTERFACE.md](docs/PINGPONG_INTERFACE.md).

**INFO for the other stations** needs their radio data. Each N1MM sends only its own radio's
band, so add `<timer PC IP>:12063` (or your subnet broadcast, e.g. `192.168.1.255:12063`) to the
**Radio** line on every other N1MM PC, and allow N1MM_timer in Windows Firewall (private
networks). Without that, INFO works only for the timer PC's own radio. FAULTs work for all
stations either way.

**Local only.** If the timer PC's N1MM is the only sender (`127.0.0.1:12063`), set
`[udp] bind = 127.0.0.1`. Then nothing from the network can reach the timer, and Windows
Firewall doesn't ask.

**Test before the contest:**
1. Open a test log on two networked PCs (e.g. CQ WW CW, category Multi-Single).
2. Log a RUN QSO on 40 m.
3. On the other PC, tune to 20 m: the timer shows INFO.
4. Log a RUN QSO there within 10 minutes: the timer shows a FAULT, flashes and beeps.

## How signals are identified

Default: N1MM's **Run1/Run2** field, the Cabrillo transmitter id. In Multi-Single, 1 = RUN and
2 = MULT. In Multi-Two, 1 = TX1 and 2 = TX2. Several PCs may share one signal. N1MM's Run/S&P
flag is **not** used, because a run station's S&P QSO is still RUN. Overrides go in
`n1mm_timer.ini`:

```
[contest]
signal_source = run1run2        ; or radionr, station
signal_map = PCE=RUN, MULT2=MULT
```

This was checked against two real 2026 logs: 9A7A CQ WW SSB Multi-Single (3 ten-minute breaches,
0 MULT-on-RUN-band) and CR3A CQ WPX SSB Multi-Two (7 hours over 8 changes). Details are in DESIGN.md.

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
| `n1mm_timer/pingpong.py` | BAND VIOLATION alerts to PingPong (interface v1, port 46071) |
| `tools/simulate_n1mm.py` | fake N1MM packets for testing |
| `CHANGELOG.md` | what changed in each release |

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

# Changelog

## 0.4.0
- Send CQ WW DX activity, band timers and hourly change counts to PingPong v1.118.
- Include empty contest instances in the database picker. Flag incomplete log rows, invalid bands and ambiguous band order on the lamps.

## 0.3.0 – 2026-11-16
- **BAND VIOLATION on every PingPong screen** (PingPong v1.117+). Unacknowledged faults go to
  the PingPong lamp on the same PC (UDP 127.0.0.1:46071, interface v1), every 2 s and at once
  on change. Acknowledging in the timer clears it everywhere. On by default
  (`[pingpong_alert]`). The old draft output (`[pingpong]`, port 12065) is removed.
- Tested against a copy of PingPong's own receiver (`tests/compat/pingpong_bandalert.py`).
- Docs: `docs/PINGPONG_INTERFACE.md` with the station's port map. The example second UDP port
  is now 12065 (12064 was 0.1.0's default).

## 0.2.1 – 2026-11-16
- Docs: contest workflow (INFO before a faulty entry, FAULT after it, PingPong later), INFO
  for other stations, local-only mode (`[udp] bind = 127.0.0.1`), test before the contest.
- The window and console call live warnings **INFO**.

## 0.2.0 – 2026-11-16
- One-PC setup: follows the log N1MM has open (Application Info, else the most recently
  written log). Stations overview. UDP port 12063 by default. A port shared with another
  program is reported instead of silently missing packets.
- RUN/MULT (Multi-Single) and TX1/TX2 (Multi-Two) come from N1MM's Run1Run2 field, the
  Cabrillo transmitter id. N1MM's Run/S&P flag is no longer used. `signal_map` sets a per-PC
  override.
- The 10-minute rule is judged in whole minutes, like the CQ log checker. The live countdown
  stays on exact seconds.
- A fault lists every QSO that would be removed until the signal is back in compliance
  (CQ WW / WPX FAQs).
- Removed: the "MULT not a new multiplier" check (N1MM databases store no multiplier flags).
- New: "possible early TX" INFO while N1MM transmits on a band that would break the rule.
- Checked on real 2026 logs: 9A7A CQ WW SSB Multi-Single (3 faults, 15 QSOs at risk) and
  CR3A CQ WPX SSB Multi-Two (5 hours over the limit, 64 QSOs at risk).

## 0.1.0 – 2026-11-16
- First release: 12 CQ WW / WPX 2026 rule profiles, N1MM UDP listener, N1MM database and
  Cabrillo log checks, Tk window, Defender-friendly Windows build.

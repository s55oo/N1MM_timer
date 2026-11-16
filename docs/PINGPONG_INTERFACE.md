# N1MM_timer → PingPong: BAND VIOLATION (interface v1)

Implemented by **N1MM_timer 0.3.0** (`n1mm_timer/pingpong.py`) and **PingPong v1.117**
(`pingpong_bandalert.py`, `pingpong_lamp_band.py`). The tests in `tests/test_pingpong.py` run
every message through a copy of PingPong's own receiver (`tests/compat/pingpong_bandalert.py`).

## Ports on the station PC

| Port | Direction | Used by |
|---|---|---|
| 12060 | N1MM → add-ons | QSOrouter, Callbooker (N1MM's usual port) |
| 12062 | N1MM → PingPong lamp | Radio data |
| 12063 | N1MM → N1MM_timer | Radio, Contacts, Application Info |
| 13064 | POUNCer → N1MM | commands (QSY, wipe, CAT) |
| 46070 | PingPong lamp ↔ lamp | PingPong network (signed) |
| **46071** | **N1MM_timer → PingPong lamp** | **this interface, 127.0.0.1 only** |

(12064 was N1MM_timer 0.1.0's default. An old `n1mm_timer.ini` can still hold it, so change it to 12063.)

## Transport

1. N1MM_timer runs on the **PingPong master PC**. It sends one UTF-8 JSON datagram (at most 1400
   bytes) to UDP **127.0.0.1:46071** every **2 s** as a heartbeat, and at once when the open
   violations change. The latest message replaces the previous one.
2. The lamp on that PC accepts 46071 **only from 127.0.0.1** (the trust boundary is "same PC",
   so no password is needed). It passes the alert on to every lamp and the Overview over
   PingPong's signed network (UDP 46070). Other lamps accept it only that way, and only from
   the master.

## Message

```json
{
  "type": "n1mm_timer.alert", "v": 1,
  "instance": "3f9c1a7e",            // random per N1MM_timer start
  "seq": 1234,                        // +1 per message
  "sent_utc": "2026-11-28T14:31:05Z",
  "ttl_s": 10,
  "status": "violation",              // "ok" | "violation"
  "profile": "CQWW-CW-MS",
  "violations": [                     // max 5, newest first, only not acknowledged
    {"id": "TEN_MIN:6f2c…", "kind": "TEN_MIN",       // TEN_MIN | HOURLY | MULT_SAME_BAND
     "signal": "RUN", "station": "PC2",              // N1MM StationName that logged it
     "from_band": "20", "to_band": "15",
     "first_utc": "2026-11-28T14:29:40Z", "call": "K1AR",
     "qsos": 3,                                      // QSOs a log checker would remove
     "text": "RUN 20→15 m 14:29 – 3 QSOs"}           // ≤ 60 characters
  ]
}
```

Limits as PingPong v1.117 checks them:
- `id` ≤ 120 characters.
- `signal`, `station`, `from_band`, `to_band`, `call` and `profile` ≤ 40 characters each.
- `text` 1–60 characters.
- `ttl_s` between 1 and 300.
- A message older than `ttl_s` + 2 s is dropped.
- `seq` must increase for the same `instance`; a new `instance` (the timer restarted)
  starts over.

N1MM_timer shortens long fields, and if the datagram would be bigger than 1400 bytes it drops
the oldest violations first.

## What PingPong shows (every lamp and the Overview)

- `status` = `"violation"`: a red footer `BAND VIOLATION: <text of the newest>` plus
  `(+N more)`. On the lamp whose own N1MM StationName equals `station`, it adds
  `· YOUR STATION`. The sound plays once per new `id`, following each lamp's sound setting.
- `status` = `"ok"` or an empty list: no footer.
- No valid message for longer than `ttl_s`: the footer clears, and a grey `Band watcher offline`
  shows if the timer was heard before.
- Advisory only. It never touches the ping-pong lock, F-key blocking, radios or N1MM.

## Acknowledging

Acknowledging stays in N1MM_timer. An acknowledged fault leaves `violations`, so it disappears
from every PingPong screen within 2 s. A later v2 may add an ack message from PingPong back to
the timer.

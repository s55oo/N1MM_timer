"""Send fake N1MM Logger+ UDP packets to N1MM_timer for testing without N1MM.

  python tools/simulate_n1mm.py                 CQ WW CW MS scenario with a 10-minute breach
  python tools/simulate_n1mm.py --speed 60      run 60x faster than real time
  python tools/simulate_n1mm.py --host 192.168.1.20 --port 12060

Timestamps are 'now' (UTC) shifted by the scenario offsets, so live countdowns work.
"""
import argparse
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone

FREQ = {"160": 182500, "80": 352500, "40": 702500, "20": 1402500, "15": 2102500, "10": 2802500}
MHZ = {"160": "1.8", "80": "3.5", "40": "7", "20": "14", "15": "21", "10": "28"}

# (minute offset, station, band, call, is_run, is_mult)
SCENARIO = [
    (0.0, "RUN-PC", "40", "K1AR", True, True),
    (1.0, "RUN-PC", "40", "W1AW", True, False),
    (2.0, "MULT-PC", "20", "JA1XX", False, True),
    (4.0, "RUN-PC", "40", "N2IC", True, False),
    (6.0, "MULT-PC", "15", "ZS6A", False, True),     # MULT moved after 4 min -> TEN_MIN
    (8.0, "RUN-PC", "20", "VK2XX", True, True),      # RUN moved after 8 min -> TEN_MIN
    (9.0, "MULT-PC", "20", "PY1XX", False, True),    # MULT on RUN band (and 3 min after its last move) -> MULT_SAME_BAND + TEN_MIN
]


def contact(ts, station, band, call, is_run, is_mult, contest, qid, tag="contactinfo"):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<{tag}><app>N1MM</app><contestname>{contest}</contestname><contestnr>1</contestnr>
<timestamp>{ts:%Y-%m-%d %H:%M:%S}</timestamp><mycall>S50A</mycall><band>{MHZ[band]}</band>
<rxfreq>{FREQ[band]}</rxfreq><txfreq>{FREQ[band]}</txfreq><operator>S55OO</operator><mode>CW</mode>
<call>{call}</call><radionr>1</radionr><run1run2>1</run1run2>
<ismultiplier1>{int(is_mult)}</ismultiplier1><ismultiplier2>0</ismultiplier2><ismultiplier3>0</ismultiplier3>
<IsRunQSO>{is_run}</IsRunQSO><StationName>{station}</StationName><ID>{qid}</ID>
<IsOriginal>True</IsOriginal></{tag}>""".encode()


def radio(station, band, running):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<RadioInfo><app>N1MM</app><StationName>{station}</StationName><RadioNr>1</RadioNr>
<Freq>{FREQ[band]}</Freq><TXFreq>{FREQ[band]}</TXFreq><Mode>CW</Mode><IsRunning>{running}</IsRunning>
<IsConnected>True</IsConnected></RadioInfo>""".encode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=12060)
    ap.add_argument("--speed", type=float, default=30.0, help="time compression factor")
    ap.add_argument("--contest", default="CQWWCW")
    a = ap.parse_args()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    t_real = time.monotonic()
    s.sendto(f"<AppInfo><app>N1MM</app><contestname>{a.contest}</contestname>"
             f"<StationName>RUN-PC</StationName></AppInfo>".encode(), (a.host, a.port))
    for off, station, band, call, is_run, is_mult in SCENARIO:
        wait = off * 60 / a.speed - (time.monotonic() - t_real)
        if wait > 0:
            time.sleep(wait)
        ts = start + timedelta(minutes=off)
        s.sendto(radio(station, band, is_run), (a.host, a.port))
        s.sendto(contact(ts, station, band, call, is_run, is_mult, a.contest, uuid.uuid4().hex), (a.host, a.port))
        print(f"{ts:%H:%M:%S}  {station:<8} {band:>3} m  {call}")
    # RUN radio tunes to 15 m right after a change -> live warning
    s.sendto(radio("RUN-PC", "15", True), (a.host, a.port))
    print("RUN-PC radio QSY to 15 m (should show a live warning)")


if __name__ == "__main__":
    main()

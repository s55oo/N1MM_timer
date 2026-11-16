import os
import tempfile
import time
import unittest
from datetime import datetime

from n1mm_timer import cabrillo, n1mm, rules
from n1mm_timer.__main__ import check_cabrillo
from n1mm_timer.core import Settings, Watcher
from n1mm_timer.store import Store


def contact(cid, ts, band, call, station="RUN-PC", is_run="True", mult="1", tag="contactinfo",
            contest="CQWWCW", radionr="1"):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<{tag}>
  <app>N1MM</app><contestname>{contest}</contestname><contestnr>12</contestnr>
  <timestamp>{ts}</timestamp><mycall>S50A</mycall><band>{band}</band>
  <rxfreq>702500</rxfreq><txfreq>702500</txfreq><operator>S55OO</operator><mode>CW</mode>
  <call>{call}</call><radionr>{radionr}</radionr><run1run2>1</run1run2>
  <ismultiplier1>{mult}</ismultiplier1><ismultiplier2>0</ismultiplier2><ismultiplier3>0</ismultiplier3>
  <IsRunQSO>{is_run}</IsRunQSO><StationName>{station}</StationName>
  <ID>{cid}</ID><IsOriginal>True</IsOriginal>
</{tag}>""".encode()


def radio(station, freq_10hz, radionr="1", running="True"):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<RadioInfo><app>N1MM</app><StationName>{station}</StationName><RadioNr>{radionr}</RadioNr>
<Freq>{freq_10hz}</Freq><TXFreq>{freq_10hz}</TXFreq><Mode>CW</Mode><IsRunning>{running}</IsRunning>
</RadioInfo>""".encode()


class TestParse(unittest.TestCase):
    def test_contactinfo(self):
        m = n1mm.parse(contact("abc", "2026-11-28 12:00:05", "7", "K1AR"))
        self.assertEqual(m.kind, "contact")
        c = m.contact
        self.assertEqual((c.band, c.call, c.is_run, c.is_mult, c.station), ("40", "K1AR", True, True, "RUN-PC"))
        self.assertEqual(c.ts, datetime(2026, 11, 28, 12, 0, 5))

    def test_band_fallbacks(self):
        self.assertEqual(n1mm.band_from_n1mm("3.5"), "80")
        self.assertEqual(n1mm.band_from_n1mm("1.8"), "160")
        self.assertEqual(n1mm.band_from_n1mm("", "1402500"), "20")

    def test_delete_and_radio(self):
        d = n1mm.parse(b"<contactdelete><timestamp>2026-11-28 12:00:05</timestamp><call>K1AR</call>"
                       b"<StationName>RUN-PC</StationName><ID>abc</ID></contactdelete>")
        self.assertEqual((d.kind, d.delete_id), ("delete", "abc"))
        r = n1mm.parse(radio("RUN-PC", 1402500))
        self.assertEqual(n1mm.radio_band(r.fields), "20")

    def test_garbage(self):
        self.assertIsNone(n1mm.parse(b"not xml"))

    def test_rejects_entity_expansion_and_oversize(self):
        bomb = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
                b"<contactinfo><call>&b;</call></contactinfo>")
        self.assertIsNone(n1mm.parse(bomb))
        self.assertIsNone(n1mm.parse(b"<RadioInfo>" + b" " * 20000 + b"</RadioInfo>"))
        self.assertIsNone(n1mm.parse(b""))


class TestWatcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.w = Watcher(self.store, Settings(category="MS"))

    def test_flow_breach_ack_delete(self):
        w = self.w
        w.handle_packet(contact("g1", "2026-11-28 12:00:00", "7", "K1AR"))
        w.handle_packet(contact("g2", "2026-11-28 12:04:00", "14", "W1AW"))
        s = w.snapshot(datetime(2026, 11, 28, 12, 5))
        self.assertEqual(s.profile.key, "CQWW-CW-MS")
        self.assertEqual([f.kind for f, _ in s.faults], [rules.TEN_MIN])
        self.assertEqual(len(s.new_faults), 1)
        # same fault again is not "new"
        self.assertEqual(w.snapshot(datetime(2026, 11, 28, 12, 5)).new_faults, [])
        w.ack([s.faults[0][0].id])
        self.assertTrue(w.snapshot().faults[0][1])
        # deleting the offending QSO clears the fault
        w.handle_packet(b"<contactdelete><ID>g2</ID><StationName>RUN-PC</StationName></contactdelete>")
        self.assertEqual(w.snapshot().faults, [])

    def test_replace_moves_qso_out_of_breach(self):
        w = self.w
        w.handle_packet(contact("g1", "2026-11-28 12:00:00", "7", "K1AR"))
        w.handle_packet(contact("g2", "2026-11-28 12:04:00", "14", "W1AW"))
        w.handle_packet(contact("g2", "2026-11-28 12:04:00", "7", "W1AW", tag="contactreplace"))
        self.assertEqual(w.snapshot().faults, [])

    def test_duplicate_broadcasts_counted_once(self):
        w = self.w
        for _ in range(3):
            w.handle_packet(contact("g1", "2026-11-28 12:00:00", "7", "K1AR"))
        self.assertEqual(w.snapshot().qso_count, 1)

    def test_mult_signal_from_flag(self):
        w = self.w
        w.handle_packet(contact("g1", "2026-11-28 12:00:00", "7", "K1AR"))
        w.handle_packet(contact("g2", "2026-11-28 12:01:00", "14", "JA1XX", station="MULT-PC", is_run="False"))
        s = w.snapshot(datetime(2026, 11, 28, 12, 2))
        self.assertEqual(s.faults, [])
        self.assertEqual(set(s.result.status), {"RUN", "MULT"})

    def test_live_radio_warning(self):
        w = self.w
        w.handle_packet(contact("g1", "2026-11-28 12:00:00", "7", "K1AR"))
        w.handle_packet(radio("RUN-PC", 1402500))
        s = w.snapshot(datetime(2026, 11, 28, 12, 3))
        self.assertEqual(len(s.warnings), 1)
        self.assertIn("held on 40 m", s.warnings[0][2])

    def test_m2_by_station(self):
        st = Store(os.path.join(self.tmp, "m2.db"))
        w = Watcher(st, Settings(category="M2", m2_signal_source="station"))
        for i in range(10):
            w.handle_packet(contact(f"a{i}", f"2026-11-28 12:{i:02d}:00", "7" if i % 2 else "14", f"X{i}",
                                    station="PC1"))
        w.handle_packet(contact("b0", "2026-11-28 12:00:30", "21", "Y0", station="PC2"))
        s = w.snapshot(datetime(2026, 11, 28, 12, 30))
        self.assertEqual(s.profile.key, "CQWW-CW-M2")
        self.assertEqual([f.signal for f, _ in s.faults], ["PC1"])   # 9 changes on PC1

    def test_ip_filter(self):
        st = Store(os.path.join(self.tmp, "ip.db"))
        w = Watcher(st, Settings(allowed_ips=["10.0.0.5"]))
        w.handle_packet(contact("g1", "2026-11-28 12:00:00", "7", "K1AR"), ("10.0.0.9", 1))
        self.assertEqual(w.packets, 0)

    def test_persists_across_restart(self):
        self.w.handle_packet(contact("g1", "2026-11-28 12:00:00", "7", "K1AR"))
        w2 = Watcher(Store(os.path.join(self.tmp, "t.db")), Settings(category="MS"))
        self.assertEqual(w2.contest_name, "CQWWCW")
        self.assertEqual(w2.snapshot().qso_count, 1)


CAB = """START-OF-LOG: 3.0
CONTEST: CQ-WW-CW
CATEGORY-OPERATOR: MULTI-OP
CATEGORY-TRANSMITTER: ONE
CALLSIGN: S50A
QSO:  7025 CW 2026-11-28 1200 S50A          599 15     K1AR          599 05     0
QSO:  7025 CW 2026-11-28 1203 S50A          599 15     W1AW          599 05     0
QSO: 14025 CW 2026-11-28 1204 S50A          599 15     JA1XX         599 25     1
QSO: 14025 CW 2026-11-28 1207 S50A          599 15     N2IC          599 04     0
QSO: 21025 CW 2026-11-28 1208 S50A          599 15     ZS6A          599 38     1
END-OF-LOG:
"""


class TestCabrillo(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "log.cbr")
        with open(self.path, "w") as fh:
            fh.write(CAB)

    def test_read(self):
        log = cabrillo.read(self.path)
        self.assertEqual(len(log.qsos), 5)
        self.assertEqual(cabrillo.guess_profile_key(log), "CQWW-CW-MS")
        self.assertEqual([q.tx for q in log.qsos], ["0", "0", "1", "0", "1"])
        self.assertEqual(log.qsos[2].call, "JA1XX")

    def test_rtty_three_field_exchange(self):
        t = "14085 RY 2026-09-26 0001 S50A 599 15 DX K1AR 599 05 MA 1".split()
        q = cabrillo._qso(t)
        self.assertEqual((q.call, q.tx, q.band), ("K1AR", "1", "20"))
        t = "14085 RY 2026-09-26 0001 S50A 599 15 DX K1AR 599 05 MA".split()
        self.assertIsNone(cabrillo._qso(t).tx)

    def test_check(self):
        # RUN 40 @12:00 -> 20 @12:07 (7 min) = breach; MULT 20 @12:04 -> 15 @12:08 = breach
        self.assertEqual(check_cabrillo(self.path), 1)

    def test_import_dedupes_against_udp(self):
        tmp = tempfile.mkdtemp()
        w = Watcher(Store(os.path.join(tmp, "c.db")), Settings(category="MS"))
        w.handle_packet(contact("g1", "2026-11-28 12:00:10", "7", "K1AR"))
        msg = w.import_cabrillo(self.path)
        self.assertIn("Imported 4", msg)
        self.assertEqual(w.snapshot().qso_count, 5)


if __name__ == "__main__":
    unittest.main()


def radio_full(station, freq_10hz, radionr="1", running="True", tx="False", op="S55OO", caption=""):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<RadioInfo><app>N1MM</app><StationName>{station}</StationName><RadioNr>{radionr}</RadioNr>
<Freq>{freq_10hz}</Freq><TXFreq>{freq_10hz}</TXFreq><Mode>CW</Mode><OpCall>{op}</OpCall>
<IsRunning>{running}</IsRunning><IsTransmitting>{tx}</IsTransmitting>
<FunctionKeyCaption>{caption}</FunctionKeyCaption></RadioInfo>""".encode()


class TestStations(unittest.TestCase):
    def setUp(self):
        self.w = Watcher(Store(":memory:"), Settings(category="MS"))

    def test_radio_and_log_rows(self):
        w = self.w
        w.handle_packet(contact("g1", "2026-11-28 12:00:00", "7", "K1AR", station="RUN-PC"))
        w.handle_packet(contact("g2", "2026-11-28 12:01:00", "14", "JA1XX", station="MULT-PC", is_run="False"))
        w.handle_packet(radio_full("RUN-PC", 702500, tx="True", caption="F1 CQ"))
        rows = {r.station: r for r in w.snapshot(datetime(2026, 11, 28, 12, 3)).stations}
        run = rows["RUN-PC"]
        self.assertEqual((run.band, run.freq_khz, run.signal, run.transmitting, run.tx_caption, run.op),
                         ("40", 7025.0, "RUN", True, "F1 CQ", "S55OO"))
        self.assertEqual((run.rule_level, run.qsos, run.last_call), ("hold", 1, "K1AR"))
        self.assertIn("held on 40 m – 7:00", run.rule_text)
        mult = rows["MULT-PC"]                       # known from the log only
        self.assertIsNone(mult.seen_s)
        self.assertEqual((mult.signal, mult.qsos), ("MULT", 1))

    def test_warning_on_row(self):
        w = self.w
        w.handle_packet(contact("g1", "2026-11-28 12:00:00", "7", "K1AR", station="RUN-PC"))
        w.handle_packet(radio_full("RUN-PC", 1402500))
        run = [r for r in w.snapshot(datetime(2026, 11, 28, 12, 3)).stations if r.station == "RUN-PC"][0]
        self.assertIn("A QSO now = breach", run.warning)

    def test_hourly_text_and_levels(self):
        w = Watcher(Store(":memory:"), Settings(category="MS", profile="CQWPX-CW-MS"))
        for i in range(10):     # 9 changes
            w.handle_packet(contact(f"x{i}", f"2026-05-30 12:{i:02d}:00", "7" if i % 2 else "14", f"C{i}",
                                    contest="CQWPXCW"))
        r = [r for r in w.snapshot(datetime(2026, 5, 30, 12, 30)).stations if r.station == "RUN-PC"][0]
        self.assertEqual(r.rule_level, "last")
        self.assertIn("9/10 changes 12z (entry) – 1 left", r.rule_text)

    def test_m2_radio_uses_last_qso_tx(self):
        w = Watcher(Store(":memory:"), Settings(category="M2"))   # default run1run2
        w.handle_packet(contact("a", "2026-11-28 12:00:00", "21", "X", station="PCB", radionr="1")
                        .replace(b"<run1run2>1</run1run2>", b"<run1run2>2</run1run2>"))
        w.handle_packet(radio_full("PCB", 2102500))
        r = [r for r in w.snapshot(datetime(2026, 11, 28, 12, 5)).stations if r.station == "PCB"][0]
        self.assertEqual(r.signal, "TX2")

    def test_stale_radio(self):
        w = self.w
        w.handle_packet(radio_full("RUN-PC", 702500))
        key = ("RUN-PC", "1")
        f, _ = w.radios[key]
        w.radios[key] = (f, time.monotonic() - 60)
        r = w.snapshot().stations[0]
        self.assertTrue(r.stale)

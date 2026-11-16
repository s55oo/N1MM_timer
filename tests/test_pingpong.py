"""N1MM_timer -> PingPong (interface v1), checked with PingPong v1.117's own receiver code
(tests/compat/pingpong_bandalert.py is a copy of PingPong's pingpong_bandalert.py)."""
import calendar
import json
import socket
import time
import unittest
from datetime import datetime, timedelta

from n1mm_timer import pingpong, rules
from n1mm_timer.core import Settings, Watcher
from n1mm_timer.store import Store
from tests.compat import pingpong_bandalert as pp
from tests.test_n1mm_core import contact

NOW = datetime(2026, 11, 28, 12, 30)


def epoch(dt):
    return float(calendar.timegm(dt.timetuple()))


def watcher_with_faults(n=1):
    w = Watcher(Store(":memory:"), Settings(category="MS"))
    w.handle_packet(contact("g0", "2026-11-28 12:00:00", "7", "K1AR", station="RUN-PC"))
    bands = ["14", "7"]
    for i in range(n):         # each QSO on another band within 10 min -> one fault each
        w.handle_packet(contact(f"g{i + 1}", f"2026-11-28 12:0{i + 1}:00", bands[i % 2], f"C{i}",
                                station="RUN-PC"))
    return w


class TestMessageAcceptedByPingPong(unittest.TestCase):
    def check(self, snap, seq=1):
        data = pingpong.encode(pingpong.message(snap, "abcd1234", seq))
        msg, why = pp.validate(data, epoch(snap.now))
        self.assertIsNone(why, why)
        self.assertLessEqual(len(data), pp.MAX_SIZE)
        return msg

    def test_ok_heartbeat(self):
        w = Watcher(Store(":memory:"), Settings(category="MS"))
        msg = self.check(w.snapshot(NOW))
        self.assertEqual((msg["status"], msg["violations"]), ("ok", []))
        a = pp.BandAlert()
        a.offer(msg, epoch(NOW))
        self.assertEqual(a.footer(epoch(NOW)), ("", ""))

    def test_violation_footer_on_every_screen(self):
        snap = watcher_with_faults(1).snapshot(NOW)
        msg = self.check(snap)
        self.assertEqual(msg["status"], "violation")
        v = msg["violations"][0]
        self.assertEqual((v["kind"], v["signal"], v["station"], v["from_band"], v["to_band"], v["qsos"]),
                         ("TEN_MIN", "RUN", "RUN-PC", "40", "20", 1))
        a = pp.BandAlert()
        self.assertTrue(a.offer(msg, epoch(NOW)))
        kind, text = a.footer(epoch(NOW))
        self.assertEqual(kind, "violation")
        self.assertEqual(text, "BAND VIOLATION: RUN 40→20 m 12:01 – 1 QSO")
        self.assertIn("YOUR STATION", a.footer(epoch(NOW), my_stations=["run-pc"])[1])
        self.assertEqual(a.new_ids(epoch(NOW)), [v["id"]])        # beeps once
        self.assertEqual(a.new_ids(epoch(NOW)), [])

    def test_many_faults_capped_newest_first_and_fit(self):
        snap = watcher_with_faults(8).snapshot(NOW)
        msg = self.check(snap)
        self.assertLessEqual(len(msg["violations"]), pp.MAX_VIOLATIONS)
        times = [v["first_utc"] for v in msg["violations"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertGreater(len(msg["violations"]), 1)
        alert = pp.BandAlert()
        alert.offer(msg, epoch(NOW))
        self.assertIn(f"(+{len(msg['violations']) - 1} more)", alert.footer(epoch(NOW))[1])

    def test_acknowledged_fault_disappears_everywhere(self):
        w = watcher_with_faults(1)
        w.ack_all()
        msg = self.check(w.snapshot(NOW))
        self.assertEqual(msg["status"], "ok")

    def test_long_names_are_cut_to_pingpong_limits(self):
        q = rules.Qso("x" * 200, NOW, "20", "RUN" * 30, "C" * 60, "S" * 80)
        f = rules.Fault("TEN_MIN:" + "x" * 200, rules.TEN_MIN, "RUN" * 30, q, "", [q])
        f._from_band = "40"
        v = pingpong.violation(f)
        self.assertIsNone(pp._check_violation(v))
        self.assertLessEqual(len(v["text"]), pp.MAX_TEXT)

    def test_hourly_and_mult_same_band_texts(self):
        P = rules.PROFILES["CQWW-CW-MS"]
        r = rules.evaluate(P, [rules.Qso("1", NOW, "40", rules.RUN, "K1AR"),
                               rules.Qso("2", NOW + timedelta(minutes=1), "40", rules.MULT, "JA1XX")])
        self.assertEqual(pingpong.violation_text(r.faults[0]), "MULT on RUN band 40 m 12:31 JA1XX")
        self.assertIsNone(pp._check_violation(pingpong.violation(r.faults[0])))


class TestSender(unittest.TestCase):
    def test_heartbeat_and_change_trigger_and_seq(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(2)
        s = pingpong.Sender("127.0.0.1", rx.getsockname()[1])
        w = Watcher(Store(":memory:"), Settings(category="MS"))
        try:
            self.assertTrue(s.tick(w.snapshot(NOW)))                         # first: send
            self.assertFalse(s.tick(w.snapshot(NOW + timedelta(seconds=1))))  # nothing new
            self.assertTrue(s.tick(w.snapshot(NOW + timedelta(seconds=2))))   # heartbeat
            w.handle_packet(contact("a", "2026-11-28 12:00:00", "7", "K1AR"))
            w.handle_packet(contact("b", "2026-11-28 12:03:00", "14", "W1AW"))
            self.assertTrue(s.tick(w.snapshot(NOW + timedelta(seconds=2.5))))  # change: at once
            got = [json.loads(rx.recvfrom(2000)[0]) for _ in range(3)]
            self.assertEqual([m["seq"] for m in got], [1, 2, 3])
            self.assertEqual([m["status"] for m in got], ["ok", "ok", "violation"])
            a = pp.BandAlert()
            for m in got:
                self.assertTrue(a.offer(m, time.time()))
        finally:
            rx.close()

    def test_nothing_listening_is_harmless(self):
        s = pingpong.Sender("127.0.0.1", 9)       # discard port, nobody listens
        w = Watcher(Store(":memory:"), Settings())
        s.tick(w.snapshot(NOW))
        self.assertIn("PingPong alerts → 127.0.0.1:9", s.describe())


if __name__ == "__main__":
    unittest.main()

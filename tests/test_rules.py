import unittest
from datetime import datetime, timedelta

from n1mm_timer import rules
from n1mm_timer.rules import MULT, RUN, PROFILES, Qso, evaluate

T0 = datetime(2026, 11, 28, 12, 0, 0)


def q(i, minutes, band, sig=RUN, mult=None, seconds=0):
    return Qso(str(i), T0 + timedelta(minutes=minutes, seconds=seconds), band, sig, f"C{i}", "", mult)


def kinds(res):
    return [f.kind for f in res.faults]


class TestProfiles(unittest.TestCase):
    def test_twelve_profiles(self):
        self.assertEqual(len(PROFILES), 12)

    def test_contest_name_mapping(self):
        self.assertEqual(rules.profile_for_contest("CQWWCW", "MS").key, "CQWW-CW-MS")
        self.assertEqual(rules.profile_for_contest("CQWWSSB", "M2").key, "CQWW-SSB-M2")
        self.assertEqual(rules.profile_for_contest("CQWWRTTY", "MS").key, "CQWW-RTTY-MS")
        self.assertEqual(rules.profile_for_contest("CQWPXCW", "MS").key, "CQWPX-CW-MS")
        self.assertEqual(rules.profile_for_contest("CQWPXRTTY", "M2").key, "CQWPX-RTTY-M2")
        self.assertIsNone(rules.profile_for_contest("ARRLDXCW", "MS"))
        self.assertIsNone(rules.profile_for_contest("WWDIGI", "MS"))


class TestTenMinute(unittest.TestCase):
    P = PROFILES["CQWW-CW-MS"]

    def test_change_after_exactly_ten_minutes_ok(self):
        r = evaluate(self.P, [q(1, 0, "40"), q(2, 5, "40"), q(3, 10, "20")])
        self.assertEqual(r.faults, [])

    def test_change_before_ten_minutes_breach(self):
        r = evaluate(self.P, [q(1, 0, "40"), q(2, 9, "20", seconds=59)])
        self.assertEqual(kinds(r), [rules.TEN_MIN])
        self.assertEqual(r.faults[0].id, "TEN_MIN:2")

    def test_period_starts_at_first_qso_on_band_not_last(self):
        # on 40 from 12:00, many QSOs, change at 12:12 is fine
        r = evaluate(self.P, [q(1, 0, "40"), q(2, 11, "40"), q(3, 12, "20")])
        self.assertEqual(r.faults, [])

    def test_run_and_mult_independent(self):
        r = evaluate(self.P, [q(1, 0, "40"), q(2, 1, "20", MULT, True), q(3, 3, "15", MULT, True)])
        self.assertEqual(kinds(r), [rules.TEN_MIN])
        self.assertEqual(r.faults[0].signal, MULT)

    def test_mult_same_band_as_run(self):
        r = evaluate(self.P, [q(1, 0, "40"), q(2, 1, "40", MULT, True)])
        self.assertIn(rules.MULT_SAME_BAND, kinds(r))

    def test_mult_not_new(self):
        r = evaluate(self.P, [q(1, 0, "40"), q(2, 1, "20", MULT, False)])
        self.assertEqual(kinds(r), [rules.MULT_NOT_NEW])

    def test_mult_unknown_mult_flag_not_reported(self):
        r = evaluate(self.P, [q(1, 0, "40"), q(2, 1, "20", MULT, None)])
        self.assertEqual(r.faults, [])

    def test_order_independent(self):
        a = evaluate(self.P, [q(1, 0, "40"), q(2, 5, "20")])
        b = evaluate(self.P, [q(2, 5, "20"), q(1, 0, "40")])
        self.assertEqual([f.id for f in a.faults], [f.id for f in b.faults])

    def test_status_free_at(self):
        r = evaluate(self.P, [q(1, 0, "40")], now=T0 + timedelta(minutes=3))
        s = r.status[RUN]
        self.assertEqual(s.free_at, T0 + timedelta(minutes=10))
        self.assertFalse(s.can_change(T0 + timedelta(minutes=3)))
        self.assertTrue(s.can_change(T0 + timedelta(minutes=10)))


class TestHourly(unittest.TestCase):
    def seq(self, bands, start=0, step=1, sig=RUN):
        return [q(i, start + i * step, b, sig) for i, b in enumerate(bands)]

    def test_wpx_ms_ten_changes_ok_eleventh_breach(self):
        P = PROFILES["CQWPX-CW-MS"]
        bands = ["20", "40"] * 6          # 12 QSOs -> 11 changes within the hour
        r = evaluate(P, self.seq(bands))
        self.assertEqual(kinds(r), [rules.HOURLY])
        self.assertIn("#11", r.faults[0].text)

    def test_wpx_ms_entry_wide_counts_run_and_mult_together(self):
        P = PROFILES["CQWPX-SSB-MS"]
        qs = [q(i, i, b, RUN if i % 2 else MULT) for i, b in enumerate(["20", "40"] * 6)]
        r = evaluate(P, qs)
        self.assertEqual(len(r.faults), 1)
        self.assertEqual(r.faults[0].signal, rules.ALL)

    def test_return_trip_counts_twice(self):
        P = PROFILES["CQWW-CW-M2"]
        r = evaluate(P, self.seq(["20", "40", "20"]), now=T0 + timedelta(minutes=5))
        self.assertEqual(r.status[RUN].used_this_hour, 2)

    def test_hour_boundary_keeps_previous_band(self):
        # CQ WPX FAQ style: 12:59 on 15, then in 13z: 20, 15, 20 -> 3 changes in 13z
        P = PROFILES["CQWW-CW-M2"]
        qs = [q(1, 59, "15"), q(2, 61, "20"), q(3, 62, "15"), q(4, 63, "20")]
        r = evaluate(P, qs, now=T0 + timedelta(minutes=64))
        self.assertEqual(r.status[RUN].used_this_hour, 3)

    def test_counts_reset_each_hour(self):
        P = PROFILES["CQWW-SSB-M2"]
        first = self.seq(["20", "40"] * 4 + ["20"], start=0)        # 8 changes in 12z
        second = [q(100 + i, 60 + i, b) for i, b in enumerate(["40", "20"] * 4)]  # 8 in 13z
        r = evaluate(P, first + second)
        self.assertEqual(r.faults, [])

    def test_m2_counters_per_tx(self):
        P = PROFILES["CQWW-CW-M2"]
        tx1 = [q(i, i, b, "TX1") for i, b in enumerate(["20", "40"] * 5)]       # 9 changes
        tx2 = [q(100 + i, i, b, "TX2") for i, b in enumerate(["15", "10"] * 4)]  # 7 changes
        r = evaluate(P, tx1 + tx2)
        self.assertEqual([f.signal for f in r.faults], ["TX1"])

    def test_ww_rtty_ms_eight_per_signal_and_mult_rules(self):
        P = PROFILES["CQWW-RTTY-MS"]
        run = [q(i, i * 6, b, RUN) for i, b in enumerate(["20", "40"] * 5)]      # 9 changes, 0..54 min
        r = evaluate(P, run)
        self.assertEqual(kinds(r), [rules.HOURLY])
        r = evaluate(P, [q(1, 0, "20"), q(2, 1, "20", MULT, True)])
        self.assertEqual(kinds(r), [rules.MULT_SAME_BAND])


class TestLiveWarning(unittest.TestCase):
    def test_ten_min_warning(self):
        P = PROFILES["CQWW-CW-MS"]
        now = T0 + timedelta(minutes=4)
        r = evaluate(P, [q(1, 0, "40")], now=now)
        self.assertIn("held on 40 m", rules.live_warning(P, r, RUN, "20", now))
        self.assertIsNone(rules.live_warning(P, r, RUN, "40", now))
        later = T0 + timedelta(minutes=10)
        r = evaluate(P, [q(1, 0, "40")], now=later)
        self.assertIsNone(rules.live_warning(P, r, RUN, "20", later))

    def test_mult_on_run_band_warning(self):
        P = PROFILES["CQWW-CW-MS"]
        r = evaluate(P, [q(1, 0, "40")])
        self.assertIn("RUN band", rules.live_warning(P, r, MULT, "40", T0))

    def test_hourly_exhausted_warning(self):
        P = PROFILES["CQWPX-CW-MS"]
        qs = [q(i, i, b) for i, b in enumerate(["20", "40"] * 5 + ["20"])]  # 10 changes
        now = T0 + timedelta(minutes=20)
        r = evaluate(P, qs, now=now)
        self.assertEqual(r.faults, [])
        self.assertIn("are used", rules.live_warning(P, r, RUN, "40", now))
        nxt = T0 + timedelta(minutes=61)
        r = evaluate(P, qs, now=nxt)
        self.assertIsNone(rules.live_warning(P, r, RUN, "40", nxt))


if __name__ == "__main__":
    unittest.main()

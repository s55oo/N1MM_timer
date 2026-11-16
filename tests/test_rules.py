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

    def test_mult_flag_not_used(self):
        # N1MM databases store no multiplier flags (all 0) – never a fault on its own
        r = evaluate(self.P, [q(1, 0, "40"), q(2, 1, "20", MULT, False)])
        self.assertEqual(r.faults, [])

    def test_judged_in_whole_minutes_like_cq_checker(self):
        # first QSO 12:00:30, change 12:10:05 -> legal (1200 -> 1210 in Cabrillo)
        r = evaluate(self.P, [q(1, 0, "40", seconds=30), q(2, 10, "20", seconds=5)])
        self.assertEqual(r.faults, [])
        # first QSO 12:00:30, change 12:09:59 -> breach (1209)
        r = evaluate(self.P, [q(1, 0, "40", seconds=30), q(2, 9, "20", seconds=59)])
        self.assertEqual(kinds(r), [rules.TEN_MIN])

    def test_live_countdown_stays_exact_seconds(self):
        r = evaluate(self.P, [q(1, 0, "40", seconds=30)], now=T0 + timedelta(minutes=10, seconds=5))
        self.assertFalse(r.status[RUN].can_change(T0 + timedelta(minutes=10, seconds=5)))

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


class TestUntilBackInCompliance(unittest.TestCase):
    """CQ WW FAQ: early QSOs 'are in violation ... removed without penalty';
    CQ WPX FAQ: 'loss of all QSOs on that transmitter until it is back in compliance'."""

    def test_all_early_qsos_on_new_band_are_counted(self):
        P = PROFILES["CQWW-CW-MS"]
        qs = [q(0, 0, "40")] + [q(i, 3 + i, "20") for i in range(1, 7)] + [q(9, 10, "20")]
        r = evaluate(P, qs)              # 20 m QSOs at 12:04..12:09 are early, 12:10 is legal
        self.assertEqual(len(r.faults), 1)
        f = r.faults[0]
        self.assertEqual([a.id for a in f.affected], ["1", "2", "3", "4", "5", "6"])
        self.assertIn("6 QSOs on 20 m (12:04:00–12:09:00) would be removed", f.text)
        self.assertEqual(r.status[RUN].band, "20")

    def test_return_to_old_band_is_compliance_and_period_unchanged(self):
        P = PROFILES["CQWW-CW-MS"]
        # 40 since 12:00, one early QSO on 20 at 12:04, back on 40 at 12:05, 20 again at 12:10
        r = evaluate(P, [q(1, 0, "40"), q(2, 4, "20"), q(3, 5, "40"), q(4, 10, "20")])
        self.assertEqual([a.id for f in r.faults for a in f.affected], ["2"])
        self.assertEqual((r.status[RUN].band, r.status[RUN].band_since), ("20", T0 + timedelta(minutes=10)))

    def test_ww_faq_example_1400_1410(self):
        P = PROFILES["CQWW-SSB-MS"]
        t = datetime(2026, 10, 24, 14, 0, 59)
        ok = [Qso("1", t, "20", RUN), Qso("2", datetime(2026, 10, 24, 14, 10, 0), "15", RUN)]
        self.assertEqual(evaluate(P, ok).faults, [])
        bad = [Qso("1", t, "20", RUN), Qso("2", datetime(2026, 10, 24, 14, 9, 59), "15", RUN)]
        self.assertEqual(len(evaluate(P, bad).faults), 1)

    def test_no_cascade_after_early_change(self):
        # MULT 15 m since 14:06:45, early to 20 m at 14:11:41 (3 QSOs), stays; 40 m at 14:21:51
        # is 10 min after the 20 m period began -> legal (9A7A case)
        P = PROFILES["CQWW-SSB-MS"]
        d = datetime(2026, 10, 24)
        qs = [Qso("a", d.replace(hour=14, minute=6, second=45), "15", MULT),
              Qso("b", d.replace(hour=14, minute=11, second=41), "20", MULT),
              Qso("c", d.replace(hour=14, minute=12, second=5), "20", MULT),
              Qso("d", d.replace(hour=14, minute=13, second=6), "20", MULT),
              Qso("e", d.replace(hour=14, minute=17, second=0), "20", MULT),
              Qso("f", d.replace(hour=14, minute=21, second=51), "40", MULT)]
        r = evaluate(P, qs)
        self.assertEqual(len(r.faults), 1)
        self.assertEqual([a.id for a in r.faults[0].affected], ["b", "c", "d"])

    def test_hourly_excess_lost_until_next_hour(self):
        P = PROFILES["CQWW-CW-M2"]
        bands = ["20", "40"] * 4 + ["20"]                     # 8 legal changes 12:00..12:40
        qs = [Qso(f"a{i}", T0 + timedelta(minutes=5 * i), b, "TX1") for i, b in enumerate(bands)]
        qs += [Qso("x1", T0 + timedelta(minutes=50), "15", "TX1"),   # 9th change -> out
               Qso("x2", T0 + timedelta(minutes=55), "15", "TX1"),   # still out (12z)
               Qso("y1", T0 + timedelta(minutes=58), "20", "TX1"),   # back: change #10, still out
               Qso("z1", T0 + timedelta(minutes=61), "20", "TX1")]   # 13z: back in compliance
        r = evaluate(P, qs, now=T0 + timedelta(minutes=62))
        self.assertEqual(len(r.faults), 1)
        self.assertEqual([a.id for a in r.faults[0].affected], ["x1", "x2", "y1"])
        self.assertIn("(until 13:00)", r.faults[0].text)
        self.assertEqual((r.status["TX1"].band, r.status["TX1"].used_this_hour), ("20", 0))

    def test_no_cascade_into_next_hour(self):
        # 9th change at 12:50 to 15 m, stays on 15 m into 13z: 13z starts with 0 changes
        P = PROFILES["CQWPX-SSB-M2"]
        bands = ["20", "40"] * 4 + ["20"]
        qs = [Qso(f"a{i}", T0 + timedelta(minutes=5 * i), b, "TX2") for i, b in enumerate(bands)]
        qs += [Qso("x", T0 + timedelta(minutes=50), "15", "TX2"), Qso("y", T0 + timedelta(minutes=70), "15", "TX2")]
        r = evaluate(P, qs, now=T0 + timedelta(minutes=71))
        self.assertEqual(r.status["TX2"].used_this_hour, 0)
        self.assertEqual([a.id for a in r.faults[0].affected], ["x"])

    def test_wpx_faq_example_three_changes_in_13z(self):
        P = PROFILES["CQWPX-CW-MS"]
        rows = [("12:59", "15"), ("13:00", "20"), ("13:10", "15"), ("13:59", "40"), ("14:00", "10")]
        qs = [Qso(str(i), datetime(2026, 5, 30, int(t[:2]), int(t[3:])), b, RUN) for i, (t, b) in enumerate(rows)]
        r = evaluate(P, qs[:4], now=datetime(2026, 5, 30, 13, 59))
        self.assertEqual(r.status[rules.ALL].used_this_hour, 3)

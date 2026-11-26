import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

from n1mm_timer.core import Settings, Watcher
from n1mm_timer.store import Store
from n1mm_timer.lampstatus import epoch
from tests.test_n1mmdb import DDL, qso


class LampStateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = str(Path(tmp.name) / "test.s3db")
        self.db = sqlite3.connect(path)
        self.addCleanup(self.db.close)
        self.db.executescript(DDL)
        self.w = Watcher(Store(":memory:"), Settings(db_path=path, db_contest_nr=1))
        self.addCleanup(lambda: self.w._db.close() if self.w._db else None)

    def state(self, now=datetime(2026, 11, 28, 14, 59, 58)):
        self.w.sync_db(now)
        return self.w.snapshot().lamp_state

    def test_independent_clocks_log_corrections_and_delete(self):
        qso(self.db, "1", "2026-11-28 14:00:58", 14, "C1", run=0)
        qso(self.db, "2", "2026-11-28 14:08:59", 14, "C2", pc="HELPER")
        qso(self.db, "3", "2026-11-28 14:06:30", 21, "C3", r12=2)
        state = self.state()
        self.assertEqual(state["signals"]["RUN"]["until"], epoch(datetime(2026, 11, 28, 14, 10)))
        self.assertEqual(state["signals"]["MULT"]["until"], epoch(datetime(2026, 11, 28, 14, 16)))
        self.assertIn("HELPER", state["last"])
        self.db.execute("UPDATE DXLOG SET TS='2026-11-28 14:02:01' WHERE ID='1'")
        self.db.execute("DELETE FROM DXLOG WHERE ID='3'")
        self.db.commit()
        state = self.state()
        self.assertIsNone(state["signals"]["MULT"])
        self.assertEqual(state["signals"]["RUN"]["until"], epoch(datetime(2026, 11, 28, 14, 12)))

    def test_multi_two_carries_band_across_hour(self):
        self.db.execute("UPDATE ContestInstance SET TransmitterCategory='TWO' WHERE ContestNR=1")
        qso(self.db, "1", "2026-11-28 13:59:50", 14, "C1")
        qso(self.db, "2", "2026-11-28 14:01:01", 21, "C2")
        qso(self.db, "3", "2026-11-28 15:00:01", 7, "C3", r12=2)
        state = self.state()
        next_hour = self.state(datetime(2026, 11, 28, 15, 0, 2))
        self.assertEqual(next_hour["signals"]["TX2"]["band"], "40")
        self.assertEqual(next_hour["signals"]["TX1"]["used"], 0)
        self.assertEqual(state["signals"], {"TX1": {"band": "15", "used": 1}, "TX2": None})
        self.assertEqual(state["hour"], epoch(datetime(2026, 11, 28, 14)) // 3600)

    def test_category_changes_and_missing_mapping(self):
        qso(self.db, "1", "2026-11-28 14:00:00", 14, "C1", r12=0)
        self.assertEqual(self.state()["category"], "unknown")
        for op, tx, station, expected in (
                ("SINGLE-OP", "ONE", "", "activity"), ("SINGLE-OP-ASSISTED", "ONE", "", "activity"), ("CHECKLOG", "", "", "activity"),
                ("MULTI-OP", "UNLIMITED", "", "activity"), ("MULTI-OP", "", "DISTRIBUTED", "activity"),
                ("", "ONE", "", "unknown")):
            self.db.execute("UPDATE ContestInstance SET OperatorCategory=?, TransmitterCategory=?, "
                            "StationCategory=? WHERE ContestNR=1", (op, tx, station))
            self.db.commit()
            state = self.state()
            self.assertEqual(state["category"], expected)
            self.assertEqual(state["signals"], {})
            self.assertIn("RUN", state["last"])
            self.assertIsNone(self.w.profile())

    def test_lost_database_invalidates_and_recovery_reloads(self):
        self.assertEqual(self.state()["category"], "MS")
        with patch("n1mm_timer.core.os.path.isfile", return_value=False):
            self.assertEqual(self.state()["category"], "unknown")
        self.assertEqual(self.state()["category"], "MS")

    def test_populated_log_with_wrong_dates(self):
        qso(self.db, "a", "2026-11-21 14:01:00", 14, "C1")
        self.assertEqual(self.state()["reason"], "contest_dates")

    def test_legacy_multi_one(self):
        self.db.execute("UPDATE ContestInstance SET OperatorCategory='MULTI-ONE' WHERE ContestNR=1")
        self.db.commit()
        self.assertEqual(self.state()["category"], "MS")

    def test_log_switch_discards_inflight_read(self):
        from n1mm_timer import n1mmdb
        read = n1mmdb.read_qsos
        qso(self.db, "a", "2026-11-28 14:01:00", 14, "C1")
        def switch(*args):
            contacts = read(*args)
            self.w.select_db(self.w.settings.db_path, 2)
            return contacts
        with patch.object(n1mmdb, "read_qsos", switch):
            self.w.sync_db()
        self.assertIsNone(self.w.db_info)
        self.assertEqual(len(self.w.store.rows()), 0)
        self.assertIsNone(self.state())

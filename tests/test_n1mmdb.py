"""Tests against a synthetic database with the real N1MM Logger+ DXLOG / ContestInstance layout."""
import os
import sqlite3
import tempfile
import unittest

from n1mm_timer import n1mmdb
from n1mm_timer.__main__ import check_db
from n1mm_timer.core import Settings, Watcher
from n1mm_timer.store import Store

DDL = """
CREATE TABLE ContestInstance (ContestID INT NOT NULL PRIMARY KEY, ContestName NVARCHAR(10),
  StartDate DATETIME, OperatorCategory NVARCHAR(20), BandCategory NVARCHAR(20), PowerCategory NVARCHAR(20),
  ModeCategory NVARCHAR(20), OverlayCategory NVARCHAR(20), ClaimedScore MONEY, Operators NVARCHAR(255),
  Soapbox TEXT, SentExchange NVARCHAR(50), ContestNR INT, SubType NVARCHAR(9), StationCategory NVARCHAR(20),
  AssistedCategory NVARCHAR(20), TransmitterCategory NVARCHAR(20), TimeCategory NVARCHAR(20));
CREATE TABLE DXLOG (TS DATETIME NOT NULL, Call VARCHAR(15) NOT NULL, Freq DOUBLE, Mode VARCHAR(6),
  ContestName VARCHAR(10), IsMultiplier1 TINYINT DEFAULT 0, IsMultiplier2 INTEGER DEFAULT 0, Band FLOAT,
  RadioNR TINYINT DEFAULT 1, ContestNR INTEGER, isMultiplier3 INTEGER, IsRunQSO TINYINT(1) DEFAULT 0,
  ContactType VARCHAR(1), Run1Run2 TINYINT NOT NULL, Operator VARCHAR(20), NetworkedCompNr INTEGER,
  NetBiosName varchar(255), IsOriginal Boolean, ID TEXT(16) NOT NULL, CLAIMEDQSO INTEGER DEFAULT 1,
  PRIMARY KEY (TS, Call));
INSERT INTO ContestInstance(ContestID, ContestName, StartDate, OperatorCategory, ContestNR, TransmitterCategory)
  VALUES (1,'DELETEDQS','1900-01-01','SINGLE-OP',-1,NULL), (2,'DX','1900-01-01 00:00:00','SINGLE-OP',0,''),
         (3,'CQWWCW','2026-11-28 00:00:00','MULTI-OP',1,'ONE'), (4,'CQWPXSSB','2026-03-28 00:00:00','MULTI-OP',2,'TWO');
"""


def qso(db, gid, ts, band, call, nr=1, run=1, r12=1, pc="RUN", mult=1, freq=None):
    db.execute("INSERT INTO DXLOG(TS, Call, Freq, Mode, Band, ContestNR, IsRunQSO, Run1Run2, NetBiosName, "
               "IsMultiplier1, IsMultiplier2, isMultiplier3, RadioNR, ID) VALUES (?,?,?,?,?,?,?,?,?,?,0,0,1,?)",
               (ts, call, freq if freq is not None else band * 1000 + 25, "CW", band, nr, run, r12, pc, mult, gid))
    db.commit()


class TestN1mmDb(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "S50A.s3db")
        self.db = sqlite3.connect(self.path)
        self.db.executescript(DDL)

    def test_contests_and_category(self):
        cs = n1mmdb.contests(n1mmdb.connect(self.path))
        self.assertEqual([(c.nr, c.name, c.category) for c in cs], [(1, "CQWWCW", "MS"), (2, "CQWPXSSB", "M2")])

    def test_read_skips_pre_start_and_maps_fields(self):
        qso(self.db, "a", "2026-11-27 23:50:00", 7.0, "TEST")
        qso(self.db, "b", "2026-11-28 00:01:00", 7.0, "K1AR")
        qso(self.db, "c", "2026-11-28 00:02:00", 14.0, "JA1XX", run=0, pc="MULT", mult=0)
        qso(self.db, "d", "2026-11-28 00:03:00", 0, "W1AW", freq=21025.0)       # band missing -> from Freq kHz
        db = n1mmdb.connect(self.path)
        info = n1mmdb.pick_contest(db, 1)
        cs, deleted, skipped, unreadable = n1mmdb.read_qsos(db, info)
        self.assertEqual(skipped, 1)
        by = {c.id: c for c in cs}
        self.assertEqual((by["b"].band, by["b"].is_run, by["b"].station), ("40", True, "RUN"))
        self.assertEqual((by["c"].is_run, by["c"].is_mult), (False, False))
        self.assertEqual(by["d"].band, "15")

    def test_live_sync_add_edit_delete(self):
        qso(self.db, "g1", "2026-11-28 12:00:00", 7.0, "K1AR")
        w = Watcher(Store(":memory:"), Settings(db_path=self.path))
        w.sync_db()
        self.assertEqual(w.profile().key, "CQWW-CW-MS")          # category from TransmitterCategory=ONE
        qso(self.db, "g2", "2026-11-28 12:04:00", 14.0, "W1AW")
        w.sync_db()
        self.assertEqual([f.kind for f, _ in w.snapshot().faults], ["TEN_MIN"])
        # edit in N1MM: band corrected back to 40 m
        self.db.execute("UPDATE DXLOG SET Band=7.0 WHERE ID='g2'"); self.db.commit()
        w.sync_db()
        self.assertEqual(w.snapshot().faults, [])
        # delete in N1MM: row moves to ContestNR -1
        self.db.execute("UPDATE DXLOG SET ContestNR=-1 WHERE ID='g1'"); self.db.commit()
        w.sync_db()
        self.assertEqual(w.snapshot().qso_count, 1)

    def test_m2_run1run2_shared_by_several_pcs(self):
        # TX1 used by PCE and PCB alternating bands -> 9 changes in one hour on TX1
        for i in range(10):
            qso(self.db, f"x{i}", f"2026-03-28 13:{i * 5:02d}:00", 14.0 if i % 2 else 28.0, f"C{i}", nr=2,
                r12=1, pc="PCE" if i % 2 else "PCB")
        qso(self.db, "y", "2026-03-28 13:01:00", 21.0, "Y", nr=2, r12=2, pc="PC1")
        w = Watcher(Store(":memory:"), Settings(db_path=self.path, db_contest_nr=2))
        w.sync_db()
        snap = w.snapshot()
        self.assertEqual(snap.profile.key, "CQWPX-SSB-M2")
        self.assertEqual([f.signal for f, _ in snap.faults], ["TX1"])
        # per-PC mapping would hide it
        w2 = Watcher(Store(":memory:"), Settings(db_path=self.path, db_contest_nr=2, signal_source="station"))
        w2.sync_db()
        self.assertEqual(w2.snapshot().faults, [])

    def test_check_db_cli(self):
        qso(self.db, "g1", "2026-11-28 12:00:00", 7.0, "K1AR")
        qso(self.db, "g2", "2026-11-28 12:04:00", 14.0, "W1AW")
        self.assertEqual(check_db(self.path, 1), 1)
        self.assertEqual(check_db(self.path, 2), 0)

    def test_db_is_not_modified(self):
        qso(self.db, "g1", "2026-11-28 12:00:00", 7.0, "K1AR")
        self.db.close()
        before = os.path.getmtime(self.path), os.path.getsize(self.path)
        w = Watcher(Store(":memory:"), Settings(db_path=self.path))
        w.sync_db()
        w.snapshot()
        self.assertEqual(before, (os.path.getmtime(self.path), os.path.getsize(self.path)))

    def test_missing_db_reports_error(self):
        w = Watcher(Store(":memory:"), Settings(db_path=os.path.join(self.dir, "nope.s3db")))
        self.assertIn("database error", w.sync_db())


if __name__ == "__main__":
    unittest.main()


def appinfo(dbname, contestnr="1", station="RUN-PC"):
    return (f'<?xml version="1.0" encoding="utf-8"?><AppInfo><app>N1MM</app><dbname>{dbname}</dbname>'
            f"<contestnr>{contestnr}</contestnr><contestname>CQWWCW</contestname>"
            f"<StationName>{station}</StationName></AppInfo>").encode()


class TestFollowOpenLog(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.a = os.path.join(self.dir, "S50A_CQWWCW.s3db")
        self.b = os.path.join(self.dir, "TEST.s3db")
        for p in (self.a, self.b):
            db = sqlite3.connect(p)
            db.executescript(DDL)
            db.close()
        self.dba, self.dbb = sqlite3.connect(self.a), sqlite3.connect(self.b)
        qso(self.dba, "a1", "2026-11-28 12:00:00", 7.0, "K1AR")
        qso(self.dbb, "b1", "2026-11-28 12:00:00", 14.0, "W1AW")
        qso(self.dbb, "b2", "2026-11-28 12:01:00", 21.0, "N2IC")

    def watcher(self, store=None):
        return Watcher(store or Store(":memory:"), Settings(db_follow=True, db_dirs=[self.dir]))

    def test_follows_appinfo_full_path_and_switches(self):
        w = self.watcher()
        w.handle_packet(appinfo(self.a), ("127.0.0.1", 5000))
        w.sync_db()
        self.assertEqual((w.settings.db_path, w.settings.db_contest_nr), (self.a, 1))
        self.assertEqual(w.follow_source, "N1MM App Info")
        self.assertEqual(w.snapshot().qso_count, 1)
        w.handle_packet(appinfo(self.b), ("127.0.0.1", 5000))     # operator opened another log
        w.sync_db()
        self.assertEqual(w.settings.db_path, self.b)
        self.assertEqual(w.snapshot().qso_count, 2)              # fresh ledger, only TEST.s3db
        self.assertIn("following N1MM (N1MM App Info)", w.db_status)

    def test_bare_file_name_is_looked_up(self):
        w = self.watcher()
        w.handle_packet(appinfo("TEST.s3db"), ("127.0.0.1", 5000))
        self.assertEqual(w.settings.db_path, self.b)

    def test_remote_appinfo_ignored(self):
        w = self.watcher()
        w.handle_packet(appinfo(self.a), ("203.0.113.9", 5000))
        self.assertEqual(w.settings.db_path, "")

    def test_unknown_db_reported(self):
        w = self.watcher()
        w.handle_packet(appinfo("NOPE.s3db"), ("127.0.0.1", 5000))
        self.assertIn("not found on this PC", w.db_status)

    def test_guess_newest_until_appinfo(self):
        os.utime(self.a, (1, 1))                                  # TEST.s3db is the newest
        os.utime(self.b, None)
        w = self.watcher()
        w.sync_db()
        self.assertEqual(w.settings.db_path, self.b)
        self.assertTrue(w.follow_source.startswith("guessed"))
        w.handle_packet(appinfo(self.a), ("127.0.0.1", 5000))     # App Info wins
        w.sync_db()
        self.assertEqual((w.settings.db_path, w.follow_source), (self.a, "N1MM App Info"))

    def test_admin_db_never_guessed(self):
        admin = os.path.join(self.dir, "N1MM Admin.s3db")
        db = sqlite3.connect(admin)
        db.executescript(DDL)
        db.close()
        self.assertNotEqual(n1mmdb.newest_log([self.dir]), admin)

    def test_restart_keeps_following(self):
        path = os.path.join(self.dir, "ledger.db")
        w = self.watcher(Store(path))
        w.handle_packet(appinfo(self.a), ("127.0.0.1", 5000))
        w2 = self.watcher(Store(path))
        self.assertEqual(w2.settings.db_path, self.a)

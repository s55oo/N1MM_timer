"""Builds the real Tk window, feeds it N1MM packets and checks what it shows.
Skipped where tkinter or a display is not available (e.g. some Linux CI); runs on Windows CI."""
import os
import socket
import tempfile
import unittest

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAVE_TK = True
except Exception:  # no tkinter module, or no display
    HAVE_TK = False


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(HAVE_TK, "tkinter/display not available")
class TestWindow(unittest.TestCase):
    def setUp(self):
        from n1mm_timer import app, config
        self.tmp = tempfile.mkdtemp()
        self._orig = config.app_dir
        config.app_dir = lambda: self.tmp
        app.config.app_dir = config.app_dir
        self.cp = config.load(os.path.join(self.tmp, "n1mm_timer.ini"))
        self.cp.set("udp", "port", str(_free_port()))
        self.cp.set("udp", "bind", "127.0.0.1")
        self.cp.set("alerts", "sound", "false")
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = app.App(self.root, self.cp)

    def tearDown(self):
        from n1mm_timer import config
        self.app.listener.stop()
        self.root.destroy()
        config.app_dir = self._orig

    def test_stations_tiles_and_faults(self):
        from tests.test_n1mm_core import contact, radio_full
        w = self.app.watcher
        w.handle_packet(contact("g1", "2026-11-28 12:00:00", "7", "K1AR", station="RUN-PC"))
        w.handle_packet(contact("g2", "2026-11-28 12:04:00", "14", "W1AW", station="RUN-PC"))
        w.handle_packet(contact("g3", "2026-11-28 12:01:00", "21", "JA1XX", station="MULT-PC", is_run="False"))
        w.handle_packet(radio_full("RUN-PC", 1402500, tx="True", caption="F1 CQ"))
        w.handle_packet(radio_full("MULT-PC", 2102500, running="False"))
        self.app.refresh()
        self.root.update()
        stations = self.app.st_tree.get_children()
        self.assertEqual(len(stations), 2)
        vals = {self.app.st_tree.item(i, "values")[0]: self.app.st_tree.item(i, "values") for i in stations}
        self.assertEqual(vals["RUN-PC"][7], "TX F1 CQ")
        self.assertEqual(vals["MULT-PC"][8], "MULT")
        self.assertEqual(set(self.app.tiles), {"RUN", "MULT"})
        self.assertEqual(len(self.app.tree.get_children()), 1)        # one TEN_MIN fault
        self.assertGreater(self.app.open_faults, 0)
        self.app.ack_all()
        self.app.refresh()
        self.assertEqual(self.app.open_faults, 0)
        self.assertIn("UDP", self.app.l_info.cget("text"))
        self.app.follow_db()
        self.app.refresh()
        self.assertTrue(self.app.watcher.settings.db_follow)


if __name__ == "__main__":
    unittest.main()

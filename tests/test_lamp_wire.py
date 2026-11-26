"""Exercise the released receiver and the real loopback datagrams from Sender."""
import calendar
import importlib.util
import os
import socket
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from n1mm_timer.pingpong import Sender


@unittest.skipUnless(os.getenv("PINGPONG_SOURCE"), "set PINGPONG_SOURCE to a PingPong checkout")
class WireTest(unittest.TestCase):
    def test_lamp_display_from_sender(self):
        source = Path(os.environ["PINGPONG_SOURCE"]) / "pingpong_contest.py"
        spec = importlib.util.spec_from_file_location("pingpong_contest", source)
        receiver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(receiver)
        now = datetime(2026, 11, 28, 14)
        utc = calendar.timegm(now.timetuple())
        cases = [
            ({"category": "MS", "signals": {"RUN": {"band": "20", "until": utc+4}, "MULT": None},
              "last": {}}, "RUN · 20 m · 0:04 left"),
            ({"category": "M2", "hour": utc//3600, "signals": {"TX1": {"band": "15", "used": 8},
              "TX2": {"reason": "qso_order"}}, "last": {}}, "TX1 · 15 m · 8/8 changes\nTX2 · Check log"),
            ({"category": "activity", "signals": {}, "last": {"LOGGER": utc-60}}, "Last QSO · 1:00 ago"),
            ({"category": "unknown", "signals": {}, "last": {}, "reason": "invalid_band"}, "invalid CQ WW band"),
            (None, ""),
        ]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rx:
            rx.bind(("127.0.0.1", 0))
            rx.settimeout(2)
            sender = Sender(port=rx.getsockname()[1])
            self.addCleanup(sender.sock.close)
            for state, expected in cases:
                with self.subTest(state=state):
                    snap = SimpleNamespace(now=now, faults=[], profile=None, lamp_state=state)
                    self.assertTrue(sender.send(snap))
                    self.assertIn(b"n1mm_timer.alert", rx.recv(65535))
                    payload = rx.recv(65535)
                    self.assertTrue(payload.startswith(receiver.MAGIC))
                    msg = receiver.decode(payload, utc+5)
                    self.assertIsNotNone(msg)
                    text = receiver.display(msg, utc+5, {"LOGGER"}, True)
                    if expected:
                        self.assertIn(expected, text)
                    else:
                        self.assertEqual(text, "")

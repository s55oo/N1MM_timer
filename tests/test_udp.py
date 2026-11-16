import socket
import time
import unittest

from n1mm_timer.n1mm import Listeners, UdpListener, parse_ports


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TestPorts(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_ports("12060"), [12060])
        self.assertEqual(parse_ports("12060, 12064;12060 12070"), [12060, 12064, 12070])
        self.assertEqual(parse_ports(""), [12063])
        with self.assertRaises(ValueError):
            parse_ports("70000")

    def test_receives_on_several_ports(self):
        got = []
        p1, p2 = free_port(), free_port()
        ls = Listeners([p1, p2], lambda d, a: got.append(d), "127.0.0.1").start()
        try:
            self.assertEqual(ls.errors(), [])
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            tx.sendto(b"<RadioInfo/>", ("127.0.0.1", p1))
            tx.sendto(b"<AppInfo/>", ("127.0.0.1", p2))
            for _ in range(40):
                if len(got) == 2:
                    break
                time.sleep(0.05)
            self.assertEqual(sorted(got), [b"<AppInfo/>", b"<RadioInfo/>"])
        finally:
            ls.stop()

    def test_busy_port_is_reported_not_shared(self):
        other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # "another program"
        other.bind(("127.0.0.1", 0))
        port = other.getsockname()[1]
        try:
            l = UdpListener(port, lambda d, a: None, "127.0.0.1")
            l.start()
            l.bound.wait(2)
            self.assertIsNone(l.sock)
            self.assertIn("already used by another program", l.error)
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()

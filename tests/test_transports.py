"""LAN(소켓)과 USB(VISA) 통신 계층을 시뮬레이터에 붙여 전체 흐름을 확인한다."""
import ctypes
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from scopetech import transport
from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.sim import SimScope
from scopetech.transport import VI_SUCCESS_MAX_CNT, SocketTransport, VisaTransport
from scopetech.workflow import Bench

ROOT = Path(__file__).resolve().parents[1]


def run_flow(test: unittest.TestCase, t, sim: SimScope) -> None:
    scope = MSO5B(t, settle_s=0)
    test.assertIn("MSO58B", scope.connect())
    cfg = Config(ROOT / "config.ini")
    cfg.cp.set("measure", "stats_acquisitions", "10")
    with tempfile.TemporaryDirectory() as d:
        bench = Bench(scope, cfg, Path(d), echo=lambda s: None)
        rec = bench.vih(1, 2)
        test.assertAlmostEqual(rec.result["analysis"]["vt_plus"]["stats"]["mean"], 1.90, delta=0.01)
        shots = bench.capture()
        bench.fetch_pending()
        test.assertTrue(all(p.read_bytes().startswith(b"\x89PNG") for p in shots))
        saved = bench.save([1, 2])
        test.assertEqual((rec.folder / "CH1.wfm").read_bytes()[:4], b"WFM#")
        test.assertEqual(len(saved), 4)
        bench.voh([2])
    test.assertEqual(sim.error_log, [])


class SocketTest(unittest.TestCase):
    def test_socket_flow(self):
        sim = SimScope()
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def serve():
            conn, _ = srv.accept()
            buf = b""
            with conn:
                while True:
                    data = conn.recv(65536)
                    if not data:
                        return
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        r = sim.handle(line.decode("latin-1"))
                        if r:
                            conn.sendall(r)

        th = threading.Thread(target=serve, daemon=True)
        th.start()
        t = SocketTransport("127.0.0.1", port, timeout=5)
        try:
            run_flow(self, t, sim)
        finally:
            t.close()
            srv.close()


class FakeVisa:
    """ctypes 로 호출되는 visa64.dll 함수 흉내. 응답은 SimScope 가 만든다."""

    CHUNK = 4096  # 작은 버퍼로 VI_SUCCESS_MAX_CNT 반복 읽기 확인

    def __init__(self, sim: SimScope):
        self.sim = sim
        self.pending = b""
        self.cleared = 0
        for name in ("viOpenDefaultRM", "viFindRsrc", "viFindNext", "viOpen", "viClose", "viClear",
                     "viSetAttribute", "viWrite", "viRead", "viStatusDesc"):
            fn = getattr(self, "_" + name)
            setattr(self, name, _Fn(fn))

    def _viOpenDefaultRM(self, ref):
        ref._obj.value = 1
        return 0

    def _viFindRsrc(self, rm, expr, flist, count, desc):
        count._obj.value = 2
        ctypes.memmove(desc, b"ASRL1::INSTR\0", 13)
        self._next = b"USB0::0x0699::0x0522::SIM0001::INSTR\0"
        return 0

    def _viFindNext(self, flist, desc):
        ctypes.memmove(desc, self._next, len(self._next))
        return 0

    def _viOpen(self, rm, name, mode, tmo, vi):
        assert name == b"USB0::0x0699::0x0522::SIM0001::INSTR"
        vi._obj.value = 2
        return 0

    def _viClose(self, obj):
        return 0

    def _viClear(self, vi):
        self.cleared += 1
        self.pending = b""
        return 0

    def _viSetAttribute(self, vi, attr, value):
        return 0

    def _viWrite(self, vi, data, n, ret):
        for line in data[:n].decode("latin-1").splitlines():
            r = self.sim.handle(line)
            if r:
                self.pending += r
        ret._obj.value = n
        return 0

    def _viRead(self, vi, buf, size, ret):
        if not self.pending:
            return transport.VI_ERROR_TMO
        n = min(size, self.CHUNK, len(self.pending))
        ctypes.memmove(buf, self.pending[:n], n)
        self.pending = self.pending[n:]
        ret._obj.value = n
        return VI_SUCCESS_MAX_CNT if self.pending else 0

    def _viStatusDesc(self, obj, status, buf):
        ctypes.memmove(buf, b"fake\0", 5)
        return 0


class _Fn:
    """argtypes/restype 속성을 받을 수 있는 호출 객체."""

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


class VisaTest(unittest.TestCase):
    def test_visa_flow(self):
        sim = SimScope()
        fake = FakeVisa(sim)
        with mock.patch.object(transport, "_load_visa_library", return_value=fake):
            t = VisaTransport(timeout=5)
        self.assertEqual(t.description, "USB USB0::0x0699::0x0522::SIM0001::INSTR")
        run_flow(self, t, sim)

    def test_picks_scope_among_tek_devices(self):
        fake = FakeVisa(SimScope())
        afg = b"USB0::0x0699::0x0353::AFG001::INSTR\0"
        scope = b"USB0::0x0699::0x0522::SIM0001::INSTR\0"
        names = [afg, scope]

        def find(rm, expr, flist, count, desc):
            count._obj.value = 2
            ctypes.memmove(desc, names[0], len(names[0]))
            return 0

        def find_next(flist, desc):
            ctypes.memmove(desc, names[1], len(names[1]))
            return 0

        opened = {}

        def vopen(rm, name, mode, tmo, vi):
            vi._obj.value = len(opened) + 10
            opened[vi._obj.value] = name
            return 0

        def write(vi, data, n, ret):
            ret._obj.value = n
            if opened.get(getattr(vi, "value", vi)) == afg.rstrip(b"\0"):
                fake.pending = b"TEKTRONIX,AFG31022,AFG001,FV:1.0\n"
                return 0
            return fake._viWrite(vi, data, n, ret)

        fake.viFindRsrc.fn, fake.viFindNext.fn, fake.viOpen.fn, fake.viWrite.fn = find, find_next, vopen, write
        with mock.patch.object(transport, "_load_visa_library", return_value=fake):
            t = VisaTransport(timeout=5)
        self.assertIn("0x0522", t.description)

    def test_timeout_raises(self):
        fake = FakeVisa(SimScope())
        with mock.patch.object(transport, "_load_visa_library", return_value=fake):
            t = VisaTransport(timeout=1)
        with self.assertRaises(transport.ScopeTimeout):
            t.read_line()
        t.recover()
        self.assertEqual(fake.cleared, 1)


if __name__ == "__main__":
    unittest.main()

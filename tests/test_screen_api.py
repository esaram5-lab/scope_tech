import shutil
import struct
import sys
import tempfile
import textwrap
import threading
import unittest
import zlib
from pathlib import Path

from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.sim import SimTransport
from scopetech.transport import ScopeError
from scopetech.workflow import Bench

REAL = Path(__file__).resolve().parents[2] / "esaram5-lab" / "scope-capture-api"

FAKE_CLIENT = '''
from pathlib import Path


class CaptureError(RuntimeError):
    pass


class ScopeCaptureClient:
    calls = []

    def __init__(self, host, *, token=None, token_file=None, port=8765, timeout=5):
        self.host, self.port = host, port
        self.token = Path(token_file).read_text().strip()

    def health(self):
        if self.token != "good":
            raise CaptureError("HTTP 401")
        return {"status": "ok"}

    def capture_png(self):
        ScopeCaptureClient.calls.append("capture")
        return b"\\x89PNG fake"
'''


def png():
    def chunk(k, d):
        return struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))


class ScreenApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(sys.modules.pop, "scope_capture_client", None)

    def bench(self, client_dir, token="good", **opts):
        (self.tmp / "tok.txt").write_text(token)
        cfg = Config()
        cfg.cp.set("capture", "method", "screen_api")
        cfg.cp.set("capture", "settle_s", "0")
        for k, v in {"host": "127.0.0.1", "token_file": str(self.tmp / "tok.txt"), "client_dir": str(client_dir),
                     "settle_s": "0", **opts}.items():
            cfg.cp.set("screen_api", k, v)
        t = SimTransport()
        s = MSO5B(t, settle_s=0)
        s.connect()
        return Bench(s, cfg, self.tmp / "out", echo=lambda _: None), t

    def fake_dir(self):
        d = self.tmp / "client"
        d.mkdir()
        (d / "scope_capture_client.py").write_text(textwrap.dedent(FAKE_CLIENT), encoding="utf-8")
        self.addCleanup(lambda: str(d) in sys.path and sys.path.remove(str(d)))
        return d

    def test_capture_uses_server_and_sends_no_scope_file_commands(self):
        b, t = self.bench(self.fake_dir())
        b.dut_pin = "2"
        rec = b.vih(1, 2)
        t.sim.log.clear()
        out = b.capture()
        self.assertEqual([p.name for p in out], ["meas.png", "clean.png"])
        self.assertEqual((rec.folder / "meas.png").read_bytes(), b"\x89PNG fake")
        self.assertFalse(any(x in c.upper() for c in t.sim.log for x in ("ACTONEVENT", "SAVE:IMAGE", "READFILE")))
        self.assertEqual(b.pending_count(), 0)

    def test_acquire_single_takes_new_acquisition_each_time(self):
        b, t = self.bench(self.fake_dir(), acquire="single")
        t.sim.log.clear()
        b.snap("edge")
        self.assertEqual(sum(1 for c in t.sim.log if c.upper().startswith("ACQUIRE:STATE ON")), 1)

    def test_bad_token_and_missing_client_are_clear_errors(self):
        b, _ = self.bench(self.fake_dir(), token="bad")
        with self.assertRaisesRegex(ScopeError, "연결 실패"):
            b.capturer.take(self.tmp / "x.png")
        b2, _ = self.bench(self.tmp / "nowhere")
        if not (Path(__file__).resolve().parents[1] / "scope_capture_client.py").exists():
            with self.assertRaisesRegex(ValueError, "scope_capture_client.py"):
                b2.capturer.take(self.tmp / "y.png")

    @unittest.skipUnless((REAL / "scope_capture_server.py").exists(), "scope-capture-api 저장소 없음")
    def test_real_server_and_client(self):
        sys.path.insert(0, str(REAL))
        self.addCleanup(sys.path.remove, str(REAL))
        from scope_capture_server import CaptureServer
        srv = CaptureServer(("127.0.0.1", 0), "good", png)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        b, _ = self.bench(REAL, port=str(srv.server_address[1]))
        rec = b.snap("real")
        self.assertEqual((rec.folder / "screen.png").read_bytes(), png())

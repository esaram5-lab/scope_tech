import os
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

# 동봉된 실제 서버·클라이언트 코드(scope_capture/)로도 시험한다
REAL = Path(os.environ.get("SCOPE_CAPTURE_API", Path(__file__).resolve().parents[1] / "scope_capture"))

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
        return {"status": "ok", "features": ["stable", "window"], "window": {"title": "TekScope", "found": True,
                                                                          "matched": "TekScope", "foreground": True}}

    def capture_png(self, stable_ms=0, interval_ms=None, frames=None):
        ScopeCaptureClient.calls.append((stable_ms, interval_ms, frames))
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
                     "settle_s": "0", "stable_interval_ms": "20", **opts}.items():
            cfg.cp.set("screen_api", k, v)
        t = SimTransport()
        s = MSO5B(t, settle_s=0)
        s.connect()
        return Bench(s, cfg, self.tmp / "out", echo=lambda _: None), t

    def fake_dir(self):
        d = (self.tmp / "client").resolve()
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
        calls = sys.modules["scope_capture_client"].ScopeCaptureClient.calls
        self.assertEqual(calls[-2:], [(2000, 20, 3), (2000, 20, 3)])

    def test_stable_off_and_old_server(self):
        b, _ = self.bench(self.fake_dir(), stable_ms="0")
        b.capturer.take(self.tmp / "a.png")
        self.assertEqual(sys.modules["scope_capture_client"].ScopeCaptureClient.calls[-1], (0, None, None))
        mod = sys.modules["scope_capture_client"]
        old_health = mod.ScopeCaptureClient.health
        mod.ScopeCaptureClient.health = lambda self: {"status": "ok"}  # v1.1 서버: features 없음
        try:
            b2, _ = self.bench(self.tmp / "client")
            with self.assertRaisesRegex(ValueError, "stable"):
                b2.capturer.take(self.tmp / "b.png")
        finally:
            mod.ScopeCaptureClient.health = old_health

    def test_acquire_single_takes_new_acquisition_each_time(self):
        b, t = self.bench(self.fake_dir(), acquire="single")
        t.sim.log.clear()
        b.snap("edge")
        self.assertEqual(sum(1 for c in t.sim.log if c.upper().startswith("ACQUIRE:STATE ON")), 1)

    def test_bad_token_and_missing_client_are_clear_errors(self):
        b, _ = self.bench(self.fake_dir(), token="bad")
        with self.assertRaisesRegex(ScopeError, "연결 실패"):
            b.capturer.take(self.tmp / "x.png")
        from scopetech.capture import screen_api
        b2, _ = self.bench(self.tmp / "nowhere")
        root = screen_api.ROOT
        screen_api.ROOT = self.tmp / "empty" / "root"  # scope.py 옆·옆 폴더에도 없는 상황
        try:
            with self.assertRaisesRegex(ValueError, "scope_capture_client.py"):
                b2.capturer.take(self.tmp / "y.png")
        finally:
            screen_api.ROOT = root

    @unittest.skipUnless((REAL / "scope_capture_server.py").exists(), "scope-capture-api 저장소 없음")
    def test_real_server_and_client(self):
        sys.path.insert(0, str(REAL))
        self.addCleanup(lambda: [sys.path.remove(p) for p in list(sys.path) if p == str(REAL.resolve())])
        from scope_capture_server import CaptureServer
        srv = CaptureServer(("127.0.0.1", 0), "good", png)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        b, _ = self.bench(REAL, port=str(srv.server_address[1]))
        rec = b.snap("real")  # stable_ms=2000: 같은 화면 3장 연속이면 반환
        self.assertEqual((rec.folder / "screen.png").read_bytes(), png())
        frames = iter(range(10**6))
        srv.grab = lambda: (next(frames), png)  # 계속 바뀌는 화면 -> 저장하지 않고 안내
        b2, _ = self.bench(REAL, port=str(srv.server_address[1]), stable_ms="200")
        with self.assertRaisesRegex(ScopeError, "acquire"):
            b2.snap("moving")

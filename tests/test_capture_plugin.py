import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from scopetech import capture
from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.sim import SimTransport
from scopetech.workflow import Bench

PLUGIN = '''
from scopetech.capture.base import Capture as Base


class MyShot(Base):
    label = "테스트용 직접 구현"
    taken = []

    def take(self, local):
        self.scope.stop()
        local.write_bytes(b"\\x89PNG fake " + local.name.encode())
        MyShot.taken.append(local.name)
        return local
'''


class CapturePluginTest(unittest.TestCase):
    def bench(self, d, **capture_opts):
        cfg = Config()
        for k, v in capture_opts.items():
            cfg.cp.set("capture", k, v)
        s = MSO5B(SimTransport(), settle_s=0)
        s.connect()
        return Bench(s, cfg, Path(d), echo=lambda _: None), s

    def test_default_is_aoe_and_off_switches(self):
        with tempfile.TemporaryDirectory() as d:
            b, _ = self.bench(d)
            self.assertEqual(type(b.capturer).__module__, "scopetech.capture.aoe")
            self.assertTrue(b.capture_enabled())
            b2, _ = self.bench(d, enabled="no")
            self.assertEqual(type(b2.capturer).__module__, "scopetech.capture.off")
            self.assertFalse(b2.capture_enabled())
            b3, _ = self.bench(d, method="off")
            self.assertFalse(b3.capture_enabled())

    def test_user_module_next_to_scope_py(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "my_capture_t.py").write_text(textwrap.dedent(PLUGIN), encoding="utf-8")
            sys.path.insert(0, d)
            self.addCleanup(sys.path.remove, d)
            self.addCleanup(sys.modules.pop, "my_capture_t", None)
            b, s = self.bench(Path(d) / "out", method="my_capture_t:MyShot")
            b.dut_pin = "2"
            rec = b.vih(1, 2)
            s.t.sim.log.clear()
            out = b.capture()
            self.assertEqual([p.name for p in out], ["meas.png", "clean.png"])
            self.assertEqual((rec.folder / "clean.png").read_bytes(), b"\x89PNG fake clean.png")
            self.assertFalse(any("ACTONEVENT" in c.upper() for c in s.t.sim.log))
            self.assertEqual(b.pending_count(), 0)
            snap = b.snap("edge")
            self.assertTrue((snap.folder / "screen.png").exists())

    def test_bad_method_names(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "찾을 수 없습니다"):
                self.bench(d, method="no_such_capture_xyz")[0].capturer
            with self.assertRaisesRegex(ValueError, "클래스가 없습니다"):
                self.bench(d, method="aoe:Nope")[0].capturer

    def test_template_tells_what_to_implement(self):
        with tempfile.TemporaryDirectory() as d:
            b, _ = self.bench(d, method="template")
            with self.assertRaises(NotImplementedError):
                b.capturer.take(Path(d) / "x.png")
            self.assertTrue(issubclass(type(b.capturer), capture.Capture))

    def test_shipped_default_is_screen_api_and_old_aoe_images_still_fetch(self):
        import configparser
        cp = configparser.ConfigParser()
        cp.read(Path(__file__).resolve().parents[1] / "config.ini", encoding="utf-8")
        self.assertEqual(cp.get("capture", "method"), "screen_api")
        with tempfile.TemporaryDirectory() as d:
            b, scope = self.bench(d)
            b.dut_pin = "2"
            b.snap("old")  # aoe 로 스코프에 저장해 둔 캡처
            cfg = Config()
            cfg.cp.set("capture", "method", "off")
            b2 = Bench(scope, cfg, Path(d), echo=lambda _: None)  # 방법을 바꾼 뒤 같은 스코프
            self.assertEqual(b2.pending_count(), 1)
            self.assertEqual(b2.fetch_pending(), (1, 0))


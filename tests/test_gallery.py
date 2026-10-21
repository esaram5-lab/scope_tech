import tempfile
import unittest
from pathlib import Path

from scopetech.config import Config
from scopetech.gallery import image_name
from scopetech.mso import MSO5B
from scopetech.sim import SimTransport
from scopetech.workflow import Bench


class GalleryTest(unittest.TestCase):
    def bench(self, d, result_dir="result"):
        cfg = Config()
        cfg.cp.set("output", "result_dir", result_dir)
        s = MSO5B(SimTransport(), settle_s=0)
        s.connect()
        b = Bench(s, cfg, Path(d), echo=lambda _: None)
        b.name, b.temp = "INV 3V3", "-40"
        return b

    def test_names(self):
        r = {"item": "VIH", "name": "INV", "temp": "85", "dut_pin": "2", "out_pin": "12"}
        self.assertEqual(image_name(r, "meas.png"), "INV_85C_PIN2_OUT12_VIL-VIH")
        self.assertEqual(image_name(r, "clean.png"), "INV_85C_PIN2_OUT12_VIL-VIH_clean")
        self.assertEqual(image_name({"item": "VOH", "name": "INV", "temp": "25", "dut_pin": "20"}, "clean.png"),
                         "INV_25C_PIN20_VOH-VOL_clean")
        self.assertEqual(image_name({"item": "MANUAL", "name": "T", "temp": "25", "dut_pin": "5", "manual_item": "R pu"},
                                    "screen.png"), "T_25C_PIN5_R_pu")

    def test_deferred_captures_are_copied_when_fetched_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            b = self.bench(d)
            b.dut_pin, b.out_pin = "2", "12"
            for _ in range(2):  # 같은 조건 재측정
                b.vih(1, 2)
                b.capture()
            self.assertFalse((Path(d) / "result").exists())  # aoe: 가져오기 전에는 파일 없음
            b.fetch_pending()
            b.dut_pin, b.out_pin = "7", ""
            b.snap("edge")
            b.fetch_pending()
            names = sorted(p.name for p in (Path(d) / "result").iterdir())
            self.assertEqual(names, ["INV_3V3_-40C_PIN2_OUT12_VIL-VIH.png", "INV_3V3_-40C_PIN2_OUT12_VIL-VIH_2.png",
                                     "INV_3V3_-40C_PIN2_OUT12_VIL-VIH_clean.png",
                                     "INV_3V3_-40C_PIN2_OUT12_VIL-VIH_clean_2.png", "INV_3V3_-40C_PIN7_edge.png"])
            original = next((Path(d) / "VIH").glob("*/meas.png"))
            self.assertEqual((Path(d) / "result" / names[0]).read_bytes(), original.read_bytes())

    def test_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            b = self.bench(d, result_dir="")
            b.dut_pin = "7"
            b.snap("edge")
            b.fetch_pending()
            self.assertFalse((Path(d) / "result").exists())

import tempfile
import unittest
from pathlib import Path

import report
from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.sim import SimTransport
from scopetech.workflow import Bench


class ReportTest(unittest.TestCase):
    def test_groups_values_by_pin_and_judges(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "captures"
            cfg = Config()
            cfg.cp.add_section("spec:T")
            for k, v in (("vih_min", "2.0"), ("vil_max", "1.5"), ("voh_min", "3.0"), ("vol_max", "0.4")):
                cfg.cp.set("spec:T", k, v)
            s = MSO5B(SimTransport(), settle_s=0)
            s.connect()
            b = Bench(s, cfg, base, echo=lambda _: None)
            b.spec_name = "T"
            b.dut_pin, b.out_pin = "2", "12"
            b.vih(1, 2)
            b.capture()
            b.dut_pin, b.out_pin = "7", ""
            b.snap("edge")
            b.fetch_pending()
            cfg_path = Path(d) / "config.ini"
            with cfg_path.open("w", encoding="utf-8") as f:
                cfg.cp.write(f)
            out = Path(d) / "r.md"
            self.assertEqual(report.main(["--config", str(cfg_path), "--dir", str(base), "--out", str(out)]), 0)
            md = out.read_text(encoding="utf-8")
            pin2 = md.split("## 핀 2\n")[1].split("## 핀 ")[0]
            pin12 = md.split("## 핀 12\n")[1].split("## 핀 ")[0]
            self.assertIn("VT+ (VIH측)", pin2)
            self.assertNotIn("VOH (출력 High)", pin2)
            self.assertIn("VOH (출력 High)", pin12)
            # 시뮬레이터 VT- 약 1.4 V < VIL(max) 1.5 -> FAIL, VT+ 약 1.9 V <= VIH(min) 2.0 -> PASS
            self.assertIn("**FAIL**", pin2)
            self.assertIn("PASS", pin2)
            self.assertIn("![meas.png](captures/VIH/", md)
            self.assertIn("## 핀 7", md)
            self.assertIn("screen.png", md.split("## 핀 7")[1])
            self.assertIn("| T | 2.000 | 1.500 | 3.000 | 0.400 |", md)

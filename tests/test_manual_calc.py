import json
import tempfile
import unittest
from pathlib import Path

import report
import temp_table
from scopetech import manual_calc
from scopetech.analysis import Waveform
from scopetech.config import Config


def dc(source, volts, n=10):
    return Waveform(source, [volts] * n, 1e-6)


def pin_voltage(rp, pull_to_vdd_ext, vdd=3.3, r_ext=1e6, r_probe=10e6):
    """회로를 풀어 핀 전압을 만든다(시험용 정답)."""
    if pull_to_vdd_ext:  # Rpd: R_ext 핀->VDD, 아래 Rpd||probe
        low = manual_calc.parallel(rp, r_probe)
        return vdd * low / (r_ext + low)
    low = manual_calc.parallel(r_ext, r_probe)  # Rpu: 위 Rpu, 아래 R_ext||probe
    return vdd * low / (rp + low)


class ManualCalcTest(unittest.TestCase):
    def test_kind_from_label(self):
        self.assertEqual([manual_calc.kind_of(x) for x in ("R_pd", "rpd", "R pu", "IOH", "i_o_l", "R_x")],
                         ["RPD", "RPD", "RPU", "IOH", "IOL", None])

    def test_resistance_recovers_true_value_with_probe_correction(self):
        cfg = Config()
        for rp in (50e3, 100e3, 2e6):
            calc = manual_calc.compute("R_pd", [dc("CH1", pin_voltage(rp, True))], cfg)
            self.assertAlmostEqual(calc["value"] / rp, 1, places=6)
            calc = manual_calc.compute("R_pu", [dc("CH1", pin_voltage(rp, False))], cfg)
            self.assertAlmostEqual(calc["value"] / rp, 1, places=6)
        # 프로브 보정이 없으면 Rpu 100k 를 약 10% 크게 읽는다(1MΩ ‖ 10MΩ = 0.909MΩ)
        v = pin_voltage(100e3, False)
        self.assertAlmostEqual(1e6 * (3.3 - v) / v / 100e3, 1.1, places=3)

    def test_out_of_range_is_reported_not_guessed(self):
        cfg = Config()
        for label, v in (("R_pd", 3.3), ("R_pd", 3.0), ("R_pu", 0.0)):
            calc = manual_calc.compute(label, [dc("CH1", v)], cfg)
            self.assertIsNone(calc["value"], (label, v))
            self.assertTrue(calc["note"])

    def test_current_and_voltage_channels(self):
        calc = manual_calc.compute("IOH", [dc("CH3", -0.004), dc("CH1", 3.05)], Config())
        self.assertEqual(manual_calc.display(calc), ("-4.000", "mA"))
        self.assertAlmostEqual(calc["inputs"]["v_pin"], 3.05)
        self.assertIsNone(manual_calc.compute("R_x", [dc("CH1", 1)], Config()))

    def test_report_and_temp_table_show_values(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            for temp, rp in (("25", 100e3), ("85", 120e3)):
                folder = base / "MANUAL" / f"PIN5_{temp}"
                folder.mkdir(parents=True)
                calc = manual_calc.compute("R_pd", [dc("CH1", pin_voltage(rp, True))], Config())
                folder.joinpath("result.json").write_text(json.dumps(
                    {"item": "MANUAL", "manual_item": "R_pd", "dut_pin": "5", "temp": temp, "name": "PD",
                     "out_type": "PP", "timestamp": f"2026-10-21T0{temp[0]}:00:00", "metrics": [], "calc": calc}),
                    encoding="utf-8")
            out = base / "r.md"
            self.assertEqual(report.main(["--dir", str(base), "--out", str(out)]), 0)
            md = out.read_text(encoding="utf-8")
            self.assertIn("## 핀별 저항·전류", md)
            self.assertIn("| 5 | Rpd | PD | 25 | 100.0 kΩ |", md)
            self.assertIn("| 5 | Rpd | PD | 85 | 120.0 kΩ |", md)
            temps, rows = temp_table.table([e for e in report.load(base, Config(), type("A", (), {
                "name": None, "temp": None, "spec": None})())[0]])
            self.assertEqual(temps, ["25", "85"])
            self.assertEqual(rows, [["5", "Rpd (kΩ)", "PD", "PP", "CH1", "100.0000", "120.0000"]])

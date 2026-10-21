import csv
import tempfile
import unittest
from pathlib import Path

import temp_table


def entry(pin, temp, ts, values, name="INV", out_type="PP", channel="CH1->CH2"):
    return {"pin": pin, "channel": channel, "result": {"temp": temp, "timestamp": ts, "name": name, "out_type": out_type},
            "values": {k: {"worst": w, "typ": t} for k, (w, t) in values.items()}}


class TempTableTest(unittest.TestCase):
    ENTRIES = [
        entry("2", "85", "2026-10-21T10:00:00", {"VIL": (1.30, 1.31), "VIH": (1.95, 1.94)}),
        entry("2", "-40", "2026-10-21T09:00:00", {"VIL": (1.40, 1.41), "VIH": (1.90, 1.89)}),
        entry("2", "-40", "2026-10-21T09:30:00", {"VIL": (1.45, 1.46), "VIH": (1.85, 1.84)}),  # 재측정
        entry("12", "25", "2026-10-21T09:00:00", {"VOL": (0.05, 0.04), "VOH": (3.2, 3.25)}, channel="CH2"),
    ]

    def test_pivot_by_temperature(self):
        temps, rows = temp_table.table(self.ENTRIES)
        self.assertEqual(temps, ["-40", "25", "85"])
        self.assertEqual(rows[0], ["2", "VIL (VT-)", "INV", "PP", "CH1->CH2", "1.4500", "", "1.3000"])  # 마지막 측정
        self.assertEqual(rows[1], ["2", "VIH (VT+)", "INV", "PP", "CH1->CH2", "1.8500", "", "1.9500"])
        self.assertEqual(rows[2][:2], ["12", "VOL"])
        self.assertEqual(rows[3][5:], ["", "3.2000", ""])

    def test_pick_worst_and_typ(self):
        _, rows = temp_table.table(self.ENTRIES, pick="worst")
        self.assertEqual(rows[0][5], "1.4000")  # VIL 최악 = 가장 낮은 VT-
        self.assertEqual(rows[1][5], "1.9000")  # VIH 최악 = 가장 높은 VT+
        _, rows = temp_table.table(self.ENTRIES, value="typ")
        self.assertEqual(rows[0][5], "1.4600")

    def test_cli_writes_excel_csv(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            folder = base / "VIH" / "x"
            folder.mkdir(parents=True)
            (folder / "result.json").write_text(
                '{"item":"VIH","dut_pin":"2","out_pin":"12","temp":"85","name":"INV","out_type":"PP",'
                '"timestamp":"t","metrics":[{"channel":"CH1->CH2","signal":"s","metric":"VT+ (VIH측)","key":"VIH",'
                '"typ":1.9,"worst":1.95,"n":3}]}', encoding="utf-8")
            out = base / "t.csv"
            self.assertEqual(temp_table.main(["--dir", str(base), "--out", str(out)]), 0)
            self.assertTrue(out.read_bytes().startswith(b"\xef\xbb\xbf"))
            with out.open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows, [["핀", "항목", "시험", "출력", "채널", "85°C"],
                                    ["2", "VIH (VT+)", "INV", "PP", "CH1->CH2", "1.9500"]])

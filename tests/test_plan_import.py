import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

import plan_import as cli
from scopetech import results, wavefile
from scopetech.analysis import Waveform, find_thresholds
from scopetech.config import Config
from scopetech.plan import DONE, PlanStore, load_plan
from scopetech.plan_import import SOURCE, apply, plan_import, scan
from tests.test_analysis import schmitt, triangle

ROOT = Path(__file__).resolve().parents[1]


def stamp(ts: str) -> str:
    return ts.replace("-", "").replace(":", "").replace("T", "_")


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.cfg = Config()
        self.rows = load_plan(ROOT / "test_plan.csv")
        self.store = PlanStore(self.base)

    # ------------------------------------------------------------ 예전 형식 기록 만들기
    def vih(self, pin, ts, out_pin=None, temp="25", vt=(1.9, 1.4), folder=None, pin_key=True, with_output=False):
        """예전 메뉴 1 기록: out_pin 없음, 입력 VT+/VT- 만 (출력 VOH/VOL 은 0f30bb7~550a658 사이 버전에 없었음)."""
        name = folder or f"PIN{pin}" + (f"_OUT{out_pin}" if out_pin else "") + f"_OLD_{temp}C_CH1-CH2_{stamp(ts)}"
        d = self.base / "VIH" / name
        d.mkdir(parents=True)
        vin = triangle()
        wi, wo = Waveform("CH1", vin, 1e-6), Waveform("CH2", schmitt(vin, *vt), 1e-6)
        wavefile.write_csv(d, [wi, wo])
        th = find_thresholds(wi, wo)
        r = {"item": "VIH", "name": "OLD", "temp": temp, "out_type": "PP", "timestamp": ts,
             "channels": {"in": "CH1", "out": "CH2"}, "analysis": th.to_dict(),
             "metrics": results.vih_metrics(th, "CH1->CH2", "CH1->CH2")
             + (results.vih_output_metrics(th, "CH2", "CH2") if with_output else [])}
        if pin_key:
            r["dut_pin"] = pin
        if out_pin and pin_key:
            r["out_pin"] = out_pin
        (d / "result.json").write_text(json.dumps(r), encoding="utf-8")
        return d

    def manual(self, pin, label, ts, waves, temp="25", calc=None):
        """예전 메뉴 3 기록: waves = {채널: 평균값}, 입력한 채널 순서대로 저장."""
        chans = list(waves)
        d = self.base / "MANUAL" / f"PIN{pin}_OLD_{temp}C_{'-'.join(chans)}_{stamp(ts)}_{label}"
        d.mkdir(parents=True)
        if waves:
            wavefile.write_csv(d, [Waveform(c, [v] * 50, 1e-6) for c, v in waves.items()])
        r = {"dut_pin": pin, "item": "MANUAL", "name": "OLD", "temp": temp, "timestamp": ts, "manual_item": label,
             "channels": chans, "metrics": [], "status": "complete"}
        if calc:
            r["calc"] = calc
        (d / "result.json").write_text(json.dumps(r), encoding="utf-8")
        return d

    def run_import(self, **kw):
        return plan_import(self.rows, self.store, self.cfg, scan(self.base), **kw)

    def pick(self, picks, no, temp="25"):
        return next(p for p in picks if p.row.no == no and p.temp == temp)

    def progress(self, temp="25"):
        return self.store.load(temp)["rows"]

    # ------------------------------------------------------------ 시험
    def test_old_vih_record_gets_output_pin_and_levels_from_waveform(self):
        d = self.vih("2", "2026-10-10T10:00:00")
        original = (d / "result.json").read_text(encoding="utf-8")
        picks, unmatched = self.run_import()
        p = self.pick(picks, "1")
        self.assertEqual(p.state, SOURCE)
        self.assertTrue(p.changed)
        self.assertTrue(any("출력 VOH/VOL" in n for n in p.notes))
        self.assertTrue(any("출력 핀 12" in n for n in p.notes))
        self.assertFalse((d / "result.orig.json").exists())  # 미리보기만으로는 파일을 바꾸지 않음
        apply(picks, self.store)
        self.assertEqual((d / "result.orig.json").read_text(encoding="utf-8"), original)
        r = json.loads((d / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(r["out_pin"], "12")
        self.assertTrue(any(m.get("output") and m["key"] == "VOH" for m in r["metrics"]))
        e = self.progress()[self.rows[0].key]
        self.assertEqual((e["status"], e["source"], e["folder"], e["time"]), (DONE, SOURCE, f"VIH/{d.name}", "2026-10-10T10:00:00"))
        self.assertIn("출력 VOH 3.", e["summary"])

    def test_pin_and_temp_from_folder_name(self):
        self.vih("20", "2026-10-10T10:00:00", folder="PIN20_OUT4_OLD_85C_CH1-CH2_20261010_100000", pin_key=False, temp="")
        picks, unmatched = self.run_import()
        self.assertEqual(unmatched, [])
        p = self.pick(picks, "2", "85")
        self.assertEqual((p.state, p.found.pin, p.found.out_pin), (SOURCE, "20", "4"))
        self.assertEqual(p.result["dut_pin"], "20")

    def test_latest_usable_record_wins(self):
        self.vih("3", "2026-10-10T10:00:00", out_pin="16", vt=(1.8, 1.3))
        new = self.vih("3", "2026-10-12T10:00:00", out_pin="16", vt=(2.0, 1.5), with_output=True)
        self.vih("3", "2026-10-11T10:00:00", out_pin="16", vt=(1.7, 1.2))
        # Rpd 핀 2: 가장 최근 기록은 핀 전압이 VDD 라 값이 안 나옴 -> 그 전 기록
        self.manual("2", "R_pd", "2026-10-10T09:00:00", {"CH2": 1.65})
        self.manual("2", "R_pd", "2026-10-12T09:00:00", {"CH2": 3.3})
        picks, _ = self.run_import()
        p = self.pick(picks, "3")
        self.assertEqual((p.found.folder, p.candidates, p.changed), (new, 3, False))
        self.assertAlmostEqual(next(m["worst"] for m in p.result["metrics"] if m["key"] == "VIH"), 2.0, delta=0.01)
        rpd = self.pick(picks, "16")
        self.assertEqual(rpd.found.time, "2026-10-10T09:00:00")
        self.assertTrue(any("더 최근 기록을 쓸 수 없어" in n for n in rpd.notes))
        # 1.65 V = VDD/2 -> Rpd‖10M = 1M -> Rpd = 1.111 MΩ
        self.assertAlmostEqual(rpd.result["calc"]["value"], 1 / (1 / 1e6 - 1 / 10e6), delta=10)

    def test_manual_calc_uses_plan_channel_order_and_bias_check(self):
        self.manual("12", "IOH", "2026-10-10T10:00:00", {"CH2": 2.4, "CH8": -0.004})  # 채널을 2,8 순서로 저장
        wrong = {"kind": "IOH", "name": "IOH", "value": 2.4, "unit": "A", "inputs": {"channel": "CH2"}}
        self.manual("4", "ioh", "2026-10-10T10:00:00", {"CH2": 2.0, "CH8": -0.006}, calc=wrong)
        picks, _ = self.run_import()
        p12, p4 = self.pick(picks, "9"), self.pick(picks, "10")
        self.assertAlmostEqual(p12.result["calc"]["value"], -0.004, places=6)
        self.assertAlmostEqual(p12.result["calc"]["inputs"]["v_pin"], 2.4, places=6)
        self.assertNotIn("check", p12.result["calc"])
        self.assertAlmostEqual(p4.result["calc"]["value"], -0.006, places=6)
        self.assertTrue(any("계획의 채널 순서" in n for n in p4.notes))
        self.assertIn("부하 조건", p4.result["calc"]["check"])  # 2.0 V 는 2.4 ± 0.1 밖

    def test_plan_mode_rows_are_kept_unless_replace(self):
        old = self.vih("2", "2026-10-10T10:00:00", out_pin="12", with_output=True)
        new = self.vih("2", "2026-10-12T10:00:00", out_pin="12", with_output=True)
        key = self.rows[0].key
        self.store.save("25", {"rows": {key: {"status": DONE, "folder": f"VIH/{old.name}", "time": "2026-10-10T10:00:00"}}})
        picks, _ = self.run_import()
        p = self.pick(picks, "1")
        self.assertEqual(p.state, "유지")
        self.assertTrue(any("--replace" in n for n in p.notes))
        apply(picks, self.store)
        self.assertEqual(self.progress()[key]["folder"], f"VIH/{old.name}")
        picks, _ = self.run_import(replace=True)
        apply(picks, self.store)
        self.assertEqual(self.progress()[key]["folder"], f"VIH/{new.name}")

    def test_unmatched_records_report_why(self):
        self.manual("5", "leak", "2026-10-10T10:00:00", {"CH2": 1.0})
        self.vih("99", "2026-10-10T10:00:00", out_pin="98")
        self.vih("", "2026-10-10T10:00:00", folder="OLD_25C_CH1-CH2_20261010_100000", pin_key=False)
        self.manual("6", "R_pu", "2026-10-10T10:00:00", {})  # 저장 중 실패
        _, unmatched = self.run_import()
        why = sorted(f.problem for f in unmatched)
        self.assertEqual(len(why), 4)
        self.assertTrue(any("'leak'" in w for w in why))
        self.assertTrue(any("입력 99 -> 출력 98" in w for w in why))
        self.assertTrue(any("핀 정보 없음" in w for w in why))
        self.assertTrue(any("파형 CSV 도 없음" in w for w in why))

    def test_rerun_keeps_first_backup_and_filters(self):
        d = self.vih("2", "2026-10-10T10:00:00")
        self.vih("20", "2026-09-01T10:00:00", out_pin="4", temp="-40")
        picks, _ = self.run_import(since="2026-10-01")
        self.assertEqual([p.row.no for p in picks], ["1"])
        apply(picks, self.store)
        backup = (d / "result.orig.json").read_text(encoding="utf-8")
        picks, _ = self.run_import(since="2026-10-01")
        self.assertEqual([p.state for p in picks], ["같음"])
        self.assertEqual((d / "result.orig.json").read_text(encoding="utf-8"), backup)
        picks, _ = self.run_import(temps=["-40"])
        self.assertEqual([(p.temp, p.row.no) for p in picks], [("-40", "2")])
        self.vih("6", "2026-10-10T10:00:00", out_pin="16", temp="85")
        picks, _ = self.run_import(order=["25", "85", "-40"])
        self.assertEqual([p.temp for p in picks], ["25", "85", "-40"])

    def test_broken_waveform_does_not_stop_import(self):
        d = self.vih("2", "2026-10-10T10:00:00")
        (d / "waveform.csv").write_text("# x\ntime_s,CH1_V,CH2_V\n0,abc\n", encoding="utf-8")
        m = self.manual("20", "R_pd", "2026-10-10T10:00:00", {"CH2": 1.0})
        (m / "waveform.csv").write_text("# x\ntime_s,CH2_V\n", encoding="utf-8")
        picks, _ = self.run_import()
        vi, rpd = self.pick(picks, "1"), self.pick(picks, "19")
        self.assertEqual(vi.state, SOURCE)  # 입력 VIH/VIL 은 있으니 가져오고 출력은 없다고 적음
        self.assertTrue(any("출력 VOH/VOL 없음" in n for n in vi.notes))
        self.assertEqual(rpd.state, "없음")

    def test_cli_preview_confirm_and_summary(self):
        self.vih("2", "2026-10-10T10:00:00", temp="85.0")
        self.manual("20", "Rpd", "2026-10-10T10:00:00", {"CH2": 1.0}, temp="85")
        args = ["--dir", str(self.base)]
        out = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(out))
        self.assertEqual(cli.main(args + ["--dry-run"]), 0)
        self.assertFalse(self.store.progress_path("85").exists())
        self.assertEqual(cli.main(args, ask=lambda _: "n"), 0)
        self.assertFalse(self.store.progress_path("85").exists())
        self.assertEqual(cli.main(args, ask=lambda _: "y"), 0)
        rows = self.store.load("85")["rows"]
        self.assertEqual(sorted(e["source"] for e in rows.values()), [SOURCE, SOURCE])
        with (self.store.dir / "plan_summary.csv").open(encoding="utf-8-sig", newline="") as f:
            table = list(csv.reader(f))
        self.assertEqual(table[0][4:], ["25°C", "85°C", "-40°C"])
        cells = {(r[1], r[2]): r[5] for r in table[1:]}
        self.assertTrue(cells[("2", "VIH")] and cells[("12", "VOH")] and cells[("20", "Rpd")])
        self.assertEqual(cells[("20", "VIH")], "")
        self.assertEqual(len(list(self.store.dir.glob("import_*.csv"))), 1)
        self.assertEqual(cli.main(args + ["--apply"]), 0)  # 다시 해도 바뀔 것 없음
        self.assertIn("새로 가져올 항목이 없습니다", out.getvalue())


if __name__ == "__main__":
    unittest.main()

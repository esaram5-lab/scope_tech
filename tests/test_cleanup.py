import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

import cleanup
from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.plan import PlanRunner, PlanStore, load_layout, load_plan
from scopetech.samples import Samples, guess, order_key
from scopetech.sim import SimTransport
from scopetech.workflow import Bench
from tests import test_plan_import

ROOT = Path(__file__).resolve().parents[1]


class SamplesTest(unittest.TestCase):
    def test_guess_and_ids(self):
        self.assertEqual(guess("TSMC_1"), ("TSMC", "1"))
        self.assertEqual(guess("inv tsmc 3"), ("TSMC", "3"))
        self.assertEqual(guess("VIS-2"), ("VIS", "2"))
        self.assertEqual(guess("Vanguard_02"), ("VIS", "2"))
        self.assertEqual(guess("뱅가드3"), ("VIS", "3"))
        self.assertEqual(guess("SIM"), ("", ""))
        self.assertEqual(guess("TSMC"), ("TSMC", ""))
        with tempfile.TemporaryDirectory() as d:
            s = Samples(Path(d))
            self.assertEqual(s.id_of("TSMC_1"), "TSMC_1")
            self.assertEqual(s.id_of("INV 3V3"), "INV_3V3")  # 모르면 이름 그대로 한 샘플
            self.assertEqual(s.sync(["TSMC_1", "오타TSMC1", "SIM", "TSMC_1"]), ["TSMC_1", "오타TSMC1", "SIM"])
            (Path(d) / "samples.csv").write_text("시험이름,fab,샘플\nTSMC_1,TSMC,1\n오타TSMC1,tsmc,2\nSIM,VIS,3\n",
                                                 encoding="utf-8-sig")
            s = Samples(Path(d))
            self.assertEqual([s.id_of(n) for n in ("TSMC_1", "오타TSMC1", "SIM")], ["TSMC_1", "TSMC_2", "VIS_3"])
            self.assertEqual(s.sync(["SIM", "VIS_1"]), ["VIS_1"])
            self.assertEqual(Samples(Path(d)).id_of("SIM"), "VIS_3")  # 사용자가 고친 줄은 그대로
        self.assertEqual(sorted(["TSMC_2", "X", "VIS_10", "TSMC_1", "VIS_2"], key=order_key),
                         ["VIS_2", "VIS_10", "TSMC_1", "TSMC_2", "X"])


class CleanupTest(unittest.TestCase):
    def setUp(self):
        self.h = test_plan_import.ImportTest("test_pin_and_temp_from_folder_name")  # 예전 형식 기록을 만드는 도우미
        self.h.setUp()
        self.addCleanup(self.h.tmp.cleanup)
        self.base = self.h.base
        self.root = PlanStore(self.base)
        self.rows = load_plan(ROOT / "test_plan.csv")
        self.out = io.StringIO()

    def run_cleanup(self, *extra, answer="y"):
        with contextlib.redirect_stdout(self.out):
            return cleanup.main(["--dir", str(self.base), *extra], ask=lambda _: answer)

    def table(self, path):
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.reader(f))

    def test_samples_are_not_mixed(self):
        t1 = self.h.vih("2", "2026-10-10T10:00:00", out_pin="12", vt=(1.8, 1.3), name="TSMC_1")
        v1 = self.h.vih("2", "2026-10-12T10:00:00", out_pin="12", vt=(2.0, 1.5), name="VIS_1")  # 더 최근이지만 다른 샘플
        self.h.vih("2", "2026-10-09T10:00:00", out_pin="12", vt=(1.7, 1.2), name="TSMC 1 old")
        self.assertEqual(self.run_cleanup(), 0)
        key = self.rows[0].key
        self.assertEqual(self.root.of("TSMC_1").load("25")["rows"][key]["folder"], f"VIH/{t1.name}")  # 같은 샘플의 최신
        self.assertEqual(self.root.of("VIS_1").load("25")["rows"][key]["folder"], f"VIH/{v1.name}")
        all_ = self.table(self.root.root / "plan_summary_all.csv")
        self.assertEqual(all_[0][4:7], ["VIS_1 25°C", "VIS_1 85°C", "VIS_1 -40°C"])
        self.assertEqual(all_[0][7], "TSMC_1 25°C")
        vih = next(r for r in all_ if r[1:3] == ["2", "VIH"])
        self.assertAlmostEqual(float(vih[4]), 2.0, delta=0.01)
        self.assertAlmostEqual(float(vih[7]), 1.8, delta=0.01)
        self.assertTrue((self.root.of("TSMC_1").summary_path).exists())
        names = {r[0]: r[1:] for r in self.table(self.root.root / "samples.csv")[1:]}
        self.assertEqual(names["TSMC 1 old"], ["TSMC", "1"])

    def test_missing_pins_round_trip(self):
        self.h.vih("", "2026-10-10T10:00:00", folder="TSMC_1_25C_CH1-CH2_20261010_100000", pin_key=False)
        self.assertEqual(self.run_cleanup("--dry-run"), 0)
        pins = self.root.root / "missing_pins.csv"
        table = self.table(pins)
        self.assertEqual(table[1][0], "VIH/TSMC_1_25C_CH1-CH2_20261010_100000")
        self.assertEqual(table[1][2], "OLD")  # 시험 이름은 result.json 의 name
        table[1][5:7] = ["3", "16"]
        with pins.open("w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows(table)
        self.assertEqual(self.run_cleanup(), 0)
        e = self.root.of("OLD").load("25")["rows"][self.rows[2].key]
        self.assertEqual(e["folder"], "VIH/TSMC_1_25C_CH1-CH2_20261010_100000")
        r = json.loads((self.base / e["folder"] / "result.json").read_text(encoding="utf-8"))
        self.assertEqual((r["dut_pin"], r["out_pin"]), ("3", "16"))
        self.assertEqual(self.table(pins)[1][5:7], ["3", "16"])  # 다시 써도 채운 칸 유지

    def test_legacy_progress_results_csv_and_rerun(self):
        d = self.h.vih("2", "2026-10-10T10:00:00", name="TSMC_2")  # 출력 핀·VOH/VOL 없는 예전 형식
        self.root.root.mkdir(parents=True)
        legacy = {"name": "TSMC_2", "temp": "25", "rows": {self.rows[1].key: {"status": "건너뜀"}}}
        (self.root.root / "progress_25C.json").write_text(json.dumps(legacy), encoding="utf-8")
        (self.root.root / "plan_summary.csv").write_text("old", encoding="utf-8")
        (self.base / "results.csv").write_text("old", encoding="utf-8")
        self.assertEqual(self.run_cleanup(), 0)
        out = self.out.getvalue()
        self.assertIn("출력 핀 없음: 1개", out)
        self.assertIn("progress_25C.json -> TSMC_2/", out)
        self.assertFalse((self.root.root / "progress_25C.json").exists())
        self.assertFalse((self.root.root / "plan_summary.csv").exists())
        rows = self.root.of("TSMC_2").load("25")["rows"]
        self.assertEqual(rows[self.rows[1].key]["status"], "건너뜀")  # 옮긴 기록 유지
        self.assertEqual(rows[self.rows[0].key]["folder"], f"VIH/{d.name}")
        self.assertTrue((d / "result.orig.json").exists())
        self.assertTrue(list(self.base.glob("results_before_cleanup_*.csv")))
        self.assertIn("VOH (출력 High)", (self.base / "results.csv").read_text(encoding="utf-8-sig"))
        self.out = io.StringIO()
        self.assertEqual(self.run_cleanup(), 0)
        self.assertIn("빠진 값 채우기 0개 기록", self.out.getvalue())
        self.assertIn("진행 기록 연결 0개", self.out.getvalue())

    def test_refuses_while_cli_runs_and_dry_run_writes_nothing_else(self):
        self.h.vih("2", "2026-10-10T10:00:00", name="TSMC_1")
        self.assertEqual(self.run_cleanup("--dry-run"), 0)
        self.assertFalse(self.root.of("TSMC_1").progress_path("25").exists())
        self.assertTrue((self.root.root / "samples.csv").exists())
        from scopetech.plan_state import StateWriter
        w = StateWriter(self.root.root / "state.json", interval=10)
        w.start(temp="25")
        try:
            self.assertEqual(self.run_cleanup(), 1)
        finally:
            w.stop()


class RunnerSampleTest(unittest.TestCase):
    def test_runner_uses_sample_folder(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            cfg = Config()
            cfg.cp.set("capture", "enabled", "no")
            t = SimTransport()
            s = MSO5B(t, settle_s=0)
            s.connect()
            bench = Bench(s, cfg, base, echo=lambda _: None)
            rows, layout = load_plan(ROOT / "test_plan.csv"), load_layout(ROOT / "test_plan_summary.csv")
            out = []
            it = iter(["25", "tsmc-2", "", "q"])
            PlanRunner(bench, rows, layout, ask=lambda _: next(it), echo=out.append).run()
            root = PlanStore(base)
            self.assertEqual(root.samples(), ["TSMC_2"])
            self.assertIn("샘플 TSMC_2 (fab TSMC, 샘플 2)", "\n".join(out))
            self.assertTrue((base / "plan" / "TSMC_2" / "plan_summary_TSMC_2.csv").exists())
            self.assertTrue((base / "plan" / "plan_summary_all.csv").exists())
            self.assertEqual(Samples(root.root).rows["tsmc-2"], ("TSMC", "2"))


if __name__ == "__main__":
    unittest.main()

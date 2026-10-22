import csv
import json
import tempfile
import unittest
from pathlib import Path

import plan_summary
from scopetech.analysis import Waveform, power_on_levels
from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.plan import (DONE, PlanRow, PlanRunner, PlanStore, collect_values, load_layout, load_plan, norm_temp,
                            ordered_temps, write_summary)
from scopetech.sim import SimTransport
from scopetech.workflow import Bench

ROOT = Path(__file__).resolve().parents[1]


def por_waves(low_ms=2.0, n=1000, dt=10e-6, vol=0.05, voh=3.25, rise_at_ms=1.0, noise=0.0):
    t = [i * dt for i in range(n)]
    vdd = [0.0 if ti < rise_at_ms * 1e-3 else 3.3 for ti in t]
    out = [0.0 if ti < rise_at_ms * 1e-3 else (vol if ti < (rise_at_ms + low_ms) * 1e-3 else voh) for ti in t]
    if noise:
        out = [v + (noise if i % 2 else -noise) for i, v in enumerate(out)]
    return Waveform("CH1", vdd, dt), Waveform("CH2", out, dt)


class PowerOnTest(unittest.TestCase):
    def test_vol_during_por_delay_then_voh(self):
        r = power_on_levels(*por_waves(noise=0.002))
        self.assertAlmostEqual(r.vol, 0.05, places=3)
        self.assertAlmostEqual(r.voh, 3.25, places=3)
        self.assertAlmostEqual(r.delay_s, 2e-3, delta=2e-5)
        self.assertAlmostEqual(r.vdd, 3.3, places=6)
        self.assertEqual(r.warnings, [])

    def test_output_never_rises_or_already_high(self):
        r = power_on_levels(*por_waves(low_ms=100))
        self.assertAlmostEqual(r.vol, 0.05, places=6)
        self.assertIsNone(r.voh)
        self.assertTrue(any("High 로 바뀌지" in w for w in r.warnings))
        r = power_on_levels(*por_waves(low_ms=0))
        self.assertIsNone(r.vol)
        self.assertAlmostEqual(r.voh, 3.25, places=6)
        self.assertTrue(r.warnings)

    def test_no_power_edge(self):
        flat = Waveform("CH1", [3.3] * 100, 1e-5)
        r = power_on_levels(flat, Waveform("CH2", [0.0] * 100, 1e-5))
        self.assertIsNone(r.vol)
        self.assertTrue(r.warnings)


class PlanFileTest(unittest.TestCase):
    def test_shipped_plan_and_summary_order(self):
        rows = load_plan(ROOT / "test_plan.csv")
        self.assertEqual([(r.kind, r.in_pin, r.out_pin, r.out_type) for r in rows[:8]], [
            ("VI", "2", "12", "PP"), ("VI", "20", "4", "PP"), ("VI", "3", "16", "OD"), ("VI", "6", "16", "OD"),
            ("VI", "5", "19", "OD"), ("VI", "15", "18", "OD"), ("AI", "10", "7", "PP"), ("POR", "", "14", "PP")])
        self.assertEqual([(r.kind, r.out_pin, r.channels) for r in rows[8:]],
                         [("IOH", p, [8, 2]) for p in ("12", "4", "14", "7")]
                         + [("RPU", p, [2]) for p in ("5", "6", "15")] + [("RPD", p, [2]) for p in ("2", "3", "10", "20")])
        layout = [(p, i) for _, p, i, _ in load_layout(ROOT / "test_plan_summary.csv")]
        expect = [(p, i) for p in ("2", "20", "3", "6", "5", "15") for i in ("VIH", "VIL")]
        expect += [("10", "VIH"), ("10", "VIL")]
        expect += [(p, i) for p in ("12", "4", "14", "7") for i in ("VOH", "VOL")]
        expect += [(p, "VOL") for p in ("16", "19", "18")]
        expect += [(p, "IOH") for p in ("12", "4", "14", "7")]
        expect += [(p, "Rpu") for p in ("5", "6", "15")] + [(p, "Rpd") for p in ("2", "3", "10", "20")]
        self.assertEqual(layout, expect)

    def test_bad_plan_rows_are_reported(self):
        cases = ["1,XX,2,12,PP,1 2,", "1,VI,2,12,PP,1,", "1,VI,,12,PP,1 2,", "1,IOH,X,X,X,8 2,", "1,VI,2,12,PP,1 9,",
                 "1,VI,2,12,ZZ,1 2,", "1,RPD,X,2,X,2 2,"]
        with tempfile.TemporaryDirectory() as d:
            for line in cases:
                p = Path(d) / "p.csv"
                p.write_text("순서,항목,입력핀,출력핀,출력타입,채널,준비\n" + line + "\n", encoding="utf-8")
                with self.assertRaises(ValueError, msg=line):
                    load_plan(p)

    def test_temps(self):
        self.assertEqual([norm_temp(x) for x in ("25.0", "+25", "-40", "12.5")], ["25", "25", "-40", "12.5"])
        with tempfile.TemporaryDirectory() as d:
            store = PlanStore(Path(d))
            for t in ("-40", "25", "150"):
                store.save(t, {"rows": {}})
            self.assertEqual(ordered_temps(store, ["25", "85", "-40"]), ["25", "85", "-40", "150"])


class PlanSummaryTest(unittest.TestCase):
    def test_worst_of_two_sources_and_units(self):
        rows = [PlanRow("3", "VI", "3", "16", "OD", [1, 2]), PlanRow("4", "VI", "6", "16", "OD", [1, 2]),
                PlanRow("9", "IOH", "", "12", "PP", [8, 2]), PlanRow("16", "RPD", "", "2", "PP", [2])]
        results = {
            "3": {"metrics": [{"key": "VIH", "worst": 1.9}, {"key": "VIL", "worst": 1.4},
                              {"key": "VOL", "worst": 0.05, "output": True}, {"key": "VOH", "worst": 3.0, "output": True}]},
            "4": {"metrics": [{"key": "VOL", "worst": 0.08, "output": True}, {"key": "HYS", "worst": 0.5}]},
            "9": {"calc": {"value": -0.0042, "unit": "A"}},
            "16": {"calc": {"value": 987654.0, "unit": "ohm"}},
        }
        with tempfile.TemporaryDirectory() as d:
            store = PlanStore(Path(d))
            prog = {}
            for r in rows:
                folder = f"X/{r.no}"
                (Path(d) / folder).mkdir(parents=True)
                (Path(d) / folder / "result.json").write_text(json.dumps(results[r.no]), encoding="utf-8")
                prog[r.key] = {"status": DONE, "folder": folder}
            store.save("25", {"rows": prog})
            vals, src = collect_values(rows, store, "25")
            self.assertEqual(vals[("16", "VOL")], 0.08)  # 두 측정 중 높은 VOL
            self.assertEqual(src[("16", "VOL")], ["3", "4"])
            self.assertEqual(vals[("3", "VIH")], 1.9)
            layout = [("VO_OD", "16", "VOL", "V"), ("IOH", "12", "IOH", "mA"), ("Rpd", "2", "Rpd", "kΩ"),
                      ("VI", "99", "VIH", "V")]
            out = write_summary(rows, layout, store, ["25", "85"], Path(d) / "s.csv")
            with out.open(encoding="utf-8-sig", newline="") as f:
                table = list(csv.reader(f))
            self.assertEqual(table, [["구분", "핀", "항목", "단위", "25°C", "85°C"],
                                     ["VO_OD", "16", "VOL", "V", "0.0800", ""],
                                     ["IOH", "12", "IOH", "mA", "-4.200", ""],
                                     ["Rpd", "2", "Rpd", "kΩ", "987.7", ""],
                                     ["VI", "99", "VIH", "V", "", ""]])


class PlanRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        cfg = Config()
        cfg.cp.set("capture", "enabled", "no")
        self.t = SimTransport()
        self.t.sim.on[8] = True
        s = MSO5B(self.t, settle_s=0)
        s.connect()
        self.bench = Bench(s, cfg, self.base, echo=lambda _: None)
        self.rows = load_plan(ROOT / "test_plan.csv")
        self.layout = load_layout(ROOT / "test_plan_summary.csv")

    def run_plan(self, answers):
        it = iter(answers)
        out = []

        def ask(prompt):
            out.append(prompt)
            return next(it)
        code = PlanRunner(self.bench, self.rows, self.layout, ask=ask, echo=out.append).run()
        return code, "\n".join(out)

    def progress(self, temp="25"):
        return json.loads((self.base / "plan" / f"progress_{temp}C.json").read_text(encoding="utf-8"))["rows"]

    def test_full_run_writes_summary_and_progress(self):
        code, out = self.run_plan(["25", "T"] + [""] * 19 + [""])  # 마지막 Enter: 끝내기
        self.assertEqual(code, 0)
        self.assertTrue(all(e["status"] == DONE for e in self.progress().values()))
        self.assertIn("Enter: 끝내기 / g: 번호로 이동해 다시 측정", out)
        with (self.base / "plan" / "plan_summary.csv").open(encoding="utf-8-sig", newline="") as f:
            table = list(csv.reader(f))
        self.assertEqual(table[0], ["구분", "핀", "항목", "단위", "25°C", "85°C", "-40°C"])
        self.assertEqual(len(table), 1 + len(self.layout))
        filled = {(r[1], r[2]): r[4] for r in table[1:]}
        for key in (("2", "VIH"), ("10", "VIL"), ("16", "VOL"), ("12", "IOH"), ("5", "Rpu"), ("20", "Rpd")):
            self.assertNotEqual(filled[key], "", key)
        self.assertTrue((self.base / "plan" / "plan_progress_25C.csv").exists())
        self.assertIn("[1/19] VI  입력 2 -> 출력 12  PP", out)
        self.assertIn("[IOH/IOL]", out)

    def test_pause_resume_skip_and_back(self):
        code, out = self.run_plan(["25", "T", "", "k", "", "q"])  # 1 측정, 2 건너뜀, 3 측정, 멈춤
        self.assertEqual(code, 0)
        st = self.progress()
        self.assertEqual([st.get(r.key, {}).get("status") for r in self.rows[:4]], [DONE, "건너뜀", DONE, None])
        code, out = self.run_plan(["", "", "p", "q"])  # 기본 온도 25, 이어서: 4번부터, p 로 3번
        first = [line for line in out.splitlines() if line[:1] == "[" and line[1:2].isdigit()]
        self.assertTrue(first[0].startswith("[4/19]"), first)
        self.assertTrue(first[1].startswith("[3/19]"), first)
        self.assertIn("지난 결과(완료)", out)
        code, out = self.run_plan(["", "", "g", "1", "k", "q"])  # 완료된 1번에서 k: 결과 유지
        self.assertEqual(self.progress()[self.rows[0].key]["status"], DONE)

    def test_failure_is_recorded_with_hint_and_retry(self):
        self.t.sim.on[8] = False
        code, out = self.run_plan(["25", "T", "g", "9", "", "q"])
        self.assertEqual(code, 0)
        e = self.progress()[self.rows[8].key]
        self.assertEqual(e["status"], "실패")
        self.assertIn("CH8", e["error"])
        self.assertIn("켜 주세요", e["error"])

    def test_ioh_bias_check_and_skipped_revisit(self):
        answers = ["25", "T", "g", "9", ""] + ["k"] * 18 + ["", "q"]
        code, out = self.run_plan(answers)
        e = self.progress()[self.rows[8].key]
        self.assertEqual(e["status"], DONE)
        self.assertTrue(any("2.4" in w for w in e["warnings"]))  # 시뮬레이터 CH2 는 1.7 V 근처
        self.assertIn("건너뛴 항목 측정", out)
        self.assertNotIn("지난 결과(남음)", out)
        self.assertEqual(self.progress()[self.rows[0].key]["status"], "건너뜀")  # 측정 전에 끝내면 건너뜀 기록 유지

    def test_manual_rows_take_a_fresh_acquisition(self):
        self.run_plan(["25", "T", "g", "9", "", "q"])
        log = self.t.sim.log
        start = max(i for i, line in enumerate(log) if line.upper().startswith("*CLS"))  # 9번 항목의 상태 정리
        self.assertTrue(any(line.upper().startswith("ACQUIRE:STOPAFTER SEQ") for line in log[start:]), log[start:])
        self.assertEqual(self.progress()[self.rows[8].key]["status"], DONE)

    def test_failed_remeasure_keeps_result_and_redo_after_all_done(self):
        self.run_plan(["25", "T"] + [""] * 19 + [""])
        before = self.progress()[self.rows[8].key]
        self.t.sim.on[8] = False
        code, out = self.run_plan(["25", "", "g", "9", "", "q"])  # 모두 완료 -> g 9 -> 다시 재다 실패
        self.assertEqual(code, 0)
        self.assertIn("앞의 측정 결과를 그대로 둡니다", out)
        self.assertEqual(self.progress()[self.rows[8].key], before)
        self.t.sim.on[8] = True
        code, out = self.run_plan(["25", "", "g", "1", "", ""])  # 1번 다시 측정 후 Enter 로 끝내기
        self.assertEqual(code, 0)
        self.assertNotEqual(self.progress()[self.rows[0].key]["folder"], before["folder"])

    def test_measurement_without_value_is_not_done(self):
        self.t.sim.on[8] = True
        self.bench.cfg.cp.set("manual", "vdd_v", "0.5")  # 시뮬레이터 CH2 ≈ 1.7 V ≥ VDD -> Rpd 값 없음
        code, out = self.run_plan(["25", "T", "g", "16", "", "q"])
        e = self.progress()[self.rows[15].key]
        self.assertEqual(e["status"], "실패")
        self.assertIn("요약표에 넣을 값이 없음", e["error"])
        self.assertTrue(e["folder"])

    def test_rebuild_cli(self):
        self.run_plan(["85", "T"] + [""] * 19 + [""])
        cfg_path = self.base / "c.ini"
        cfg_path.write_text("[plan]\nsummary = no_such_layout.csv\n", encoding="utf-8")
        self.assertEqual(plan_summary.main(["--config", str(cfg_path), "--dir", str(self.base),
                                            "--plan", str(ROOT / "test_plan.csv")]), 1)  # 요약 순서 파일 없음
        cfg_path.write_text(f"[plan]\nsummary = {ROOT / 'test_plan_summary.csv'}\n", encoding="utf-8")
        self.assertEqual(plan_summary.main(["--config", str(cfg_path), "--dir", str(self.base),
                                            "--plan", str(ROOT / "test_plan.csv")]), 0)
        with (self.base / "plan" / "plan_summary.csv").open(encoding="utf-8-sig", newline="") as f:
            table = list(csv.reader(f))
        self.assertEqual(table[0][4:], ["25°C", "85°C", "-40°C"])
        self.assertTrue(table[1][5])  # 85°C 값

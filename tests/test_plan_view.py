import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scopetech import plan_view
from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.plan import DONE, PlanRunner, load_layout, load_plan
from scopetech.plan_state import StateWriter, read_overrides
from scopetech.plan_view import PlanData, gui_alive, launch_gui, mark_gui
from scopetech.sim import SimTransport
from scopetech.workflow import Bench

ROOT = Path(__file__).resolve().parents[1]


class ViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """시뮬레이터로 25°C 계획을 한 번 진행해 둔 결과 폴더."""
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cfg = Config()
        cfg.cp.set("capture", "enabled", "no")
        t = SimTransport()
        t.sim.on[8] = True
        s = MSO5B(t, settle_s=0)
        s.connect()
        bench = Bench(s, cfg, cls.base, echo=lambda _: None)
        rows, layout = load_plan(ROOT / "test_plan.csv"), load_layout(ROOT / "test_plan_summary.csv")
        answers = iter(["25", "T"] + [""] * 8 + ["k"] + [""] * 10 + ["q"])
        PlanRunner(bench, rows, layout, ask=lambda _: next(answers), echo=lambda _: None).run()
        folder = cls.base / json.loads((cls.base / "plan" / "T" / "progress_25C.json").read_text(encoding="utf-8"))["rows"][rows[0].key]["folder"]
        (folder / "meas.png").write_bytes(b"png")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.data = PlanData(self.base, here=ROOT)
        self.over = self.base / "plan" / "overrides.ini"
        if self.over.exists():
            self.over.unlink()

    def test_progress_rows_and_counts(self):
        rows = self.data.progress_rows("25")
        self.assertEqual(len(rows), 19)
        self.assertEqual(rows[0]["pins"], "2 -> 12")
        self.assertEqual(rows[0]["status"], DONE)
        self.assertIn("VIH", rows[0]["result"])
        self.assertEqual(rows[8]["status"], "건너뜀")
        self.assertIn("완료 18 / 건너뜀 1", self.data.counts(rows))
        self.assertFalse(any(r["current"] for r in rows))  # CLI 가 끝났으면 강조 없음
        self.assertEqual(self.data.cli_text()[:4], "CLI:")
        self.assertIn("진행 중 아님", self.data.cli_text())
        self.assertEqual(self.data.temps(), ["25", "85", "-40"])
        self.assertEqual(self.data.progress_rows("85")[0]["status"], "남음")

    def test_current_row_follows_cli_state(self):
        w = StateWriter(self.data.state_path, interval=10)
        w.start(temp="25", name="T", sample="T")
        try:
            w.update(current=self.data.rows[3].key, measuring=True)
            rows = self.data.progress_rows("25")
            self.assertTrue(rows[3]["current"] and rows[3]["measuring"])
            self.assertFalse(self.data.progress_rows("85")[3]["current"])  # 다른 온도
            self.assertIn("측정 중", self.data.cli_text())
            sig = self.data.signature("25")
            w.update(measuring=False)
            self.assertNotEqual(self.data.signature("25"), sig)
            sig = self.data.signature("25")
            w.update()  # 살아 있다는 표시만 바뀜 -> 다시 그리지 않음
            self.assertEqual(self.data.signature("25"), sig)
        finally:
            w.stop()

    def test_detail_with_image_and_calc(self):
        d = self.data.detail("25", self.data.rows[0].key)
        self.assertEqual(d["image"].name, "meas.png")
        self.assertTrue(d["folder"].is_dir())
        text = "\n".join(d["lines"])
        self.assertIn("입력 VT+", text)
        self.assertIn("출력 VOH", text)
        self.assertIn("측정 설정: 화면 캡처 끔 (config.ini 와 다름)", text)  # 시험에서 캡처를 끔
        d = self.data.detail("25", self.data.rows[15].key)  # RPD
        self.assertIn("Rpd = ", "\n".join(d["lines"]))
        self.assertIn("근거:", "\n".join(d["lines"]))
        self.assertIsNone(d["image"])
        d = self.data.detail("25", self.data.rows[8].key)  # 건너뜀
        self.assertIsNone(d["folder"])

    def test_summary_matches_csv_and_blank_flags(self):
        head, rows, blank = self.data.summary("25")
        self.assertEqual(head, ["구분", "핀", "항목", "단위", "25°C", "85°C", "-40°C"])
        self.assertEqual(self.data.sample, "T")
        csv_rows = (self.base / "plan" / "T" / "plan_summary_T.csv").read_text(encoding="utf-8-sig").splitlines()[1:]
        self.assertEqual([",".join(r) for r in rows], csv_rows)
        by = {(r[1], r[2]): b for r, b in zip(rows, blank)}
        self.assertTrue(by[("12", "IOH")])    # 9번 IOH 12 건너뜀
        self.assertFalse(by[("2", "VIH")])
        self.assertTrue(all(self.data.summary("85")[2]))

    def test_settings_save_validate_and_reset(self):
        form = {f["id"]: f for f in self.data.settings_form()}
        self.assertEqual(form["manual.vdd_v"]["value"], "3.3")
        self.assertIsNone(form["manual.vdd_v"]["override"])
        values = {k: f["value"] for k, f in form.items()}
        values.update({"manual.vdd_v": "3.25", "session.name": "N"})
        self.assertEqual(self.data.save_settings(values), [])
        self.assertEqual(read_overrides(self.over), {"manual.vdd_v": "3.25", "session.name": "N"})  # 같은 값은 안 적음
        errs = self.data.save_settings({**values, "manual.vdd_v": "x", "measure.out_cross": "2"})
        self.assertEqual(len(errs), 2)
        self.assertEqual(read_overrides(self.over)["manual.vdd_v"], "3.25")  # 오류면 저장 안 함
        self.data.reset_settings()
        self.assertEqual(read_overrides(self.over), {"session.name": "N"})

    def test_import_preview_and_apply_blocked_while_cli_runs(self):
        picks, unmatched, cfg = self.data.import_preview()
        self.assertEqual({p.state for p in picks}, {"같음"})  # 계획 모드 기록이 이미 연결됨
        w = StateWriter(self.data.state_path, interval=10)
        w.start(temp="25")
        try:
            with self.assertRaises(RuntimeError):
                self.data.import_apply(picks, unmatched)
        finally:
            w.stop()


class LaunchTest(unittest.TestCase):
    def test_launch_checks_tk_and_running_gui(self):
        with tempfile.TemporaryDirectory() as d:
            plan_dir = Path(d) / "plan"
            out = []
            with mock.patch.object(plan_view.importlib.util, "find_spec", return_value=None):
                self.assertFalse(launch_gui(ROOT / "plan_gui.py", ROOT / "config.ini", Path(d), plan_dir, out.append))
            self.assertIn("tkinter 없음", out[-1])
            with mock.patch.object(plan_view.importlib.util, "find_spec", return_value=object()), \
                    mock.patch.object(plan_view.subprocess, "Popen") as popen:
                self.assertTrue(launch_gui(ROOT / "plan_gui.py", ROOT / "config.ini", Path(d), plan_dir, out.append))
                cmd = popen.call_args[0][0]
                self.assertEqual(cmd[1:], [str(ROOT / "plan_gui.py"), "--config", str(ROOT / "config.ini"), "--dir", d])
                mark_gui(plan_dir)
                self.assertTrue(gui_alive(plan_dir))
                self.assertFalse(launch_gui(ROOT / "plan_gui.py", ROOT / "config.ini", Path(d), plan_dir, out.append))
                self.assertEqual(popen.call_count, 1)  # 이미 열린 창이 있으면 또 띄우지 않음
            self.assertFalse(gui_alive(plan_dir, now=time.time() + 60))  # 창이 비정상 종료돼 표시가 멈춤
            mark_gui(plan_dir, closing=True)
            self.assertFalse(gui_alive(plan_dir))
            self.assertFalse(gui_alive(Path(d) / "none"))


if __name__ == "__main__":
    unittest.main()

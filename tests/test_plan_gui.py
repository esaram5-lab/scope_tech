"""결과·설정 창(tkinter) 동작. tkinter 와 화면(DISPLAY 또는 Windows)이 있을 때만 실행한다.

리눅스에서 화면 없이: xvfb-run -a python3 -m unittest tests.test_plan_gui
"""
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.plan import PlanRunner, load_layout, load_plan
from scopetech.plan_state import StateWriter, read_overrides
from scopetech.sim import SimTransport
from scopetech.workflow import Bench

ROOT = Path(__file__).resolve().parents[1]
HAS_TK = importlib.util.find_spec("tkinter") is not None
HAS_SCREEN = sys.platform == "win32" or bool(os.environ.get("DISPLAY"))


@unittest.skipUnless(HAS_TK and HAS_SCREEN, "tkinter 또는 화면 없음")
class GuiTest(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from scopetech import plan_gui
        from scopetech.plan_view import PlanData
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        cfg = Config()
        cfg.cp.set("capture", "enabled", "no")
        t = SimTransport()
        t.sim.on[8] = True
        s = MSO5B(t, settle_s=0)
        s.connect()
        bench = Bench(s, cfg, self.base, echo=lambda _: None)
        rows, layout = load_plan(ROOT / "test_plan.csv"), load_layout(ROOT / "test_plan_summary.csv")
        answers = iter(["25", "T", "", "", "q"])
        PlanRunner(bench, rows, layout, ask=lambda _: next(answers), echo=lambda _: None).run()
        self.data = PlanData(self.base, here=ROOT)
        self.root = tk.Tk()
        self.addCleanup(self._close)
        self.win = plan_gui.PlanWindow(self.root, self.data)
        self.pump()

    def _close(self):
        try:
            self.win.close()
        except Exception:
            pass

    def pump(self, secs=0.1):
        end = time.time() + secs
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def test_progress_detail_and_cli_follow(self):
        w = self.win
        self.assertEqual(w.temp.get(), "25")
        items = w.ptree.get_children()
        self.assertEqual(len(items), 19)
        self.assertIn("완료 2", w.counts.cget("text"))
        w.ptree.selection_set(items[0])
        self.pump()
        text = w.detail.get("1.0", "end")
        self.assertIn("VIH", text)
        self.assertEqual(str(w.open_folder.cget("state")), "normal")
        st = StateWriter(self.data.state_path, interval=10)
        st.start(temp="25", name="T")
        try:
            st.update(current=self.data.rows[2].key)
            w.refresh()
            self.assertIn("입력 대기", w.cli.cget("text"))
            self.assertTrue(w.ptree.item(items[2], "values")[0].startswith("▶"))
            self.assertEqual(w.ptree.selection(), (items[2],))  # 따라가기
        finally:
            st.stop()
        w.refresh()
        self.assertIn("진행 중 아님", w.cli.cget("text"))

    def test_summary_tab(self):
        rows = self.win.stree.get_children()
        self.assertEqual(len(rows), len(self.data.layout))
        first = self.win.stree.item(rows[0], "values")
        self.assertEqual(first[:3], ("VI", "2", "VIH"))
        self.assertTrue(first[4])
        self.assertIn("채운 칸", self.win.sum_info.cget("text"))

    def test_settings_save_and_error(self):
        w = self.win
        w.vars["manual.vdd_v"].set("3.2")
        self.assertTrue(w.dirty)
        w._save_settings()
        self.assertEqual(read_overrides(self.data.overrides_path)["manual.vdd_v"], "3.2")
        self.assertFalse(w.dirty)
        w.vars["measure.out_cross"].set("7")
        with mock.patch("scopetech.plan_gui.messagebox.showerror") as err:
            w._save_settings()
        self.assertTrue(err.called)
        self.assertNotIn("measure.out_cross", read_overrides(self.data.overrides_path))
        with mock.patch("scopetech.plan_gui.messagebox.askyesno", return_value=True):
            w._reset_settings()
        self.assertNotIn("manual.vdd_v", read_overrides(self.data.overrides_path))
        self.assertEqual(w.vars["manual.vdd_v"].get(), "3.3")

    def test_import_tab_preview_toggle_and_guard(self):
        w = self.win
        w._import_preview()
        for _ in range(200):
            self.pump(0.05)
            if w._job is None:
                break
        self.assertTrue(w.picks)
        self.assertEqual(str(w.apply_btn.cget("state")), "disabled")  # 모두 이미 연결됨
        w.itree.selection_set(w.itree.get_children()[0])
        self.pump()
        self.assertTrue(w.ctree.get_children())
        st = StateWriter(self.data.state_path, interval=10)
        st.start(temp="25")
        try:
            w.refresh()
            self.assertIn("진행 중", w.imp_info.cget("text"))
        finally:
            st.stop()


if __name__ == "__main__":
    unittest.main()

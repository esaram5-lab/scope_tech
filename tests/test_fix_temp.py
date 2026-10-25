import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import fix_temp as cli
from scopetech import fix_temp as fx
from scopetech import gallery
from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.plan import PlanRunner, PlanStore, load_layout, load_plan
from scopetech.plan_state import StateWriter
from scopetech.sim import SimTransport
from scopetech.workflow import Bench

ROOT = Path(__file__).resolve().parents[1]


class FixTempTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.rows = load_plan(ROOT / "test_plan.csv")
        self.layout = load_layout(ROOT / "test_plan_summary.csv")
        self.root = PlanStore(self.base)

    def run_plan(self, temp, n, name="TSMC_1"):
        cfg = Config()
        cfg.cp.set("capture", "enabled", "no")
        s = MSO5B(SimTransport(), settle_s=0)
        s.connect()
        b = Bench(s, cfg, self.base, echo=lambda _: None)
        it = iter([temp, name] + [""] * n + ["q"])
        PlanRunner(b, self.rows, self.layout, ask=lambda _: next(it), echo=lambda _: None).run()

    def progress(self, temp, sid="TSMC_1"):
        return self.root.of(sid).load(temp).get("rows", {})

    def set_time(self, rel, ts):
        p = self.base / rel / "result.json"
        r = json.loads(p.read_text(encoding="utf-8"))
        r["timestamp"] = ts
        p.write_text(json.dumps(r), encoding="utf-8")

    def tool(self, answers, *args):
        it = iter(answers)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--dir", str(self.base), *args], ask=lambda _: next(it))
        return code, out.getvalue()

    def test_moves_records_keeps_existing_right_temp_rows_and_photos(self):
        self.run_plan("85", 3)  # 1~3번을 85°C 로 잘못 넣음
        self.run_plan("25", 2)  # 1~2번은 25°C 로 이미 제대로 잼
        wrong = {k: e["folder"] for k, e in self.progress("85").items()}
        right_before = {k: e["folder"] for k, e in self.progress("25").items()}
        # 사진 모음: 잘못된 온도 이름의 복사본
        folder = self.base / wrong[self.rows[0].key]
        (folder / "meas.png").write_bytes(b"png-1")
        gallery.collect(folder / "meas.png", self.base / "result")
        self.assertTrue((self.base / "result" / "TSMC_1_85C_PIN2_VIL-VIH.png").exists())

        code, out = self.tool(["1", "85", "25", "a", "n", "y"])
        self.assertEqual(code, 0)
        self.assertIn("이미 완료된 같은 항목 2개", out)
        self.assertFalse(self.root.of("TSMC_1").progress_path("85").exists())  # 비었으니 지움
        now = self.progress("25")
        self.assertEqual(now[self.rows[0].key]["folder"], right_before[self.rows[0].key])  # n: 25°C 기록 유지
        self.assertEqual(now[self.rows[2].key]["folder"], wrong[self.rows[2].key])  # 3번은 옮김
        r = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(r["temp"], "25")
        self.assertEqual(r["temp_fixed"][0]["from"], "85")
        self.assertEqual(json.loads((folder / "result.orig.json").read_text(encoding="utf-8"))["temp"], "85")
        self.assertFalse((self.base / "result" / "TSMC_1_85C_PIN2_VIL-VIH.png").exists())
        self.assertTrue(list((self.base / "result").glob("TSMC_1_25C_PIN2_VIL-VIH*.png")))
        self.assertTrue(list(self.base.glob("results_before_fix_*.csv")))  # 이전 results.csv 보관
        self.assertIn('"25"', (folder / "result.json").read_text(encoding="utf-8"))
        cells = {(r.split(",")[1], r.split(",")[2]): r.split(",")[4:7]
                 for r in (self.root.root / "plan_summary_all.csv").read_text(encoding="utf-8-sig").splitlines()[1:]}
        self.assertTrue(cells[("16", "VOL")][0])   # 3번(3->16) 이 25°C 칸으로
        self.assertEqual(cells[("16", "VOL")][1], "")  # 85°C 칸은 비었음
        self.assertTrue(list(self.root.dir.glob("fix_temp_*.txt")))

    def test_replace_and_only_chosen_session(self):
        self.run_plan("25", 1)
        self.run_plan("85", 2)  # 1~2번: 85°C 로 잘못
        rows85 = self.progress("85")
        k1, k2 = self.rows[0].key, self.rows[1].key
        self.set_time(rows85[k1]["folder"], "2026-10-24T09:00:00")  # 묶음 1 (잘못 잰 것)
        self.set_time(rows85[k2]["folder"], "2026-10-24T15:00:00")  # 묶음 2 (85°C 로 제대로 잰 것)
        code, out = self.tool(["1", "85", "25", "1", "y", "y"])
        self.assertEqual(code, 0)
        self.assertIn("1) 2026-10-24 09:00:00", out)
        self.assertEqual(self.progress("25")[k1]["folder"], rows85[k1]["folder"])  # y: 고친 기록으로 바꿈
        self.assertEqual(list(self.progress("85")), [k2])  # 고르지 않은 묶음은 85°C 그대로
        r2 = json.loads((self.base / rows85[k2]["folder"] / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(r2["temp"], "85")

    def test_args_mode_and_guards(self):
        self.run_plan("85", 1)
        code, out = self.tool([], "--sample", "TSMC_1", "--wrong", "85", "--right", "-40", "--apply")
        self.assertEqual(code, 0)
        self.assertIn(self.rows[0].key, self.progress("-40"))
        code, out = self.tool([], "--sample", "NONE", "--wrong", "85", "--right", "25", "--apply")
        self.assertEqual(code, 1)
        code, out = self.tool(["1", "q"])
        self.assertEqual(code, 0)
        self.assertIn("아무것도 바꾸지 않았습니다", out)
        w = StateWriter(self.root.root / "state.json", interval=10)
        w.start(temp="25")
        try:
            code, out = self.tool([])
        finally:
            w.stop()
        self.assertEqual(code, 1)
        self.assertIn("실행 중", out)

    def test_sessions_split_by_gap(self):
        recs = [fx.Rec(Path("."), str(i), {}, "n", "85", t) for i, t in enumerate(
            ["2026-10-24T09:00:00", "2026-10-24T09:20:00", "2026-10-24T10:00:00", "2026-10-25T08:00:00"])]
        self.assertEqual([len(g) for g in fx.sessions(recs)], [2, 1, 1])


if __name__ == "__main__":
    unittest.main()

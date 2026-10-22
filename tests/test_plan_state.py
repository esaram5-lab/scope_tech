import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scopetech import plan_state
from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.plan import DONE, PlanRunner, load_layout, load_plan
from scopetech.plan_state import (BY_ID, PlanSettings, SettingsView, StateWriter, cli_running, read_overrides, read_state,
                                  replace_retry, validate, write_overrides)
from scopetech.sim import SimTransport
from scopetech.workflow import Bench

ROOT = Path(__file__).resolve().parents[1]


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "plan" / "overrides.ini"

    def test_validate(self):
        self.assertEqual(validate(BY_ID["manual.vdd_v"], " 3.25 "), "3.25")
        self.assertEqual(validate(BY_ID["manual.r_ext_ohm"], "1.02e6"), "1020000")
        self.assertEqual(validate(BY_ID["measure.vin_avg_points"], "20.0"), "20")
        self.assertEqual(validate(BY_ID["capture.enabled"], "OFF"), "no")
        self.assertEqual(validate(BY_ID["session.name"], " INV 3V3 "), "INV 3V3")
        for sid, bad in (("manual.vdd_v", "abc"), ("manual.vdd_v", "nan"), ("manual.vdd_v", "100"),
                         ("measure.out_cross", "1.5"), ("measure.vin_avg_points", "2.5"), ("session.name", " "),
                         ("manual.probe_r_ohm", "inf")):
            with self.assertRaises(ValueError, msg=(sid, bad)):
                validate(BY_ID[sid], bad)

    def test_overrides_round_trip_and_bad_values_ignored(self):
        write_overrides(self.path, {"manual.vdd_v": "3.25", "session.name": "A"})
        self.assertEqual(read_overrides(self.path), {"manual.vdd_v": "3.25", "session.name": "A"})
        self.path.write_text("[manual]\nvdd_v = 99\nr_ext_ohm = 2e6\n[x]\ny=1\n", encoding="utf-8")
        self.assertEqual(read_overrides(self.path), {"manual.r_ext_ohm": "2000000"})  # 범위 밖·모르는 항목 무시
        self.path.write_text("not an ini", encoding="utf-8")
        self.assertEqual(read_overrides(self.path), {})
        self.assertEqual(read_overrides(self.path.with_name("none.ini")), {})

    def test_apply_reports_changes_and_returns_to_config_value(self):
        cfg = Config()
        ps = PlanSettings(cfg, self.path)
        self.assertEqual(ps.apply(), [])
        write_overrides(self.path, {"manual.vdd_v": "3.25", "capture.enabled": "no"})
        changed = {s.id: (old, new) for s, old, new in ps.apply()}
        self.assertEqual(changed, {"manual.vdd_v": ("3.3", "3.25"), "capture.enabled": ("yes", "no")})
        self.assertEqual(cfg.getfloat("manual", "vdd_v"), 3.25)
        self.assertEqual(ps.apply(), [])
        self.path.unlink()
        changed = {s.id: (old, new) for s, old, new in ps.apply()}
        self.assertEqual(changed["manual.vdd_v"], ("3.25", "3.3"))
        ps.set("session.name", "B")
        self.assertEqual(read_overrides(self.path), {"session.name": "B"})
        self.assertNotIn("session.name", ps.snapshot())
        self.assertEqual(ps.snapshot()["manual.vdd_v"], "3.3")

    def test_settings_view_prefers_recorded_values(self):
        cfg = Config()
        v = SettingsView(cfg, {"manual.vdd_v": "3.0", "measure.vin_avg_points": "5"})
        self.assertEqual(v.getfloat("manual", "vdd_v"), 3.0)
        self.assertEqual(v.getint("measure", "vin_avg_points"), 5)
        self.assertEqual(v.getfloat("manual", "r_ext_ohm"), 1e6)
        self.assertEqual(v.channel_name(2), "CH2")  # 그 밖은 Config 그대로


class StateTest(unittest.TestCase):
    def test_writer_heartbeat_and_stale(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plan" / "state.json"
            w = StateWriter(path, interval=0.05)
            w.start(temp="25")
            first = read_state(path)["alive"]
            time.sleep(0.3)
            self.assertGreater(read_state(path)["alive"], first)  # 입력을 기다리는 동안에도 고침
            self.assertTrue(cli_running(read_state(path)))
            w.update(current="1|VI|2|12")
            self.assertEqual(read_state(path)["current"], "1|VI|2|12")
            w.stop()
            s = read_state(path)
            self.assertFalse(s["running"])
            self.assertFalse(cli_running(s))
            self.assertFalse(cli_running({"running": True, "alive": time.time() - 60}))  # 비정상 종료
            self.assertFalse(cli_running({"running": True, "alive": "x"}))

    def test_replace_retries_while_file_is_busy(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "a", Path(d) / "b"
            src.write_text("x")
            real = plan_state.os.replace
            calls = []

            def flaky(a, b):
                calls.append(1)
                if len(calls) < 3:
                    raise PermissionError("busy")
                real(a, b)
            with mock.patch.object(plan_state.os, "replace", flaky):
                replace_retry(src, dst, wait=0)
            self.assertEqual(dst.read_text(), "x")
            self.assertEqual(len(calls), 3)
            src.write_text("y")
            with mock.patch.object(plan_state.os, "replace", side_effect=PermissionError("busy")):
                with self.assertRaises(PermissionError):
                    replace_retry(src, dst, tries=2, wait=0)


class RunnerLinkTest(unittest.TestCase):
    """CLI(계획 진행) 쪽: overrides.ini 반영, 측정 설정 기록, state.json."""

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
        self.over = self.base / "plan" / "overrides.ini"
        self.state = self.base / "plan" / "state.json"

    def run_plan(self, answers, on_prompt=None):
        it = iter(answers)
        out = []

        def ask(prompt):
            out.append(prompt)
            if on_prompt:
                on_prompt(prompt)
            return next(it)
        code = PlanRunner(self.bench, self.rows, self.layout, ask=ask, echo=out.append).run()
        return code, "\n".join(out)

    def progress(self):
        return json.loads((self.base / "plan" / "progress_25C.json").read_text(encoding="utf-8"))["rows"]

    def test_overrides_applied_before_each_measurement_and_recorded(self):
        write_overrides(self.over, {"manual.vdd_v": "3.0"})
        seen = []

        def edit(prompt):  # 16번(RPD 2)을 잰 뒤 17번 앞에서 GUI 가 VDD·이름을 바꾼다
            if prompt.startswith("Enter: 측정") and len(seen) == 2:
                write_overrides(self.over, {"manual.vdd_v": "3.1", "session.name": "NEW"})
            if prompt.startswith("Enter: 측정"):
                seen.append(read_state(self.state))
        code, out = self.run_plan(["25", "T", "g", "16", "", "", "q"], edit)
        self.assertEqual(code, 0)
        self.assertIn("설정(overrides.ini): VDD 3.3 -> 3 V", out)
        self.assertIn("설정 변경 반영: VDD 3 -> 3.1 V", out)
        self.assertIn("설정 변경 반영: 시험 이름 T -> NEW", out)
        self.assertEqual(read_overrides(self.over)["session.name"], "NEW")
        rows = self.progress()
        r16 = json.loads((self.base / rows[self.rows[15].key]["folder"] / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(r16["plan_settings"]["manual.vdd_v"], "3")
        self.assertEqual(r16["calc"]["inputs"]["vdd"], 3.0)
        r17 = json.loads((self.base / rows[self.rows[16].key]["folder"] / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(r17["plan_settings"]["manual.vdd_v"], "3.1")
        self.assertEqual(r17["name"], "NEW")
        doc = json.loads((self.base / "plan" / "progress_25C.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["name"], "NEW")
        # state.json: 진행 중엔 현재 항목, 끝나면 running False
        self.assertTrue(seen[0]["running"])
        self.assertEqual(seen[0]["current"], self.rows[0].key)
        self.assertEqual(seen[1]["current"], self.rows[15].key)
        self.assertEqual(seen[2]["last"], self.rows[15].key)
        self.assertEqual(seen[2]["last_status"], DONE)
        self.assertFalse(read_state(self.state)["running"])

    def test_cli_name_is_written_for_gui_and_capture_toggle(self):
        write_overrides(self.over, {"session.name": "FROM_GUI", "capture.enabled": "no"})
        code, out = self.run_plan(["25", "", "q"])  # 기본 이름 = GUI 에서 정한 이름
        self.assertIn("시험 이름 [FROM_GUI]", out)
        self.assertEqual(read_overrides(self.over)["session.name"], "FROM_GUI")
        code, out = self.run_plan(["25", "CLI_NAME", "q"])
        self.assertEqual(read_overrides(self.over)["session.name"], "CLI_NAME")
        self.bench.explicit_options = {"name"}
        self.bench.name = "ARG"
        write_overrides(self.over, {"session.name": "GUI2"})
        code, out = self.run_plan(["25", "", "q"])
        self.assertIn("시험 이름 [ARG]", out)  # --name 이 우선

    def test_bad_overrides_file_does_not_stop_measuring(self):
        self.over.parent.mkdir(parents=True)
        self.over.write_text("[manual\nvdd_v = 3.0", encoding="utf-8")
        code, out = self.run_plan(["25", "T", "", "q"])
        self.assertEqual(code, 0)
        self.assertEqual(self.progress()[self.rows[0].key]["status"], DONE)

    def test_change_during_measurement_applies_to_next_one_only(self):
        write_overrides(self.over, {"manual.vdd_v": "3.0"})

        def echo(text):  # 16번을 재는 도중(상태 정리 직후) GUI 가 VDD 를 바꾼다
            if "스코프 상태 정리" in text and not changed:
                changed.append(1)
                write_overrides(self.over, {"manual.vdd_v": "3.1"})
        changed = []
        it = iter(["25", "T", "g", "16", "", "", "q"])
        PlanRunner(self.bench, self.rows, self.layout, ask=lambda _: next(it), echo=echo).run()
        rows = self.progress()
        r16 = json.loads((self.base / rows[self.rows[15].key]["folder"] / "result.json").read_text(encoding="utf-8"))
        r17 = json.loads((self.base / rows[self.rows[16].key]["folder"] / "result.json").read_text(encoding="utf-8"))
        self.assertEqual((r16["plan_settings"]["manual.vdd_v"], r16["calc"]["inputs"]["vdd"]), ("3", 3.0))
        self.assertEqual((r17["plan_settings"]["manual.vdd_v"], r17["calc"]["inputs"]["vdd"]), ("3.1", 3.1))

    def test_ctrl_c_marks_cli_stopped(self):
        def ask(prompt):
            if prompt.startswith("Enter: 측정"):
                self.assertTrue(cli_running(read_state(self.state)))
                raise KeyboardInterrupt
            return {"온": "25"}.get(prompt[:1], "T")
        PlanRunner(self.bench, self.rows, self.layout, ask=ask, echo=lambda _: None).run()
        self.assertFalse(cli_running(read_state(self.state)))

    def test_progress_saved_while_gui_reads_it(self):
        real = plan_state.os.replace
        calls = []

        def busy(a, b):  # Windows: GUI 가 읽는 순간 교체가 두 번 막힘
            calls.append(str(b))
            if str(b).endswith("progress_25C.json") and calls.count(str(b)) <= 2:
                raise PermissionError("busy")
            real(a, b)
        with mock.patch.object(plan_state.os, "replace", busy):
            code, out = self.run_plan(["25", "T", "", "q"])
        self.assertNotIn("진행 기록을 쓰지 못했습니다", out)
        self.assertEqual(self.progress()[self.rows[0].key]["status"], DONE)


if __name__ == "__main__":
    unittest.main()

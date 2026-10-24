import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import psu as psu_cli
from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.n6705c import ArbSource, N6705C, at, kind_of
from scopetech.plan import PlanRunner, load_layout, load_plan, parse_range
from scopetech.psu_sim import PsuSim, PsuSimTransport
from scopetech.sim import SimTransport
from scopetech.transport import ScopeError
from scopetech.workflow import Bench

ROOT = Path(__file__).resolve().parents[1]


def make(modules=("N6763A", "N6781A", "N6791A", "")):
    t = PsuSimTransport(PsuSim(modules))
    p = N6705C(t)
    p.connect()
    return p, t.sim


class DriverTest(unittest.TestCase):
    def test_basics(self):
        self.assertEqual(at(1), "(@1)")
        self.assertEqual(at([1, 3]), "(@1,3)")
        self.assertEqual([kind_of(m) for m in ("N6763A", "N6781A", "N6791A", "", "X")],
                         ["precision", "smu", "load", "empty", "unknown"])

    def test_check_report_lists_modules_and_arb(self):
        p, sim = make()
        lines, data = p.check_report()
        text = "\n".join(lines)
        self.assertIn("CH1: N6763A (precision) ARB 가능", text)
        self.assertIn("CH3: N6791A (load) ARB 안 됨", text)
        self.assertIn("CH4: (비어 있음)", text)
        self.assertEqual([c["arb"] for c in data["channels"]], [True, True, False, None])
        self.assertEqual(p.errors(), [])  # 확인 과정의 오류는 대기열에 남기지 않음
        self.assertEqual(sim.ch[1]["OUTP"], "0")

    def test_errors_and_check(self):
        p, sim = make()
        p.write("BOGUS 1")
        with self.assertRaises(ScopeError):
            p.check("시험")
        self.assertEqual(p.errors(), [])

    def test_not_n6705c(self):
        t = PsuSimTransport()
        t.sim.handle = lambda line: b"SOME,OTHER,1,1\n" if line.startswith("*IDN") else None
        with self.assertRaises(ScopeError):
            N6705C(t).connect()


class ArbSourceTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.cfg.cp.set("supply", "settle_s", "0")

    def test_start_stop_sequence(self):
        p, sim = make()
        src = ArbSource(p, self.cfg)
        self.assertEqual(src.prepare(), [])
        sim.log.clear()
        info = src.start(0.0, 1.8)
        self.assertEqual((info["low_v"], info["high_v"], info["times_ms"], info["current_limit_ma"]),
                         (0.0, 1.8, [1.0, 1.0, 1.0, 1.0, 0.0], 20.0))
        log = sim.log
        idx = {k: next(i for i, line in enumerate(log) if line.startswith(k)) for k in
               ("OUTP OFF", "CURR 0.02,(@1)", "ARB:VOLT:TRAP:TOP 1.8", "VOLT:MODE ARB", "OUTP ON", "TRIG:TRAN")}
        self.assertLess(idx["OUTP OFF"], idx["CURR 0.02,(@1)"])
        self.assertLess(idx["CURR 0.02,(@1)"], idx["OUTP ON"])  # 전류 제한을 먼저 건 뒤 켬
        self.assertLess(idx["VOLT:MODE ARB"], idx["OUTP ON"])
        self.assertLess(idx["OUTP ON"], idx["TRIG:TRAN"])
        for cmd in ("ARB:VOLT:TRAP:STAR 0,(@1)", "ARB:VOLT:TRAP:RTIM 0.001,(@1)", "ARB:VOLT:TRAP:END:TIM 0,(@1)",
                    "ARB:COUN INF,(@1)", "ARB:FUNC:SHAP TRAP,(@1)"):
            self.assertIn(cmd, log)
        self.assertEqual(sim.arb_running, {1})
        src.stop()
        self.assertEqual((sim.ch[1]["OUTP"], sim.ch[1]["VOLT:MODE"]), ("0", "FIX"))
        self.assertEqual(sim.arb_running, set())
        src.stop()  # 두 번 불러도 됨

    def test_settings_and_protection(self):
        p, sim = make()
        self.cfg.cp.set("supply", "arb_rise_ms", "2.5")
        self.cfg.cp.set("supply", "arb_current_limit_ma", "5")
        src = ArbSource(p, self.cfg)
        self.assertEqual(src.start(0.9, 1.2)["times_ms"][1], 2.5)
        self.assertIn("CURR 0.005,(@1)", sim.log)
        src.stop()
        sim.ques[1] = 2
        with self.assertRaises(ScopeError):
            src.start(0, 3.3)

    def test_prepare_warns_on_wrong_module(self):
        p, sim = make(("N6791A", "N6763A", "", ""))
        warns = ArbSource(p, self.cfg).prepare()
        self.assertTrue(any("N6791A" in w for w in warns))
        self.assertTrue(any("ARB 명령" in w for w in warns))


class PlanArbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.cfg = Config()
        self.cfg.cp.set("capture", "enabled", "no")
        self.cfg.cp.set("supply", "settle_s", "0")
        self.t = SimTransport()
        self.t.sim.on[8] = True
        s = MSO5B(self.t, settle_s=0)
        s.connect()
        self.bench = Bench(s, self.cfg, self.base, echo=lambda _: None)
        self.rows = load_plan(ROOT / "test_plan.csv")
        self.layout = load_layout(ROOT / "test_plan_summary.csv")
        self.psu, self.psim = make()
        self.src = ArbSource(self.psu, self.cfg)

    def run_plan(self, answers, supply=True):
        it = iter(answers)
        out = []
        code = PlanRunner(self.bench, self.rows, self.layout, ask=lambda p: (out.append(p), next(it))[1],
                          echo=out.append, supply=self.src if supply else None).run()
        return code, "\n".join(out)

    def result_of(self, row_index):
        doc = json.loads((self.base / "plan" / "T" / "progress_25C.json").read_text(encoding="utf-8"))
        e = doc["rows"][self.rows[row_index].key]
        return e, (json.loads((self.base / e["folder"] / "result.json").read_text(encoding="utf-8")) if e.get("folder") else None)

    def test_shipped_ranges(self):
        self.assertEqual({r.in_pin: r.swing for r in self.rows if r.kind in ("VI", "AI")},
                         {"2": (0, 3.3), "20": (0, 3.3), "3": (0, 1.8), "6": (0, 1.8), "5": (0, 3.3), "15": (0, 1.8),
                          "10": (0.9, 1.2)})
        self.assertIsNone(self.rows[7].swing)  # POR
        self.assertEqual(self.rows[8].swing, (0, 3.3))
        self.assertEqual(parse_range("0.9~1.2V"), (0.9, 1.2))
        for bad in ("3.3-0", "a-b", "0-30"):
            with self.assertRaises(ValueError):
                parse_range(bad)

    def test_ranges_trigger_level_and_off_after_each_row(self):
        code, out = self.run_plan(["25", "T", "g", "3", "", "g", "7", "", "", "", "q"])  # 3 VI(1.8), 7 AI, 8 POR, 9 IOH
        self.assertEqual(code, 0)
        _, r3 = self.result_of(2)
        self.assertEqual((r3["arb"]["low_v"], r3["arb"]["high_v"]), (0, 1.8))
        _, r7 = self.result_of(6)
        self.assertEqual((r7["arb"]["low_v"], r7["arb"]["high_v"]), (0.9, 1.2))
        _, r8 = self.result_of(7)
        self.assertNotIn("arb", r8)  # POR: CH1 = VDD
        _, r9 = self.result_of(8)
        self.assertEqual((r9["arb"]["low_v"], r9["arb"]["high_v"]), (0, 3.3))  # IOH 트리거 스윙
        self.assertIn("CH1 트리거 레벨 0.9 V", out)
        self.assertIn("CH1 트리거 레벨 1.05 V", out)
        self.assertEqual(self.t.sim.trigger_level[1], 1.65)  # 마지막 IOH
        self.assertEqual(out.count("ARB 켬"), out.count("ARB 끔"))
        self.assertEqual(self.psim.ch[1]["OUTP"], "0")
        self.assertIn("입력: 0~1.8 V 사다리꼴 (N6705C ARB 자동)", out)

    def test_supply_failure_fails_row_and_turns_off(self):
        self.psim.ques[1] = 4
        code, out = self.run_plan(["25", "T", "", "q"])
        e, _ = self.result_of(0)
        self.assertEqual(e["status"], "실패")
        self.assertIn("보호", e["error"])
        self.assertEqual(self.psim.ch[1]["OUTP"], "0")

    def test_without_supply_guides_function_generator(self):
        code, out = self.run_plan(["25", "T", "", "q"], supply=False)
        self.assertIn("입력: 0~3.3 V 사다리꼴 (함수발생기로 맞추세요)", out)
        self.assertNotIn("ARB 켬", out)
        _, r1 = self.result_of(0)
        self.assertNotIn("arb", r1)


class CliTest(unittest.TestCase):
    def test_check_and_arb(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.ini"
            cfg.write_text(f"[output]\nlog_dir = {d}/logs\n[supply]\nsettle_s = 0\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(psu_cli.main(["--config", str(cfg), "--sim", "check"]), 0)
                self.assertEqual(psu_cli.main(["--config", str(cfg), "--sim", "arb", "0", "3.3"], ask=lambda _: ""), 0)
                self.assertEqual(psu_cli.main(["--config", str(cfg), "--sim", "measure"]), 0)
            self.assertIn("ARB 가능", out.getvalue())
            self.assertIn("ARB 끔", out.getvalue())
            self.assertEqual(len(list((Path(d) / "logs").glob("psu_check_*.txt"))), 1)


if __name__ == "__main__":
    unittest.main()

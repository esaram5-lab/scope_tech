import json
import tempfile
import unittest
from pathlib import Path

from scopetech import results, wavefile
from scopetech.analysis import Waveform
from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.sim import SimTransport
from scopetech.transport import parse_block_header
from scopetech.workflow import Bench

ROOT = Path(__file__).resolve().parents[1]


class SimFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.t = SimTransport()
        self.sim = self.t.sim
        self.scope = MSO5B(self.t, settle_s=0)
        self.scope.connect()
        cfg = Config(ROOT / "config.ini")
        cfg.cp.set("measure", "stats_acquisitions", "10")
        self.bench = Bench(self.scope, cfg, self.out, echo=lambda s: None)

    def tearDown(self):
        self.tmp.cleanup()
        self.assertEqual(self.sim.error_log, [], "스코프 명령 오류 발생")

    def test_vih_capture_save(self):
        self.bench.name, self.bench.temp = "INV_3V3", "-40"
        rec = self.bench.vih(1, 2)
        self.assertEqual(self.sim.acq_mode, "HIRES")
        a = rec.result["analysis"]
        self.assertAlmostEqual(a["vt_plus"]["stats"]["mean"], 1.90, delta=0.01)
        self.assertAlmostEqual(a["vt_minus"]["stats"]["mean"], 1.40, delta=0.01)
        self.assertEqual(self.sim.cursor["STATE"], "1")
        self.assertEqual(self.sim.cursor["ASOURCE"], "CH1")
        self.assertIn("-40C", rec.folder.name)

        shots = self.bench.capture()
        self.assertEqual([p.name for p in shots], ["meas.png", "clean.png"])
        self.assertNotEqual(shots[0].read_bytes(), shots[1].read_bytes())
        self.assertEqual(self.sim.cursor["STATE"], "1")  # 캡처 후 커서 복원

        saved = self.bench.save()
        names = {p.name for p in saved}
        self.assertIn("waveform.csv", names)
        self.assertIn("setup.set", names)
        self.assertEqual({f"CH{c}.wfm" for c in range(1, 8)} & names, {f"CH{c}.wfm" for c in range(1, 8)})
        self.assertEqual(self.sim.files, {})  # 스코프 임시 파일 정리
        wfs = wavefile.read_csv(rec.folder / "waveform.csv")
        self.assertEqual([w.source for w in wfs], [f"CH{c}" for c in range(1, 8)])
        self.assertEqual(len(wfs[0]), self.sim.reclen)

        data = json.loads((rec.folder / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(data["temp"], "-40")
        self.assertTrue((self.out / "results.csv").exists())

    def test_voh_measurements_restored_after_capture(self):
        self.bench.out_type = "OD"
        rec = self.bench.voh([2, 3])
        self.assertEqual(len(self.sim.meas), 8)
        top = rec.result["measurements"]["TOP_CH2"]
        self.assertAlmostEqual(top["mean"], 3.28, delta=0.02)
        self.bench.capture()
        self.assertEqual(len(self.sim.meas), 8)
        self.assertEqual({m["TYPE"] for m in self.sim.meas.values()}, {"TOP", "BASE", "MAXIMUM", "MINIMUM"})
        # 다음 측정 시작 시 이전 측정 정리
        rec = self.bench.vih(1, 2, hold=True)
        # 이전 VOH 측정 8개는 지워지고, VIL/VIH 용 출력 TOP/BASE 2개만 남는다
        self.assertEqual(sorted((m["TYPE"], m["SOURCE1"]) for m in self.sim.meas.values()),
                         [("BASE", "CH2"), ("TOP", "CH2")])
        voh = {m["metric"]: m for m in rec.result["metrics"]}
        self.assertAlmostEqual(voh["VOH (출력 High)"]["worst"], 3.28, delta=0.02)
        self.assertAlmostEqual(voh["VOL (출력 Low)"]["worst"], 0.04, delta=0.02)
        self.assertGreaterEqual(voh["VOH (출력 High)"]["n"], 2)

    def test_results_csv_locked_does_not_abort(self):
        from unittest import mock
        msgs = []
        self.bench.echo = msgs.append
        with mock.patch.object(results, "append_csv", side_effect=PermissionError("locked")):
            rec = self.bench.vih(1, 2, hold=True)
        self.assertTrue((rec.folder / "result.json").exists())
        self.assertTrue(list(self.out.glob("results_pending_*.csv")))
        self.assertTrue(any("results.csv" in m for m in msgs))
        self.bench.capture()  # 이후 흐름 계속

    def test_measurement_events_are_not_fatal(self):
        self.sim.esr |= 0x10
        self.sim.events.append('2228,"Measurement error, Positive Clipping"')
        self.scope.check("단일 획득")  # 예외 없음
        self.assertTrue(self.scope.warnings)
        self.sim.esr |= 0x20
        self.sim.events.append('-113,"Undefined header"')
        with self.assertRaises(Exception):
            self.scope.check("명령")
        self.sim.error_log.clear()

    def test_capture_without_measure(self):
        shots = self.bench.capture()
        self.assertEqual([p.name for p in shots], ["clean.png"])
        self.assertEqual(shots[0].parent.parent.name, "MISC")


class ResultsTest(unittest.TestCase):
    spec = {"vih_min": 2.0, "vil_max": 0.8, "voh_min": 2.4, "vol_max": 0.4}

    def test_rules(self):
        m = results.metric
        cases = [
            (m("c", "s", "VT+", "VIH", 1.9, 1.95), "PP", "PASS"),
            (m("c", "s", "VT+", "VIH", 1.9, 2.05), "PP", "FAIL"),
            (m("c", "s", "VT-", "VIL", 1.4, 0.9), "PP", "PASS"),
            (m("c", "s", "VT-", "VIL", 1.4, 0.7), "PP", "FAIL"),
            (m("c", "s", "VOH", "VOH", 3.2, 2.3), "PP", "FAIL"),
            (m("c", "s", "VOH", "VOH", 3.2, 2.3), "OD", "N/A(OD)"),
            (m("c", "s", "VOL", "VOL", 0.1, 0.2), "OD", "PASS"),
        ]
        for metric, out_type, expect in cases:
            self.assertEqual(results.judge(metric, self.spec, out_type)[2], expect, metric)
        self.assertEqual(results.judge(cases[0][0], {}, "PP")[2], "N/A")

    def test_unknown_polarity_not_judged(self):
        from scopetech.analysis import ThresholdResult, Transition
        th = ThresholdResult(vt_plus=[Transition(0, 0, 1.9, "rise", "rise")],
                             vt_minus=[Transition(1, 1, 1.4, "fall", "rise")], polarity="unknown")
        rows = results.vih_metrics(th, "CH1->CH2", "s")
        self.assertEqual([results.judge(r, self.spec, "PP")[2] for r in rows[:2]], ["N/A(극성불명)"] * 2)

    def test_block_header(self):
        start, length = parse_block_header(b"#41234" + b"x" * 1234 + b"\n")
        self.assertEqual((start, length), (6, 1234))

    def test_csv_roundtrip_separate_timebase(self):
        with tempfile.TemporaryDirectory() as d:
            a = Waveform("CH1", [0, 1, 2], 1e-3, 0.0)
            b = Waveform("CH2", [5, 6], 2e-3, 0.0)
            paths = wavefile.write_csv(Path(d), [a, b])
            self.assertEqual([p.name for p in paths], ["waveform_CH1.csv", "waveform_CH2.csv"])
            back = wavefile.read_csv(paths[1])[0]
            self.assertEqual(list(back.raw), [5.0, 6.0])
            self.assertAlmostEqual(back.xincr, 2e-3)


if __name__ == "__main__":
    unittest.main()

import random
import unittest

from scopetech.analysis import Waveform, find_thresholds, levels

N = 30000
XINCR = 1e-6


def triangle(n=N, period=10000, vmax=3.3, noise=0.002, seed=0):
    rng = random.Random(seed)
    return [vmax * (1 - abs(((i / period) % 1) * 2 - 1)) + rng.gauss(0, noise) for i in range(n)]


def schmitt(vin, vt_plus, vt_minus, hi=3.3, lo=0.0, invert=True, noise=0.002, seed=1):
    rng = random.Random(seed)
    state, out = vin[0] > vt_plus, []
    for v in vin:
        if not state and v >= vt_plus:
            state = True
        elif state and v <= vt_minus:
            state = False
        level = (lo if state else hi) if invert else (hi if state else lo)
        out.append(level + rng.gauss(0, noise))
    return out


def wf(name, volts):
    return Waveform(name, volts, XINCR)


class ThresholdTest(unittest.TestCase):
    def test_inverter_with_hysteresis(self):
        vin = triangle()
        res = find_thresholds(wf("CH1", vin), wf("CH2", schmitt(vin, 1.9, 1.4)))
        self.assertEqual(res.polarity, "inverting")
        self.assertEqual(len(res.vt_plus), 3)
        self.assertEqual(len(res.vt_minus), 3)
        for v in res.vt_plus_v:
            self.assertAlmostEqual(v, 1.9, delta=0.005)
        for v in res.vt_minus_v:
            self.assertAlmostEqual(v, 1.4, delta=0.005)
        self.assertAlmostEqual(res.hysteresis, 0.5, delta=0.01)
        self.assertEqual(res.chatter, 0)
        self.assertEqual(res.warnings, [])

    def test_non_inverting_level_shifter(self):
        vin = triangle(vmax=1.8)
        out = schmitt(vin, 0.95, 0.85, hi=3.3, invert=False)
        res = find_thresholds(wf("CH1", vin), wf("CH2", out))
        self.assertEqual(res.polarity, "non-inverting")
        self.assertAlmostEqual(res.vt_plus_v[0], 0.95, delta=0.005)
        self.assertAlmostEqual(res.vt_minus_v[0], 0.85, delta=0.005)

    def test_adc_codes_scaling(self):
        vin = triangle()
        out = schmitt(vin, 2.0, 1.2)
        ymult, yzero = 1e-4, 1.65
        code = lambda vs: [round((v - yzero) / ymult) for v in vs]  # noqa: E731
        a = Waveform("CH1", code(vin), XINCR, -0.015, 0, ymult, 0, yzero)
        b = Waveform("CH2", code(out), XINCR, -0.015, 0, ymult, 0, yzero)
        res = find_thresholds(a, b)
        self.assertAlmostEqual(res.vt_plus_v[0], 2.0, delta=0.005)
        self.assertAlmostEqual(res.vt_minus_v[0], 1.2, delta=0.005)
        self.assertAlmostEqual(res.vt_plus[0].time, a.time_at(res.vt_plus[0].index), places=9)

    def test_chatter_uses_worst_crossing(self):
        vin = triangle()
        out = schmitt(vin, 1.9, 1.4)
        # 첫 상승 구간 전환 직후 출력이 한 번 되돌아갔다가 다시 전환(떨림)
        first = next(i for i in range(1, N) if out[i - 1] > 1.65 >= out[i])
        for i in range(first + 30, first + 60):
            out[i] = 3.3
        res = find_thresholds(wf("CH1", vin), wf("CH2", out))
        self.assertEqual(res.chatter, 1)
        # 떨림 구간에서는 입력이 더 올라간 뒤의 마지막 전환(최악값)을 VT+ 로 쓴다
        self.assertGreater(res.vt_plus_v[0], 1.9 + 0.02)
        self.assertAlmostEqual(res.vt_plus_v[1], 1.9, delta=0.005)
        self.assertTrue(any("떨림" in w for w in res.warnings))

    def test_flat_output_warns(self):
        vin = triangle()
        res = find_thresholds(wf("CH1", vin), wf("CH2", [3.3] * N))
        self.assertEqual(res.vt_plus, [])
        self.assertTrue(res.warnings)

    def test_levels_ignore_overshoot(self):
        sq = []
        for i in range(20000):
            high = (i // 1000) % 2 == 1
            sq.append(3.3 if high else 0.1)
        for k in range(1000, 20000, 2000):  # 상승 에지마다 오버슈트
            for j in range(10):
                sq[k + j] = 3.8
        lv = levels(wf("CH2", sq))
        self.assertAlmostEqual(lv.top, 3.3, delta=0.01)
        self.assertAlmostEqual(lv.base, 0.1, delta=0.01)
        self.assertAlmostEqual(lv.vmax, 3.8)


if __name__ == "__main__":
    unittest.main()

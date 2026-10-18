"""파형 분석: 레벨(Top/Base) 추정과 입력 전환 전압(VT+/VT-) 검출.

VIL/VIH 측정 원리
    입력(CH_in)에 느린 삼각파를 넣고 출력(CH_out)이 바뀌는 순간(출력 50% 지점)의
    입력 전압을 읽는다.
    - 입력 상승 중 전환 -> VT+ (VIH 쪽 실제 문턱)
    - 입력 하강 중 전환 -> VT- (VIL 쪽 실제 문턱)
    판정: max(VT+) <= VIH(min),  min(VT-) >= VIL(max)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class Waveform:
    """스코프 파형. raw 는 ADC 코드(또는 전압), 스케일은 WFMOutpre 값."""

    source: str
    raw: Sequence[float]
    xincr: float
    xzero: float = 0.0
    pt_off: float = 0.0
    ymult: float = 1.0
    yoff: float = 0.0
    yzero: float = 0.0

    def __len__(self) -> int:
        return len(self.raw)

    def time_at(self, idx: float) -> float:
        return (idx - self.pt_off) * self.xincr + self.xzero

    def index_at(self, t: float) -> float:
        return (t - self.xzero) / self.xincr + self.pt_off

    def to_volts(self, code: float) -> float:
        return (code - self.yoff) * self.ymult + self.yzero

    def to_code(self, volts: float) -> float:
        return (volts - self.yzero) / self.ymult + self.yoff

    def same_timebase(self, other: "Waveform") -> bool:
        return (
            len(self) == len(other)
            and abs(self.xincr - other.xincr) <= 1e-9 * abs(self.xincr)
            and abs(self.time_at(0) - other.time_at(0)) <= 0.01 * abs(self.xincr)
        )


@dataclass
class Levels:
    base: float  # 하단 안정 레벨(최빈값) [V]
    top: float  # 상단 안정 레벨(최빈값) [V]
    vmin: float
    vmax: float

    @property
    def amplitude(self) -> float:
        return self.top - self.base


def levels(wf: Waveform, bins: int = 256) -> Levels:
    """Tek TOP/BASE 와 같은 방식(중간값 위/아래의 최빈값)으로 레벨을 추정한다."""
    raw = wf.raw
    lo, hi = min(raw), max(raw)
    if hi == lo:
        v = wf.to_volts(lo)
        return Levels(v, v, v, v)
    scale = (bins - 1) / (hi - lo)
    counts = [0] * bins
    for c in raw:
        counts[int((c - lo) * scale)] += 1
    half = bins // 2
    b_base = max(range(0, half), key=counts.__getitem__)
    b_top = max(range(half, bins), key=counts.__getitem__)
    # 최빈 구간 안의 실제 평균으로 정밀화
    s_base = s_top = 0.0
    n_base = n_top = 0
    for c in raw:
        b = int((c - lo) * scale)
        if b == b_base:
            s_base += c
            n_base += 1
        elif b == b_top:
            s_top += c
            n_top += 1
    return Levels(
        base=wf.to_volts(s_base / n_base),
        top=wf.to_volts(s_top / n_top),
        vmin=wf.to_volts(lo),
        vmax=wf.to_volts(hi),
    )


@dataclass
class Transition:
    index: float  # 출력 파형 기준 (소수) 인덱스
    time: float  # 트리거 기준 시간 [s]
    vin: float  # 그 순간의 입력 전압 [V]
    input_dir: str  # 'rise' | 'fall' | 'unknown'
    output_dir: str  # 'rise' | 'fall'
    extra: int = 0  # 같은 램프 구간에서 추가로 발생한 전환 수(떨림)


def summarize(values: Sequence[float]) -> dict | None:
    if not values:
        return None
    return {
        "n": len(values),
        "min": min(values),
        "mean": sum(values) / len(values),
        "max": max(values),
    }


@dataclass
class ThresholdResult:
    vt_plus: list[Transition] = field(default_factory=list)
    vt_minus: list[Transition] = field(default_factory=list)
    polarity: str = "unknown"  # 'non-inverting' | 'inverting' | 'unknown'
    chatter: int = 0
    out_levels: Levels | None = None
    in_levels: Levels | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def hysteresis(self) -> float | None:
        p, m = summarize(self.vt_plus_v), summarize(self.vt_minus_v)
        if p and m:
            return p["mean"] - m["mean"]
        return None

    @property
    def vt_plus_v(self) -> list[float]:
        return [t.vin for t in self.vt_plus]

    @property
    def vt_minus_v(self) -> list[float]:
        return [t.vin for t in self.vt_minus]

    def to_dict(self) -> dict:
        def tr(t: Transition) -> dict:
            return {"time_s": t.time, "vin_V": t.vin, "output": t.output_dir, "chatter": t.extra}

        return {
            "vt_plus": {"stats": summarize(self.vt_plus_v), "events": [tr(t) for t in self.vt_plus]},
            "vt_minus": {"stats": summarize(self.vt_minus_v), "events": [tr(t) for t in self.vt_minus]},
            "hysteresis_V": self.hysteresis,
            "polarity": self.polarity,
            "chatter_groups": self.chatter,
            "out_top_V": self.out_levels.top if self.out_levels else None,
            "out_base_V": self.out_levels.base if self.out_levels else None,
            "in_min_V": self.in_levels.vmin if self.in_levels else None,
            "in_max_V": self.in_levels.vmax if self.in_levels else None,
            "warnings": list(self.warnings),
        }


def _crossing(raw: Sequence[float], i: int, stop: int, level: float, rising: bool) -> float:
    """i 이전에서 level 을 마지막으로 통과한 (소수) 인덱스."""
    for j in range(i - 1, max(stop, 0) - 1, -1):
        a, b = raw[j], raw[j + 1]
        if (rising and a < level <= b) or (not rising and a > level >= b):
            return j + (level - a) / (b - a)
    return float(i)


def _window_mean(raw: Sequence[float], center: int, half: int) -> float:
    j0 = max(0, center - half)
    j1 = min(len(raw) - 1, center + half)
    return sum(raw[j0 : j1 + 1]) / (j1 - j0 + 1)


def find_thresholds(
    win: Waveform,
    wout: Waveform,
    avg_points: int = 10,
    cross: float = 0.5,
    band: float = 0.2,
    min_amp_v: float = 0.1,
) -> ThresholdResult:
    """출력 전환 시점의 입력 전압(VT+/VT-)을 찾는다.

    avg_points: 입력 전압을 읽을 때 전환 지점 앞뒤로 평균할 샘플 수(노이즈 저감)
    cross: 전환 시점으로 볼 출력 레벨(출발 레벨에서 진폭 대비). 0.5 = 50% 지점.
           OD 출력처럼 상승이 느리면 0.1~0.2 로 낮추면 실제 전환 시작점에 가까워진다.
    band: 출력 전환 판정 히스테리시스(진폭 대비, 0.2 -> 30%/70% 를 넘어야 전환으로 인정)
    """
    res = ThresholdResult()
    res.out_levels = lv = levels(wout)
    res.in_levels = levels(win)
    if lv.amplitude < min_amp_v:
        res.warnings.append(
            f"출력({wout.source}) 진폭이 {lv.amplitude:.3f} V 로 너무 작아 전환을 찾을 수 없습니다. "
            "출력 채널/연결/입력 램프 범위를 확인하세요."
        )
        return res

    raw = wout.raw
    c_base, c_top = wout.to_code(lv.base), wout.to_code(lv.top)
    if c_top < c_base:  # ymult 가 음수인 경우 대비
        c_base, c_top = c_top, c_base
    amp = c_top - c_base
    mid = c_base + 0.5 * amp
    hi = c_base + (0.5 + band) * amp
    lo = c_base + (0.5 - band) * amp
    rise_level = c_base + cross * amp
    fall_level = c_top - cross * amp

    events: list[tuple[float, str]] = []
    high = raw[0] > mid
    last = 0
    for i in range(1, len(raw)):
        v = raw[i]
        if high:
            if v <= lo:
                events.append((_crossing(raw, i, last, fall_level, rising=False), "fall"))
                high, last = False, i
        elif v >= hi:
            events.append((_crossing(raw, i, last, rise_level, rising=True), "rise"))
            high, last = True, i

    n_in = len(win)
    span = max(5, n_in // 200)  # 입력 기울기 판별 구간
    noise_v = 1e-3
    unknown = 0
    transitions: list[Transition] = []
    for idx_out, out_dir in events:
        t = wout.time_at(idx_out)
        j = round(win.index_at(t))
        if j < 0 or j >= n_in:
            continue
        vin = win.to_volts(_window_mean(win.raw, j, avg_points))
        before = win.to_volts(_window_mean(win.raw, max(0, j - span), 3))
        after = win.to_volts(_window_mean(win.raw, min(n_in - 1, j + span), 3))
        if after - before > noise_v:
            in_dir = "rise"
        elif before - after > noise_v:
            in_dir = "fall"
        else:
            in_dir = "unknown"
            unknown += 1
        transitions.append(Transition(idx_out, t, vin, in_dir, out_dir))

    # 출력 극성: 입력 방향과 출력 방향이 같은 전환이 많으면 비반전
    known = [t for t in transitions if t.input_dir != "unknown"]
    same = sum(1 for t in known if t.input_dir == t.output_dir)
    if same * 2 > len(known):
        res.polarity = "non-inverting"
    elif same * 2 < len(known):
        res.polarity = "inverting"

    # 같은 램프 구간(입력 방향이 같은 연속 전환)은 하나로 묶고, 극성에 맞는 전환 중 최악값을 대표값으로 사용
    # (입력 상승: 가장 높은 입력 전압 = VT+ 최악, 입력 하강: 가장 낮은 입력 전압 = VT- 최악)
    groups: list[list[Transition]] = []
    for tr in known:
        if groups and groups[-1][0].input_dir == tr.input_dir:
            groups[-1].append(tr)
        else:
            groups.append([tr])
    reps: list[Transition] = []
    skipped = 0
    for g in groups:
        if res.polarity == "unknown":
            match = g
        else:
            want_same = res.polarity == "non-inverting"
            match = [t for t in g if (t.input_dir == t.output_dir) == want_same]
        if not match:
            skipped += 1
            continue
        rep = max(match, key=lambda t: t.vin) if g[0].input_dir == "rise" else min(match, key=lambda t: t.vin)
        rep.extra = len(g) - 1
        reps.append(rep)
    res.chatter = sum(1 for r in reps if r.extra)
    res.vt_plus = [r for r in reps if r.input_dir == "rise"]
    res.vt_minus = [r for r in reps if r.input_dir == "fall"]

    if not res.vt_plus:
        res.warnings.append("입력 상승 구간에서 출력 전환이 없습니다. 램프 진폭/시간축(3주기)을 확인하세요.")
    if not res.vt_minus:
        res.warnings.append("입력 하강 구간에서 출력 전환이 없습니다. 램프 진폭/시간축(3주기)을 확인하세요.")
    if res.chatter:
        res.warnings.append(
            f"문턱 근처에서 출력이 여러 번 바뀐 구간이 {res.chatter}곳 있습니다(떨림). 각 구간의 최악 전환(VT+ 최대, VT- 최소)을 사용했습니다."
        )
    if unknown:
        res.warnings.append(f"입력 방향을 판별할 수 없는 전환 {unknown}건(램프 꼭지점 근처)은 제외했습니다.")
    if skipped:
        res.warnings.append(f"출력 극성과 맞지 않는 전환 구간 {skipped}곳은 제외했습니다. 파형을 확인하세요.")
    if res.polarity == "unknown" and reps:
        res.warnings.append("출력 극성을 판별할 수 없습니다(반전/비반전 전환 수가 같음). 파형을 확인하세요.")
    return res

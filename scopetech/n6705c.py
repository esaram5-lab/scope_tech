"""Keysight N6705C DC 전원 분석기 API (USB: VISA, LAN: 소켓 5025). 필요한 기능을 꺼내 쓰는 형태.

현재 장착(사용자): 1번 N6763A(정밀 DC), 2번 N6781A(2사분면 SMU), 3번 N6791A(전자 부하).
계획 모드는 ArbSource 로 1번 채널에서 사다리꼴 ARB(VI/AI 입력, IOH/Rp 의 CH1 트리거용 스윙)를 낸다.

    psu = N6705C(transport); psu.connect()
    psu.check()                         장비·옵션·모듈·채널별 기능(ARB 등) 확인 보고서
    psu.output(1, True) / psu.set_voltage(1, 3.3) / psu.set_current(1, 0.02) / psu.measure_voltage(1)
    psu.arb_trapezoid(1, 0, 3.3, (1e-3, 1e-3, 1e-3, 1e-3, 0)); psu.arb_start(1); psu.arb_stop(1)
    psu.arb_ramp / arb_staircase / arb_user(전압·머무는 시간 목록)
    psu.smu_source_measure(2, 2.4, 0.01)   N6781A: 전압을 걸고 전류 읽기 (IOH/Rp 직접 측정용, 실장비 미확인)
    psu.load_cc(3, 0.005)                  N6791A: 정전류 부하 (실장비 미확인)

SCPI 는 Keysight N6700 계열 프로그래밍 설명서 기준. 모듈·펌웨어에 따라 다를 수 있어 check() 로 먼저 확인한다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .transport import ScopeError, Transport

KINDS = {  # 모델 접두어 -> 종류
    "N673": "basic", "N674": "basic", "N675": "high-perf", "N676": "precision",
    "N677": "basic", "N678": "smu", "N679": "load",
}
INF = "INF"


def at(ch) -> str:
    """채널 목록 '(@1)' / '(@1,2)'."""
    chans = ch if isinstance(ch, (list, tuple)) else [ch]
    return "(@" + ",".join(str(int(c)) for c in chans) + ")"


def kind_of(model: str) -> str:
    m = model.strip().upper()
    return next((k for p, k in KINDS.items() if m.startswith(p)), "unknown" if m else "empty")


@dataclass
class ChannelInfo:
    channel: int
    model: str
    kind: str
    options: str = ""
    arb: bool | None = None     # ARB 설정 명령이 받아들여지는지 (None = 확인 안 함)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"channel": self.channel, "model": self.model, "kind": self.kind, "options": self.options,
                "arb": self.arb, "notes": list(self.notes)}


class N6705C:
    def __init__(self, transport: Transport, echo=None):
        self.t = transport
        self.echo = echo or (lambda _: None)
        self.idn = ""
        self.channels: dict[int, ChannelInfo] = {}

    # ------------------------------------------------------------ 기본
    def write(self, cmd: str) -> None:
        self.t.write(cmd)

    def query(self, cmd: str) -> str:
        return self.t.query(cmd).strip()

    def errors(self) -> list[str]:
        """오류 대기열을 비우며 읽는다 (없으면 빈 목록)."""
        out = []
        for _ in range(30):
            e = self.query("SYST:ERR?")
            try:
                code = int(e.split(",")[0])
            except ValueError:
                code = -1 if e else 0
            if code == 0:
                break
            out.append(e)
        return out

    def check(self, what: str) -> None:
        errs = self.errors()
        if errs:
            raise ScopeError(f"N6705C {what} 오류: {'; '.join(errs)}")

    def connect(self) -> str:
        self.idn = self.query("*IDN?")
        if "N670" not in self.idn.upper():
            raise ScopeError(f"N6705C 가 아닌 장비입니다: {self.idn}")
        self.write("*CLS")
        self.read_modules()
        return self.idn

    def close(self) -> None:
        self.t.close()

    # ------------------------------------------------------------ 장비 정보
    def options(self) -> str:
        return self.query("*OPT?")

    def read_modules(self) -> dict[int, ChannelInfo]:
        try:
            n = int(float(self.query("SYST:CHAN:COUN?")))
        except ValueError:
            n = 4
        n = max(1, min(n, 4))
        models = [m.strip().strip('"') for m in self.query(f"SYST:CHAN:MOD? (@1:{n})").split(",")]
        self.channels = {}
        for i in range(1, n + 1):
            model = models[i - 1] if i <= len(models) else ""
            self.channels[i] = ChannelInfo(i, model, kind_of(model))
        self.errors()
        return self.channels

    def model(self, ch: int) -> str:
        info = self.channels.get(ch)
        return info.model if info else ""

    def probe_arb(self, ch: int) -> bool:
        """채널이 ARB 설정 명령(조회)을 받아들이는지. 출력·설정은 바꾸지 않는다."""
        self.errors()
        self.query(f"ARB:FUNC:TYPE? {at(ch)}")
        self.query(f"ARB:FUNC:SHAP? {at(ch)}")
        return not self.errors()

    def capabilities(self) -> dict[int, ChannelInfo]:
        """채널별 모델·종류·옵션·ARB 가능 여부 (확장 개발 때 쓸 기능 목록)."""
        if not self.channels:
            self.read_modules()
        for ch, info in self.channels.items():
            if info.kind == "empty":
                continue
            try:
                info.options = self.query(f"SYST:CHAN:OPT? {at(ch)}").strip('"')
            except ScopeError:
                info.options = ""
            self.errors()
            info.arb = self.probe_arb(ch)
            info.notes = {
                "precision": ["정밀 DC 전원: 전압·전류 정밀 측정, ARB(사다리꼴 등) 입력 신호"],
                "smu": ["2사분면 SMU: 전류 공급·흡수, µA 측정 -> IOH/IOL·Rp 직접 측정 후보"],
                "load": ["전자 부하: 정전류(CC) 등 -> 출력 부하 후보"],
            }.get(info.kind, [])
        return self.channels

    def check_report(self) -> tuple[list[str], dict]:
        """사람이 읽을 보고서 줄과 기록용 dict."""
        caps = self.capabilities()
        opts = self.options()
        lines = [f"장비: {self.idn}", f"옵션(*OPT?): {opts or '-'}"]
        for ch, info in caps.items():
            if info.kind == "empty":
                lines.append(f"  CH{ch}: (비어 있음)")
                continue
            arb = {True: "ARB 가능", False: "ARB 안 됨", None: "ARB 미확인"}[info.arb]
            lines.append(f"  CH{ch}: {info.model} ({info.kind}) {arb}" + (f", 모듈 옵션 {info.options}" if info.options else ""))
            lines += [f"        {n}" for n in info.notes]
        return lines, {"idn": self.idn, "options": opts, "channels": [c.to_dict() for c in caps.values()]}

    # ------------------------------------------------------------ 출력
    def output(self, ch, on: bool) -> None:
        self.write(f"OUTP {'ON' if on else 'OFF'},{at(ch)}")

    def output_state(self, ch: int) -> bool:
        return self.query(f"OUTP? {at(ch)}").strip() in ("1", "ON")

    def set_voltage(self, ch, volts: float) -> None:
        self.write(f"VOLT {volts:.6g},{at(ch)}")

    def set_current(self, ch, amps: float) -> None:
        """전류 제한 (정전압 모드에서 이 값 이상 흐르지 않음)."""
        self.write(f"CURR {amps:.6g},{at(ch)}")

    def voltage_setting(self, ch: int) -> float:
        return float(self.query(f"VOLT? {at(ch)}"))

    def current_setting(self, ch: int) -> float:
        return float(self.query(f"CURR? {at(ch)}"))

    def measure_voltage(self, ch: int) -> float:
        return float(self.query(f"MEAS:VOLT? {at(ch)}"))

    def measure_current(self, ch: int) -> float:
        return float(self.query(f"MEAS:CURR? {at(ch)}"))

    def questionable(self, ch: int) -> int:
        """보호·이상 상태 비트 (0 이면 정상)."""
        return int(float(self.query(f"STAT:QUES:COND? {at(ch)}")))

    def clear_protection(self, ch) -> None:
        self.write(f"OUTP:PROT:CLE {at(ch)}")

    # ------------------------------------------------------------ ARB
    def _arb_common(self, ch: int, shape: str, count) -> None:
        self.write(f"ARB:FUNC:TYPE VOLT,{at(ch)}")
        self.write(f"ARB:FUNC:SHAP {shape},{at(ch)}")
        self.write(f"ARB:COUN {count},{at(ch)}")
        self.write(f"ARB:TERM:LAST OFF,{at(ch)}")  # ARB 가 끝나면 ARB 전 설정값으로

    def arb_trapezoid(self, ch: int, low: float, high: float, times, count=INF) -> None:
        """사다리꼴: 시작(low) 유지 -> 상승 -> 꼭대기(high) 유지 -> 하강 -> 끝(low) 유지. times = (시작, 상승, 꼭대기, 하강, 끝) 초."""
        t0, t1, t2, t3, t4 = (float(x) for x in times)
        self._arb_common(ch, "TRAP", count)
        a = at(ch)
        for cmd in (f"ARB:VOLT:TRAP:STAR {low:.6g}", f"ARB:VOLT:TRAP:TOP {high:.6g}",
                    f"ARB:VOLT:TRAP:STAR:TIM {t0:.6g}", f"ARB:VOLT:TRAP:RTIM {t1:.6g}",
                    f"ARB:VOLT:TRAP:TOP:TIM {t2:.6g}", f"ARB:VOLT:TRAP:FTIM {t3:.6g}",
                    f"ARB:VOLT:TRAP:END:TIM {t4:.6g}"):
            self.write(f"{cmd},{a}")
        self.write(f"VOLT:MODE ARB,{a}")
        self.check("ARB(사다리꼴) 설정")

    def arb_ramp(self, ch: int, low: float, high: float, start_s: float, rise_s: float, end_s: float, count=INF) -> None:
        self._arb_common(ch, "RAMP", count)
        a = at(ch)
        for cmd in (f"ARB:VOLT:RAMP:STAR {low:.6g}", f"ARB:VOLT:RAMP:END {high:.6g}",
                    f"ARB:VOLT:RAMP:STAR:TIM {start_s:.6g}", f"ARB:VOLT:RAMP:RTIM {rise_s:.6g}",
                    f"ARB:VOLT:RAMP:END:TIM {end_s:.6g}"):
            self.write(f"{cmd},{a}")
        self.write(f"VOLT:MODE ARB,{a}")
        self.check("ARB(램프) 설정")

    def arb_staircase(self, ch: int, low: float, high: float, steps: int, start_s: float, step_s: float, end_s: float,
                      count=INF) -> None:
        self._arb_common(ch, "STA", count)
        a = at(ch)
        for cmd in (f"ARB:VOLT:STA:STAR {low:.6g}", f"ARB:VOLT:STA:END {high:.6g}", f"ARB:VOLT:STA:NST {int(steps)}",
                    f"ARB:VOLT:STA:STAR:TIM {start_s:.6g}", f"ARB:VOLT:STA:TIM {step_s:.6g}",
                    f"ARB:VOLT:STA:END:TIM {end_s:.6g}"):
            self.write(f"{cmd},{a}")
        self.write(f"VOLT:MODE ARB,{a}")
        self.check("ARB(계단) 설정")

    def arb_user(self, ch: int, levels: list[float], dwells: list[float], count=INF) -> None:
        """사용자 정의: 점마다 전압과 머무는 시간(초)."""
        if not levels or len(levels) != len(dwells):
            raise ValueError("전압과 시간 목록의 길이가 같아야 합니다.")
        self._arb_common(ch, "UDEF", count)
        a = at(ch)
        self.write(f"ARB:VOLT:UDEF:LEV {','.join(f'{v:.6g}' for v in levels)},{a}")
        self.write(f"ARB:VOLT:UDEF:DWEL {','.join(f'{t:.6g}' for t in dwells)},{a}")
        self.write(f"VOLT:MODE ARB,{a}")
        self.check("ARB(사용자 정의) 설정")

    def arb_start(self, ch: int) -> None:
        a = at(ch)
        self.write("TRIG:ARB:SOUR BUS")
        self.write(f"INIT:TRAN {a}")
        self.write(f"TRIG:TRAN {a}")
        self.check("ARB 시작")

    def arb_stop(self, ch: int) -> None:
        a = at(ch)
        self.write(f"ABOR:TRAN {a}")
        self.write(f"VOLT:MODE FIX,{a}")

    # ------------------------------------------------------------ SMU / 부하 (확장용, 실장비 미확인)
    def smu_source_measure(self, ch: int, volts: float, current_limit: float, settle_s: float = 0.05) -> float:
        """N6781A: 전압을 걸고(정전압, 전류 제한) 흐르는 전류(A)를 읽는다. 출력을 켠 상태로 둔다."""
        a = at(ch)
        self.write(f"EMUL PS2Q,{a}")
        self.write(f"SENS:CURR:RANG:AUTO ON,{a}")
        self.set_voltage(ch, volts)
        self.write(f"CURR:LIM {current_limit:.6g},{a}")
        self.write(f"CURR:LIM:NEG {current_limit:.6g},{a}")
        self.output(ch, True)
        time.sleep(settle_s)
        self.check("SMU 설정")
        return self.measure_current(ch)

    def load_cc(self, ch: int, amps: float) -> None:
        """N6791A: 정전류 부하로 amps 를 흡수(출력은 켜지 않음)."""
        a = at(ch)
        self.write(f"FUNC CURR,{a}")
        self.write(f"CURR {amps:.6g},{a}")
        self.check("부하 설정")


# ---------------------------------------------------------------- 계획 모드용 ARB 신호원
class ArbSource:
    """[supply] 설정의 채널에서 사다리꼴 ARB 를 켜고 끈다. 측정 항목마다 start(low, high) -> stop()."""

    def __init__(self, psu: N6705C, cfg, echo=print):
        self.psu, self.cfg, self.echo = psu, cfg, echo
        self.on = False
        self.last: dict = {}

    @property
    def channel(self) -> int:
        return self.cfg.getint("supply", "arb_channel")

    def times(self) -> tuple[float, ...]:
        return tuple(self.cfg.getfloat("supply", f"arb_{k}_ms") / 1e3 for k in ("start", "rise", "top", "fall", "end"))

    def describe(self) -> str:
        ch = self.channel
        return f"N6705C CH{ch} {self.psu.model(ch) or '?'}"

    def prepare(self) -> list[str]:
        """연결 직후 확인: ARB 채널 모델·ARB 가능 여부. 주의 문구 목록."""
        ch = self.channel
        warns = []
        model = self.psu.model(ch)
        want = self.cfg.get("supply", "arb_model").upper()
        if want and not model.upper().startswith(want):
            warns.append(f"CH{ch} 모듈이 {model or '없음'} 입니다({want} 예상). [supply] arb_channel 을 확인하세요.")
        try:
            if not self.psu.probe_arb(ch):
                warns.append(f"CH{ch} 이 ARB 명령을 받지 않습니다(옵션·모듈 확인, python psu.py check).")
        except ScopeError as e:
            warns.append(f"CH{ch} ARB 확인 실패: {e}")
        self.psu.output(ch, False)
        return warns

    def start(self, low: float, high: float) -> dict:
        """출력 끈 채로 설정 -> 켜기 -> ARB 시작. 기록용 dict."""
        ch, psu = self.channel, self.psu
        limit = self.cfg.getfloat("supply", "arb_current_limit_ma") / 1e3
        times = self.times()
        psu.output(ch, False)
        psu.set_voltage(ch, low)
        psu.set_current(ch, limit)
        psu.arb_trapezoid(ch, low, high, times)
        psu.output(ch, True)
        self.on = True
        psu.arb_start(ch)
        time.sleep(self.cfg.getfloat("supply", "settle_s"))
        bits = psu.questionable(ch)
        if bits:
            raise ScopeError(f"N6705C CH{ch} 보호·이상 상태({bits}) — 배선·전류 제한({limit * 1e3:g} mA) 확인")
        self.last = {"channel": ch, "model": psu.model(ch), "shape": "trapezoid", "low_v": low, "high_v": high,
                     "times_ms": [round(t * 1e3, 6) for t in times], "current_limit_ma": limit * 1e3}
        return dict(self.last)

    def stop(self) -> None:
        if not self.on:
            return
        ch = self.channel
        try:
            self.psu.arb_stop(ch)
        finally:
            self.psu.output(ch, False)
            self.on = False


# ---------------------------------------------------------------- 연결
def open_transport(cfg, logger=None, sim: bool = False):
    timeout = cfg.getfloat("supply", "timeout_s")
    if sim:
        from .psu_sim import PsuSimTransport
        return PsuSimTransport(timeout=timeout, logger=logger)
    iface = cfg.get("supply", "interface").lower()
    if iface == "lan":
        from .transport import SocketTransport
        return SocketTransport(cfg.get("supply", "ip"), cfg.getint("supply", "port"), timeout, logger)
    if iface == "usb":
        from .transport import KEYSIGHT_VENDOR_IDS, VisaTransport
        return VisaTransport(cfg.get("supply", "usb_resource"), cfg.get("connection", "visa_library"), timeout, logger,
                             clear=True, vendors=KEYSIGHT_VENDOR_IDS, models=("N670",), device="Keysight N6705C")
    raise ScopeError(f"config.ini [supply] interface 값이 잘못되었습니다: {iface} (usb 또는 lan)")


def open_psu(cfg, logger=None, sim: bool = False, echo=print) -> N6705C:
    psu = N6705C(open_transport(cfg, logger, sim), echo)
    try:
        psu.connect()
    except BaseException:
        psu.close()
        raise
    return psu

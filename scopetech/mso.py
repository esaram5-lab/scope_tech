"""Tektronix 4/5/6 Series B MSO(MSO58B) 제어. 명령은 077189805 프로그래머 매뉴얼 기준."""
from __future__ import annotations

import sys
import time
from array import array

from .analysis import Waveform
from .transport import ScopeError, ScopeTimeout, Transport

CURSOR = "DISplay:WAVEView1:CURSor:CURSOR1"
ESR_ERROR_MASK = 0x3C  # QYE | DDE | EXE | CME
NO_VALUE = 9.9e37  # 측정값 없음


class MSO5B:
    def __init__(self, transport: Transport, temp_dir: str = "C:/scope_tech_tmp", settle_s: float = 0.5,
                 num_channels: int = 8):
        self.t = transport
        self.temp_dir = temp_dir.rstrip("/")
        self.settle_s = settle_s
        self.num_channels = num_channels
        self.idn = ""

    # ------------------------------------------------------------------ 기본
    def connect(self) -> str:
        self.write("*CLS")
        self.write("HEADer OFF")
        self.write("VERBose ON")
        self.idn = self.query("*IDN?")
        if not self._make_temp_dir():
            home = self.query("FILESystem:HOMEDir?").strip().strip('"').rstrip("/")
            self.temp_dir = f"{home}/scope_tech_tmp"
            if not self._make_temp_dir():
                raise ScopeError(f"스코프에 임시 폴더를 만들 수 없습니다: {self.temp_dir} (config.ini 의 [scope] temp_dir 확인)")
        return self.idn

    def _make_temp_dir(self) -> bool:
        """임시 폴더를 만든다. '이미 있음'(257) 은 정상.
        실장비에서 FILESystem:CWD 변경 + LDIR? 직후 TekScope 가 멈춰서 CWD 는 건드리지 않는다."""
        self.write(f'FILESystem:MKDir "{self.temp_dir}"')
        self.t.sync()
        esr = int(float(self.query("*ESR?")))
        if not esr & ESR_ERROR_MASK:
            return True
        events = self.query("ALLEv?")
        return "already contains" in events or events.lstrip().startswith("257")

    def write(self, cmd: str) -> None:
        self.t.write(cmd)

    def query(self, cmd: str) -> str:
        return self.t.query(cmd)

    def qfloat(self, cmd: str) -> float | None:
        v = float(self.query(cmd))
        return None if abs(v) >= NO_VALUE else v

    def check(self, context: str, raise_: bool = True) -> str | None:
        """*ESR? 로 오류를 확인하고, 있으면 ALLEv? 메시지와 함께 알린다."""
        esr = int(float(self.query("*ESR?")))
        if not esr & ESR_ERROR_MASK:
            return None
        msg = f"{context} 중 스코프 오류: {self.query('ALLEv?')}"
        if raise_:
            raise ScopeError(msg)
        return msg

    def recover(self) -> None:
        self.t.recover()
        self.write("*CLS")

    # ------------------------------------------------------------------ 획득
    def is_running(self) -> bool:
        return self.query("ACQuire:STATE?") != "0"

    def stop(self) -> None:
        """획득을 멈추고 스코프가 처리를 끝낼 때까지 기다린다."""
        if self.is_running():
            self.write("ACQuire:STATE STOP")
            deadline = time.monotonic() + 10
            while self.query("ACQuire:STATE?") != "0" and time.monotonic() < deadline:
                time.sleep(0.1)
        self.wait_idle()

    def freeze(self, timeout: float) -> None:
        """캡처/저장 전 화면 고정. 실행 중이면 강제 STOP 대신 Single 로 한 획득을 끝까지 받고 멈춘다
        (실장비에서 실행 중 STOP 직후 화면 저장 시 TekScope 가 멈춘 사례가 있음)."""
        if self.is_running():
            try:
                self.single(timeout)
            except ScopeTimeout:
                self.stop()
        self.wait_idle()

    def wait_idle(self, timeout: float = 15) -> None:
        """BUSY? 가 0 이 될 때까지 기다린 뒤 화면 갱신 시간을 준다."""
        deadline = time.monotonic() + timeout
        while self.query("BUSY?") not in ("0", "") and time.monotonic() < deadline:
            time.sleep(0.1)
        time.sleep(self.settle_s)

    def run(self) -> None:
        self.write("ACQuire:STOPAfter RUNSTop")
        self.write("ACQuire:STATE RUN")

    def acq_mode(self) -> str:
        return self.query("ACQuire:MODe?").upper()

    def set_acq_mode(self, mode: str) -> None:
        self.write(f"ACQuire:MODe {mode}")

    def single(self, timeout: float) -> None:
        """단일 획득(Single) 후 완료될 때까지 기다린다."""
        self.write("ACQuire:STOPAfter SEQuence")
        self.write("ACQuire:STATE ON")
        deadline = time.monotonic() + timeout
        while self.query("ACQuire:STATE?") != "0":
            if time.monotonic() > deadline:
                self.write("ACQuire:STATE STOP")
                raise ScopeTimeout(
                    f"{timeout:.0f}초 안에 트리거가 걸리지 않았습니다. 트리거 설정/입력 신호를 확인하세요."
                )
            time.sleep(0.1)
        self.check("단일 획득")

    def acquire_stats(self, count: int, timeout: float) -> int:
        """통계를 지우고 count 회 획득한 뒤 정지한다. 실제 획득 횟수를 반환."""
        self.write("ACQuire:STOPAfter RUNSTop")
        self.write("CLEAR")
        self.write("ACQuire:STATE RUN")
        deadline = time.monotonic() + timeout
        n = 0
        while True:
            time.sleep(0.2)
            n = int(float(self.query("ACQuire:NUMACq?")))
            if n >= count or time.monotonic() > deadline:
                break
        self.stop()
        if n == 0:
            raise ScopeTimeout("획득이 한 번도 되지 않았습니다. 트리거 설정을 확인하세요.")
        return n

    # ------------------------------------------------------------------ 채널/파형
    def displayed_channels(self) -> list[int]:
        return [
            ch for ch in range(1, self.num_channels + 1)
            if self.query(f"DISplay:GLObal:CH{ch}:STATE?") not in ("0", "OFF")
        ]

    def fetch_waveform(self, ch: int) -> Waveform:
        src = f"CH{ch}"
        self.write(f"DATa:SOUrce {src}")
        self.write("DATa:ENCdg SRIbinary")
        self.write("WFMOutpre:BYT_Nr 2")
        self.write("DATa:STARt 1")
        self.write("DATa:STOP 2000000000")  # 레코드 끝까지 (멈춘 레코드가 현재 설정보다 길어도 잘리지 않게)
        pre = {k: float(self.query(f"WFMOutpre:{k}?")) for k in ("NR_Pt", "XINcr", "XZEro", "PT_Off", "YMUlt", "YOFf", "YZEro")}
        self.write("CURVe?")
        with self.t.temp_timeout(10 + 2 * pre["NR_Pt"] / 1e6):
            data = self.t.read_block()
        raw = array("h")
        raw.frombytes(data[: len(data) // 2 * 2])
        if sys.byteorder == "big":
            raw.byteswap()
        self.check(f"{src} 파형 전송")
        if len(raw) != int(pre["NR_Pt"]):
            raise ScopeError(f"{src} 파형 길이 불일치: {len(raw)} != {int(pre['NR_Pt'])}")
        return Waveform(src, raw, pre["XINcr"], pre["XZEro"], pre["PT_Off"], pre["YMUlt"], pre["YOFf"], pre["YZEro"])

    # ------------------------------------------------------------------ 측정
    def list_measurements(self) -> list[str]:
        r = self.query("MEASUrement:LIST?").strip().strip('"')
        if not r or r.upper() == "NONE":
            return []
        return [s.strip().strip('"') for s in r.split(",")]

    def free_measurement_names(self, count: int) -> list[str]:
        used = {m.upper() for m in self.list_measurements()}
        names, i = [], 1
        while len(names) < count:
            if f"MEAS{i}" not in used:
                names.append(f"MEAS{i}")
            i += 1
        return names

    def delete_all_measurements(self) -> None:
        self.write("MEASUrement:DELETEALL")
        self.pace()

    def delete_measurement(self, name: str) -> None:
        self.write(f'MEASUrement:DELete "{name}"')

    def add_measurement(self, name: str, mtype: str, ch: int, show_stats: bool = True) -> None:
        self.write(f'MEASUrement:ADDNew "{name}"')
        self.write(f"MEASUrement:{name}:TYPe {mtype}")
        self.write(f"MEASUrement:{name}:SOUrce1 CH{ch}")
        if show_stats:
            self.write(f"MEASUrement:{name}:DISPlaystat:ENABle ON")
        self.pace()

    def measurement_results(self, name: str) -> dict:
        base = f"MEASUrement:{name}:RESUlts"
        pop = self.qfloat(f"{base}:ALLAcqs:POPUlation?")
        return {
            "mean": self.qfloat(f"{base}:ALLAcqs:MEAN?"),
            "min": self.qfloat(f"{base}:ALLAcqs:MINimum?"),
            "max": self.qfloat(f"{base}:ALLAcqs:MAXimum?"),
            "population": int(pop) if pop is not None else 0,
            "last": self.qfloat(f"{base}:CURRentacq:MEAN?"),
        }

    # ------------------------------------------------------------------ 커서
    def results_bar(self, on: bool) -> None:
        self.write(f"MAINWindow:RRBDisplaystate {'ON' if on else 'OFF'}")

    def pace(self) -> None:
        """TekScope 가 앞 명령을 처리할 때까지 기다린다(연속 명령 폭주 방지)."""
        self.t.sync()
        time.sleep(0.1)

    def cursor_on(self, on: bool) -> None:
        self.write(f"{CURSOR}:STATE {'ON' if on else 'OFF'}")

    def cursor_is_on(self) -> bool:
        return self.query(f"{CURSOR}:STATE?") not in ("0", "OFF")

    def cursor_waveform(self, ch: int, ta: float, tb: float) -> None:
        """파형 커서: 지정 시간의 CHx 전압을 화면에 읽어준다."""
        self.cursor_on(True)
        self.write(f"{CURSOR}:FUNCtion WAVEFORM")
        self.write(f"{CURSOR}:SPLITMODE SAME")
        self.write(f"{CURSOR}:ASOUrce CH{ch}")
        self.write(f"{CURSOR}:BSOUrce CH{ch}")
        self.write(f"{CURSOR}:MODe INDEPENDENT")
        self.write(f"{CURSOR}:WAVEform:APOSition {ta:.6E}")
        self.write(f"{CURSOR}:WAVEform:BPOSition {tb:.6E}")
        self.pace()

    def cursor_hbars(self, ch: int, va: float, vb: float) -> None:
        """수평 커서: 지정 전압 위치에 가로선."""
        self.cursor_on(True)
        self.write(f"{CURSOR}:FUNCtion HBArs")
        self.write(f"{CURSOR}:ASOUrce CH{ch}")
        self.write(f"{CURSOR}:BSOUrce CH{ch}")
        self.write(f"{CURSOR}:MODe INDEPENDENT")
        self.write(f"{CURSOR}:HBArs:APOSition {va:.6E}")
        self.write(f"{CURSOR}:HBArs:BPOSition {vb:.6E}")
        self.pace()

    # ------------------------------------------------------------------ 파일
    def _fetch_file(self, fname: str, timeout: float) -> bytes:
        path = f"{self.temp_dir}/{fname}"
        self.write(f'FILESystem:READFile "{path}"')
        with self.t.temp_timeout(timeout):
            data = self.t.read_file(None)
            self.t.sync()
        # MSO58B 는 파일 뒤에 줄바꿈 1바이트를 붙여 보낸다(실장비 확인: 항상 파일 크기 +1)
        if data.endswith(b"\n"):
            data = data[:-1]
        self.write(f'FILESystem:DELEte "{path}"')
        self.check(f"파일 전송({fname})")
        if not data:
            raise ScopeError(f"{fname}: 받은 데이터가 없습니다.")
        return data

    def _save_on_scope(self, cmd: str, context: str, timeout: float) -> None:
        self.write(cmd)
        with self.t.temp_timeout(timeout):
            self.t.sync()
        self.check(context)

    def screenshot(self, ink_saver: bool = False, timeout: float = 30) -> bytes:
        """화면 캡처. 원격 SAVe:IMAGe 는 실장비(MSO58B FV 2.24.4)에서 TekScope 화면을 멈추게 해서 쓰지 않는다.
        대신 Act On Event(트리거 시 스코프가 스스로 화면 저장)를 켜고 Single 1회로 저장시킨 뒤 파일을 가져온다.
        Single 이 새로 한 번 획득하므로 캡처 화면은 직전 화면과 다른 획득이다(반복 트리거 신호면 같은 모양)."""
        dest = self.query("FILESystem:CWD?").strip().strip('"').rstrip("/")
        prefix = f"scopetech{time.time_ns() // 1_000_000 % 10**9}"
        self.write(f"SAVe:IMAGe:COMPosition {'INVErted' if ink_saver else 'NORMal'}")
        self.write(f'SAVEONEVent:FILEDest "{dest}"')
        self.write(f'SAVEONEVent:FILEName "{prefix}"')
        self.write("SAVEONEVent:IMAGe:FILEFormat PNG")
        self.write("ACTONEVent:LIMit UNLIMITED")
        self.write("ACTONEVent:TRIGger:ACTION:SAVEIMAGe:STATE ON")
        self.write("ACTONEVent:ENable 1")
        self.pace()
        name = None
        try:
            self.single(timeout)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                names = sorted(n.strip().strip('"') for n in self.query("FILESystem:DIR?").split(",") if prefix in n)
                if names:
                    name = names[-1]
                    break
                time.sleep(0.5)
        finally:
            self.write("ACTONEVent:TRIGger:ACTION:SAVEIMAGe:STATE OFF")
            self.write("ACTONEVent:ENable 0")
            self.pace()
        if not name:
            raise ScopeTimeout("화면 캡처 파일이 만들어지지 않았습니다(트리거/Act On Event 확인).")
        path = f"{dest}/{name}"
        data = b""
        for _ in range(5):  # 파일이 다 써질 때까지(PNG 끝 IEND 확인)
            time.sleep(0.5)
            self.write(f'FILESystem:READFile "{path}"')
            with self.t.temp_timeout(30):
                data = self.t.read_file(None)
                self.t.sync()
            if data.endswith(b"\n"):
                data = data[:-1]
            if data.startswith(b"\x89PNG") and data[-12:-8] == b"\x00\x00\x00\x00" and data[-8:-4] == b"IEND":
                break
        else:
            raise ScopeError(f"화면 캡처 파일을 완전히 받지 못했습니다: {path}")
        self.write(f'FILESystem:DELEte "{path}"')
        self.check("캡처 파일 삭제", raise_=False)
        return data

    def save_wfm(self, ch: int, timeout: float = 60) -> bytes:
        fname = f"scope_tech_CH{ch}.wfm"
        self._save_on_scope(f'SAVe:WAVEform CH{ch},"{self.temp_dir}/{fname}"', f"CH{ch} .wfm 저장", timeout)
        return self._fetch_file(fname, timeout)

    def save_setup(self) -> bytes:
        fname = "scope_tech_setup.set"
        self._save_on_scope(f'SAVe:SETUp "{self.temp_dir}/{fname}"', "설정 저장", 30)
        return self._fetch_file(fname, 30)

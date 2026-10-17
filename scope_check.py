"""스코프 연결/명령 사전 점검 도구.

본 측정(scope.py) 전에 단계별로 통신, 파일 저장, 파일 전송, 파형 전송, 측정/커서 명령을 확인하고
결과를 logs/check_날짜_시각.txt 로 남긴다. 문제가 생기면 이 파일과 logs/scpi_날짜.log 를 보내주세요.

    python scope_check.py            전체 점검
    python scope_check.py --sim      시뮬레이터로 점검 흐름 확인
    python scope_check.py --from 5   5단계부터
"""
from __future__ import annotations

import argparse
import ctypes
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scope import make_logger, open_transport  # noqa: E402
from scopetech.config import Config  # noqa: E402
from scopetech.transport import (  # noqa: E402
    VI_ERROR_TMO, VI_SUCCESS_MAX_CNT, ScopeError, StreamTransport, VisaTransport,
)

CURSOR = "DISplay:WAVEView1:CURSor:CURSOR1"


class Checker:
    def __init__(self, t, temp_dir: str, report):
        self.t = t
        self.temp_dir = temp_dir.rstrip("/")
        self.report = report
        self.orig_cwd = ""

    # ---------------------------------------------------------------- 기본 도구
    def out(self, text: str = "") -> None:
        print(text)
        self.report.write(text + "\n")
        self.report.flush()

    def q(self, cmd: str, timeout: float | None = None) -> str:
        t0 = time.monotonic()
        if timeout:
            with self.t.temp_timeout(timeout):
                r = self.t.query(cmd)
        else:
            r = self.t.query(cmd)
        self.out(f"    {cmd:<45} -> {r[:150]!s}   ({time.monotonic() - t0:.2f}s)")
        return r

    def w(self, cmd: str) -> None:
        self.t.write(cmd)
        self.out(f"    {cmd}")

    def errors(self) -> str | None:
        esr = int(float(self.t.query("*ESR?")))
        if esr & 0x3C:
            ev = self.t.query("ALLEv?")
            self.out(f"    [스코프 오류] ESR={esr}  {ev}")
            return ev
        return None

    def wait_opc(self, what: str, timeout: float) -> float:
        """*OPC? 로 완료를 기다린다. 걸린 시간 반환."""
        t0 = time.monotonic()
        with self.t.temp_timeout(timeout):
            r = self.t.query("*OPC?")
        dt = time.monotonic() - t0
        self.out(f"    {what} 완료 대기(*OPC?) -> {r}  ({dt:.2f}s)")
        return dt

    def raw_read(self, timeout: float) -> tuple[bytes, str]:
        """응답 원시 바이트를 timeout 까지 모두 읽는다. (데이터, 종료 방식)"""
        if isinstance(self.t, VisaTransport):
            lib, buf, ret = self.t.lib, self.t._readbuf, ctypes.c_uint32()
            self.t._apply_timeout(timeout)
            chunks, how = [], "?"
            try:
                while True:
                    st = lib.viRead(self.t.vi, buf, len(buf), ctypes.byref(ret))
                    if st == VI_ERROR_TMO:
                        how = "시간초과(END 없음)"
                        break
                    if st < 0:
                        how = f"오류 {st & 0xFFFFFFFF:#x}"
                        break
                    chunks.append(ctypes.string_at(buf, ret.value))
                    if st != VI_SUCCESS_MAX_CNT:
                        how = "END 수신(정상)"
                        break
            finally:
                self.t._apply_timeout(self.t.timeout)
            return b"".join(chunks), how
        assert isinstance(self.t, StreamTransport)
        data = self.t._read_until_idle(timeout)
        return data, "수신 멈춤까지 읽음(소켓)"

    def file_size(self, fname: str) -> int | None:
        self.w(f'FILESystem:CWD "{self.temp_dir}"')
        listing = self.q("FILESystem:LDIR?")
        if self.orig_cwd:
            self.w(f'FILESystem:CWD "{self.orig_cwd}"')
        m = re.search(re.escape(fname) + r";FILE;(\d+)", listing, re.IGNORECASE)
        size = int(m.group(1)) if m else None
        self.out(f"    LDIR 에서 읽은 {fname} 크기: {size if size is not None else '찾지 못함'}")
        return size

    def fetch(self, fname: str, timeout: float) -> bytes:
        size = self.file_size(fname)
        path = f"{self.temp_dir}/{fname}"
        self.w(f'FILESystem:READFile "{path}"')
        t0 = time.monotonic()
        data, how = self.raw_read(timeout)
        self.out(f"    READFile 수신 {len(data)} bytes, {how}, {time.monotonic() - t0:.2f}s"
                 + (f"  (LDIR 크기와 차이 {len(data) - size:+d})" if size is not None else ""))
        if data[-1:] == b"\n":
            self.out("    마지막 바이트가 줄바꿈(\\n)입니다.")
        self.w(f'FILESystem:DELEte "{path}"')
        self.errors()
        return data

    # ---------------------------------------------------------------- 단계
    def s1_identify(self):
        """연결 확인 (*IDN?)"""
        self.w("*CLS")
        self.w("HEADer OFF")
        self.w("VERBose ON")
        idn = self.q("*IDN?")
        self.q("*OPT?")
        if "MSO5" not in idn.upper():
            self.out("    [주의] MSO 5 시리즈가 아닙니다.")
        self.errors()

    def s2_state(self):
        """기본 상태 조회 (스코프 변경 없음)"""
        for c in ("ACQuire:STATE?", "ACQuire:STOPAfter?", "ACQuire:MODe?", "ACQuire:NUMACq?",
                  "HORizontal:RECOrdlength?", "HORizontal:MODe?", "TRIGger:A:MODe?", "MEASUrement:LIST?"):
            self.q(c)
        on = [ch for ch in range(1, 9) if self.q(f"DISplay:GLObal:CH{ch}:STATE?") not in ("0", "OFF")]
        self.out(f"    화면에 켜진 채널: {on}")
        self.errors()

    def s3_filesystem(self):
        """파일 시스템: 현재 폴더, 홈 폴더, 임시 폴더 생성"""
        self.orig_cwd = self.q("FILESystem:CWD?").strip().strip('"')
        self.q("FILESystem:HOMEDir?")
        self.w(f'FILESystem:MKDir "{self.temp_dir}"')
        self.q("*OPC?")
        self.errors()  # 이미 있으면 오류가 정상
        self.w(f'FILESystem:CWD "{self.temp_dir}"')
        if self.errors():
            raise ScopeError(f"임시 폴더 {self.temp_dir} 에 들어갈 수 없습니다. config.ini [scope] temp_dir 을 바꾸세요.")
        self.q("FILESystem:LDIR?")
        if self.orig_cwd:
            self.w(f'FILESystem:CWD "{self.orig_cwd}"')

    def s4_small_file(self):
        """작은 파일 왕복: 설정 저장(SAVe:SETUp) -> PC 로 전송(READFile)"""
        fname = "check_setup.set"
        self.w(f'SAVe:SETUp "{self.temp_dir}/{fname}"')
        self.wait_opc("설정 저장", 30)
        self.errors()
        data = self.fetch(fname, 15)
        if not data:
            raise ScopeError("READFile 응답이 없습니다.")
        self.q("*OPC?", timeout=5)  # 남은 바이트가 있으면 여기서 드러남

    def s5_screenshot(self):
        """화면 캡처 저장(SAVe:IMAGe) - 저장만, 전송 없음"""
        self.q("ACQuire:STATE?")
        self.w("SAVe:IMAGe:COMPosition INVErted")
        fname = "check_screen.png"
        self.w(f'SAVe:IMAGe "{self.temp_dir}/{fname}"')
        dt = self.wait_opc("화면 저장", 60)
        self.errors()
        if dt > 10:
            self.out(f"    [주의] 화면 저장에 {dt:.0f}초 걸렸습니다.")
        self.file_size(fname)

    def s6_screenshot_transfer(self):
        """화면 캡처 파일 PC 로 전송(READFile)"""
        fname = "check_screen.png"
        if self.file_size(fname) is None:
            self.w(f'SAVe:IMAGe "{self.temp_dir}/{fname}"')
            self.wait_opc("화면 저장", 60)
        data = self.fetch(fname, 30)
        self.out(f"    PNG 형식: {'정상' if data.startswith(bytes([0x89]) + b'PNG') else '아님'}")
        out = HERE / "logs" / "check_screen.png"
        out.write_bytes(data)
        self.out(f"    저장: {out}")
        self.q("*OPC?", timeout=5)

    def s7_stop_screenshot(self):
        """획득 정지(STOP) 후 화면 캡처 - capture 명령과 같은 순서"""
        self.w("ACQuire:STATE STOP")
        self.wait_opc("정지", 10)
        self.q("ACQuire:STATE?")
        fname = "check_screen2.png"
        self.w(f'SAVe:IMAGe "{self.temp_dir}/{fname}"')
        self.wait_opc("화면 저장", 60)
        self.errors()
        self.fetch(fname, 30)
        self.q("*OPC?", timeout=5)

    def s8_curve(self):
        """파형 데이터 전송(CURVe?) - CH1 앞부분 1000 포인트, 이후 전체"""
        self.w("DATa:SOUrce CH1")
        self.w("DATa:ENCdg SRIbinary")
        self.w("WFMOutpre:BYT_Nr 2")
        self.w("DATa:STARt 1")
        self.w("DATa:STOP 1000")
        for k in ("NR_Pt", "XINcr", "XZEro", "PT_Off", "YMUlt", "YOFf", "YZEro"):
            self.q(f"WFMOutpre:{k}?")
        self.w("CURVe?")
        data, how = self.raw_read(15)
        self.out(f"    CURVe?(1000pt) 수신 {len(data)} bytes, {how}, 앞부분 {data[:12]!r}")
        reclen = int(float(self.q("HORizontal:RECOrdlength?")))
        self.w(f"DATa:STOP {reclen}")
        self.q("WFMOutpre:NR_Pt?")
        self.w("CURVe?")
        t0 = time.monotonic()
        data, how = self.raw_read(10 + 2 * reclen / 1e6)
        dt = time.monotonic() - t0
        self.out(f"    CURVe?(전체 {reclen}pt) 수신 {len(data)} bytes, {how}, {dt:.2f}s"
                 + (f", {len(data) / dt / 1e6:.1f} MB/s" if dt > 0 else ""))
        self.errors()

    def s9_measure_cursor(self):
        """측정/커서 명령 (만든 뒤 삭제)"""
        self.w('MEASUrement:ADDNew "MEAS8"')
        self.w("MEASUrement:MEAS8:TYPe TOP")
        self.w("MEASUrement:MEAS8:SOUrce1 CH1")
        self.w("MEASUrement:MEAS8:DISPlaystat:ENABle ON")
        self.errors()
        time.sleep(1)
        for k in ("ALLAcqs:MEAN", "ALLAcqs:MINimum", "ALLAcqs:MAXimum", "ALLAcqs:POPUlation", "CURRentacq:MEAN"):
            self.q(f"MEASUrement:MEAS8:RESUlts:{k}?")
        self.w('MEASUrement:DELete "MEAS8"')
        cur = self.q(f"{CURSOR}:STATE?")
        self.w(f"{CURSOR}:STATE ON")
        self.w(f"{CURSOR}:FUNCtion WAVEFORM")
        self.w(f"{CURSOR}:ASOUrce CH1")
        self.w(f"{CURSOR}:BSOUrce CH1")
        self.w(f"{CURSOR}:WAVEform:APOSition 0")
        self.q(f"{CURSOR}:WAVEform:APOSition?")
        self.w(f"{CURSOR}:STATE {'ON' if cur not in ('0', 'OFF') else 'OFF'}")
        self.errors()


STEPS = [Checker.s1_identify, Checker.s2_state, Checker.s3_filesystem, Checker.s4_small_file,
         Checker.s5_screenshot, Checker.s6_screenshot_transfer, Checker.s7_stop_screenshot,
         Checker.s8_curve, Checker.s9_measure_cursor]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="스코프 사전 점검")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--from", dest="start", type=int, default=1, help="시작 단계 번호")
    ap.add_argument("--yes", action="store_true", help="단계마다 묻지 않고 진행")
    args = ap.parse_args(argv)

    cfg = Config(args.config)
    log_dir = HERE / cfg.get("output", "log_dir")
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"check_{datetime.now():%Y%m%d_%H%M%S}.txt"
    report = path.open("w", encoding="utf-8")
    logger = make_logger(log_dir, False)

    print(f"점검 결과 파일: {path}")
    report.write(f"scope_check {datetime.now():%Y-%m-%d %H:%M:%S}  interface={cfg.get('connection', 'interface')}"
                 f"  timeout_s={cfg.get('connection', 'timeout_s')}  python={sys.version.split()[0]}\n")
    try:
        t = open_transport(cfg, args.sim, logger)
    except Exception as e:
        print(f"[연결 실패] {e}")
        report.write(f"[연결 실패] {e}\n")
        return 1
    ck = Checker(t, cfg.get("scope", "temp_dir"), report)
    ck.out(f"연결: {t.description}")

    failed = []
    for n, step in enumerate(STEPS, 1):
        if n < args.start:
            continue
        title = step.__doc__.strip().splitlines()[0]
        ck.out(f"\n[{n}/{len(STEPS)}] {title}")
        if not args.yes:
            a = input("    Enter=진행, s=건너뛰기, q=끝내기 > ").strip().lower()
            if a == "q":
                break
            if a == "s":
                ck.out("    건너뜀")
                continue
        t0 = time.monotonic()
        try:
            step(ck)
            ck.out(f"  => 통과 ({time.monotonic() - t0:.1f}s)")
        except KeyboardInterrupt:
            ck.out("  => 사용자가 중단")
            failed.append(n)
            break
        except Exception as e:
            ck.out(f"  => 실패: {type(e).__name__}: {e}")
            report.write(traceback.format_exc())
            failed.append(n)
            try:
                t.recover()
                t.write("*CLS")
                ck.q("*IDN?", timeout=5)
                ck.out("    통신 복구됨, 다음 단계 진행 가능")
            except Exception as e2:
                ck.out(f"    통신 복구 실패({e2}). 스코프 화면이 응답하는지 확인 후 다시 실행하세요"
                       f" (예: python scope_check.py --from {n + 1}).")
                break
    ck.out("\n" + ("모든 단계 통과" if not failed else f"실패한 단계: {failed}"))
    ck.out(f"이 파일과 {log_dir / ('scpi_' + datetime.now().strftime('%Y%m%d') + '.log')} 를 보내주세요.")
    t.close()
    report.close()
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())

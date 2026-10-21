"""화면 캡처 전용 시험 도구 (원인 분리용).

SAVe:IMAGe 직후 TekScope 가 멈추는 원인을 찾기 위해, 캡처를 가장 단순한 형태부터
한 가지 조건만 바꿔 가며 시험한다. 각 시험 뒤 스코프 화면이 반응하는지 직접 확인해 입력한다.
결과: logs/capture_test_날짜_시각.txt

    python capture_test.py          시험 목록에서 골라 실행
    python capture_test.py 2        2번 시험만 실행
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scope import make_logger, open_transport  # noqa: E402
from scopetech.config import Config  # noqa: E402

# (이름, 설명, 저장 경로(None=TekScope 기본 폴더), *OPC? 사용, PC 로 전송)
TESTS = [
    ("기본폴더_대기만", "기본 폴더에 저장, *OPC? 없이 5초 대기, 전송 안 함", None, False, False),
    ("기본폴더_OPC", "기본 폴더에 저장, *OPC? 로 완료 대기, 전송 안 함", None, True, False),
    ("임시폴더_대기만", "C:/scope_tech_tmp 에 저장, *OPC? 없이 5초 대기, 전송 안 함", "C:/scope_tech_tmp", False, False),
    ("기본폴더_전송", "기본 폴더에 저장, 5초 대기 후 PC 로 전송(READFile), 삭제 안 함", None, False, True),
    ("트리거저장_AOE", "SAVe:IMAGe 를 쓰지 않고 Act On Event(트리거 시 스코프가 스스로 화면 저장) + Single", "AOE", False, True),
]


class Session:
    def __init__(self, t, report):
        self.t = t
        self.report = report

    def out(self, text: str = "") -> None:
        print(text)
        self.report.write(text + "\n")
        self.report.flush()

    def q(self, cmd: str, timeout: float = 5) -> str:
        t0 = time.monotonic()
        with self.t.temp_timeout(timeout):
            r = self.t.query(cmd)
        self.out(f"    {cmd:<40} -> {r[:100]}   ({time.monotonic() - t0:.2f}s)")
        return r

    def w(self, cmd: str) -> None:
        self.t.write(cmd)
        self.out(f"    {cmd}")

    def alive(self) -> bool:
        """원격 응답 확인 + 사용자에게 스코프 화면 반응 여부 확인."""
        try:
            self.q("*IDN?", timeout=5)
            remote = True
        except Exception as e:  # noqa: BLE001
            self.out(f"    원격 응답 없음: {e}")
            remote = False
        a = input("    스코프 화면(TekScope)이 터치/노브에 반응하나요? (y/n) > ").strip().lower()
        self.out(f"    화면 반응: {'예' if a == 'y' else '아니오'}, 원격 응답: {'예' if remote else '아니오'}")
        return remote and a == "y"

    def run_aoe(self) -> bool:
        """Act On Event: 트리거가 걸리면 스코프 내부 기능으로 화면 저장 (원격 SAVe:IMAGe 미사용)."""
        self.out("  2) Act On Event 설정")
        cwd = self.q("FILESystem:CWD?").strip().strip('"')
        prefix = f"aoe{datetime.now():%H%M%S}"
        self.w("ACQuire:STATE STOP")
        time.sleep(1)
        self.w(f'SAVEONEVent:FILEDest "{cwd}"')
        self.w(f'SAVEONEVent:FILEName "{prefix}"')
        self.w("SAVEONEVent:IMAGe:FILEFormat PNG")
        self.w("SAVe:IMAGe:COMPosition NORMal")
        self.w("ACTONEVent:LIMit SPECIFY_MAXIMUM")
        self.w("ACTONEVent:LIMITCount 1")
        self.w("ACTONEVent:TRIGger:ACTION:SAVEIMAGe:STATE ON")
        self.w("ACTONEVent:ENable 1")
        self.q("*ESR?")
        self.q("ALLEv?")
        self.out("  3) Single 획득 -> 트리거 시 스코프가 화면 저장")
        self.w("ACQuire:STOPAfter SEQuence")
        self.w("ACQuire:STATE ON")
        t0 = time.monotonic()
        while self.t.query("ACQuire:STATE?") != "0" and time.monotonic() - t0 < 30:
            time.sleep(0.2)
        self.out(f"    Single 완료 {time.monotonic() - t0:.1f}s, 저장 대기 5초")
        time.sleep(5)
        self.w("ACTONEVent:TRIGger:ACTION:SAVEIMAGe:STATE OFF")
        self.w("ACTONEVent:ENable 0")
        self.out("  4) 저장 직후 상태")
        if not self.alive():
            return False
        self.out("  5) 저장된 파일 찾기(DIR?) 및 전송")
        listing = self.q("FILESystem:DIR?", timeout=10)
        names = sorted(n.strip().strip('"') for n in listing.split(",") if prefix in n)
        self.out(f"    찾은 파일: {names}")
        if not names:
            self.out("    저장된 파일이 없습니다.")
            return False
        self.w(f'FILESystem:READFile "{cwd}/{names[-1]}"')
        with self.t.temp_timeout(30):
            data = self.t.read_file(None)
        if data.endswith(b"\n"):
            data = data[:-1]
        out = HERE / "logs" / names[-1]
        out.write_bytes(data)
        self.out(f"    {len(data)} bytes, PNG {'정상' if data[1:4] == b'PNG' else '아님'} -> {out}")
        self.out("  6) 전송 후 상태")
        return self.alive()

    def run(self, name: str, desc: str, folder: str | None, use_opc: bool, transfer: bool) -> bool:
        self.out(f"\n=== 시험 [{name}] {desc}")
        if folder == "AOE":
            self.out("  1) 시작 전 상태")
            if not self.alive():
                self.out("  시작 전부터 멈춰 있습니다. TekScope 를 재시작한 뒤 다시 실행하세요.")
                return False
            ok = self.run_aoe()
            self.out(f"  => {'정상' if ok else '멈춤/실패'}")
            return ok
        self.out("  1) 시작 전 상태")
        if not self.alive():
            self.out("  시작 전부터 멈춰 있습니다. TekScope 를 재시작한 뒤 다시 실행하세요.")
            return False
        self.out("  2) 화면 고정 (Stop)")
        if self.q("ACQuire:STATE?") != "0":
            self.w("ACQuire:STATE STOP")
            time.sleep(2)
            self.q("ACQuire:STATE?")
        self.w("SAVe:IMAGe:COMPosition NORMal")
        time.sleep(1)
        fname = f"cap_{datetime.now():%H%M%S}.png"
        path = f"{folder}/{fname}" if folder else fname
        self.out("  3) 화면 저장")
        t0 = time.monotonic()
        self.w(f'SAVe:IMAGe "{path}"')
        if use_opc:
            self.q("*OPC?", timeout=60)
        else:
            time.sleep(5)
        self.out(f"    저장 명령 후 {time.monotonic() - t0:.1f}s")
        self.out("  4) 저장 직후 상태")
        ok = self.alive()
        if ok and transfer:
            self.out("  5) PC 로 전송")
            self.w(f'FILESystem:READFile "{path}"')
            with self.t.temp_timeout(30):
                data = self.t.read_file(None)
            if data.endswith(b"\n"):
                data = data[:-1]
            out = HERE / "logs" / fname
            out.write_bytes(data)
            self.out(f"    {len(data)} bytes, PNG {'정상' if data[1:4] == b'PNG' else '아님'} -> {out}")
            self.out("  6) 전송 후 상태")
            ok = self.alive()
        self.out(f"  => {'정상' if ok else '멈춤'}")
        return ok


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cfg = Config(HERE / "config.ini")
    log_dir = HERE / cfg.get("output", "log_dir")
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"capture_test_{datetime.now():%Y%m%d_%H%M%S}.txt"
    report = path.open("w", encoding="utf-8")
    t = open_transport(cfg, "--sim" in argv, make_logger(log_dir, False))
    s = Session(t, report)
    s.out(f"결과 파일: {path}")
    s.out("먼저 프로그램 없이 확인: 스코프 메뉴 File > Save As > Screen Capture 로 직접 저장했을 때도 멈추나요?")
    pick = [a for a in argv if a.isdigit()]
    if not pick:
        for i, (name, desc, *_ ) in enumerate(TESTS, 1):
            print(f"  {i}. {name}: {desc}")
        pick = [input("시험 번호 (멈추면 TekScope 재시작 후 다음 번호로) > ").strip()]
    for p in pick:
        name, desc, folder, use_opc, transfer = TESTS[int(p) - 1]
        s.run(name, desc, folder, use_opc, transfer)
    s.out(f"\n이 파일을 올려주세요: {path}")
    t.close()
    report.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

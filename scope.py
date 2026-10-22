"""Tektronix MSO58B 파형 저장 / VIL·VIH·VOH·VOL 측정 도우미.

    python scope.py                 대화형 실행
    python scope.py --sim           스코프 없이 시뮬레이터로 연습
    python scope.py all vih 1 2     명령 하나만 실행하고 종료
"""
from __future__ import annotations

import argparse
import os
import sys
import signal
import threading
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scopetech.config import Config  # noqa: E402
from scopetech.mso import MSO5B  # noqa: E402
from scopetech.shell import ScopeShell  # noqa: E402
from scopetech.transport import ScopeError, SocketTransport, VisaTransport  # noqa: E402
from scopetech.workflow import Bench  # noqa: E402


def make_logger(log_dir: Path, echo: bool):
    log_dir.mkdir(parents=True, exist_ok=True)
    f = (log_dir / f"scpi_{datetime.now():%Y%m%d}.log").open("a", encoding="utf-8")
    f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 시작 =====\n")

    def log(direction: str, text: str) -> None:
        line = f"{datetime.now():%H:%M:%S.%f}"[:-3] + f" {direction} {text[:300]}"
        f.write(line + "\n")
        f.flush()
        if echo:
            print("   " + line)

    log.close = f.close
    return log


def open_transport(cfg: Config, sim: bool, logger):
    timeout = cfg.getfloat("connection", "timeout_s")
    if sim:
        from scopetech.sim import SimTransport

        return SimTransport(timeout=timeout, logger=logger)
    iface = cfg.get("connection", "interface").lower()
    if iface == "lan":
        return SocketTransport(cfg.get("connection", "ip"), cfg.getint("connection", "port"), timeout, logger)
    if iface == "usb":
        return _open_usb(cfg, timeout, logger)
    raise ScopeError(f"config.ini 의 interface 값이 잘못되었습니다: {iface} (usb 또는 lan)")


USB_HANG_HELP = """
[USB 응답 없음] '{stage}' 단계에서 {secs:.0f}초 동안 진행되지 않아 프로그램을 끝냅니다.
스코프 쪽 USB 통신(TekScope 프로그램)이 멈춘 상태입니다. 순서대로 해 주세요:
  1) 작업 관리자에서 남은 python.exe / py.exe 종료
  2) 스코프에서 TekScope 프로그램 재시작 (안 되면 스코프 재부팅)
  3) 스코프 뒷면 USB 케이블을 뽑고 10초 뒤 다시 연결
  4) 다시 실행
"""


def _open_usb(cfg: Config, timeout: float, logger):
    """USB 연결. VISA 호출이 스코프 응답을 기다리며 끝없이 막히는 경우가 있어 별도 스레드에서 열고,
    제한 시간 안에 끝나지 않으면 멈춘 단계와 조치 방법을 출력하고 종료한다."""
    box: dict = {}
    stage = ["시작"]

    def progress(text: str) -> None:
        stage[0] = text
        print(f"  USB 연결: {text}", flush=True)
        if logger:
            logger("#", text)

    def work() -> None:
        try:
            box["t"] = VisaTransport(cfg.get("connection", "usb_resource"), cfg.get("connection", "visa_library"),
                                     timeout, logger, progress, cfg.getbool("connection", "usb_clear"))
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    limit = max(20.0, timeout * 2)
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(limit)
    if th.is_alive():
        print(USB_HANG_HELP.format(stage=stage[0], secs=limit), flush=True)
        if logger:
            logger("#", f"USB 응답 없음: {stage[0]} 단계에서 {limit:.0f}초")
        os._exit(3)  # 막힌 VISA 호출은 취소할 수 없어 프로세스를 바로 끝낸다
    if "e" in box:
        raise box["e"]
    return box["t"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tektronix MSO58B VIL/VIH/VOH/VOL 측정 도우미")
    ap.add_argument("--config", default=str(HERE / "config.ini"), help="설정 파일 (기본: config.ini)")
    ap.add_argument("--sim", action="store_true", help="스코프 없이 시뮬레이터로 실행")
    ap.add_argument("--shell", action="store_true", help="기존 명령어 셸로 실행")
    ap.add_argument("--fetch", action="store_true", help="스코프에 저장해 둔 캡처 PNG 만 가져오고 종료")
    ap.add_argument("--no-capture", action="store_true", help="화면 캡처 없이 측정·파형 저장만 (run_nocap.bat)")
    ap.add_argument("--plan", nargs="?", const="", metavar="CSV", help="시험 계획 진행 (기본: config.ini [plan] file, run_plan.bat)")
    ap.add_argument("--no-gui", action="store_true", help="시험 계획 진행 때 결과·설정 창(GUI)을 띄우지 않음")
    ap.add_argument("--debug", action="store_true", help="SCPI 송수신을 화면에 출력")
    ap.add_argument("--temp", help="온도 (예: 85)")
    ap.add_argument("--name", help="시험 이름 (예: INV_3V3)")
    ap.add_argument("--spec", help="판정 기준 프로파일 (config.ini 의 [spec:이름])")
    ap.add_argument("--type", choices=["pp", "od", "PP", "OD"], help="출력 타입")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="한 번만 실행할 명령 (예: all vih 1 2)")
    args = ap.parse_args(argv)

    cfg = Config(args.config)
    if args.no_capture:
        cfg.cp.set("capture", "enabled", "no")
    out_dir = HERE / cfg.get("output", "sim_dir" if args.sim else "base_dir")
    logger = make_logger(HERE / cfg.get("output", "log_dir"), args.debug)

    transport = None
    scope = None
    status = 1
    previous_break = None
    if hasattr(signal, "SIGBREAK"):
        previous_break = signal.signal(signal.SIGBREAK, signal.default_int_handler)
    try:
        transport = open_transport(cfg, args.sim, logger)
        scope = MSO5B(
            transport,
            temp_dir=cfg.get("scope", "temp_dir"),
            settle_s=cfg.getfloat("capture", "settle_s"),
            num_channels=cfg.getint("scope", "num_channels"),
        )
        idn = scope.connect()
        print(f"연결됨: {idn}  ({transport.description})")
        if not cfg.getbool("capture", "enabled"):
            print("화면 캡처 끔: 측정·커서 표시·파형 저장만 합니다.")
        if "MSO5" not in idn.upper() and not args.sim:
            print("  [주의] MSO 5 시리즈가 아닌 것 같습니다. 명령이 맞지 않을 수 있습니다.")
    
        bench = Bench(scope, cfg, out_dir)
        if args.temp:
            bench.temp = args.temp
        if args.name:
            bench.name = args.name
        if args.spec:
            bench.spec_name = args.spec
        if args.type:
            bench.out_type = args.type.upper()
    
        bench.explicit_options = {key for key, value in (("temp", args.temp), ("name", args.name), ("out_type", args.type)) if value}
        if args.fetch:
            got, left = bench.fetch_pending()
            print(f"캡처 파일 가져옴 {got}개, 남음 {left}개")
            status = 0 if not left else 1
        elif args.command:
            ScopeShell(bench).onecmd(" ".join(args.command))
            status = 0
        elif args.shell:
            ScopeShell(bench).cmdloop()
            status = 0
        elif args.plan is not None:
            from scopetech.plan import PlanRunner, load_layout, load_plan
            rows = load_plan(HERE / (args.plan or cfg.get("plan", "file")), cfg.getint("scope", "num_channels"))
            runner = PlanRunner(bench, rows, load_layout(HERE / cfg.get("plan", "summary")))
            if not args.no_gui and cfg.getbool("plan", "gui"):
                from scopetech.plan_view import launch_gui
                launch_gui(HERE / "plan_gui.py", Path(args.config).resolve(), out_dir, runner.store.dir)
            status = runner.run()
        else:
            from scopetech.wizard import Wizard
            state_path = HERE / (".session_sim.json" if args.sim else ".session.json")
            status = Wizard(bench, state_path).run()
    except (KeyboardInterrupt, EOFError):
        print("\n[중단] 작업을 취소했습니다. 저장 중이었다면 결과가 불완전할 수 있습니다.")
        status = 130
    except (ScopeError, OSError, ValueError, KeyError) as e:
        print(f"[오류] {e}")
        status = 1
    finally:
        try:
            if scope is not None and (status != 0 or scope.aoe_active):
                errors = scope.abort_capture()
                logger("!", "종료 정리 요청" + (": " + "; ".join(errors) if errors else ""))
                print("획득 정지·필요한 이벤트 해제를 요청했습니다. TekScope 화면 상태를 확인해주세요.")
                if errors:
                    print("[경고] 종료 정리 일부 실패: " + "; ".join(errors))
        except Exception as e:
            logger("!", f"종료 정리 실패: {e}")
        finally:
            try:
                if transport is not None:
                    transport.close()
            finally:
                logger.close()
                if previous_break is not None:
                    signal.signal(signal.SIGBREAK, previous_break)
    return status


if __name__ == "__main__":
    sys.exit(main())


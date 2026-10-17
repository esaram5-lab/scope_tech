"""Tektronix MSO58B 파형 저장 / VIL·VIH·VOH·VOL 측정 도우미.

    python scope.py                 대화형 실행
    python scope.py --sim           스코프 없이 시뮬레이터로 연습
    python scope.py all vih 1 2     명령 하나만 실행하고 종료
"""
from __future__ import annotations

import argparse
import sys
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
        return VisaTransport(cfg.get("connection", "usb_resource"), cfg.get("connection", "visa_library"), timeout, logger)
    raise ScopeError(f"config.ini 의 interface 값이 잘못되었습니다: {iface} (usb 또는 lan)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tektronix MSO58B VIL/VIH/VOH/VOL 측정 도우미")
    ap.add_argument("--config", default=str(HERE / "config.ini"), help="설정 파일 (기본: config.ini)")
    ap.add_argument("--sim", action="store_true", help="스코프 없이 시뮬레이터로 실행")
    ap.add_argument("--debug", action="store_true", help="SCPI 송수신을 화면에 출력")
    ap.add_argument("--temp", help="온도 (예: 85)")
    ap.add_argument("--name", help="시험 이름 (예: INV_3V3)")
    ap.add_argument("--spec", help="판정 기준 프로파일 (config.ini 의 [spec:이름])")
    ap.add_argument("--type", choices=["pp", "od", "PP", "OD"], help="출력 타입")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="한 번만 실행할 명령 (예: all vih 1 2)")
    args = ap.parse_args(argv)

    cfg = Config(args.config)
    out_dir = HERE / cfg.get("output", "sim_dir" if args.sim else "base_dir")
    logger = make_logger(HERE / cfg.get("output", "log_dir"), args.debug)

    try:
        transport = open_transport(cfg, args.sim, logger)
        scope = MSO5B(
            transport,
            temp_dir=cfg.get("scope", "temp_dir"),
            settle_s=cfg.getfloat("capture", "settle_s"),
            num_channels=cfg.getint("scope", "num_channels"),
        )
        idn = scope.connect()
    except ScopeError as e:
        print(f"[연결 실패] {e}")
        return 1
    print(f"연결됨: {idn}  ({transport.description})")
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

    shell = ScopeShell(bench)
    try:
        if args.command:
            shell.onecmd(" ".join(args.command))
        else:
            shell.cmdloop()
    except KeyboardInterrupt:
        print()
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Keysight N6705C 직접 다루기 (스코프 연결 불필요). 연결 설정: config.ini [supply].

    python psu.py check             장비·옵션·모듈·채널별 기능(ARB 가능 여부) 확인 -> logs/psu_check_*.txt (run_psu_check.bat)
    python psu.py arb 0 3.3         ARB 채널에서 사다리꼴(구간 [supply] arb_*_ms) 켜기, Enter 로 끔
    python psu.py off               ARB 채널 출력 끄기 (--ch 2 처럼 채널 지정)
    python psu.py measure           모든 채널 출력 상태·설정·측정 전압/전류
    --sim                           시뮬레이터로 연습
API 는 scopetech/n6705c.py (N6705C, ArbSource).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scopetech.config import Config  # noqa: E402
from scopetech.n6705c import ArbSource, open_psu  # noqa: E402
from scopetech.transport import ScopeError  # noqa: E402


def main(argv: list[str] | None = None, ask=input) -> int:
    ap = argparse.ArgumentParser(description="Keysight N6705C 확인·제어")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--sim", action="store_true", help="시뮬레이터")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="장비·옵션·모듈·ARB 가능 여부 확인")
    a = sub.add_parser("arb", help="사다리꼴 ARB 켜기 (Enter 로 끔)")
    a.add_argument("low", type=float)
    a.add_argument("high", type=float)
    o = sub.add_parser("off", help="출력 끄기")
    o.add_argument("--ch", type=int, help="채널 (기본: [supply] arb_channel)")
    sub.add_parser("measure", help="채널별 상태·측정값")
    args = ap.parse_args(argv)
    cfg = Config(args.config)
    log_dir = HERE / cfg.get("output", "log_dir")
    try:
        psu = open_psu(cfg, sim=args.sim)
    except (ScopeError, OSError) as e:
        print(f"[오류] N6705C 연결 실패: {e}")
        return 1
    try:
        if args.cmd == "check":
            lines, data = psu.check_report()
            src = ArbSource(psu, cfg)
            warns = src.prepare()
            lines.append(f"ARB 채널 설정: CH{src.channel} ({cfg.get('supply', 'arb_model')} 예상)"
                         + (" -> 정상" if not warns else ""))
            lines += [f"  [주의] {w}" for w in warns]
            data["arb_warnings"] = warns
            print("\n".join(lines))
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / f"psu_check_{datetime.now():%Y%m%d_%H%M%S}.txt"
            path.write_text("\n".join(lines) + "\n\n" + json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"기록: {path}")
            return 0 if not warns else 2
        if args.cmd == "arb":
            src = ArbSource(psu, cfg)
            info = src.start(args.low, args.high)
            print(f"ARB 켬: CH{info['channel']} {args.low:g}~{args.high:g} V 사다리꼴 "
                  f"{'/'.join(f'{t:g}' for t in info['times_ms'])} ms, 전류 제한 {info['current_limit_ma']:g} mA")
            try:
                ask("Enter: 끄기 > ")
            except (EOFError, KeyboardInterrupt):
                pass
            src.stop()
            print("ARB 끔")
            return 0
        if args.cmd == "off":
            ch = args.ch or cfg.getint("supply", "arb_channel")
            psu.output(ch, False)
            print(f"CH{ch} 출력 끔")
            return 0
        for ch, info in psu.channels.items():
            if info.kind == "empty":
                continue
            on = psu.output_state(ch)
            print(f"CH{ch} {info.model:<7} 출력 {'켬' if on else '끔'}  설정 {psu.voltage_setting(ch):g} V / "
                  f"{psu.current_setting(ch):g} A  측정 {psu.measure_voltage(ch):.4f} V / {psu.measure_current(ch):.6g} A")
        return 0
    except (ScopeError, OSError, ValueError) as e:
        print(f"[오류] {e}")
        return 1
    finally:
        psu.close()


if __name__ == "__main__":
    sys.exit(main())

"""시험 계획 결과·설정 창 (스코프 연결 불필요, 측정은 run_plan.bat 의 CLI 에서).

    run_plan.bat 을 실행하면 이 창도 함께 뜬다(--no-gui 로 끔, config.ini [plan] gui = no).
    python plan_gui.py               captures/ 의 계획 진행 기록을 본다 (run_plan_gui.bat)
    python plan_gui.py --sim         시뮬레이터 결과 폴더(captures_sim)
탭: 진행(항목 표·상세·캡처 이미지) / 요약표 / 설정(plan/overrides.ini, 다음 측정부터 반영) / 가져오기(예전 측정)
오류는 logs/plan_gui.log 에 남는다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scopetech.config import Config  # noqa: E402


def alert(text: str) -> None:
    """tkinter 를 못 쓰면 Windows 메시지 상자(pythonw 는 콘솔이 없음), 아니면 화면 출력."""
    print(text)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "시험 계획 창", 0x10)
        except (OSError, AttributeError):
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="시험 계획 결과·설정 창")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--dir", help="결과 폴더 (기본: config.ini 의 base_dir)")
    ap.add_argument("--sim", action="store_true", help="시뮬레이터 결과 폴더(sim_dir)")
    ap.add_argument("--plan", help="계획 파일 (기본: config.ini [plan] file)")
    args = ap.parse_args(argv)
    cfg = Config(args.config)
    base = Path(args.dir) if args.dir else HERE / cfg.get("output", "sim_dir" if args.sim else "base_dir")
    try:
        from scopetech import plan_gui
        from scopetech.plan_view import PlanData
    except ImportError as e:
        alert(f"결과·설정 창을 열 수 없습니다(tkinter 없음): {e}\nPython 설치에서 tcl/tk 를 포함해 다시 설치하세요.")
        return 1
    try:
        data = PlanData(base, args.config, Path(args.plan) if args.plan else None, HERE)
    except (OSError, ValueError) as e:
        alert(f"계획 파일을 읽을 수 없습니다: {e}")
        return 1
    plan_gui.run(data, HERE / cfg.get("output", "log_dir") / "plan_gui.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())

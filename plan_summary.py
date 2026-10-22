"""시험 계획 요약표·진행 기록을 다시 만든다 (스코프 연결 불필요).

    python plan_summary.py          -> captures/plan/plan_summary.csv, plan_progress_온도C.csv
요약표: 행 = test_plan_summary.csv 순서(구분, 핀, 항목, 단위), 열 = 온도(config.ini [plan] temps 순서).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scopetech.config import Config  # noqa: E402
from scopetech.plan import PlanStore, load_layout, load_plan, rebuild  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="시험 계획 요약표 다시 만들기")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--dir", help="결과 폴더 (기본: config.ini 의 base_dir)")
    ap.add_argument("--plan", help="계획 파일 (기본: config.ini [plan] file)")
    args = ap.parse_args(argv)
    cfg = Config(args.config)
    base = Path(args.dir) if args.dir else HERE / cfg.get("output", "base_dir")
    try:
        rows = load_plan(Path(args.plan) if args.plan else HERE / cfg.get("plan", "file"), cfg.getint("scope", "num_channels"))
        layout = load_layout(HERE / cfg.get("plan", "summary"))
    except (OSError, ValueError) as e:
        print(f"[오류] {e}")
        return 1
    store = PlanStore(base, cfg.get("plan", "dir") or "plan")
    if not store.temps():
        print(f"진행 기록이 없습니다: {store.dir}")
        return 1
    temps = [t.strip() for t in cfg.get("plan", "temps").replace(",", " ").split() if t.strip()]
    try:
        summary, progress = rebuild(rows, layout, store, temps)
    except OSError as e:
        print(f"[오류] 쓸 수 없습니다(엑셀에서 열려 있나요?): {e}")
        return 1
    print(f"요약표: {summary}")
    for p in progress:
        print(f"진행 기록: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

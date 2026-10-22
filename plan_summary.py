"""시험 계획 요약표·진행 기록을 다시 만든다 (스코프 연결 불필요).

    python plan_summary.py          -> captures/plan/<샘플>/plan_summary_<샘플>.csv, plan_progress_온도C.csv,
                                       captures/plan/plan_summary_all.csv (샘플 × 온도를 나란히)
요약표: 행 = test_plan_summary.csv 순서(구분, 핀, 항목, 단위), 열 = 온도(config.ini [plan] temps 순서).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scopetech.config import Config  # noqa: E402
from scopetech.plan import PlanStore, load_layout, load_plan, migrate_legacy, rebuild_all  # noqa: E402
from scopetech.samples import Samples  # noqa: E402


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
    temps = [t.strip() for t in cfg.get("plan", "temps").replace(",", " ").split() if t.strip()]
    try:
        for m in migrate_legacy(store, Samples(store.root)):
            print(f"예전 진행 기록 옮김: {m}")
        every, summaries = rebuild_all(rows, layout, store, temps)
    except OSError as e:
        print(f"[오류] 쓸 수 없습니다(엑셀에서 열려 있나요?): {e}")
        return 1
    if not summaries:
        print(f"진행 기록이 없습니다: {store.dir}")
        return 1
    for p in summaries:
        print(f"요약표: {p}")
    print(f"전체 샘플 요약표: {every}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

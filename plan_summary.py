"""시험 계획 요약표·진행 기록을 다시 만든다 (스코프 연결 불필요).

    python plan_summary.py          -> captures/plan/<샘플>/plan_summary_<샘플>.csv, plan_progress_온도C.csv (최악값),
                                       captures/plan/plan_summary_all.csv      (샘플 × 온도를 나란히, 최악값)
                                       captures/plan/plan_summary_all_avg.csv  (같은 표, 평균값)
                                       captures/plan/plan_summary_all_best.csv (같은 표, 최선값)
    python plan_summary.py --value avg,best   만들 값 고르기 (기본: config.ini [plan] summary_values)
요약표: 행 = test_plan_summary.csv 순서(구분, 핀, 항목, 단위), 열 = 온도(config.ini [plan] temps 순서).
최악값: 측정마다 VIH 최대, VIL·VOH 최소, VOL 최대. 최선값: 그 반대(VIH 최소, VIL·VOH 최대, VOL 최소).
평균값: 측정마다 평균(typ). 한 샘플에서 같은 값이 여러 측정에서 나오면 최악값 표는 그중 최악, 최선값 표는 그중 최선,
평균값 표는 평균. IOH/IOL/Rpu/Rpd 와 POR 은 측정 1건에 값이 하나라 세 표가 같다.
최악값 표(plan_summary_all.csv)는 측정·정리 때 항상 다시 만들어지고, 고르지 않아도 지우지 않는다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scopetech.config import Config  # noqa: E402
from scopetech.plan import (MODE_LABEL, MODES, PlanStore, all_temps, load_layout, load_plan, migrate_legacy,  # noqa: E402
                            rebuild_all, summary_all_path, write_summary_all)
from scopetech.samples import Samples  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="시험 계획 요약표 다시 만들기")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--dir", help="결과 폴더 (기본: config.ini 의 base_dir)")
    ap.add_argument("--plan", help="계획 파일 (기본: config.ini [plan] file)")
    ap.add_argument("--value", help="전체 샘플 요약표 값: worst, avg, best 중 쉼표로 (기본: config.ini [plan] summary_values)")
    args = ap.parse_args(argv)
    cfg = Config(args.config)
    text = args.value if args.value is not None else cfg.get("plan", "summary_values", "worst")
    modes = [m.strip().lower() for m in text.replace(",", " ").split()]
    bad = [m for m in modes if m not in MODES]
    if bad or not modes:
        print(f"[오류] 요약표 값은 worst, avg, best 중에서 고르세요: {text!r}")
        return 1
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
        made = {"worst": every}
        stores = [store.of(sid) for sid in store.samples()]
        for m in modes:
            if summaries and m != "worst":
                made[m] = write_summary_all(rows, layout, stores, all_temps(stores, temps), summary_all_path(store, m), m)
    except OSError as e:
        print(f"[오류] 쓸 수 없습니다(엑셀에서 열려 있나요?): {e}")
        return 1
    if not summaries:
        print(f"진행 기록이 없습니다: {store.dir}")
        return 1
    for p in summaries:
        print(f"요약표: {p}")
    for m in MODES:
        if m in modes:
            print(f"전체 샘플 요약표({MODE_LABEL[m]}): {made[m]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""예전 측정 기록을 시험 계획 진행 기록·요약표에 붙인다 (스코프 연결 불필요).

    python plan_import.py                 captures/ 의 VIH / MANUAL / POR 기록을 계획 줄에 연결해 미리 보여주고, y 로 적용
    python plan_import.py --apply         묻지 않고 적용      --dry-run   미리보기만
    python plan_import.py --replace       계획 모드로 이미 잰 줄도 가장 최근 기록으로 바꿈
    python plan_import.py --name INV --since 2026-10-01 --temp 25
같은 줄의 기록이 여럿이면 가장 최근 기록. 예전 VIL/VIH 기록의 출력 VOH/VOL, 계산값이 없는 R_pd/R_pu/IOH 는
저장된 파형으로 채운다(그 result.json 은 고쳐 쓰고 원본은 result.orig.json 으로 남김).
적용하면 plan/progress_온도C.json, plan_summary.csv, plan_progress_온도C.csv 와 가져오기 내역 CSV 를 쓴다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scopetech.config import Config  # noqa: E402
from scopetech.plan import PlanStore, load_layout, load_plan, norm_temp, rebuild  # noqa: E402
from scopetech.plan_import import SOURCE, apply, plan_import, preview, scan, write_log  # noqa: E402


def main(argv: list[str] | None = None, ask=input) -> int:
    ap = argparse.ArgumentParser(description="예전 측정 기록을 시험 계획에 붙이기")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--dir", help="결과 폴더 (기본: config.ini 의 base_dir)")
    ap.add_argument("--plan", help="계획 파일 (기본: config.ini [plan] file)")
    ap.add_argument("--name", help="시험 이름에 이 글자가 들어간 기록만")
    ap.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후 기록만")
    ap.add_argument("--temp", help="이 온도만 (쉼표 구분, 예: 25,85)")
    ap.add_argument("--replace", action="store_true", help="계획 모드로 이미 완료한 줄도 가장 최근 기록으로 바꿈")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="묻지 않고 적용")
    mode.add_argument("--dry-run", action="store_true", help="미리보기만")
    args = ap.parse_args(argv)

    cfg = Config(args.config)
    base = Path(args.dir) if args.dir else HERE / cfg.get("output", "base_dir")
    if not base.is_dir():
        print(f"결과 폴더가 없습니다: {base}")
        return 1
    try:
        rows = load_plan(Path(args.plan) if args.plan else HERE / cfg.get("plan", "file"), cfg.getint("scope", "num_channels"))
        layout = load_layout(HERE / cfg.get("plan", "summary"))
        temps = [norm_temp(t) for t in args.temp.replace(",", " ").split()] if args.temp else None
    except (OSError, ValueError) as e:
        print(f"[오류] {e}")
        return 1
    store = PlanStore(base, cfg.get("plan", "dir") or "plan")
    temps_cfg = [t.strip() for t in cfg.get("plan", "temps").replace(",", " ").split() if t.strip()]
    found = scan(base)
    print(f"가져오기 미리보기 — {base} 의 측정 기록 {len(found)}개, 계획 {len(rows)}개 항목")
    picks, unmatched = plan_import(rows, store, cfg, found, replace=args.replace, name=args.name or "",
                                   since=args.since or "", temps=temps, order=[norm_temp(t) for t in temps_cfg])
    preview(picks, unmatched, rows)
    todo = [p for p in picks if p.state == SOURCE]
    if not todo:
        print("\n새로 가져올 항목이 없습니다.")
        return 0
    rewrite = sum(p.changed for p in todo)
    print(f"\n가져올 항목 {len(todo)}개" + (f" (그중 {rewrite}개는 파형으로 값을 채워 result.json 을 고쳐 씀, 원본은 result.orig.json)"
                                       if rewrite else ""))
    if args.dry_run:
        print("미리보기만 했습니다(--dry-run).")
        return 0
    if not args.apply:
        try:
            if ask("적용할까요? (y/N) > ").strip().lower() != "y":
                print("적용하지 않았습니다.")
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\n적용하지 않았습니다.")
            return 0
    try:
        written = apply(picks, store)
        log = write_log(picks, unmatched, store.dir / f"import_{datetime.now():%Y%m%d_%H%M%S}.csv")
        summary, progress = rebuild(rows, layout, store, temps_cfg)
    except OSError as e:
        print(f"[오류] 쓸 수 없습니다(엑셀에서 열려 있나요?): {e}")
        return 1
    for p in written:
        print(f"진행 기록: {p}")
    print(f"가져오기 내역: {log}\n요약표: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""여러 버전의 측정 기록이 섞인 결과 폴더 정리 (스코프 연결 불필요, run_cleanup.bat).

예전 captures 의 측정 폴더(VIH / MANUAL / POR / VOH / SNAP / MISC)를 최신 captures 에 붙여 넣은 뒤 실행한다.
(plan/, result/, results.csv, _pending_images.json 은 붙여 넣지 않는다 — 여기서 다시 만든다.)

    python cleanup.py              미리보기 -> y 로 적용
    python cleanup.py --apply      묻지 않고 적용      --dry-run   미리보기만
    python cleanup.py --replace    계획 모드로 잰 줄도 같은 샘플의 가장 최근 기록으로 바꿈

하는 일
1. plan/samples.csv: 기록의 시험 이름 -> fab·샘플 대응표에 새 이름 추가(TSMC / VIS 와 번호를 추정, 엑셀로 고쳐서 다시 실행)
2. plan/missing_pins.csv: 핀 정보 없는 예전 기록 목록. 입력핀/출력핀 칸을 채워 다시 실행하면 연결된다
3. 구버전 기록의 빠진 값(출력 핀, 출력 VOH/VOL, Rpd/Rpu/IOH 계산값)을 저장된 파형으로 채움. 원본은 result.orig.json
4. 샘플·온도·계획 항목마다 같은 샘플 안의 가장 최근 기록을 진행 기록에 연결 (샘플끼리는 섞지 않음)
5. 샘플별 요약표 plan/<샘플>/plan_summary_<샘플>.csv, 나란히 놓은 plan/plan_summary_all.csv
6. results.csv(이전 것은 results_before_cleanup_시각.csv 로 보관)와 사진 모음(result/)을 다시 만듦
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scopetech import results  # noqa: E402
from scopetech.config import Config  # noqa: E402
from scopetech.gallery import collect_all  # noqa: E402
from scopetech.plan import PlanStore, load_layout, load_plan, migrate_legacy, norm_temp, rebuild_all  # noqa: E402
from scopetech.plan_import import (SOURCE, apply, export_missing_pins, generation, load_missing_pins,  # noqa: E402
                                   normalize, plan_import, preview, scan, write_log, write_result)
from scopetech.plan_state import OVERRIDES_FILE, STATE_FILE, PlanSettings, cli_running, read_state  # noqa: E402
from scopetech.samples import Samples, order_key  # noqa: E402


def rebuild_results_csv(base: Path, cfg) -> tuple[Path, int, Path | None]:
    """모든 result.json 으로 results.csv 를 다시 쓴다. (파일, 줄 수, 보관한 이전 파일)."""
    rows = []
    for meta in sorted(Path(base).glob("*/*/result.json"), key=lambda p: p.parent.name):
        try:
            r = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(r, dict):
            continue
        try:
            spec = cfg.spec(r.get("spec") or None)
        except KeyError:
            spec = cfg.spec(None)
        rows += results.rows_for(r, spec, meta.parent.relative_to(base).as_posix())
    rows.sort(key=lambda x: (x["timestamp"], x["folder"]))
    path = Path(base) / "results.csv"
    old = None
    if path.exists():
        old = path.with_name(f"results_before_cleanup_{datetime.now():%Y%m%d_%H%M%S}.csv")
        path.replace(old)
    results.write_csv(path, rows)
    return path, len(rows), old


def main(argv: list[str] | None = None, ask=input) -> int:
    ap = argparse.ArgumentParser(description="여러 버전이 섞인 측정 기록 정리")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--dir", help="결과 폴더 (기본: config.ini 의 base_dir)")
    ap.add_argument("--plan", help="계획 파일 (기본: config.ini [plan] file)")
    ap.add_argument("--replace", action="store_true", help="계획 모드로 잰 줄도 같은 샘플의 가장 최근 기록으로 바꿈")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="묻지 않고 적용")
    mode.add_argument("--dry-run", action="store_true", help="미리보기만 (samples.csv / missing_pins.csv 는 만듦)")
    args = ap.parse_args(argv)

    cfg = Config(args.config)
    base = Path(args.dir) if args.dir else HERE / cfg.get("output", "base_dir")
    if not base.is_dir():
        print(f"결과 폴더가 없습니다: {base}")
        return 1
    try:
        rows = load_plan(Path(args.plan) if args.plan else HERE / cfg.get("plan", "file"), cfg.getint("scope", "num_channels"))
        layout = load_layout(HERE / cfg.get("plan", "summary"))
    except (OSError, ValueError) as e:
        print(f"[오류] {e}")
        return 1
    root = PlanStore(base, cfg.get("plan", "dir") or "plan")
    if cli_running(read_state(root.dir / STATE_FILE)):
        print("계획 진행(run_plan.bat)이 실행 중입니다. CLI 를 끝낸(q) 뒤 정리하세요.")
        return 1
    PlanSettings(cfg, root.dir / OVERRIDES_FILE).apply()
    temps_cfg = [t.strip() for t in cfg.get("plan", "temps").replace(",", " ").split() if t.strip()]
    samples = Samples(root.root)

    found = scan(base, load_missing_pins(root.root))
    try:  # 대응표·핀 목록은 미리보기에서도 만든다(엑셀로 채운 뒤 다시 실행)
        added = samples.sync(f.name for f in found if f.name)
        pins_path, empty = export_missing_pins(found, root.root)
    except OSError as e:
        print(f"[오류] {samples.path.name} / missing_pins.csv 를 쓸 수 없습니다(엑셀에서 열려 있나요?): {e}")
        return 1
    picks, unmatched = plan_import(rows, root, cfg, found, replace=args.replace,
                                   order=[norm_temp(t) for t in temps_cfg], samples=samples)
    fixes = normalize(picks, cfg)

    print(f"정리 미리보기 — {base}\n측정 기록 {len(found)}개 (VIH / MANUAL / POR)")
    for gen, n in Counter(generation(f) for f in found).most_common():
        print(f"  {gen}: {n}개")
    names: dict[str, list[str]] = {}
    for f in found:
        if f.name:
            names.setdefault(samples.id_of(f.name), [])
            if f.name not in names[samples.id_of(f.name)]:
                names[samples.id_of(f.name)].append(f.name)
    print(f"\n샘플 {len(names)}개 ({samples.path})" + (f" — 새 이름 {len(added)}개 추가" if added else ""))
    for sid in sorted(names, key=order_key):
        print(f"  {sid:<12} <- {', '.join(names[sid])}")
    print("  (fab·샘플이 틀리면 samples.csv 를 고치고 다시 실행하세요)")
    preview(picks, unmatched, rows, limit=20)
    if empty:
        print(f"\n핀 정보가 없는 기록 {empty}개 -> {pins_path} 의 입력핀/출력핀을 채우고 다시 실행하면 연결됩니다.")
    todo = [p for p in picks if p.state == SOURCE]
    legacy = sorted(root.root.glob("progress_*C.json"))
    print(f"\n적용하면: 빠진 값 채우기 {len(fixes)}개 기록(원본 result.orig.json), 진행 기록 연결 {len(todo)}개"
          + (f", 예전 진행 기록 {len(legacy)}개를 샘플 폴더로 이동" if legacy else "")
          + ", 요약표·results.csv·사진 모음 다시 만들기")
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
        for m in migrate_legacy(root, samples):
            print(f"예전 진행 기록 옮김: {m}")
        if legacy:  # 옮긴 진행 기록 기준으로 다시 고른다
            picks, unmatched = plan_import(rows, root, cfg, found, replace=args.replace,
                                           order=[norm_temp(t) for t in temps_cfg], samples=samples)
        for f, r in fixes:
            write_result(f, r)
        written = apply(picks, root)
        log = write_log(picks, unmatched, root.dir / f"cleanup_{datetime.now():%Y%m%d_%H%M%S}.csv")
        every, summaries = rebuild_all(rows, layout, root, temps_cfg)
        csv_path, n_rows, old = rebuild_results_csv(base, cfg)
        sub = cfg.get("output", "result_dir").strip() or "result"
        copied, seen = collect_all(base, base / sub)
    except OSError as e:
        print(f"[오류] 쓸 수 없습니다(엑셀에서 열려 있나요?): {e}")
        return 1
    print(f"\n빠진 값 채운 기록 {len(fixes)}개, 진행 기록 {len(written)}개 파일 갱신, 내역 {log}")
    for p in summaries:
        print(f"요약표: {p}")
    if every:
        print(f"전체 샘플 요약표: {every}")
    print(f"results.csv: {n_rows}줄 -> {csv_path}" + (f" (이전 파일: {old.name})" if old else ""))
    print(f"사진 모음: {seen}장 확인, 새로 복사 {copied}장 -> {base / sub}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

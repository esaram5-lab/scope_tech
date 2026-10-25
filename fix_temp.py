"""온도를 잘못 넣고 측정한 기록 고치기 (스코프 연결 불필요, run_fix_temp.bat).

    python fix_temp.py           차례로 물어봄: 샘플 -> 잘못 넣은 온도 -> 원래 온도 -> 고칠 측정 묶음 -> 확인
    python fix_temp.py --sample TSMC_1 --wrong 85 --right 25 --since "2026-10-24 13:00" --until "2026-10-24 15:00" --apply
측정 묶음은 측정 시각이 30분 넘게 비는 곳에서 나눈다. 잘못된 온도에서 제대로 잰 측정이 섞였으면 그 묶음을 빼고 고르면 된다.
고친 뒤 샘플별·전체 요약표, results.csv(이전 것은 results_before_fix_*.csv), 사진 모음을 다시 만든다.
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cleanup import rebuild_results_csv  # noqa: E402
from scopetech import fix_temp as fx  # noqa: E402
from scopetech.config import Config  # noqa: E402
from scopetech.plan import PlanStore, load_layout, load_plan, norm_temp, rebuild_all  # noqa: E402
from scopetech.plan_state import STATE_FILE, cli_running, read_state  # noqa: E402
from scopetech.samples import Samples, order_key  # noqa: E402


def _temp(text: str) -> str | None:
    try:
        v = float(text)
        return norm_temp(text) if math.isfinite(v) else None
    except ValueError:
        return None


def _tkey(t: str):
    return (0, float(t)) if _temp(t) else (1, t)


def _when(text: str) -> str:
    """'2026-10-24 13:00' / '2026-10-24T13:00' -> ISO 문자열 (비교용)."""
    return datetime.fromisoformat(text.strip().replace(" ", "T")).isoformat(timespec="seconds")


class _Quit(Exception):
    pass


def main(argv: list[str] | None = None, ask=input) -> int:
    try:
        return _run(argv, ask)
    except _Quit:
        print("\n그만둡니다. 아무것도 바꾸지 않았습니다.")
        return 0


def _run(argv: list[str] | None, ask) -> int:
    ap = argparse.ArgumentParser(description="잘못 넣은 온도로 측정한 기록 고치기")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--dir", help="결과 폴더 (기본: config.ini 의 base_dir)")
    ap.add_argument("--plan", help="계획 파일 (기본: config.ini [plan] file)")
    ap.add_argument("--sample", help="샘플 ID 또는 시험 이름")
    ap.add_argument("--wrong", help="잘못 넣은 온도")
    ap.add_argument("--right", help="원래 온도")
    ap.add_argument("--since", help="이 시각부터 (예: 2026-10-24 13:00)")
    ap.add_argument("--until", help="이 시각까지")
    ap.add_argument("--replace", action="store_true", help="원래 온도에 이미 완료된 같은 항목도 고친 기록으로 바꿈")
    ap.add_argument("--apply", action="store_true", help="묻지 않고 적용 (--sample --wrong --right 필요)")
    args = ap.parse_args(argv)

    def prompt(label, valid, default=""):
        while True:
            try:
                v = ask(f"{label}" + (f" [{default}]" if default else "") + " > ").strip() or default
            except (EOFError, KeyboardInterrupt):
                raise _Quit from None
            if v.lower() == "q":
                raise _Quit
            if valid(v):
                return v
            print("입력값을 확인해주세요 (q: 그만두기).")

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
        print("계획 진행(run_plan.bat)이 실행 중입니다. CLI 를 끝낸(q) 뒤 고치세요.")
        return 1
    samples = Samples(root.root)
    groups = fx.by_sample(fx.scan(base, (root.root.name, cfg.get("output", "result_dir") or "result")), samples)
    if not groups:
        print(f"측정 기록이 없습니다: {base}")
        return 1
    order = sorted(groups, key=order_key)

    # 1) 샘플
    if args.sample:
        sid = args.sample if args.sample in groups else samples.id_of(args.sample)
        if sid not in groups:
            print(f"샘플 '{args.sample}' 의 기록이 없습니다. 있는 샘플: {', '.join(order)}")
            return 1
    else:
        print("\n샘플 (시험 이름 -> samples.csv)")
        for i, s in enumerate(order, 1):
            temps = {}
            for r in groups[s]:
                temps[r.temp] = temps.get(r.temp, 0) + 1
            names = sorted({r.name for r in groups[s]})
            print(f"  {i}) {s or '(이름 없음)':<12} " + ", ".join(f"{t}°C {n}건" for t, n in sorted(temps.items(), key=lambda x: _tkey(x[0])))
                  + f"   [{', '.join(names)}]")
        sid = order[int(prompt("고칠 샘플 번호", lambda v: v.isdigit() and 1 <= int(v) <= len(order))) - 1]
    recs = groups[sid]

    # 2) 잘못 넣은 온도
    temps = sorted({r.temp for r in recs}, key=_tkey)
    if args.wrong:
        wrong = _temp(args.wrong)
    else:
        print(f"\n{sid} 기록의 온도: " + ", ".join(f"{t}°C {sum(r.temp == t for r in recs)}건" for t in temps))
        wrong = _temp(prompt("잘못 넣은 온도(°C)", lambda v: _temp(v) in temps))
    if wrong not in temps:
        print(f"{sid} 에 {wrong}°C 기록이 없습니다.")
        return 1
    right = _temp(args.right) if args.right else _temp(prompt("원래 온도(°C)", lambda v: _temp(v) is not None and _temp(v) != wrong))
    if right is None or right == wrong:
        print("원래 온도를 확인해주세요.")
        return 1
    cand = [r for r in recs if r.temp == wrong]

    # 3) 언제 잰 것인지 (측정 묶음)
    try:
        since = _when(args.since) if args.since else ""
        until = _when(args.until) if args.until else ""
    except ValueError:
        print("--since / --until 은 '2026-10-24 13:00' 형식으로 적어주세요.")
        return 1
    if since or until:
        chosen = [r for r in cand if (not since or r.time >= since) and (not until or r.time <= until)]
    elif args.apply:
        chosen = cand
    else:
        sess = fx.sessions(cand)
        print(f"\n{sid} {wrong}°C 측정 묶음 (30분 넘게 비면 나눔)")
        for i, g in enumerate(sess, 1):
            print(f"  {i}) {g[0].time.replace('T', ' ')} ~ {g[-1].time.replace('T', ' ')}  {len(g)}건")
        print("  잘못된 온도에서 제대로 잰 측정이 섞여 있으면 그 묶음은 빼고 고르세요.")

        def pick_ok(v):
            if v.lower() == "a":
                return True
            parts = v.replace(",", " ").split()
            return bool(parts) and all(p.isdigit() and 1 <= int(p) <= len(sess) for p in parts)
        v = prompt("고칠 묶음 번호 (예: 1 또는 1,3 / a: 전부)", pick_ok, "a" if len(sess) == 1 else "")
        chosen = cand if v.lower() == "a" else [r for p in dict.fromkeys(v.replace(",", " ").split()) for r in sess[int(p) - 1]]
    if not chosen:
        print("고칠 기록이 없습니다.")
        return 0

    # 4) 확인
    print(f"\n{sid}: {wrong}°C -> {right}°C 로 고칠 기록 {len(chosen)}개")
    for r in chosen:
        print(f"  {r.time.replace('T', ' ')}  {r.rel}")
    clash = fx.conflicts(root, chosen, wrong, right)
    replace = args.replace
    if clash:
        print(f"\n{right}°C 진행 기록에 이미 완료된 같은 항목 {len(clash)}개: " + ", ".join(k.split('|')[0].split()[-1] + "번" for k in clash))
        if not args.apply:
            replace = prompt(f"그 항목을 고친 기록으로 바꿀까요? (y: 바꿈 / n: {right}°C 기록 유지)",
                             lambda v: v.lower() in ("y", "n")).lower() == "y"
    if not args.apply and prompt("적용할까요? (y/N)", lambda v: True, "n").lower() != "y":
        print("적용하지 않았습니다.")
        return 0
    sub = cfg.get("output", "result_dir").strip()
    try:
        rep = fx.apply(base, root, rows, chosen, wrong, right, replace=replace, result_dir=sub)
        log = fx.write_log(rep, chosen, wrong, right, root.dir / f"fix_temp_{datetime.now():%Y%m%d_%H%M%S}.txt")
        every, _ = rebuild_all(rows, layout, root, [t.strip() for t in cfg.get("plan", "temps").replace(",", " ").split()])
        csv_path, n_rows, old = rebuild_results_csv(base, cfg, prefix="results_before_fix")
    except OSError as e:
        print(f"[오류] 쓸 수 없습니다(엑셀에서 열려 있나요?): {e}")
        return 1
    print(f"\n고친 result.json {len(rep.fixed)}개 (원본 result.orig.json), 진행 기록 옮김 {len(rep.moved)}개"
          + (f", {right}°C 기록 유지 {len(rep.kept)}개" if rep.kept else ""))
    for f in rep.removed_files:
        print(f"  지운 진행 기록: {f}")
    if sub:
        print(f"사진 모음: 잘못된 이름 {rep.photos_removed}장 지움, 새 이름 {rep.photos_copied}장 복사")
    if every:
        print(f"전체 샘플 요약표: {every}")
    print(f"results.csv: {n_rows}줄" + (f" (이전 파일: {old.name})" if old else "") + f"\n내역: {log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

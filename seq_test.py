"""측정 프로그램 동작 순서 시험 (디버깅 강화).

측정 프로그램과 **같은 코드**로 아래 순서를 단계별로 실행하고 N 회 반복한다.
  0 상태정리 → 1 VIL/VIH 측정(Single·파형전송·분석·커서) → 2 meas.png 캡처
  → 3 clean.png 캡처(결과바/커서 숨김) → 4 파형·설정 저장
각 단계의 소요 시간, 모든 SCPI 명령의 응답 시간을 기록하고, 오류가 나면
직전 SCPI 기록과 스코프 상태(가능하면)를 화면과 파일에 남긴 뒤 멈춘다.

    python seq_test.py                    입력 CH1 / 출력 CH2, 3회 반복
    python seq_test.py --repeat 5 --in 1 --out 2
    python seq_test.py --only capture     화면 캡처만 반복(가장 문제였던 부분)
    python seq_test.py --save-wait 10     캡처 대기(aoe_save_wait_s) 임시 변경
    python seq_test.py --sweep            캡처 대기 시간을 단계적으로 줄이며 최소 안전값 찾기
    python seq_test.py --sweep --sweep-param save --levels 1,0.5,0.2,0   aoe_save_wait_s 만
    python seq_test.py --sweep --start-level 0.3   (멈춘 뒤 TekScope 재시작 후 이어서)
    python seq_test.py --sim              시뮬레이터
결과: logs/seqtest_날짜_시각.txt  (+ logs/scpi_날짜.log)
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scope import make_logger, open_transport  # noqa: E402
from scopetech.config import Config  # noqa: E402
from scopetech.mso import MSO5B  # noqa: E402
from scopetech.workflow import Bench  # noqa: E402

STATE_QUERIES = ("ACQuire:STATE?", "ACQuire:STOPAfter?", "ACQuire:MODe?", "BUSY?",
                 "ACTONEVent:ENable?", "ACTONEVent:TRIGger:ACTION:SAVEIMAGe:STATE?", "*ESR?", "ALLEv?")


class Recorder:
    """SCPI 송수신을 시각·간격과 함께 기록하고 최근 N 줄을 보관."""

    def __init__(self, base_logger, report, keep: int = 60):
        self.base, self.report = base_logger, report
        self.recent = collections.deque(maxlen=keep)
        self.last = time.monotonic()

    def __call__(self, direction: str, text: str) -> None:
        now = time.monotonic()
        line = f"{datetime.now():%H:%M:%S.%f}"[:-3] + f" (+{(now - self.last) * 1000:6.0f}ms) {direction} {text[:200]}"
        self.last = now
        self.recent.append(line)
        self.base(direction, text)
        self.report.write("      " + line + "\n")
        self.report.flush()  # 멈춰도 마지막 명령까지 파일에 남도록


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="측정 동작 순서 시험")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--in", dest="ch_in", type=int, default=1)
    ap.add_argument("--out", dest="ch_out", type=int, default=2)
    ap.add_argument("--only", choices=["capture"], help="capture: 화면 캡처만 반복")
    ap.add_argument("--save-wait", type=float, help="aoe_save_wait_s 임시 변경(초)")
    ap.add_argument("--post-wait", type=float, help="aoe_post_wait_s 임시 변경(초)")
    ap.add_argument("--sweep", action="store_true", help="캡처 대기 시간을 단계적으로 줄이며 반복 시험")
    ap.add_argument("--sweep-param", choices=["all", "arm", "save", "post"], default="all",
                    help="줄일 대기 항목 (기본: 세 항목 모두 같은 비율)")
    ap.add_argument("--levels", default="1,0.7,0.5,0.35,0.2,0.1,0", help="config 값 대비 비율 목록")
    ap.add_argument("--start-level", type=float, help="이 비율부터 시작(이전 실행에서 멈춘 뒤 이어하기)")
    args = ap.parse_args(argv)

    cfg = Config(args.config)
    if args.save_wait is not None:
        cfg.cp.set("capture", "aoe_save_wait_s", str(args.save_wait))
    if args.post_wait is not None:
        cfg.cp.set("capture", "aoe_post_wait_s", str(args.post_wait))
    log_dir = HERE / cfg.get("output", "log_dir")
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"seqtest_{datetime.now():%Y%m%d_%H%M%S}.txt"
    report = path.open("w", encoding="utf-8")

    def out(text: str = "") -> None:
        print(text)
        report.write(text + "\n")
        report.flush()

    rec = Recorder(make_logger(log_dir, False), report)
    out(f"seq_test {datetime.now():%Y-%m-%d %H:%M:%S}  python={sys.version.split()[0]}  "
        f"interface={cfg.get('connection', 'interface')}  in=CH{args.ch_in} out=CH{args.ch_out}  repeat={args.repeat}")
    out("capture 대기: " + ", ".join(f"{k}={cfg.get('capture', k)}" for k in ("aoe_arm_wait_s", "aoe_save_wait_s", "aoe_post_wait_s")))
    try:
        t = open_transport(cfg, args.sim, rec)
        scope = MSO5B(t, temp_dir=cfg.get("scope", "temp_dir"), settle_s=cfg.getfloat("capture", "settle_s"),
                      num_channels=cfg.getint("scope", "num_channels"))
        out(f"연결: {scope.connect()}  ({t.description})")
    except Exception as e:  # noqa: BLE001
        out(f"[연결 실패] {type(e).__name__}: {e}")
        return 1

    bench = Bench(scope, cfg, HERE / ("captures_sim" if args.sim else "captures") / "_seqtest", echo=lambda s: out("    " + s))
    bench.name = "SEQTEST"
    shots = {"n": 0}

    def shot_path() -> Path:
        shots["n"] += 1
        return bench.out_dir / "shots" / f"shot_{shots['n']:03d}.png"

    bench.shot_path = shot_path

    def snapshot() -> None:
        out("  --- 스코프 상태 (각 질의 5초 제한) ---")
        for q in STATE_QUERIES:
            try:
                with t.temp_timeout(5):
                    out(f"    {q:<45} {t.query(q)}")
            except Exception as e:  # noqa: BLE001
                out(f"    {q:<45} 응답 없음 ({type(e).__name__}) -> TekScope 멈춤 가능성. 이후 질의 생략")
                return

    def step(label: str, fn) -> bool:
        out(f"  [{label}] 시작")
        t0 = time.monotonic()
        try:
            fn()
        except BaseException as e:  # noqa: BLE001
            out(f"  [{label}] 실패 ({time.monotonic() - t0:.1f}s): {type(e).__name__}: {e}")
            report.write(traceback.format_exc())
            out("  --- 직전 SCPI 기록 (최근 순서대로, 시각 / 이전 명령과의 간격) ---")
            for line in rec.recent:
                out("    " + line)
            if not isinstance(e, KeyboardInterrupt):
                snapshot()
            return False
        out(f"  [{label}] 통과 ({time.monotonic() - t0:.1f}s)")
        return True

    try:
        offset = scope.clock_offset()
        out(f"스코프 시계 - PC 시계 = {offset:+.2f}s (캡처 파일 이름의 시각을 PC 시각으로 바꿔 저장 소요 시간을 계산)")
    except Exception as e:  # noqa: BLE001
        offset = None
        out(f"스코프 시계 확인 실패({type(e).__name__}: {e}) -> 저장 시각 분석 생략")
    fetch = lambda: _fetch_all(bench, out, scope, offset)  # noqa: E731

    if args.sweep:
        return sweep(args, cfg, scope, bench, t, step, out, path, report, fetch)

    ok_runs = 0
    try:
        for n in range(1, args.repeat + 1):
            out(f"\n===== {n}/{args.repeat} 회 =====")
            steps = [("0 상태정리", scope.reset_state)]
            if args.only == "capture":
                steps += [("2 화면캡처", lambda: bench._take(False, shot_path()))]
            else:
                def clean():
                    bench._hide_overlay(bench.record)
                    try:
                        bench._take(False, shot_path())
                    finally:
                        bench._restore_overlay(bench.record)
                steps += [
                    ("1 VIL/VIH 측정", lambda: bench.vih(args.ch_in, args.ch_out)),
                    ("2 meas 캡처", lambda: bench._take(False, shot_path())),
                    ("3 clean 캡처", clean),
                    ("4 파형·설정 저장", lambda: bench.save([args.ch_in, args.ch_out])),
                ]
            if not all(step(label, fn) for label, fn in steps):
                out(f"\n=> {n}회차에서 실패. 위 '직전 SCPI 기록'의 마지막 줄이 멈춘 명령입니다.")
                break
            ok_runs += 1
        if ok_runs == args.repeat and bench.pending_count():
            out("\n===== 캡처 파일 한 번에 가져오기 =====")
            if not step(f"5 가져오기({bench.pending_count()}개)", fetch):
                ok_runs -= 1
    except KeyboardInterrupt:
        out("\n사용자 중단")
    finally:
        try:
            scope.abort_capture()
        except Exception:  # noqa: BLE001
            pass
        t.close()
    out(f"\n결과: {ok_runs}/{args.repeat} 회 성공")
    out(f"이 파일을 올려주세요: {path}")
    report.close()
    return 0 if ok_runs == args.repeat else 2


def _fetch_all(bench, out, scope=None, offset=None) -> None:
    items = bench._load_pending()
    got, left = bench.fetch_pending()
    out(f"    가져옴 {got}개, 남음 {left}개")
    if offset is not None:
        _timing_report(items, getattr(scope, "fetched_names", {}), offset, out)
    if left:
        raise RuntimeError(f"캡처 {left}개를 가져오지 못했습니다")


def _timing_report(items, names, offset, out) -> None:
    """캡처 파일 이름의 시각(스코프 시계)을 PC 시각으로 바꿔 Single 시작 -> 파일 시각 -> AOE 해제 간격을 보여준다."""
    rows = []
    for it in items:
        m = re.search(r"_(\d{17})\.png$", names.get(it["prefix"], ""), re.I)
        if not m or "single_at" not in it or "disarm_at" not in it:
            continue
        file_dt = datetime.strptime(m.group(1)[:14], "%Y%m%d%H%M%S") + timedelta(milliseconds=int(m.group(1)[14:]))
        file_at = (file_dt - timedelta(seconds=offset)).timestamp()
        rows.append((Path(it["local"]).name, file_at - it["single_at"], it["disarm_at"] - file_at))
    if not rows:
        return
    out("    --- 파일 이름 시각 분석 (시계 오차 약 ±0.1s). 실측 결과 이 시각은 트리거 시각이며 저장 완료 시각이 아님 ---")
    for name, lat, margin in rows:
        out(f"    {name:<16} Single 시작 -> 트리거 {lat:5.2f}s   트리거 -> AOE 해제 {margin:5.2f}s")


WAIT_KEYS = {"arm": "aoe_arm_wait_s", "save": "aoe_save_wait_s", "post": "aoe_post_wait_s"}


def sweep(args, cfg, scope, bench, t, step, out, path, report, fetch) -> int:
    """대기 시간을 비율로 줄여가며 캡처를 반복. 처음 실패한 단계에서 멈추고 직전 단계를 권장값으로 제시."""
    keys = list(WAIT_KEYS.values()) if args.sweep_param == "all" else [WAIT_KEYS[args.sweep_param]]
    base = {k: cfg.getfloat("capture", k) for k in WAIT_KEYS.values()}
    levels = sorted({float(x) for x in args.levels.split(",")}, reverse=True)
    if args.start_level is not None:
        levels = [lv for lv in levels if lv <= args.start_level]
    out(f"\n스윕: {', '.join(keys)} 를 config 값 {', '.join(f'{k}={base[k]:g}' for k in keys)} 대비 {levels} 배로, 단계마다 {args.repeat}회")
    out("주의: 실패하면 TekScope 가 멈출 수 있습니다. 재시작 후 --start-level 로 이어서 하세요.")
    passed, failed_at, rows = [], None, []
    try:
        for lv in levels:
            vals = {k: (base[k] * lv if k in keys else base[k]) for k in WAIT_KEYS.values()}
            for k, v in vals.items():
                cfg.cp.set("capture", k, f"{v:.3f}")
            out(f"\n===== 비율 {lv:g}: " + ", ".join(f"{k}={v:.2f}s" for k, v in vals.items()) + " =====")
            times = []
            for n in range(1, args.repeat + 1):
                t0 = time.monotonic()
                ok = step(f"{n}/{args.repeat} 상태정리", scope.reset_state) and step(
                    f"{n}/{args.repeat} 화면캡처", lambda: bench._take(False, bench.shot_path()))
                if not ok:
                    failed_at = lv
                    break
                times.append(time.monotonic() - t0)
            if failed_at is not None:
                rows.append((lv, vals, f"실패({len(times)}/{args.repeat} 성공)"))
                break
            rows.append((lv, vals, f"성공 {args.repeat}/{args.repeat}, 캡처 1회 평균 {sum(times) / len(times):.1f}s"))
            passed.append((lv, vals))
        if failed_at is None and bench.pending_count():
            out("\n===== 캡처 파일 한 번에 가져오기 =====")
            if not step(f"가져오기({bench.pending_count()}개)", fetch):
                rows.append(("가져오기", {}, "실패"))
    except KeyboardInterrupt:
        out("\n사용자 중단")
    finally:
        try:
            scope.abort_capture()
        except Exception:  # noqa: BLE001
            pass
        t.close()
    out("\n===== 스윕 결과 =====")
    for lv, vals, res in rows:
        head = f"비율 {lv:<5g}" if isinstance(lv, float) else f"{lv:<9}"
        out(f"  {head} " + " ".join(f"{k}={v:5.2f}" for k, v in vals.items()) + f"  -> {res}")
    if passed:
        lv, vals = passed[-1]
        out(f"\n가장 짧게 통과한 비율: {lv:g}" + ("" if failed_at is None else f"  (비율 {failed_at:g} 에서 실패)"))
        out("권장 config.ini [capture] (통과값에 1.5배 여유):")
        for k in WAIT_KEYS.values():
            out(f"  {k} = {round(vals[k] * 1.5, 1) if k in keys else vals[k]:g}")
    else:
        out("\n통과한 단계가 없습니다. 현재 config 값에서도 실패합니다.")
    out(f"이 파일을 올려주세요: {path}")
    report.close()
    return 0 if failed_at is None else 2


if __name__ == "__main__":
    sys.exit(main())

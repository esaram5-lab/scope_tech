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
    python seq_test.py --sim              시뮬레이터
결과: logs/seqtest_날짜_시각.txt  (+ logs/scpi_날짜.log)
"""
from __future__ import annotations

import argparse
import collections
import sys
import time
import traceback
from datetime import datetime
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

    ok_runs = 0
    try:
        for n in range(1, args.repeat + 1):
            out(f"\n===== {n}/{args.repeat} 회 =====")
            steps = [("0 상태정리", scope.reset_state)]
            if args.only == "capture":
                steps += [("2 화면캡처", lambda: bench._shot(False))]
            else:
                def clean():
                    bench._hide_overlay(bench.record)
                    try:
                        bench._shot(False)
                    finally:
                        bench._restore_overlay(bench.record)
                steps += [
                    ("1 VIL/VIH 측정", lambda: bench.vih(args.ch_in, args.ch_out)),
                    ("2 meas 캡처", lambda: bench._shot(False)),
                    ("3 clean 캡처", clean),
                    ("4 파형·설정 저장", lambda: bench.save([args.ch_in, args.ch_out])),
                ]
            if not all(step(label, fn) for label, fn in steps):
                out(f"\n=> {n}회차에서 실패. 위 '직전 SCPI 기록'의 마지막 줄이 멈춘 명령입니다.")
                break
            ok_runs += 1
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


if __name__ == "__main__":
    sys.exit(main())

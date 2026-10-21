"""Tektronix 공식 Python 라이브러리 tm_devices 로 같은 스코프를 시험한다.

먼저 install_tm_devices.bat 로 설치(오프라인 PC: get_tm_devices.bat 로 받은 폴더 필요).
    python tmd_test.py                 1~3 단계 (화면 캡처 3회)
    python tmd_test.py --shots 10      화면 캡처 10회
    python tmd_test.py --resource USB0::0x0699::0x0522::C081164::INSTR
결과: logs/tmd_test_날짜_시각.txt (tm_devices 가 보낸 명령 포함)

주의: tm_devices 의 save_screenshot() 은 원격 SAVe:IMAGe 를 쓴다. 이 스코프(FV 2.24.4)에서
우리 프로그램이 같은 명령으로 TekScope 가 멈췄던 적이 있으므로, 3단계에서 멈추면 TekScope 를 재시작하세요.
스코프 설정은 바꾸지 않는다(*RST 등 tm_devices 의 cleanup 은 끔).
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for s in self.streams:
            s.write(text)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description="tm_devices 시험")
    ap.add_argument("--resource", default="", help="VISA 주소 (비우면 USB 에서 Tektronix 장비 자동 탐색)")
    ap.add_argument("--shots", type=int, default=3, help="화면 캡처 반복 횟수")
    ap.add_argument("--gap", type=float, default=3.0, help="캡처 사이 대기(초)")
    args = ap.parse_args()

    log_dir = HERE / "logs"
    log_dir.mkdir(exist_ok=True)
    path = log_dir / f"tmd_test_{datetime.now():%Y%m%d_%H%M%S}.txt"
    f = path.open("w", encoding="utf-8")
    sys.stdout = sys.stderr = Tee(sys.__stdout__, f)
    print(f"tmd_test {datetime.now():%Y-%m-%d %H:%M:%S}  python={sys.version.split()[0]}")

    try:
        import tm_devices
        from tm_devices import DeviceManager
        from tm_devices.helpers import DMConfigOptions
    except ImportError:
        print("tm_devices 가 설치되어 있지 않습니다. install_tm_devices.bat 를 먼저 실행하세요.")
        return 1
    print(f"tm_devices {getattr(tm_devices, '__version__', '?')}")

    resource = args.resource or find_resource()
    print(f"주소: {resource}")
    shots_dir = HERE / "captures" / "_tmdtest"
    ok = True
    t_start = time.monotonic()

    def step(label, fn):
        nonlocal ok
        print(f"\n[{label}] 시작 {datetime.now():%H:%M:%S.%f}"[:-3])
        t0 = time.monotonic()
        try:
            fn()
        except BaseException as e:  # noqa: BLE001
            ok = False
            print(f"[{label}] 실패 ({time.monotonic() - t0:.1f}s): {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
        print(f"[{label}] 통과 ({time.monotonic() - t0:.1f}s)")
        return True

    opts = DMConfigOptions(setup_cleanup=False, teardown_cleanup=False)
    with DeviceManager(verbose=True, config_options=opts) as dm:
        scope = None

        def connect():
            nonlocal scope
            scope = dm.add_scope(resource)
            print(f"    연결: {scope.idn_string}")

        def curve():
            data = scope.curve_query(1)
            print(f"    CH1 {len(data)} 포인트, 처음 5개 {list(data[:5])}")

        steps = [("1 연결(*IDN?)", connect), ("2 파형 받기 curve_query(CH1)", curve)]
        for n in range(1, args.shots + 1):
            name = f"tmd_{n:03d}.png"
            steps.append((f"3 화면 캡처 save_screenshot {n}/{args.shots}",
                          lambda name=name: (scope.save_screenshot(name, local_folder=shots_dir),
                                             print(f"    저장: {shots_dir / name}"), time.sleep(args.gap))))
        for label, fn in steps:
            if not step(label, fn):
                print("\n=> 여기서 멈춤/실패. 위 기록의 마지막 명령을 확인하세요.")
                break
    print(f"\n결과: {'모두 통과' if ok else '실패'} (총 {time.monotonic() - t_start:.0f}s)")
    print(f"이 파일을 올려주세요: {path}")
    return 0 if ok else 2


def find_resource() -> str:
    import pyvisa

    rm = pyvisa.ResourceManager()
    found = [r for r in rm.list_resources("USB?*INSTR") if "0x0699" in r.lower()]
    if not found:
        raise SystemExit("USB 에서 Tektronix 장비를 찾지 못했습니다. --resource 로 주소를 지정하세요.")
    print(f"발견: {', '.join(found)}")
    return found[0]


if __name__ == "__main__":
    sys.exit(main())

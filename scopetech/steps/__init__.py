"""측정 동작(단계) 모듈. 각 모듈의 run(bench, ...) 이 한 동작을 한다.

    reset.py    상태 정리 (매 측정 전)
    vih.py      VIL/VIH 측정 (+ 같은 파형의 출력 VOH/VOL), 커서
    voh.py      VOH/VOL 측정 (TOP/BASE 통계), 커서
    capture.py  화면 캡처 meas.png / clean.png (캡처 방법은 scopetech/capture/)
    save.py     파형 CSV / .wfm / 설정 저장
    manual.py   데이터·화면 저장 (메뉴 3)
    snap.py     화면만 캡처 (메뉴 4)

bench(workflow.Bench)가 공통 상태와 도구를 준다:
    bench.scope / cfg / echo / out_dir / name / temp / out_type / spec_name / dut_pin / out_pin / record / capturer
    bench.new_record(item, channels) / base_result(item) / finish(rec) / ensure_record() / label_folder(rec, label)
    bench.prepare_overlay() / hide_overlay(rec) / restore_overlay(rec)
새 동작은 같은 형식의 모듈을 만들고 Bench 에 메서드 한 줄을 추가하면 메뉴·셸에서 부를 수 있다.
"""


def volts(x: float | None, unit: str = "V") -> str:
    return "-" if x is None else f"{x:.4f} {unit}"

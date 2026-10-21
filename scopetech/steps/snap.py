"""화면만 캡처 (메뉴 4): 파형 데이터·측정·설정 파일 없이 커서를 켜고 지금 화면 1장."""
from __future__ import annotations

from .. import results


def run(bench, label: str, note: str = "", cursor: bool = True):
    """화면만 저장: 파형 데이터·측정·설정 파일 없이 커서를 켜고 지금 화면을 캡처한다.
    캡처 방법은 bench.capturer (기본 aoe: Act On Event 라 Single 1회 획득이 일어난다)."""
    if not label.strip():
        raise ValueError("캡처 이름을 입력해주세요.")
    if not bench.capturer.enabled:
        raise ValueError("화면 캡처를 끈 상태입니다(run_nocap / [capture] enabled = no).")
    rec = bench.new_record("SNAP", [])
    rec.result = bench.base_result("SNAP")
    rec.result.update({"snap_item": label, "note": note, "cursor": cursor, "metrics": [],
                       "status": "incomplete", "capture_note": f"{bench.capturer.label}. 파형 데이터 없음."})
    bench.label_folder(rec, label)
    bench.record = rec
    results.write_json(rec.folder / "result.json", rec.result)
    if cursor:
        bench.echo("[1/2] 커서 표시 (스코프에 설정된 커서 위치 그대로)")
        bench.scope.cursor_on(True)
        bench.scope.pace()
    bench.echo(f"[{2 if cursor else 1}/{2 if cursor else 1}] 화면 캡처 ({bench.capturer.label})")
    bench.take(rec.folder / "screen.png")
    rec.result["status"] = "complete"
    results.write_json(rec.folder / "result.json", rec.result)
    return rec

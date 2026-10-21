"""데이터·화면 저장 (메뉴 3): 기존 측정/커서는 그대로 두고 선택 채널 CSV + 화면 1장."""
from __future__ import annotations

from .. import results, wavefile


def run(bench, label: str, chans: list[int], note: str = ""):
    """수동 시험: 기존 측정/커서는 유지하고 CSV와 화면 1장을 저장한다."""
    if not label.strip() or not chans or any(c < 1 or c > bench.scope.num_channels for c in chans):
        raise ValueError("시험 이름과 저장 채널을 확인해주세요.")
    s = bench.scope
    s.stop()
    rec = bench.new_record("MANUAL", chans)
    rec.result = bench.base_result("MANUAL")
    rec.result.update({"manual_item": label, "note": note,
                       "channels": [f"CH{c}" for c in chans],
                       "metrics": [], "status": "incomplete",
                       "capture_note": f"CSV 저장 후 화면 캡처({bench.capturer.label}). 동일 획득이 아닐 수 있음."
                       if bench.capturer.enabled else "화면 캡처 끔"})
    bench.label_folder(rec, label)
    bench.record = rec
    results.write_json(rec.folder / "result.json", rec.result)
    cap = bench.capturer.enabled
    bench.echo(f"[1/{2 if cap else 1}] 선택한 채널의 파형 CSV 저장")
    wfs = [s.fetch_waveform(c) for c in chans]
    wavefile.write_csv(rec.folder, wfs, {f"CH{c}": bench.cfg.channel_name(c) for c in chans})
    if cap:
        bench.echo(f"[2/2] 기존 측정·커서를 표시한 화면 저장 ({bench.capturer.label})")
        bench.take(rec.folder / "screen.png")
    else:
        rec.result["capture"] = "off"
    rec.result.update({"status": "complete", "saved_channels": [f"CH{c}" for c in chans]})
    results.write_json(rec.folder / "result.json", rec.result)
    return rec

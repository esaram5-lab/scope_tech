"""화면 캡처 (측정 직후): 측정/커서가 보이는 meas.png + 숨긴 clean.png.

실제 이미지를 만드는 방법은 bench.capturer (scopetech/capture/, config [capture] method) 가 정한다.
여기서는 '어떤 화면을 몇 장' 찍을지만 정한다.
"""
from __future__ import annotations

from pathlib import Path

from .. import results


def run(bench) -> list[Path]:
    rec = bench.ensure_record()
    cap = bench.capturer
    if not cap.enabled:
        rec.result["capture"] = "off"
        results.write_json(rec.folder / "result.json", rec.result)
        bench.echo("  화면 캡처 생략 (캡처 끔)")
        return []
    bench.scope.freeze(bench.cfg.getfloat("measure", "single_timeout_s"))  # 두 캡처와 저장 파형이 같은 획득이 되도록
    bench.echo(f"  화면 캡처: {cap.label}")
    out = []
    if rec.meas or rec.cursor:
        out.append(bench.take(rec.folder / "meas.png"))
        bench.hide_overlay(rec)
        try:
            out.append(bench.take(rec.folder / "clean.png"))
        finally:
            bench.restore_overlay(rec)
    else:
        out.append(bench.take(rec.folder / "clean.png"))
        bench.echo("  측정/커서가 없어 clean.png 만 저장했습니다.")
    later = " (스코프에 저장, 종료 시 한 번에 가져옴)" if cap.pending_count() else ""
    bench.echo("[캡처] " + ", ".join(x.name for x in out) + later + f"  -> {rec.folder}")
    return out

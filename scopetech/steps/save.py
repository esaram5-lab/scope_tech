"""파형·설정 저장: 파형 CSV(CURVe?), 채널별 .wfm, setup.set ([save] 항목으로 선택)."""
from __future__ import annotations

import re
from pathlib import Path

from .. import results, wavefile


def run(bench, chans: list[int] | None = None) -> list[Path]:
    rec = bench.ensure_record()
    s, cfg = bench.scope, bench.cfg
    s.freeze(cfg.getfloat("measure", "single_timeout_s"))
    if not chans:
        setting = cfg.get("save", "channels").lower()
        if setting in ("", "displayed", "all"):
            chans = s.displayed_channels()
        else:
            chans = [int(x) for x in re.split(r"[,\s]+", setting) if x]
    if not chans:
        raise ValueError("저장할 채널이 없습니다(화면에 켜진 채널 없음).")
    out: list[Path] = []
    if cfg.getbool("save", "csv"):
        bench.echo(f"  파형 데이터 전송 중... ({', '.join(f'CH{c}' for c in chans)})")
        wfs = [s.fetch_waveform(c) for c in chans]
        names = {f"CH{c}": cfg.channel_name(c) for c in chans}
        out += wavefile.write_csv(rec.folder, wfs, names)
    if cfg.getbool("save", "wfm"):
        for c in chans:
            p = rec.folder / f"CH{c}.wfm"
            p.write_bytes(s.save_wfm(c))
            out.append(p)
    if cfg.getbool("save", "setup"):
        p = rec.folder / "setup.set"
        p.write_bytes(s.save_setup())
        out.append(p)
    rec.result.setdefault("saved_channels", [])
    rec.result["saved_channels"] = sorted(set(rec.result["saved_channels"]) | {f"CH{c}" for c in chans})
    results.write_json(rec.folder / "result.json", rec.result)
    bench.echo("[저장] " + ", ".join(x.name for x in out) + f"  -> {rec.folder}")
    return out

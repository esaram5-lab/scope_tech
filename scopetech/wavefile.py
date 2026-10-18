"""파형 CSV 쓰기/읽기. 시간축이 같으면 한 파일(time_s, CH1_V, CH2_V, ...)로 저장한다."""
from __future__ import annotations

import csv
from pathlib import Path

from .analysis import Waveform


def write_csv(folder: Path, wfs: list[Waveform], names: dict[str, str] | None = None) -> list[Path]:
    if not wfs:
        return []
    groups = [wfs] if all(w.same_timebase(wfs[0]) for w in wfs) else [[w] for w in wfs]
    paths = []
    for group in groups:
        path = folder / ("waveform.csv" if len(groups) == 1 else f"waveform_{group[0].source}.csv")
        _write_group(path, group, names or {})
        paths.append(path)
    return paths


def _write_group(path: Path, group: list[Waveform], names: dict[str, str]) -> None:
    ref = group[0]
    scales = [(w.raw, w.ymult, w.yoff, w.yzero) for w in group]
    with path.open("w", newline="", encoding="utf-8-sig") as f:  # BOM: 엑셀에서 한글 채널 이름
        # 주석 줄: 채널 이름(엑셀에서 열면 첫 줄에 보임)
        f.write("# " + ", ".join(f"{w.source}={names.get(w.source, w.source)}" for w in group) + "\n")
        f.write("time_s," + ",".join(f"{w.source}_V" for w in group) + "\n")
        lines = []
        for i in range(len(ref)):
            t = (i - ref.pt_off) * ref.xincr + ref.xzero
            vals = ",".join(f"{(raw[i] - yoff) * ym + yz:.6g}" for raw, ym, yoff, yz in scales)
            lines.append(f"{t:.10g},{vals}\n")
            if len(lines) >= 20000:
                f.writelines(lines)
                lines.clear()
        f.writelines(lines)


def read_csv(path: Path) -> list[Waveform]:
    """write_csv 로 저장한 파일을 Waveform 목록(전압 단위)으로 읽는다."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = (r for r in csv.reader(f) if r and not r[0].startswith("#"))
        header = next(rows)
        cols: list[list[float]] = [[] for _ in header]
        for r in rows:
            for c, v in zip(cols, r):
                c.append(float(v))
    t = cols[0]
    xincr = (t[-1] - t[0]) / (len(t) - 1) if len(t) > 1 else 1.0
    return [Waveform(h.removesuffix("_V"), c, xincr, t[0]) for h, c in zip(header[1:], cols[1:])]

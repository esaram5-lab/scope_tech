"""저장된 측정 결과를 다시 판정하고 요약한다 (스코프 연결 불필요).

    python analyze.py                  captures/ 의 모든 결과를 현재 config.ini 기준으로 재판정 -> captures/summary.csv
    python analyze.py --spec 3V3       판정 기준 프로파일 지정(결과에 기록된 기준 대신)
    python analyze.py --recompute      VIH 결과를 waveform.csv 로 다시 계산(vin_avg_points / out_cross 를 바꾼 경우)
    python analyze.py --plot           VIH 폴더마다 xy.svg (입력-출력 전달 특성, 히스테리시스 루프) 생성
    python analyze.py --dir captures_sim
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scopetech import results  # noqa: E402
from scopetech.config import Config  # noqa: E402
from scopetech.recompute import load_waves, recompute_vih  # noqa: E402


def plot_xy(folder: Path, result: dict, spec: dict, max_points: int = 4000) -> Path:
    """입력(X) - 출력(Y) 전달 특성 SVG."""
    ch_in, ch_out = result["channels"]["in"], result["channels"]["out"]
    waves = load_waves(folder, [ch_in, ch_out])
    wi, wo = waves[ch_in], waves[ch_out]
    n = min(len(wi), len(wo))
    step = max(1, n // max_points)
    xs = [wi.to_volts(wi.raw[i]) for i in range(0, n, step)]
    ys = [wo.to_volts(wo.raw[i]) for i in range(0, n, step)]
    a = result.get("analysis", {})
    vlines = []
    for key, color, label in (("vt_plus", "#d62728", "VT+"), ("vt_minus", "#1f77b4", "VT-")):
        st = (a.get(key) or {}).get("stats")
        if st:
            vlines.append((st["mean"], color, f"{label} {st['mean']:.3f} V", "6,0"))
    if spec.get("vih_min") is not None:
        vlines.append((spec["vih_min"], "#d62728", f"VIH(min) {spec['vih_min']:.3f}", "4,4"))
    if spec.get("vil_max") is not None:
        vlines.append((spec["vil_max"], "#1f77b4", f"VIL(max) {spec['vil_max']:.3f}", "4,4"))

    x0, x1 = min(xs + [v[0] for v in vlines]), max(xs + [v[0] for v in vlines])
    y0, y1 = min(ys), max(ys)
    padx, pady = (x1 - x0) * 0.05 or 0.1, (y1 - y0) * 0.08 or 0.1
    x0, x1, y0, y1 = x0 - padx, x1 + padx, y0 - pady, y1 + pady
    W, H, L, R, T, B = 760, 480, 70, 20, 40, 55

    def sx(v: float) -> float:
        return L + (v - x0) / (x1 - x0) * (W - L - R)

    def sy(v: float) -> float:
        return H - B - (v - y0) / (y1 - y0) * (H - T - B)

    def ticks(lo: float, hi: float) -> list[float]:
        raw = (hi - lo) / 6
        mag = 10 ** math.floor(math.log10(raw))
        step_ = min((m * mag for m in (1, 2, 5, 10) if m * mag >= raw), default=mag * 10)
        t = math.ceil(lo / step_) * step_
        out = []
        while t <= hi:
            out.append(round(t, 10))
            t += step_
        return out

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="white"/>']
    for t in ticks(x0, x1):
        svg.append(f'<line x1="{sx(t):.1f}" y1="{T}" x2="{sx(t):.1f}" y2="{H - B}" stroke="#eee"/>')
        svg.append(f'<text x="{sx(t):.1f}" y="{H - B + 16}" text-anchor="middle">{t:g}</text>')
    for t in ticks(y0, y1):
        svg.append(f'<line x1="{L}" y1="{sy(t):.1f}" x2="{W - R}" y2="{sy(t):.1f}" stroke="#eee"/>')
        svg.append(f'<text x="{L - 6}" y="{sy(t) + 4:.1f}" text-anchor="end">{t:g}</text>')
    svg.append(f'<rect x="{L}" y="{T}" width="{W - L - R}" height="{H - T - B}" fill="none" stroke="#888"/>')
    pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys))
    svg.append(f'<polyline points="{pts}" fill="none" stroke="#333" stroke-width="1"/>')
    lx, ly = W - R - 170, H - B - 12 - 16 * len(vlines)
    if vlines:
        svg.append(f'<rect x="{lx - 8}" y="{ly - 12}" width="170" height="{16 * len(vlines) + 8}" fill="white" stroke="#ccc"/>')
    for i, (v, color, label, dash) in enumerate(vlines):
        svg.append(f'<line x1="{sx(v):.1f}" y1="{T}" x2="{sx(v):.1f}" y2="{H - B}" stroke="{color}" stroke-dasharray="{dash}"/>')
        y = ly + 16 * i
        svg.append(f'<line x1="{lx}" y1="{y - 4}" x2="{lx + 22}" y2="{y - 4}" stroke="{color}" stroke-dasharray="{dash}"/>')
        svg.append(f'<text x="{lx + 28}" y="{y}" fill="{color}">{label}</text>')
    title = f"{result.get('name', '')} {result.get('temp', '')}C  {ch_in}(X) vs {ch_out}(Y)"
    svg.append(f'<text x="{W / 2}" y="22" text-anchor="middle" font-size="14">{title}</text>')
    svg.append(f'<text x="{(L + W - R) / 2}" y="{H - 12}" text-anchor="middle">입력 {ch_in} [V]</text>')
    svg.append(f'<text x="16" y="{(T + H - B) / 2}" text-anchor="middle" transform="rotate(-90 16 {(T + H - B) / 2})">출력 {ch_out} [V]</text>')
    svg.append("</svg>")
    path = folder / "xy.svg"
    path.write_text("\n".join(svg), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="저장된 측정 결과 재판정/요약")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--dir", help="결과 폴더 (기본: config.ini 의 base_dir)")
    ap.add_argument("--spec", help="판정 기준 프로파일 지정")
    ap.add_argument("--recompute", action="store_true", help="VIH 결과를 파형 CSV 로 다시 계산")
    ap.add_argument("--plot", action="store_true", help="VIH 폴더마다 xy.svg 생성")
    args = ap.parse_args(argv)

    cfg = Config(args.config)
    base = Path(args.dir) if args.dir else HERE / cfg.get("output", "base_dir")
    if not base.is_dir():
        print(f"결과 폴더가 없습니다: {base}")
        return 1

    rows = []
    files = sorted(base.glob("*/*/result.json"))
    for f in files:
        folder = f.parent
        result = json.loads(f.read_text(encoding="utf-8"))
        if not result.get("metrics"):
            continue
        profile = args.spec or result.get("spec") or ""
        try:
            spec = cfg.spec(profile)
        except KeyError:
            print(f"[주의] {folder.name}: config.ini 에 [spec:{profile}] 가 없어 판정하지 않습니다.")
            spec = cfg.spec(None)
        try:
            if args.recompute and result.get("item") == "VIH":
                recompute_vih(folder, result, cfg)
                results.write_json(f, result)
            if args.plot and result.get("item") == "VIH":
                plot_xy(folder, result, spec)
        except FileNotFoundError as e:
            print(f"[건너뜀] {e}")
        if args.spec:
            result["spec"] = args.spec
        rows += results.rows_for(result, spec, folder.relative_to(base).as_posix())

    out = base / "summary.csv"
    try:
        results.write_csv(out, rows)
    except OSError as e:
        print(f"[오류] {out} 를 쓸 수 없습니다(엑셀에서 열려 있나요?): {e}")
        return 1
    n_fail = sum(1 for r in rows if r["judge"] == "FAIL")
    n_pass = sum(1 for r in rows if r["judge"] == "PASS")
    print(f"결과 {len(files)}건, 판정 PASS {n_pass} / FAIL {n_fail}  ->  {out}")
    for r in rows:
        if r["judge"] == "FAIL":
            print(f"  FAIL  {r['folder']}  {r['metric']}  worst {r['worst_V']} {r['rule']} {r['spec_V']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

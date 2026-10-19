"""측정 결과 판정과 기록(result.json, results.csv)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .analysis import ThresholdResult, summarize

# 판정 키 -> (spec 항목, 규칙)
RULES = {
    "VIH": ("vih_min", "<="),  # 입력 상승 시 실제 문턱(VT+)의 최댓값이 VIH(min) 이하
    "VIL": ("vil_max", ">="),  # 입력 하강 시 실제 문턱(VT-)의 최솟값이 VIL(max) 이상
    "VOH": ("voh_min", ">="),  # TOP 의 최솟값이 VOH(min) 이상
    "VOL": ("vol_max", "<="),  # BASE 의 최댓값이 VOL(max) 이하
}

FIELDS = [
    "timestamp", "item", "name", "temp_C", "out_type", "spec", "channel", "signal",
    "metric", "typ_V", "worst_V", "spec_V", "rule", "judge", "n", "folder",
]


def metric(channel: str, signal: str, name: str, key: str | None, typ, worst, n=None) -> dict:
    return {"channel": channel, "signal": signal, "metric": name, "key": key, "typ": typ, "worst": worst, "n": n}


def vih_metrics(th: ThresholdResult, pair: str, signal: str) -> list[dict]:
    p, m = summarize(th.vt_plus_v), summarize(th.vt_minus_v)
    rows = [
        metric(pair, signal, "VT+ (VIH측)", "VIH", p and p["mean"], p and p["max"], p and p["n"]),
        metric(pair, signal, "VT- (VIL측)", "VIL", m and m["mean"], m and m["min"], m and m["n"]),
        metric(pair, signal, "Hysteresis", None, th.hysteresis, None),
    ]
    if th.polarity == "unknown":  # 반전/비반전을 확정하지 못하면 판정하지 않는다
        for r in rows[:2]:
            r["invalid"] = "극성불명"
    return rows


def vih_output_metrics(th: ThresholdResult, out_label: str, signal: str) -> list[dict]:
    """VIL/VIH 측정 중 같은 파형에서 본 출력 VOH/VOL. 최악값: VOH 는 가장 낮은 High, VOL 은 가장 높은 Low."""
    h, lo = summarize(th.out_highs), summarize(th.out_lows)
    return [
        metric(out_label, signal, "VOH (출력 High)", "VOH", h and h["mean"], h and h["min"], h and h["n"]),
        metric(out_label, signal, "VOL (출력 Low)", "VOL", lo and lo["mean"], lo and lo["max"], lo and lo["n"]),
    ]


def judge(m: dict, spec: dict, out_type: str) -> tuple[float | None, str, str]:
    """(spec 값, 규칙, PASS/FAIL/N/A)."""
    key = m.get("key")
    if key not in RULES:
        return None, "", ""
    if key == "VOH" and out_type.upper() == "OD":
        return None, "", "N/A(OD)"
    if m.get("invalid"):
        return None, "", f"N/A({m['invalid']})"
    spec_key, rule = RULES[key]
    limit = spec.get(spec_key)
    worst = m.get("worst")
    if limit is None or worst is None:
        return limit, rule, "N/A"
    ok = worst <= limit if rule == "<=" else worst >= limit
    return limit, rule, "PASS" if ok else "FAIL"


def rows_for(result: dict, spec: dict, folder: str) -> list[dict]:
    rows = []
    for m in result.get("metrics", []):
        limit, rule, verdict = judge(m, spec, result.get("out_type", "PP"))
        rows.append({
            "timestamp": result.get("timestamp", ""),
            "item": result.get("item", ""),
            "name": result.get("name", ""),
            "temp_C": result.get("temp", ""),
            "out_type": result.get("out_type", ""),
            "spec": result.get("spec", ""),
            "channel": m["channel"],
            "signal": m["signal"],
            "metric": m["metric"],
            "typ_V": fmt(m.get("typ")),
            "worst_V": fmt(m.get("worst")),
            "spec_V": fmt(limit),
            "rule": rule,
            "judge": verdict,
            "n": m.get("n") if m.get("n") is not None else "",
            "folder": folder,
        })
    return rows


def fmt(v) -> str:
    return "" if v is None else f"{v:.5g}"


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_csv(path: Path, rows: list[dict]) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: 엑셀에서 한글이 깨지지 않도록 BOM 포함
    with path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

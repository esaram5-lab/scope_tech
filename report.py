"""저장된 측정 결과를 핀별 보고서(Markdown) 초안으로 정리한다 (스코프 연결 불필요).

    python report.py                   captures/ 전체 -> captures/report_날짜_시각.md
    python report.py --name INV_3V3    시험 이름에 INV_3V3 가 들어간 결과만
    python report.py --temp 85         85°C 결과만
    python report.py --spec 3V3        판정 기준 프로파일 지정(결과에 기록된 기준 대신)
    python report.py --dir captures_sim --out 보고서.md

핀마다 VIL(VT-) / VIH(VT+) / 히스테리시스 / VOL / VOH 를 표로 모으고, 측정별 상세 표와 화면 캡처를 붙인다.
VIL/VIH 측정의 VOH/VOL 은 출력 핀 아래에 들어간다. 이미지는 보고서 파일 위치 기준 상대 경로로 링크한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scopetech import manual_calc, results  # noqa: E402
from scopetech.config import Config  # noqa: E402

COLS = ("VIL", "VIH", "HYS", "VOL", "VOH")
COL_TITLE = {"VIL": "VIL (VT-)", "VIH": "VIH (VT+)", "HYS": "Hys", "VOL": "VOL", "VOH": "VOH"}
# 핀 요약의 최악값: 판정 규칙과 같은 방향 (VIL·VOH 는 가장 낮은 값, VIH·VOL 은 가장 높은 값)
WORST = {"VIL": min, "VIH": max, "HYS": min, "VOL": max, "VOH": min}
ITEM_TITLE = {"VIH": "VIL/VIH", "VOH": "VOH/VOL", "MANUAL": "데이터·화면 저장", "SNAP": "화면 캡처", "MISC": "기타 캡처"}
IMAGES = ("meas.png", "clean.png", "screen.png", "xy.svg")
NO_PIN = "미지정"


def column(m: dict) -> str | None:
    if m.get("key") in ("VIL", "VIH", "VOL", "VOH"):
        return m["key"]
    return "HYS" if str(m.get("metric", "")).startswith("Hysteresis") else None


def v3(x) -> str:
    return "-" if x is None else f"{x:.3f}"


def judge_text(j: str) -> str:
    return "**FAIL**" if j == "FAIL" else (j or "-")


def overall(judges: list[str]) -> str:
    if "FAIL" in judges:
        return "**FAIL**"
    return "PASS" if "PASS" in judges else "-"


def pin_key(pin: str) -> tuple:
    return (pin == NO_PIN, int(pin) if pin.isdigit() else 10**9, pin)


def temp_key(t) -> float:
    try:
        return float(t)
    except (TypeError, ValueError):
        return float("inf")


def link(path: Path, root: Path) -> str:
    """보고서 파일 위치 기준 상대 경로 (보고서를 다른 폴더에 만들어도 이미지가 열리도록)."""
    try:
        return Path(os.path.relpath(path, root)).as_posix().replace(" ", "%20")
    except ValueError:  # Windows 에서 드라이브가 다르면 상대 경로가 없음
        return path.as_uri()


def load(base: Path, cfg: Config, args) -> tuple[list[dict], set[str]]:
    """result.json 들을 읽어 (측정, 핀, 채널) 단위 항목 목록으로 만든다."""
    entries, profiles = [], set()
    for f in sorted(base.glob("*/*/result.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"[건너뜀] {f}: {e}")
            continue
        if args.name and args.name.lower() not in str(r.get("name", "")).lower():
            continue
        if args.temp is not None and str(r.get("temp", "")) != args.temp:
            continue
        if r.get("status") == "incomplete":
            print(f"[주의] 완료되지 않은 결과 포함: {f.parent.name}")
        folder = f.parent.relative_to(base).as_posix()
        profile = args.spec or r.get("spec") or ""
        try:
            spec = cfg.spec(profile)
        except KeyError:
            print(f"[주의] {f.parent.name}: config.ini 에 [spec:{profile}] 가 없어 판정하지 않습니다.")
            profile, spec = "", cfg.spec(None)
        if profile:
            profiles.add(profile)
        dut_pin = str(r.get("dut_pin") or NO_PIN)
        out_pin = str(r.get("out_pin") or "")
        images = [f.parent.resolve() / n for n in IMAGES if (f.parent / n).exists()]
        csv = f.parent.resolve() / "waveform.csv"
        common = {"result": r, "folder": folder, "profile": profile, "images": images,
                  "csv": csv if csv.exists() else None, "dut_pin": dut_pin}
        groups: dict[tuple[str, str], dict] = {}
        for m in r.get("metrics", []):
            col = column(m)
            if col is None:
                continue
            pin = out_pin if m.get("output") and out_pin else dut_pin
            e = groups.setdefault((pin, m["channel"]), {**common, "pin": pin, "channel": m["channel"],
                                                         "signal": m.get("signal", ""), "values": {}})
            limit, rule, verdict = results.judge(m, spec, r.get("out_type", "PP"))
            e["values"][col] = {"typ": m.get("typ"), "worst": m.get("worst") if m.get("worst") is not None else m.get("typ"),
                                "n": m.get("n"), "spec": limit, "rule": rule, "judge": verdict, "metric": m["metric"]}
        if groups:
            entries += groups.values()
        else:  # 측정값 없는 화면/데이터 저장
            entries.append({**common, "pin": dut_pin, "channel": "", "signal": "", "values": {}})
    return entries, profiles


def condition(r: dict) -> str:
    return f"{r.get('name', '')}, {r.get('temp', '')}°C, {r.get('out_type', '')}"


def build(base: Path, entries: list[dict], profiles: set[str], cfg: Config, root: Path) -> str:
    out: list[str] = []
    w = out.append
    scopes = sorted({e["result"].get("scope", "") for e in entries} - {""})
    w("# I/O 레벨 측정 보고서 (초안)\n")
    w("| 항목 | 내용 |\n|---|---|")
    w("| DUT | (작성: 예 SLG4AD4449) |")
    w("| 목적 | (작성) |")
    w("| 측정 장비 | " + (" / ".join(scopes) or "-") + " |")
    temps = sorted({str(e["result"].get("temp", "")) for e in entries}, key=temp_key)
    w("| 온도 | " + ", ".join(f"{t}°C" for t in temps) + " |")
    w(f"| 결과 폴더 | `{base}` |")
    w(f"| 생성 | {datetime.now():%Y-%m-%d %H:%M} (report.py) |\n")

    w("## 측정 방법\n")
    w("- **VIL / VIH**: 입력에 0~VCCIO 삼각파를 넣고 출력이 자기 진폭의 50% 를 지나는 순간의 입력 전압을 문턱으로 봅니다. "
      "입력 상승 시 문턱 VT+ 를 VIH 측, 하강 시 문턱 VT- 를 VIL 측으로 표기하고, 여러 번 전환 중 최악값(VT+ 최댓값, VT- 최솟값)을 씁니다.")
    w("- **VOL / VOH (VIL/VIH 측정 중)**: 같은 파형에서 출력 High/Low 구간 가운데 50% 의 평균. 최악값은 VOH 가장 낮은 값, VOL 가장 높은 값.")
    w("- **VOL / VOH (단독 측정)**: 스코프 TOP(=VOH) / BASE(=VOL) 측정 통계. 최악값은 TOP 최솟값, BASE 최댓값.")
    w("- OD 출력은 풀업 전압이 High 이므로 VOH 를 판정하지 않습니다.")
    w(f"- **Rpd / Rpu**: 외부 {cfg.getfloat('manual', 'r_ext_ohm') / 1e6:g} MΩ 을 Rpd 는 핀→VDD, Rpu 는 핀→GND 로 연결하고 "
      f"핀 전압 V 를 프로브(입력 {cfg.getfloat('manual', 'probe_r_ohm') / 1e6:g} MΩ, TPP1000)로 측정, VDD = {cfg.getfloat('manual', 'vdd_v'):g} V. "
      "프로브 입력 저항을 병렬로 보정합니다: Rpd = 1/(1/(R_ext·V/(VDD−V)) − 1/R_probe), Rpu = (R_ext‖R_probe)·(VDD−V)/V.")
    w("- **IOH / IOL**: 전류 프로브 채널의 레코드 평균. 함께 저장한 핀 전압 채널이 있으면 그때 전압도 적습니다.\n")

    if profiles:
        w("## 판정 기준 (config.ini)\n")
        w("| 프로파일 | VIH(min) | VIL(max) | VOH(min) | VOL(max) |\n|---|---|---|---|---|")
        for p in sorted(profiles):
            s = cfg.spec(p)
            w(f"| {p} | {v3(s['vih_min'])} | {v3(s['vil_max'])} | {v3(s['voh_min'])} | {v3(s['vol_max'])} |")
        w("")

    pins: dict[str, list[dict]] = {}
    for e in entries:
        pins.setdefault(e["pin"], []).append(e)
    order = sorted(pins, key=pin_key)

    w("## 핀별 요약 (전 조건 최악값, 단위 V)\n")
    w("| 핀 | 측정 수 | " + " | ".join(COL_TITLE[c] for c in COLS) + " | 판정 |")
    w("|---|---|" + "---|" * len(COLS) + "---|")
    for pin in order:
        es = [e for e in pins[pin] if e["values"]]
        cells = []
        for c in COLS:
            vals = [e["values"][c]["worst"] for e in es if c in e["values"] and e["values"][c]["worst"] is not None]
            cells.append(v3(WORST[c](vals)) if vals else "-")
        judges = [v["judge"] for e in es for v in e["values"].values()]
        w(f"| {pin} | {len(pins[pin])} | " + " | ".join(cells) + f" | {overall(judges)} |")
    w("")

    calcs = [e for e in entries if e["result"].get("calc")]
    if calcs:
        w("## 핀별 저항·전류 (메뉴 3 수동 측정)\n")
        w("| 핀 | 항목 | 시험 | 온도(°C) | 값 | 핀 전압 (V) | 측정 시각 |\n|---|---|---|---|---|---|---|")
        for e in sorted(calcs, key=lambda e: (pin_key(e["pin"]), e["result"]["calc"]["kind"],
                                               temp_key(e["result"].get("temp")), e["result"].get("timestamp", ""))):
            r, c = e["result"], e["result"]["calc"]
            value, unit = manual_calc.display(c)
            vpin = c["inputs"].get("v_pin")
            w(f"| {e['pin']} | {c['name']} | {r.get('name', '')} | {r.get('temp', '')} | {value} {unit} | "
              f"{v3(vpin)} | {r.get('timestamp', '').replace('T', ' ')} |")
        w("")

    for pin in order:
        es = sorted(pins[pin], key=lambda e: (temp_key(e["result"].get("temp")), e["result"].get("timestamp", "")))
        w(f"## 핀 {pin}\n")
        measured = [e for e in es if e["values"]]
        if measured:
            w("| # | 시험 | 온도(°C) | 출력 | 채널 | " + " | ".join(COL_TITLE[c] for c in COLS) + " | 판정 |")
            w("|---|---|---|---|---|" + "---|" * len(COLS) + "---|")
            for i, e in enumerate(measured, 1):
                r = e["result"]
                cells = [v3(e["values"][c]["worst"]) if c in e["values"] else "-" for c in COLS]
                w(f"| {i} | {r.get('name', '')} | {r.get('temp', '')} | {r.get('out_type', '')} | {e['channel']} | "
                  + " | ".join(cells) + f" | {overall([v['judge'] for v in e['values'].values()])} |")
            w("")
        w("메모: (작성)\n")
        for i, e in enumerate(es, 1):
            detail(w, pin, i, e, root)
    return "\n".join(out).rstrip() + "\n"


def detail(w, pin: str, i: int, e: dict, root: Path) -> None:
    r = e["result"]
    item = r.get("item", "")
    title = ITEM_TITLE.get(item, item)
    if item == "MANUAL":
        title += f" — {r.get('manual_item', '')}"
    elif item == "SNAP":
        title += f" — {r.get('snap_item', '')}"
    w(f"### {pin}-{i}. {title} ({condition(r)}, {r.get('timestamp', '').replace('T', ' ')})\n")
    info = [f"폴더: `{e['folder']}`"]
    if item == "VIH":
        ch = r.get("channels", {})
        info.append(f"입력 핀 {r.get('dut_pin') or '-'} ({ch.get('in', '')}) → 출력 핀 {r.get('out_pin') or '-'} ({ch.get('out', '')})")
        pol = (r.get("analysis") or {}).get("polarity")
        if pol:
            info.append("출력 극성: " + {"inverting": "반전", "non-inverting": "비반전"}.get(pol, "판별 불가"))
    elif e["channel"]:
        info.append(f"채널: {e['channel']} ({e['signal']})")
    if e["profile"]:
        info.append(f"판정 기준: {e['profile']}")
    if r.get("note"):
        info.append(f"조건 메모: {r['note']}")
    if r.get("status") == "incomplete":
        info.append("**상태: 완료되지 않음**")
    for line in info:
        w(f"- {line}")
    w("")
    calc = r.get("calc")
    if calc:
        value, unit = manual_calc.display(calc)
        inp = calc["inputs"]
        w(f"| 항목 | 값 | 계산 근거 |\n|---|---|---|\n| {calc['name']} | {value} {unit} | {calc['note']} |\n")
        if calc["unit"] == "ohm":
            w(f"- 입력: 핀 전압 {inp['channel']} = {inp['v_pin']:.4f} V, VDD = {inp['vdd']:g} V, "
              f"R_ext = {inp['r_ext'] / 1e6:g} MΩ, 프로브 = {inp['r_probe'] / 1e6:g} MΩ\n")
    if e["values"]:
        w("| 항목 | typ (V) | 최악 (V) | n | 기준 (V) | 판정 |\n|---|---|---|---|---|---|")
        for c in COLS:
            v = e["values"].get(c)
            if v:
                limit = f"{v['rule']} {v3(v['spec'])}" if v["spec"] is not None else "-"
                n = v["n"] if v["n"] is not None else "-"
                worst = "-" if c == "HYS" else v3(v["worst"])  # 히스테리시스는 평균만 있음
                w(f"| {v['metric']} | {v3(v['typ'])} | {worst} | {n} | {limit} | {judge_text(v['judge'])} |")
        w("")
    primary = e["pin"] == e["dut_pin"]
    if e["images"]:
        for img in e["images"]:
            # VIL/VIH 의 출력 핀 쪽은 같은 캡처를 링크만 걸어 보고서가 길어지지 않게 한다
            w(f"![{img.name}]({link(img, root)})\n" if primary else f"- 캡처: [{img.name}]({link(img, root)})")
        if not primary:
            w("")
    elif r.get("capture") == "off":
        w("_화면 캡처 안 함 (캡처 끈 측정)_\n")
    elif r.get("item") in ("VIH", "VOH", "MANUAL", "SNAP"):
        w("_화면 캡처 없음 (스코프에서 아직 가져오지 않았다면 `py scope.py --fetch`)_\n")
    if e["csv"]:
        w(f"- 파형 데이터: [waveform.csv]({link(e['csv'], root)})\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="측정 결과를 핀별 Markdown 보고서로 정리")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--dir", help="결과 폴더 (기본: config.ini 의 base_dir)")
    ap.add_argument("--out", help="보고서 파일 (기본: 결과폴더/report_날짜_시각.md)")
    ap.add_argument("--spec", help="판정 기준 프로파일 지정")
    ap.add_argument("--name", help="시험 이름에 이 글자가 들어간 결과만")
    ap.add_argument("--temp", help="이 온도 결과만 (예: 85, -40)")
    args = ap.parse_args(argv)

    cfg = Config(args.config)
    base = Path(args.dir) if args.dir else HERE / cfg.get("output", "base_dir")
    if not base.is_dir():
        print(f"결과 폴더가 없습니다: {base}")
        return 1
    entries, profiles = load(base, cfg, args)
    if not entries:
        print(f"보고서에 넣을 결과가 없습니다: {base}")
        return 1
    out = Path(args.out) if args.out else base / f"report_{datetime.now():%Y%m%d_%H%M%S}.md"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build(base, entries, profiles, cfg, out.parent.resolve()), encoding="utf-8")
    except OSError as e:
        print(f"[오류] {out} 를 쓸 수 없습니다: {e}")
        return 1
    pins = sorted({e["pin"] for e in entries}, key=pin_key)
    print(f"보고서: {out}  (핀 {len(pins)}개: {', '.join(pins)} / 항목 {len(entries)}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

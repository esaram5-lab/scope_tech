"""온도별 핀 측정값 표(CSV): 행 = 핀·항목(VIL/VIH/Hys/VOL/VOH), 열 = 온도 (스코프 연결 불필요).

    python temp_table.py                  captures/ 전체 -> captures/temp_table_날짜_시각.csv
    python temp_table.py --name INV_3V3   시험 이름에 INV_3V3 가 들어간 결과만
    python temp_table.py --pick worst     같은 핀·온도를 여러 번 쟀으면 최악값 (기본 latest: 마지막 측정)
    python temp_table.py --value typ      값 대신 평균(typ) (기본 worst: 판정에 쓰는 최악값)

값 기준은 report.py 와 같다: VIL = VT- 최솟값, VIH = VT+ 최댓값, VOL 최댓값, VOH 최솟값 (단위 V).
VIL/VIH 측정의 출력 VOH/VOL 은 출력 핀 행에 들어간다. 엑셀에서 바로 열리도록 UTF-8(BOM) 로 쓴다.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from report import COLS, COL_TITLE, WORST, load, pin_key, temp_key  # noqa: E402
from scopetech.config import Config  # noqa: E402


def table(entries: list[dict], pick: str = "latest", value: str = "worst") -> tuple[list[str], list[list[str]]]:
    """(온도 목록, 행 목록). 행 = [핀, 항목, 시험, 출력, 채널, 온도별 값...]."""
    cells: dict[tuple, dict[str, list[tuple[str, float]]]] = {}
    temps = set()
    for e in entries:
        r = e["result"]
        temp = str(r.get("temp", ""))
        for col, v in e["values"].items():
            x = v["worst"] if value == "worst" else v["typ"]
            if x is None:
                continue
            key = (e["pin"], col, str(r.get("name", "")), str(r.get("out_type", "")), e["channel"])
            cells.setdefault(key, {}).setdefault(temp, []).append((r.get("timestamp", ""), x))
            temps.add(temp)
    order = sorted(temps, key=temp_key)
    rows = []
    for key in sorted(cells, key=lambda k: (pin_key(k[0]), k[2], COLS.index(k[1]), k[4])):
        pin, col, name, out_type, channel = key
        row = [pin, COL_TITLE[col], name, out_type, channel]
        for t in order:
            got = cells[key].get(t)
            if not got:
                row.append("")
            elif pick == "latest":
                row.append(f"{max(got)[1]:.4f}")
            else:
                row.append(f"{WORST[col](x for _, x in got):.4f}")
        rows.append(row)
    return order, rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="온도별 핀 측정값 표(CSV)")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--dir", help="결과 폴더 (기본: config.ini 의 base_dir)")
    ap.add_argument("--out", help="CSV 파일 (기본: 결과폴더/temp_table_날짜_시각.csv)")
    ap.add_argument("--name", help="시험 이름에 이 글자가 들어간 결과만")
    ap.add_argument("--pick", choices=["latest", "worst"], default="latest",
                    help="같은 핀·온도를 여러 번 쟀을 때: 마지막 측정(기본) / 최악값")
    ap.add_argument("--value", choices=["worst", "typ"], default="worst", help="측정 1회의 최악값(기본) / 평균")
    args = ap.parse_args(argv)
    args.temp, args.spec = None, None  # report.load 와 같은 필터 인자

    cfg = Config(args.config)
    base = Path(args.dir) if args.dir else HERE / cfg.get("output", "base_dir")
    if not base.is_dir():
        print(f"결과 폴더가 없습니다: {base}")
        return 1
    entries, _ = load(base, cfg, args)
    temps, rows = table([e for e in entries if e["values"]], args.pick, args.value)
    if not rows:
        print(f"표에 넣을 측정값이 없습니다: {base}")
        return 1
    out = Path(args.out) if args.out else base / f"temp_table_{datetime.now():%Y%m%d_%H%M%S}.csv"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["핀", "항목", "시험", "출력", "채널"] + [f"{t}°C" for t in temps])
            w.writerows(rows)
    except OSError as e:
        print(f"[오류] {out} 를 쓸 수 없습니다(엑셀에서 열려 있나요?): {e}")
        return 1
    print(f"온도별 표: {out}  (행 {len(rows)}개, 온도 {', '.join(f'{t}°C' for t in temps)}, "
          f"값 {args.value}, 반복 측정 {args.pick})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

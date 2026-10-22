"""예전 측정 기록(결과 폴더의 VIH / MANUAL / POR 측정)을 시험 계획 진행 기록에 붙인다 (스코프 연결 불필요).

기록 1건 -> (온도, 계획 줄) 연결
    온도: result.json 의 temp (없으면 폴더 이름의 _25C_)
    VIH(메뉴 1)      -> VI/AI 줄: 입력 핀 = dut_pin, 출력 핀 = out_pin (예전 기록에 없으면 계획에서 입력 핀으로 찾음)
    MANUAL(메뉴 3)   -> 항목 이름 R_pd/R_pu/IOH/IOL 의 RPD/RPU/IOH/IOL 줄, 핀 = dut_pin
    POR              -> POR 줄, 핀 = dut_pin
    핀은 result.json 에 없으면 폴더 이름(PIN12_OUT7_...)에서 읽는다.
같은 줄의 기록이 여럿이면 가장 최근(timestamp) 것 중 값이 나오는 기록을 쓴다.
모자란 값은 저장된 파형(waveform*.csv)으로 채운다: 예전 VIL/VIH 기록의 출력 VOH/VOL, 계산값이 없는 R_pd/R_pu/IOH.
그때 result.json 을 고쳐 쓰며, 처음 한 번 result.orig.json 으로 원본을 남긴다.
계획 모드로 이미 완료한 줄은 바꾸지 않는다(replace=True 이면 가장 최근 기록으로 바꾼다).
"""
from __future__ import annotations

import copy
import csv
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import manual_calc, results
from .plan import DONE, MANUAL_LABEL, PlanRow, PlanStore, check_bias, describe, norm_temp, row_values, warnings_of
from .plan_state import SettingsView
from .recompute import recompute_manual, recompute_vih

ITEM_DIRS = ("VIH", "MANUAL", "POR")
SOURCE = "가져옴"
_PIN = re.compile(r"^PIN([^_]+?)(?:_OUT([^_]+))?_")
_TEMP = re.compile(r"_(-?\d+(?:\.\d+)?)C_")
_TS = re.compile(r"_(\d{8})_(\d{6})(?:_|$)")
BAD_DATA = (OSError, KeyError, TypeError, ValueError, IndexError, ZeroDivisionError)  # 깨진·모자란 예전 파일


@dataclass
class Found:
    """결과 폴더의 측정 기록 1건."""
    folder: Path
    rel: str
    result: dict
    item: str
    temp: str = ""
    kind: str = ""  # VI(VI/AI 줄 모두), POR, IOH, IOL, RPU, RPD
    pin: str = ""
    out_pin: str = ""
    time: str = ""
    problem: str = ""  # 계획 줄에 연결할 수 없는 이유


@dataclass
class Pick:
    """(온도, 계획 줄) 하나에 붙일 기록."""
    row: PlanRow
    temp: str
    found: Found | None = None
    result: dict | None = None  # 채운 뒤의 결과(found.result 의 사본)
    changed: bool = False  # 파형으로 값을 채워 result.json 을 고쳐 써야 함
    notes: list[str] = field(default_factory=list)
    state: str = ""  # 가져옴 / 유지 / 같음 / 없음 / 제외
    candidates: int = 0
    options: list[Found] = field(default_factory=list)  # 같은 줄의 모든 후보(최근 것부터, GUI 에서 고르기)
    entry: dict = field(default_factory=dict)            # 지금 진행 기록의 이 줄


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _time_of(result: dict, folder: Path) -> str:
    ts = str(result.get("timestamp") or "")
    if ts:
        return ts
    m = _TS.search(folder.name)
    if m:
        d, t = m.groups()
        return f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:4]}:{t[4:]}"
    return datetime.fromtimestamp(folder.stat().st_mtime).isoformat(timespec="seconds")


def scan(base: Path) -> list[Found]:
    """결과 폴더의 VIH / MANUAL / POR 측정 기록과 각 기록의 온도·종류·핀."""
    out = []
    for item in ITEM_DIRS:
        for path in sorted((Path(base) / item).glob("*/result.json")):
            folder = path.parent
            result = _read(path)
            f = Found(folder, folder.relative_to(base).as_posix(), result or {}, item)
            if result is None:
                f.problem = "result.json 을 읽을 수 없음"
                out.append(f)
                continue
            f.time = _time_of(result, folder)
            name_pin = _PIN.match(folder.name)
            f.pin = str(result.get("dut_pin") or (name_pin.group(1) if name_pin else "")).strip()
            f.out_pin = str(result.get("out_pin") or (name_pin.group(2) if name_pin and name_pin.group(2) else "")).strip()
            try:
                f.temp = norm_temp(str(result.get("temp", "")))
            except ValueError:
                m = _TEMP.search(folder.name)
                f.temp = norm_temp(m.group(1)) if m else ""
            if item == "VIH":
                f.kind = "VI"
            elif item == "POR":
                f.kind = "POR"
            else:
                label = str(result.get("manual_item", ""))
                f.kind = manual_calc.kind_of(label) or ""
                if not f.kind:
                    f.problem = f"시험 항목 이름 '{label}' 은 계획 항목(R_pd / R_pu / IOH / IOL)이 아님"
                elif not result.get("calc") and not any(folder.glob("waveform*.csv")):
                    f.problem = "계산값도 파형 CSV 도 없음(저장 중 실패한 기록)"
            if not f.problem and not f.temp:
                f.problem = "온도를 알 수 없음"
            if not f.problem and not f.pin:
                f.problem = "핀 정보 없음(핀 번호 없이 측정한 예전 기록)"
            out.append(f)
    return out


def rows_for(f: Found, rows: list[PlanRow]) -> tuple[list[PlanRow], str]:
    """기록이 들어갈 계획 줄들과, 못 찾았으면 그 이유."""
    if f.kind == "VI":
        hit = [r for r in rows if r.kind in ("VI", "AI") and r.in_pin == f.pin and (not f.out_pin or r.out_pin == f.out_pin)]
        if not hit:
            return [], f"계획에 입력 {f.pin}" + (f" -> 출력 {f.out_pin}" if f.out_pin else "") + " 인 VI/AI 줄이 없음"
        if not f.out_pin and len({r.out_pin for r in hit}) > 1:
            return [], f"출력 핀 기록이 없고 계획에 입력 {f.pin} 인 줄이 여럿이라 정할 수 없음"
        return hit, ""
    hit = [r for r in rows if r.kind == f.kind and r.out_pin == f.pin]
    return hit, ("" if hit else f"계획에 핀 {f.pin} 의 {f.kind} 줄이 없음")


def _why(e: Exception, f: Found) -> str:
    return str(e).splitlines()[0].replace(f"{f.folder}: ", "")


def fill(row: PlanRow, f: Found, cfg) -> tuple[dict, bool, list[str]]:
    """(채운 결과 사본, 바뀌었는지, 메모). 파형으로 모자란 값을 채운다. 값이 하나도 없으면 ValueError.
    계획 모드 기록은 그 측정에 쓴 설정(plan_settings)으로, 예전 기록은 지금 설정으로 계산한다."""
    result = copy.deepcopy(f.result)
    cfg = SettingsView(cfg, result.get("plan_settings"))
    notes = []
    if not result.get("dut_pin"):  # 폴더 이름에서 읽은 핀을 보고서·사진 모음도 쓰도록 기록
        result["dut_pin"] = row.pin
    if row.kind in ("VI", "AI") and not result.get("out_pin"):
        result["out_pin"] = row.out_pin
        if not f.out_pin:
            notes.append(f"출력 핀 기록이 없어 계획의 출력 핀 {row.out_pin} 으로 연결")
    if row.kind in ("VI", "AI"):
        if not any(m.get("output") for m in result.get("metrics", [])):
            try:
                recompute_vih(f.folder, result, cfg)
                notes.append("출력 VOH/VOL 을 저장된 파형으로 계산")
            except BAD_DATA as e:
                notes.append(f"출력 VOH/VOL 없음({_why(e, f)})")
    elif row.kind != "POR":
        calc = result.get("calc") or {}
        first = f"CH{row.channels[0]}"
        saved = list(result.get("channels") or [])
        reorder = bool(calc) and (calc.get("inputs") or {}).get("channel") != first and first in saved
        if first not in saved:
            notes.append(f"저장 채널({', '.join(saved) or '없음'})에 계획 채널 {first} 가 없어 저장 순서대로 계산")
        if not calc or reorder:
            try:
                recompute_manual(f.folder, result, cfg, MANUAL_LABEL[row.kind], [f"CH{c}" for c in row.channels])
                notes.append("계산값을 저장된 파형으로 " + ("계획의 채널 순서대로 다시 계산" if reorder else "계산"))
            except BAD_DATA as e:
                if not calc:
                    raise ValueError(f"계산값 없음, {_why(e, f)}") from None
                notes.append(f"채널 순서가 계획과 다르지만 다시 계산하지 못함({_why(e, f)})")
        check_bias(row, result, cfg)
    if not row_values(row, result):
        why = (result.get("calc") or {}).get("note") if row.kind not in ("VI", "AI", "POR") else ""
        raise ValueError("요약표에 넣을 값이 없음" + (f"({why})" if why else ""))
    return result, json.dumps(result, sort_keys=True) != json.dumps(f.result, sort_keys=True), notes


def plan_import(rows: list[PlanRow], store: PlanStore, cfg, found: list[Found], replace: bool = False,
                name: str = "", since: str = "", temps: list[str] | None = None,
                order: list[str] = ()) -> tuple[list[Pick], list[Found]]:
    """(온도·줄마다 붙일 기록, 연결하지 못한 기록). 파일은 쓰지 않는다(apply 가 씀). order: 온도 표시 순서."""
    unmatched = []
    cand: dict[tuple[str, str], list[Found]] = {}
    for f in found:
        if name and name not in str(f.result.get("name", "")):
            continue
        if since and f.time < since:
            continue
        if temps is not None and f.temp and f.temp not in temps:
            continue
        hit, why = ([], f.problem) if f.problem else rows_for(f, rows)
        if not hit:
            f.problem = why
            unmatched.append(f)
        for r in hit:
            cand.setdefault((f.temp, r.key), []).append(f)
    picks = []
    order = list(order)
    for temp in sorted({t for t, _ in cand}, key=lambda t: (order.index(t) if t in order else len(order), float(t))):
        progress = store.load(temp).get("rows", {})
        for row in rows:
            got = sorted(cand.get((temp, row.key), []), key=lambda f: (f.time, f.rel), reverse=True)
            if not got:
                continue
            p = Pick(row, temp, candidates=len(got), options=got, entry=dict(progress.get(row.key, {})))
            failed = []
            for f in got:  # 최근 기록부터, 값이 나오는 첫 기록
                try:
                    p.result, p.changed, p.notes = fill(row, f, cfg)
                except ValueError as e:
                    failed.append(f"{f.rel}: {e}")
                    continue
                p.found = f
                break
            if failed:
                skipped = [f"더 최근 기록을 쓸 수 없어 건너뜀 - {x}" if p.found else f"쓸 수 없음 - {x}" for x in failed[:3]]
                p.notes = skipped + ([f"... 같은 줄의 기록 {len(failed) - 3}개 더 쓸 수 없음"] if len(failed) > 3 else []) + p.notes
            e = progress.get(row.key, {})
            done = e.get("status") == DONE
            if p.found is None:
                p.state = "없음"
            elif done and e.get("folder") == p.found.rel and not p.changed:
                p.state = "같음"
            elif done and e.get("source") != SOURCE and not replace:  # 계획 모드로 잰 값은 그대로
                p.state = "유지"
                if p.found.rel != e.get("folder"):
                    p.notes.append(f"계획 측정({e.get('folder')})과 다른 최근 기록 있음 -> --replace 로 교체")
            else:
                p.state = SOURCE
            picks.append(p)
    return picks, unmatched


def choose(p: Pick, f: Found, cfg) -> None:
    """GUI 에서 고른 후보로 바꾼다(가장 최근 기록 대신). 값이 안 나오는 기록이면 ValueError."""
    result, changed, notes = fill(p.row, f, cfg)
    p.found, p.result, p.changed = f, result, changed
    p.notes = [f"직접 고른 기록 ({f.time})"] + notes
    same = p.entry.get("status") == DONE and p.entry.get("folder") == f.rel and not changed
    p.state = "같음" if same else SOURCE


def include(p: Pick, on: bool) -> None:
    """GUI 체크: 가져올지. 기록이 없거나 이미 연결된 줄은 바꾸지 않는다."""
    if p.found is None or p.state == "같음":
        return
    p.state = SOURCE if on else "제외"


def apply(picks: list[Pick], store: PlanStore) -> list[Path]:
    """고른 기록을 진행 기록에 쓰고(필요하면 result.json 도), 쓴 진행 기록 파일 목록을 돌려준다."""
    now = datetime.now().isoformat(timespec="seconds")
    by_temp: dict[str, list[Pick]] = {}
    for p in picks:
        if p.state == SOURCE:
            by_temp.setdefault(p.temp, []).append(p)
    written = []
    for temp, ps in by_temp.items():
        doc = store.load(temp)
        doc.setdefault("rows", {})
        doc.setdefault("temp", temp)
        for p in ps:
            f = p.found
            if p.changed:
                backup = f.folder / "result.orig.json"
                if not backup.exists():
                    shutil.copy2(f.folder / "result.json", backup)
                results.write_json(f.folder / "result.json", p.result)
            doc.setdefault("name", str(p.result.get("name", "")))
            doc["rows"][p.row.key] = {"status": DONE, "folder": f.rel, "time": f.time,
                                      "summary": describe(p.row, p.result),
                                      "warnings": warnings_of(p.row, p.result),
                                      "source": SOURCE, "imported": now}
        store.save(temp, doc)
        written.append(store.progress_path(temp))
    return written


def write_log(picks: list[Pick], unmatched: list[Found], path: Path) -> Path:
    """가져오기 내역(엑셀용 CSV): 줄마다 고른 기록과 연결하지 못한 기록의 이유."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["온도", "순서", "항목", "핀", "처리", "기록", "측정 시각", "후보 수", "결과", "메모"])
        for p in picks:
            w.writerow([p.temp, p.row.no, p.row.kind, _pins(p.row), p.state, p.found.rel if p.found else "",
                        p.found.time if p.found else "", p.candidates,
                        describe(p.row, p.result) if p.result else "", " / ".join(p.notes)])
        for x in unmatched:
            w.writerow([x.temp, "", x.kind or x.item, x.pin, "연결 안 됨", x.rel, x.time, "", "", x.problem])
    return path


def _pins(row: PlanRow) -> str:
    return f"{row.in_pin}->{row.out_pin}" if row.kind in ("VI", "AI") else row.out_pin


STATE_TEXT = {SOURCE: "가져옴", "유지": "계획 측정 유지", "같음": "이미 연결됨", "없음": "쓸 수 있는 기록 없음",
              "제외": "가져오지 않음"}


def preview(picks: list[Pick], unmatched: list[Found], rows: list[PlanRow], echo=print, limit: int = 40) -> None:
    temps = []
    for p in picks:
        if p.temp not in temps:
            temps.append(p.temp)
    for temp in temps:
        ps = [p for p in picks if p.temp == temp]
        new = sum(p.state == SOURCE for p in ps)
        echo(f"\n[{temp}°C] 가져올 항목 {new}개 / 계획 {len(rows)}개 중 기록 있는 항목 {len(ps)}개")
        for p in ps:
            line = f"  {p.row.no:>2} {p.row.kind:<3} {_pins(p.row):<8} {STATE_TEXT[p.state]:<10}"
            if p.found:
                line += f" {describe(p.row, p.result)}\n{'':17}{p.found.rel}  ({p.found.time}"
                line += f", 후보 {p.candidates}개 중 최신)" if p.candidates > 1 else ")"
            for n in p.notes:
                line += f"\n{'':17}- {n}"
            echo(line)
        missing = [r.no for r in rows if all(p.row.key != r.key for p in ps)]
        if missing:
            echo(f"  기록 없는 순서: {', '.join(missing)}")
    if unmatched:
        echo(f"\n계획에 연결하지 못한 기록 {len(unmatched)}개:")
        for x in unmatched[:limit]:
            echo(f"  {x.rel} : {x.problem}")
        if len(unmatched) > limit:
            echo(f"  ... 외 {len(unmatched) - limit}개 (가져오기 내역 CSV 에 모두 기록)")

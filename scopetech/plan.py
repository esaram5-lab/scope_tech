"""시험 계획 진행: test_plan.csv 의 항목을 순서대로 측정하고, 온도별 진행 기록과 요약표(plan_summary.csv)를 만든다.

계획 파일 (test_plan.csv, 엑셀로 편집 가능, UTF-8)
    순서,항목,입력핀,출력핀,출력타입,채널,준비
    항목: VI(입력 VIL/VIH + 출력 VOH/VOL), AI(아날로그 입력, VI 와 같은 측정), POR(전원 인가 후 출력 VOL/VOH),
          IOH / IOL(전류), RPU / RPD(내부 풀업/풀다운 저항)
    IOH / RPU / RPD 처럼 핀이 하나인 항목은 대상 핀을 '출력핀' 칸에 적는다(입력핀은 X 또는 빈칸).
    채널: VI/AI = 입력 출력, POR = VDD 출력, IOH = 전류 핀전압, RPU/RPD = 핀전압 (공백·쉼표 구분)

요약표 순서 (test_plan_summary.csv)
    구분,핀,항목,단위     항목 = VIH, VIL, VOH, VOL, IOH, IOL, Rpu, Rpd / 단위 = V, mA, kΩ
값은 그 온도의 계획 측정 결과에서 가져오고, 같은 값이 여러 측정에서 나오면 최악값(VIH 최대, VIL·VOH 최소, VOL 최대).
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import manual_calc, results
from .plan_state import OVERRIDES_FILE, STATE_FILE, PlanSettings, StateWriter, describe_change, replace_retry
from .transport import ScopeError

KINDS = ("VI", "AI", "POR", "IOH", "IOL", "RPU", "RPD")
MANUAL_LABEL = {"IOH": "IOH", "IOL": "IOL", "RPU": "R_pu", "RPD": "R_pd"}
CHANNEL_COUNT = {"VI": 2, "AI": 2, "POR": 2, "IOH": (1, 2), "IOL": (1, 2), "RPU": (1, 1), "RPD": (1, 1)}
GROUP = {"VI": "VI", "AI": "VI", "POR": "POR", "IOH": "IOH", "IOL": "IOH", "RPU": "RPU", "RPD": "RPD"}
GROUP_GUIDE = {
    "VI": "[VI/AI] CH1 = 입력 핀(함수발생기 삼각파 0~VCCIO), CH2 = 출력 핀, 트리거 CH1.",
    "POR": "[POR] CH1 = VDD, CH2 = 출력 핀, 트리거 CH1 상승(VDD 켜짐), 시간축은 POR 지연(약 2 ms)보다 길게(약 1 ms/div). "
           "VDD 를 끈 상태에서 Enter 를 누른 뒤 VDD 를 켜세요.",
    "IOH": "[IOH/IOL] 전류 프로브와 핀 전압 채널 연결, 부하로 핀 전압을 맞추세요. CH1 은 트리거용 스윙(화면에서 숨김). 입력 핀은 모두 Low.",
    "RPU": "[Rpu] 외부 저항을 핀 -> GND, CH2 = 핀 전압, CH1 은 트리거용 스윙.",
    "RPD": "[Rpd] 외부 저항을 핀 -> VDD, CH2 = 핀 전압, CH1 은 트리거용 스윙.",
}
WORST = {"VIH": max, "VIL": min, "VOH": min, "VOL": max}
UNIT_FORMAT = {"V": "{:.4f}", "mA": "{:.3f}", "kΩ": "{:.1f}"}
DONE, SKIP, FAIL = "완료", "건너뜀", "실패"


@dataclass
class PlanRow:
    no: str
    kind: str
    in_pin: str
    out_pin: str
    out_type: str
    channels: list[int]
    setup: str = ""

    @property
    def key(self) -> str:
        return f"{self.no}|{self.kind}|{self.in_pin}|{self.out_pin}"

    @property
    def pin(self) -> str:
        """측정 기록의 DUT 핀: VI/AI 는 입력 핀, 나머지는 대상(출력) 핀."""
        return self.in_pin if self.kind in ("VI", "AI") else self.out_pin

    def title(self) -> str:
        if self.kind in ("VI", "AI"):
            return f"{self.kind}  입력 {self.in_pin} -> 출력 {self.out_pin}  {self.out_type}"
        if self.kind == "POR":
            return f"POR  VDD -> 출력 {self.out_pin}  (전원 인가 후 VOL/VOH)"
        return f"{self.kind}  핀 {self.out_pin}"


def _pin(value: str) -> str:
    v = (value or "").strip()
    return "" if v.upper() in ("", "X", "-") else v


def load_plan(path: Path, num_channels: int = 8) -> list[PlanRow]:
    rows = []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        for line_no, rec in enumerate(csv.DictReader(f), start=2):
            if not any((v or "").strip() for v in rec.values()):
                continue
            kind = (rec.get("항목") or "").strip().upper()
            where = f"{path} {line_no}행"
            if kind not in KINDS:
                raise ValueError(f"{where}: 항목 '{rec.get('항목')}' 은 {', '.join(KINDS)} 중 하나여야 합니다.")
            try:
                chans = [int(x) for x in (rec.get("채널") or "").replace(",", " ").split()]
            except ValueError:
                raise ValueError(f"{where}: 채널 '{rec.get('채널')}' 은 숫자여야 합니다.") from None
            want = CHANNEL_COUNT[kind]
            lo, hi = want if isinstance(want, tuple) else (want, want)
            if not lo <= len(chans) <= hi or any(not 1 <= c <= num_channels for c in chans) or len(set(chans)) != len(chans):
                raise ValueError(f"{where}: {kind} 의 채널은 서로 다른 1~{num_channels} 번 {lo}" + (f"~{hi}" if hi != lo else "") + "개")
            row = PlanRow((rec.get("순서") or "").strip() or str(len(rows) + 1), kind, _pin(rec.get("입력핀")),
                          _pin(rec.get("출력핀")), (rec.get("출력타입") or "").strip().upper() or "PP", chans,
                          (rec.get("준비") or "").strip())
            if row.out_type not in ("PP", "OD", "X"):
                raise ValueError(f"{where}: 출력타입은 PP / OD")
            if row.out_type == "X":
                row.out_type = "PP"
            if kind in ("VI", "AI") and not (row.in_pin and row.out_pin):
                raise ValueError(f"{where}: {kind} 는 입력핀과 출력핀이 필요합니다.")
            if not row.out_pin:
                raise ValueError(f"{where}: {kind} 는 출력핀(대상 핀) 칸이 필요합니다.")
            rows.append(row)
    if not rows:
        raise ValueError(f"{path}: 계획 항목이 없습니다.")
    keys = [r.key for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{path}: 순서·항목·핀이 같은 줄이 두 번 있습니다.")
    return rows


def load_layout(path: Path) -> list[tuple[str, str, str, str]]:
    out = []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        for line_no, rec in enumerate(csv.DictReader(f), start=2):
            item = (rec.get("항목") or "").strip()
            if not item:
                continue
            key = {"rpu": "Rpu", "rpd": "Rpd"}.get(item.lower(), item.upper())
            unit = (rec.get("단위") or "").strip() or ("kΩ" if key in ("Rpu", "Rpd") else "mA" if key in ("IOH", "IOL") else "V")
            if key not in ("VIH", "VIL", "VOH", "VOL", "IOH", "IOL", "Rpu", "Rpd") or unit not in UNIT_FORMAT:
                raise ValueError(f"{path} {line_no}행: 항목 '{item}' / 단위 '{unit}' 을 알 수 없습니다.")
            out.append(((rec.get("구분") or "").strip(), _pin(rec.get("핀")), key, unit))
    return out


# ---------------------------------------------------------------- 진행 기록·요약 (스코프 없이도 사용)
def temp_label(temp: str) -> str:
    return f"{temp}C"


def norm_temp(value: str) -> str:
    """'25.0' / '+25' -> '25', '-40' -> '-40' (같은 온도가 다른 기록 파일로 나뉘지 않게)."""
    v = float(value)
    return str(int(v)) if v.is_integer() else str(v)


class PlanStore:
    """온도별 진행 기록(progress_25C.json)과 CSV 들. 측정 폴더는 결과 폴더(base_dir) 기준 상대 경로로 기록."""

    def __init__(self, base: Path, plan_dir: str = "plan"):
        self.base = Path(base)
        self.dir = self.base / plan_dir

    def progress_path(self, temp: str) -> Path:
        return self.dir / f"progress_{temp_label(temp)}.json"

    def load(self, temp: str) -> dict:
        try:
            data = json.loads(self.progress_path(temp).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, temp: str, progress: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.progress_path(temp).with_suffix(".tmp")
        tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_retry(tmp, self.progress_path(temp))  # GUI 가 읽는 순간에는 잠깐 기다렸다 교체

    def temps(self) -> list[str]:
        out = []
        for p in sorted(self.dir.glob("progress_*C.json")):
            out.append(p.stem[len("progress_"):-1])
        return out

    def result_of(self, entry: dict) -> dict | None:
        folder = entry.get("folder")
        if not folder:
            return None
        try:
            return json.loads((self.base / folder / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def row_values(row: PlanRow, result: dict) -> dict[tuple[str, str], float]:
    """측정 1건에서 요약표에 들어갈 값 {(핀, 항목): 값(요약표 단위)}."""
    vals: dict[tuple[str, str], float] = {}
    if row.kind in ("VI", "AI", "POR"):
        for m in result.get("metrics", []):
            key, worst = m.get("key"), m.get("worst")
            if key not in WORST or worst is None:
                continue
            if row.kind == "POR" or m.get("output"):
                pin = row.out_pin
            else:
                pin = row.in_pin
            vals[(pin, key)] = worst
        return vals
    calc = result.get("calc") or {}
    if calc.get("value") is None:
        return vals
    name = {"IOH": "IOH", "IOL": "IOL", "RPU": "Rpu", "RPD": "Rpd"}[row.kind]
    vals[(row.out_pin, name)] = calc["value"] * (1e3 if calc.get("unit") == "A" else 1e-3)  # A->mA, Ω->kΩ
    return vals


def collect_values(rows: list[PlanRow], store: PlanStore, temp: str) -> tuple[dict, dict]:
    """(값 {(핀, 항목): 값}, 출처 {(핀, 항목): [순서...]}). 여러 측정에서 같은 값이 나오면 최악값."""
    progress = store.load(temp).get("rows", {})
    vals: dict[tuple[str, str], float] = {}
    src: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        entry = progress.get(row.key)
        if not entry or entry.get("status") != DONE:
            continue
        result = store.result_of(entry)
        if result is None:
            continue
        for k, v in row_values(row, result).items():
            pick = WORST.get(k[1])
            vals[k] = pick(vals[k], v) if (k in vals and pick) else v
            src.setdefault(k, []).append(row.no)
    return vals, src


def ordered_temps(store: PlanStore, configured: list[str]) -> list[str]:
    """설정된 온도 순서(25, 85, -40) 다음에 기록만 있는 온도."""
    norm = {float(t): t for t in store.temps()}
    out = []
    for t in configured:
        try:
            out.append(norm.pop(float(t), norm_temp(t)))
        except ValueError:
            continue
    return out + [norm[k] for k in sorted(norm)]


def write_summary(rows, layout, store: PlanStore, temps: list[str], path: Path) -> Path:
    per_temp = {t: collect_values(rows, store, t)[0] for t in temps}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["구분", "핀", "항목", "단위"] + [f"{t}°C" for t in temps])
        for group, pin, item, unit in layout:
            cells = []
            for t in temps:
                v = per_temp[t].get((pin, item))
                cells.append("" if v is None else UNIT_FORMAT[unit].format(v + 0.0))  # -0.0 -> 0.0
            w.writerow([group, pin, item, unit] + cells)
    return path


def check_bias(row: PlanRow, result: dict, cfg) -> bool:
    """IOH/IOL: 핀 전압이 부하 조건([plan] ioh_v ± ioh_tol_v)에 맞았는지 calc['check'] 에 기록. 기록했으면 True."""
    if row.kind not in ("IOH", "IOL"):
        return False
    calc = result.get("calc")
    if not calc:
        return False
    calc.pop("check", None)
    v = (calc.get("inputs") or {}).get("v_pin")
    target, tol = cfg.getfloat("plan", f"{row.kind.lower()}_v"), cfg.getfloat("plan", f"{row.kind.lower()}_tol_v")
    if v is None:
        calc["check"] = f"핀 전압 채널이 없어 부하 조건({target} V)을 확인하지 못했습니다."
    elif abs(v - target) > tol:
        calc["check"] = f"핀 전압 {v:.3f} V 가 부하 조건 {target} ± {tol} V 를 벗어남"
    return "check" in calc


def describe(row: PlanRow, result: dict | None) -> str:
    """진행 화면·기록용 한 줄 결과."""
    if not result:
        return ""
    v = row_values(row, result)

    def f(pin, item, unit="V"):
        x = v.get((pin, item))
        return "-" if x is None else UNIT_FORMAT[unit].format(x)
    if row.kind in ("VI", "AI"):
        s = f"VIH {f(row.in_pin, 'VIH')} / VIL {f(row.in_pin, 'VIL')} V | 출력 "
        s += (f"VOL {f(row.out_pin, 'VOL')} V" if row.out_type == "OD"
              else f"VOH {f(row.out_pin, 'VOH')} / VOL {f(row.out_pin, 'VOL')} V")
        return s
    if row.kind == "POR":
        d = (result.get("analysis") or {}).get("delay_s")
        return (f"VOL {f(row.out_pin, 'VOL')} / VOH {f(row.out_pin, 'VOH')} V, 지연 "
                + ("-" if d is None else f"{d * 1e3:.3f} ms"))
    calc = result.get("calc") or {}
    value, unit = manual_calc.display(calc) if calc else ("-", "")
    vpin = (calc.get("inputs") or {}).get("v_pin")
    return f"{calc.get('name', row.kind)} {value} {unit}" + ("" if vpin is None else f" (핀 {vpin:.4f} V)")


def warnings_of(row: PlanRow, result: dict | None) -> list[str]:
    if not result:
        return []
    if row.kind in ("VI", "AI", "POR"):
        return list((result.get("analysis") or {}).get("warnings", []))
    calc = result.get("calc") or {}
    out = []
    if calc.get("value") is None and calc.get("note"):
        out.append(calc["note"])
    if calc.get("check"):
        out.append(calc["check"])
    return out


def write_progress_csv(rows, store: PlanStore, temp: str) -> Path:
    progress = store.load(temp).get("rows", {})
    path = store.dir / f"plan_progress_{temp_label(temp)}.csv"
    store.dir.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["순서", "항목", "입력핀", "출력핀", "출력타입", "상태", "결과", "주의", "폴더", "시각"])
        for row in rows:
            e = progress.get(row.key, {})
            w.writerow([row.no, row.kind, row.in_pin or "X", row.out_pin, row.out_type if row.kind in ("VI", "AI") else "X",
                        e.get("status", "남음"), e.get("summary", ""), " / ".join(e.get("warnings", [])),
                        e.get("folder", ""), e.get("time", "")])
    return path


def rebuild(rows, layout, store: PlanStore, configured_temps: list[str]) -> tuple[Path, list[Path]]:
    """모든 온도의 진행 CSV 와 요약표를 다시 만든다."""
    temps = ordered_temps(store, configured_temps)
    progress = [write_progress_csv(rows, store, t) for t in store.temps()]
    summary = write_summary(rows, layout, store, temps, store.dir / "plan_summary.csv")
    return summary, progress


# ---------------------------------------------------------------- 진행 (스코프 필요)
class PlanRunner:
    def __init__(self, bench, rows: list[PlanRow], layout, ask=input, echo=print):
        self.bench, self.rows, self.layout = bench, rows, layout
        self.ask, self.echo = ask, echo
        cfg = bench.cfg
        self.store = PlanStore(bench.out_dir, cfg.get("plan", "dir") or "plan")
        self.temps = [t.strip() for t in cfg.get("plan", "temps").replace(",", " ").split() if t.strip()]
        self.settings = PlanSettings(cfg, self.store.dir / OVERRIDES_FILE)  # GUI 설정 탭에서 바꾼 값
        self.state = StateWriter(self.store.dir / STATE_FILE)               # GUI 가 보는 진행 상태

    def prompt(self, label, default="", valid=lambda x: True):
        while True:
            value = self.ask(f"{label}" + (f" [{default}]" if default else "") + " > ").strip()
            if value.lower() == "q":
                raise EOFError
            value = value or str(default)
            if valid(value):
                return value
            self.echo("입력값을 확인해주세요.")

    @staticmethod
    def _temp_ok(value):
        try:
            import math
            return math.isfinite(float(value))
        except ValueError:
            return False

    def run(self) -> int:
        self.state.start(rows=len(self.rows))
        try:
            return self._finish(self._run())
        finally:
            self.state.stop()

    def _finish(self, status: int) -> int:
        b = self.bench
        n = b.pending_count()
        if n and status == 0:
            try:
                got, left = b.fetch_pending()
                self.echo(f"캡처 파일 {got}개 가져옴" + (f", {left}개 남음 (py scope.py --fetch 로 다시 시도)" if left else ""))
            except (ScopeError, OSError) as e:
                self.echo(f"[캡처 파일 가져오기 실패] {e}\nTekScope 상태 확인/재시작 후 py scope.py --fetch 로 다시 가져오세요.")
                status = 1
        elif n:
            self.echo(f"스코프에 저장된 캡처 {n}개를 아직 가져오지 않았습니다. py scope.py --fetch 로 가져오세요.")
        try:
            summary, _ = rebuild(self.rows, self.layout, self.store, self.temps)
            self.echo(f"요약표: {summary}")
        except OSError as e:
            self.echo(f"[경고] 요약표를 쓰지 못했습니다(엑셀에서 열려 있나요?): {e}")
        return status

    def _default_temp(self) -> str:
        for t in self.temps:
            rows = self.store.load(t).get("rows", {})
            if any(rows.get(r.key, {}).get("status") != DONE for r in self.rows):
                return t
        return self.bench.temp

    def _counts(self, progress: dict) -> str:
        st = [progress.get(r.key, {}).get("status") for r in self.rows]
        return (f"완료 {st.count(DONE)} / 건너뜀 {st.count(SKIP)} / 실패 {st.count(FAIL)} / "
                f"남음 {sum(1 for s in st if s not in (DONE, SKIP, FAIL))} (전체 {len(self.rows)})")

    def _next(self, progress: dict, start: int) -> int | None:
        """start 부터 아직 완료·건너뜀이 아닌 항목. 끝까지 없으면 처음부터 다시 찾는다."""
        n = len(self.rows)
        for k in range(n):
            i = (start + k) % n
            if progress.get(self.rows[i].key, {}).get("status") not in (DONE, SKIP):
                return i
        return None

    def _run(self) -> int:
        b = self.bench
        self.echo(f"\n시험 계획 진행 — {len(self.rows)}개 항목, 온도 순서 {' -> '.join(t + '°C' for t in self.temps)}")
        try:
            self._sync_settings(None, start=True)
            b.temp = norm_temp(self.prompt("온도(°C)", self._default_temp(), self._temp_ok))
            b.name = self.prompt("시험 이름", b.name, lambda s: bool(s.strip()) and "\n" not in s)
            try:
                self.settings.set("session.name", b.name)  # GUI 설정 탭에도 같은 이름
            except (OSError, ValueError) as e:
                self.echo(f"[경고] 설정 파일에 시험 이름을 쓰지 못했습니다: {e}")
            self.state.update(temp=b.temp, name=b.name)
            doc = self.store.load(b.temp)
            doc.setdefault("rows", {})
            doc.update({"temp": b.temp, "name": b.name})
            progress = doc["rows"]
            self.echo(f"\n{b.temp}°C 진행 기록: {self._counts(progress)}")
            i = self._next(progress, 0)
            shown_group = None
            while True:
                if i is None:
                    self.state.update(current=None, current_no=None)
                    self.echo(f"\n{b.temp}°C 계획의 모든 항목이 완료되었습니다(건너뜀 포함). {self._counts(progress)}")
                    skipped = [k for k, r in enumerate(self.rows) if progress.get(r.key, {}).get("status") == SKIP]
                    choice = self.prompt(("Enter: 건너뛴 항목 측정 / g: 번호로 이동 / q: 끝내기" if skipped
                                          else "Enter: 끝내기 / g: 번호로 이동해 다시 측정"), "",
                                         lambda s: s.lower() in ("", "g")).lower()
                    if choice == "g":
                        i = self._goto()
                        continue
                    if not skipped:
                        return 0
                    for k in skipped:
                        progress[self.rows[k].key].pop("status", None)
                    i = skipped[0]
                    continue
                row = self.rows[i]
                group = GROUP[row.kind]
                if group != shown_group:
                    self.echo("\n" + "=" * 60 + f"\n{GROUP_GUIDE[group]}\n" + "=" * 60)
                    shown_group = group
                e = progress.get(row.key, {})
                self.state.update(current=row.key, current_no=row.no)
                self.echo(f"\n[{i + 1}/{len(self.rows)}] {row.title()}   채널 "
                          + ", ".join(f"CH{c}" for c in row.channels)
                          + (f"\n  준비: {row.setup}" if row.setup else "")
                          + (f"\n  지난 결과({e['status']}): {(e.get('error') if e['status'] == FAIL else '') or e.get('summary') or '-'}"
                             if e.get("status") in (DONE, SKIP, FAIL) else ""))
                action = self.prompt("Enter: 측정 / k: 건너뛰기 / p: 이전 / g: 번호로 이동 / q: 멈춤(다음에 이어서)", "",
                                     lambda s: s.lower() in ("", "k", "p", "g")).lower()
                if action == "k":
                    if e.get("status") != DONE:  # 이미 측정한 항목은 결과를 지우지 않고 넘어가기만
                        progress[row.key] = {**e, "status": SKIP, "time": datetime.now().isoformat(timespec="seconds")}
                        self._save(doc)
                    i = self._next(progress, i + 1)
                    continue
                if action == "p":
                    i = (i - 1) % len(self.rows)
                    continue
                if action == "g":
                    i = self._goto()
                    continue
                self._sync_settings(doc)
                self.state.update(measuring=True)
                try:
                    entry, code = self._measure(row)
                finally:
                    self.state.update(measuring=False)
                self.state.update(last=row.key, last_no=row.no, last_status=entry["status"])
                if entry["status"] != DONE and e.get("status") == DONE:  # 다시 재다 실패하면 앞의 결과를 그대로 둔다
                    self.echo(f"  => 실패: {entry.get('error')} — 앞의 측정 결과를 그대로 둡니다 (Enter 로 다시, k 로 넘어가기)")
                    if code is not None:
                        return code
                    continue
                progress[row.key] = entry
                self._save(doc)
                if code is not None:
                    return code
                if entry["status"] == DONE:
                    self.echo(f"  => {entry['summary']}" + "".join(f"\n  [주의] {w}" for w in entry.get("warnings", [])))
                    i = self._next(progress, i + 1)
                else:
                    self.echo(f"  => 실패: {entry.get('error')} (Enter 로 다시, k 로 건너뛰기)")
        except (EOFError, KeyboardInterrupt):
            self.echo("\n계획 진행을 멈춥니다. 다음에 같은 온도를 고르면 이어서 진행합니다.")
            return 0

    def _sync_settings(self, doc: dict | None, start: bool = False) -> None:
        """GUI 설정 탭에서 바꾼 값(overrides.ini)을 측정 직전에 반영."""
        b = self.bench
        try:
            changed = self.settings.apply()
        except (OSError, ValueError) as e:
            self.echo(f"[경고] 설정 파일을 읽지 못했습니다(이전 값으로 측정): {e}")
            return
        for s, old, new in changed:
            if s.id == "session.name":
                if start and "name" in getattr(b, "explicit_options", set()):
                    continue  # --name 으로 준 이름이 우선
                b.name = new
                if doc is not None:
                    doc["name"] = new
                    self.state.update(name=new)
            elif s.id == "capture.enabled":
                b._capturer = None  # 다음 캡처부터 켬/끔 반영
            self.echo(("  설정(overrides.ini): " if start else "  설정 변경 반영: ") + describe_change(s, old, new))

    def _goto(self) -> int:
        no = self.prompt(f"이동할 번호 (1~{len(self.rows)})", "",
                         lambda s: s.isascii() and s.isdigit() and 1 <= int(s) <= len(self.rows))
        return int(no) - 1

    def _save(self, doc: dict) -> None:
        try:
            self.store.save(self.bench.temp, doc)
            write_progress_csv(self.rows, self.store, self.bench.temp)
        except OSError as e:
            self.echo(f"[경고] 진행 기록을 쓰지 못했습니다(엑셀에서 열려 있나요?): {e}")

    def _measure(self, row: PlanRow) -> tuple[dict, int | None]:
        """(진행 기록 1건, 종료 코드 또는 None)."""
        b = self.bench
        now = datetime.now().isoformat(timespec="seconds")
        b.dut_pin = row.pin
        b.out_pin = row.out_pin if row.kind in ("VI", "AI") else ""
        b.out_type = row.out_type
        used = self.settings.snapshot()
        try:
            self.echo("  스코프 상태 정리")
            b.reset()
            if row.kind in ("VI", "AI", "POR"):
                rec = b.vih(*row.channels) if row.kind != "POR" else b.por(*row.channels)
                if b.capture_enabled():
                    b.capture()
                b.save()
            else:
                # 앞 항목(VI/POR)의 Single 뒤 스코프는 멈춰 있다. 그 파형을 저장하지 않도록 새로 1회 획득(CH1 트리거 스윙).
                self.echo("  단일 획득(Single) 대기 중...")
                b.scope.single(b.cfg.getfloat("measure", "single_timeout_s"))
                rec = b.save_manual(MANUAL_LABEL[row.kind], row.channels, f"계획 {row.no}")
                self._check_bias(row, rec)
            b.resume()  # 다음 항목을 준비하는 동안 화면이 계속 갱신되도록 Run
        except KeyboardInterrupt:
            return {"status": FAIL, "error": "사용자 취소", "time": now}, 130
        except (ScopeError, OSError, ValueError, KeyError) as e:
            msg = str(e).splitlines()[0]
            if "source off" in msg.lower():
                msg += f" -> 스코프 화면에서 CH{', CH'.join(map(str, row.channels))} 를 켜 주세요(트리거용 CH1 만 숨김)"
            return {"status": FAIL, "error": msg, "time": now}, None
        result = rec.result
        result["plan_settings"] = used  # 이 측정에 쓴 계산·분석 설정 (다시 계산할 때도 이 값)
        try:
            results.write_json(rec.folder / "result.json", result)
        except OSError as e:
            self.echo(f"  [경고] result.json 에 설정을 기록하지 못했습니다: {e}")
        entry = {"status": DONE, "folder": rec.folder.relative_to(b.out_dir).as_posix(), "time": now,
                 "summary": describe(row, result), "warnings": warnings_of(row, result)}
        if not row_values(row, result):  # 요약표에 들어갈 값이 없으면 완료로 두지 않고 다시 재거나 건너뛰게
            entry.update(status=FAIL, error="요약표에 넣을 값이 없음" + "".join(f" — {w}" for w in entry["warnings"]))
        return entry, None

    def _check_bias(self, row: PlanRow, rec) -> None:
        if check_bias(row, rec.result, self.bench.cfg):
            results.write_json(rec.folder / "result.json", rec.result)

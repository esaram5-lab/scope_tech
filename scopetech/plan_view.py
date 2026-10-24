"""계획 모드 GUI 가 보여줄 내용 (tkinter 없이 쓰는 부분: 표·상세·설정·가져오기 데이터, GUI 띄우기).

GUI 는 측정하지 않는다. 진행 기록(progress_온도C.json)·state.json·result.json·캡처 PNG 를 읽고,
설정 탭은 overrides.ini 만 쓰며, 가져오기 탭은 CLI 가 진행 중이 아닐 때만 진행 기록을 쓴다.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import manual_calc, plan_import
from .config import Config
from .plan import (DONE, FAIL, SKIP, UNIT_FORMAT, PlanStore, collect_values, describe, load_layout, load_plan,
                   migrate_legacy, norm_temp, ordered_temps, rebuild_all)
from .plan_state import (BY_ID, OVERRIDES_FILE, SETTINGS, STALE_S, STATE_FILE, PlanSettings, cli_running,
                         read_overrides, read_state, validate, write_atomic, write_overrides)
from .samples import Samples, order_key

GUI_FILE = "gui.json"   # 열려 있는 GUI 의 살아 있다는 표시 (같은 창을 두 번 띄우지 않게)
IMAGES = ("meas.png", "screen.png", "clean.png")
LEFT = "남음"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _fmt(v, unit="V", digits=4) -> str:
    return "-" if v is None else f"{v:.{digits}f} {unit}".rstrip()


class PlanData:
    def __init__(self, base: Path, config: Path | None = None, plan: Path | None = None, here: Path | None = None):
        self.here = Path(here) if here else Path(__file__).resolve().parents[1]
        self.config_path = Path(config) if config else self.here / "config.ini"
        self.cfg = Config(self.config_path)
        self.base = Path(base)
        self.rows = load_plan(Path(plan) if plan else self.here / self.cfg.get("plan", "file"),
                              self.cfg.getint("scope", "num_channels"))
        self.layout = load_layout(self.here / self.cfg.get("plan", "summary"))
        self.root = PlanStore(self.base, self.cfg.get("plan", "dir") or "plan")  # 공용 파일(plan/)
        self.configured = [t.strip() for t in self.cfg.get("plan", "temps").replace(",", " ").split() if t.strip()]
        self.by_key = {r.key: r for r in self.rows}
        if any(self.root.root.glob("progress_*C.json")) and not self.running():
            try:  # 샘플 구분 전 진행 기록은 시험 이름의 샘플 폴더로
                migrate_legacy(self.root, Samples(self.root.root))
            except OSError:
                pass
        self.sample = ""
        found = self.samples()
        state = self.state()
        self.sample = state.get("sample") if state.get("sample") in found else (found[0] if found else "")

    # ------------------------------------------------------------ 샘플
    @property
    def store(self) -> PlanStore:
        return self.root.of(self.sample) if self.sample else self.root

    def samples(self, state: dict | None = None) -> list[str]:
        """진행 기록이 있는 샘플 + CLI 가 지금 진행 중인 샘플."""
        found = self.root.samples()
        state = self.state() if state is None else state
        live = state.get("sample")
        if live and self.running(state) and live not in found:
            found = sorted(found + [live], key=order_key)
        return found

    # ------------------------------------------------------------ CLI 상태
    @property
    def state_path(self) -> Path:
        return self.root.dir / STATE_FILE

    @property
    def overrides_path(self) -> Path:
        return self.root.dir / OVERRIDES_FILE

    def state(self) -> dict:
        return read_state(self.state_path)

    def running(self, state: dict | None = None) -> bool:
        return cli_running(self.state() if state is None else state)

    def signature(self, temp: str | None, state: dict | None = None) -> tuple:
        """화면을 다시 그려야 하는지 보는 값: 진행 기록·설정 파일 수정 시각과 CLI 상태(살아 있다는 표시 시각은 빼고)."""
        state = self.state() if state is None else state
        live = (self.running(state),) + tuple(state.get(k) for k in ("sample", "temp", "name", "current", "measuring", "last"))
        paths = [self.overrides_path] + [self.store.progress_path(t) for t in self.temps()]
        return tuple(_mtime(p) for p in paths) + live + (self.sample, temp, tuple(self.samples(state)))

    def temps(self) -> list[str]:
        return ordered_temps(self.store, self.configured)

    # ------------------------------------------------------------ 진행 탭
    def progress_rows(self, temp: str, state: dict | None = None) -> list[dict]:
        state = self.state() if state is None else state
        progress = self.store.load(temp).get("rows", {})
        live = (self.running(state) and str(state.get("temp")) == str(temp)
                and (state.get("sample") or "") == self.sample)
        out = []
        for r in self.rows:
            e = progress.get(r.key, {})
            status = e.get("status") or LEFT
            pins = f"{r.in_pin} -> {r.out_pin}" if r.kind in ("VI", "AI") else r.out_pin
            text = e.get("error") if status == FAIL else e.get("summary", "")
            out.append({"key": r.key, "no": r.no, "kind": r.kind, "pins": pins,
                        "type": r.out_type if r.kind in ("VI", "AI") else "", "status": status,
                        "result": text or "", "warnings": " / ".join(e.get("warnings", [])),
                        "source": e.get("source", ""), "time": e.get("time", ""), "folder": e.get("folder", ""),
                        "current": live and state.get("current") == r.key,
                        "measuring": live and state.get("current") == r.key and bool(state.get("measuring"))})
        return out

    def counts(self, rows: list[dict]) -> str:
        st = [r["status"] for r in rows]
        return (f"완료 {st.count(DONE)} / 건너뜀 {st.count(SKIP)} / 실패 {st.count(FAIL)} / "
                f"남음 {st.count(LEFT)} (전체 {len(st)})")

    def cli_text(self, state: dict | None = None) -> str:
        state = self.state() if state is None else state
        if not self.running(state):
            when = state.get("updated", "")
            return "CLI: 진행 중 아님" + (f" (마지막 {when})" if when else "")
        row = self.by_key.get(state.get("current") or "")
        where = f"{row.no}. {row.title()}" if row else "항목 선택 전"
        doing = "측정 중" if state.get("measuring") else "입력 대기"
        sample = f" [{state['sample']}]" if state.get("sample") else ""
        psu = f"  |  ARB: {state['supply']}" if state.get("supply") else "  |  ARB: 없음(함수발생기)"
        return f"CLI: {state.get('temp', '?')}°C / {state.get('name', '')}{sample} — {where} ({doing}){psu}"

    # ------------------------------------------------------------ 상세
    def folder_of(self, temp: str, key: str) -> Path | None:
        e = self.store.load(temp).get("rows", {}).get(key, {})
        return (self.base / e["folder"]) if e.get("folder") else None

    def detail(self, temp: str, key: str) -> dict:
        row = self.by_key[key]
        e = self.store.load(temp).get("rows", {}).get(key, {})
        folder = self.base / e["folder"] if e.get("folder") else None
        lines = [f"{row.no}. {row.title()}   채널 " + ", ".join(f"CH{c}" for c in row.channels)]
        if row.setup:
            lines.append(f"준비: {row.setup}")
        lines.append(f"상태: {e.get('status') or LEFT}" + (f" ({e['source']})" if e.get("source") else "")
                     + (f"  {e['time']}" if e.get("time") else ""))
        if e.get("error"):
            lines.append(f"오류: {e['error']}")
        result = self.store.result_of(e) if folder else None
        if result:
            lines.append(f"결과: {describe(row, result)}")
            lines += self._result_lines(row, result)
        for w in e.get("warnings", []):
            lines.append(f"[주의] {w}")
        if folder:
            lines.append(f"폴더: {folder}")
        return {"title": row.title(), "lines": lines, "folder": folder if folder and folder.is_dir() else None,
                "image": self.image_of(folder) if folder else None}

    @staticmethod
    def image_of(folder: Path) -> Path | None:
        for name in IMAGES:
            if (folder / name).is_file():
                return folder / name
        pngs = sorted(folder.glob("*.png")) if folder.is_dir() else []
        return pngs[0] if pngs else None

    def _settings_text(self, used: dict) -> str:
        """측정에 쓴 설정 중 config.ini 와 다른 값만."""
        diff = []
        for sid, v in used.items():
            s = BY_ID.get(sid)
            if s is None:
                continue
            try:
                same = validate(s, self.cfg.get(s.section, s.key)) == validate(s, v)
            except ValueError:
                same = False
            if not same:
                shown = ("켬" if str(v).lower() in ("1", "yes", "true", "on", "y") else "끔") if s.kind == "bool" else v
                diff.append(f"{s.label} {shown}" + (f" {s.unit}" if s.unit and s.kind in ("float", "int") else ""))
        return "측정 설정: " + (", ".join(diff) + " (config.ini 와 다름)" if diff else "config.ini 값")

    def _result_lines(self, row, result: dict) -> list[str]:
        lines = []
        for m in result.get("metrics", []):
            if m.get("typ") is None and m.get("worst") is None:
                continue
            side = "출력" if m.get("output") else "입력"
            lines.append(f"  {side} {m.get('metric', m.get('key'))}: 평균 {_fmt(m.get('typ'))}, 최악 {_fmt(m.get('worst'))}"
                         + (f", {m['n']}회" if m.get("n") else ""))
        a = result.get("analysis") or {}
        if row.kind == "POR":
            if a.get("vdd") is not None:
                lines.append(f"  VDD {_fmt(a.get('vdd'))}")
            if a.get("delay_s") is not None:
                lines.append(f"  POR 지연 {a['delay_s'] * 1e3:.3f} ms")
        elif a.get("polarity"):
            lines.append(f"  극성: {a['polarity']}")
        calc = result.get("calc")
        if calc:
            value, unit = manual_calc.display(calc)
            lines.append(f"  {calc.get('name')} = {value} {unit}")
            if calc.get("note"):
                lines.append(f"  근거: {calc['note']}")
            inp = calc.get("inputs") or {}
            if inp.get("v_pin") is not None:
                lines.append(f"  핀 전압 {_fmt(inp['v_pin'])}" + (f" ({inp.get('v_channel') or inp.get('channel')})"))
            if calc.get("check"):
                lines.append(f"  [부하 조건] {calc['check']}")
        arb = result.get("arb")
        if arb:
            lines.append(f"  ARB: CH{arb.get('channel')} {arb.get('model', '')} {arb.get('low_v'):g}~{arb.get('high_v'):g} V "
                         f"사다리꼴 {'/'.join(f'{t:g}' for t in arb.get('times_ms', []))} ms, 전류 제한 {arb.get('current_limit_ma'):g} mA")
        used = result.get("plan_settings")
        if used:
            lines.append("  " + self._settings_text(used))
        return lines

    # ------------------------------------------------------------ 요약표 탭
    def summary(self, temp: str | None = None) -> tuple[list[str], list[list[str]], list[bool]]:
        """(머리글, 행, 행마다 temp 칸이 비었는지). plan_summary.csv 와 같은 값. temp 없으면 모든 온도 기준."""
        temps = self.temps()
        per = {t: collect_values(self.rows, self.store, t)[0] for t in temps}
        head = ["구분", "핀", "항목", "단위"] + [f"{t}°C" for t in temps]
        rows, blank = [], []
        for group, pin, item, unit in self.layout:
            cells = []
            for t in temps:
                v = per[t].get((pin, item))
                cells.append("" if v is None else UNIT_FORMAT[unit].format(v + 0.0))
            rows.append([group, pin, item, unit] + cells)
            blank.append(cells[temps.index(temp)] == "" if temp in temps else any(c == "" for c in cells))
        return head, rows, blank

    def summary_csv(self) -> Path:
        return self.store.summary_path

    def summary_all_csv(self) -> Path:
        return self.root.root / "plan_summary_all.csv"

    # ------------------------------------------------------------ 설정 탭
    def settings_form(self) -> list[dict]:
        over = read_overrides(self.overrides_path)
        base = Config(self.config_path)
        out = []
        for s in SETTINGS:
            b = base.get(s.section, s.key)
            out.append({"id": s.id, "setting": s, "base": b, "override": over.get(s.id), "value": over.get(s.id, b)})
        return out

    def save_settings(self, values: dict[str, str]) -> list[str]:
        """입력값 검사 후 overrides.ini 에 저장. 오류 목록(비었으면 저장됨). config.ini 와 같은 값은 적지 않는다."""
        errors, clean = [], {}
        base = Config(self.config_path)
        for sid, text in values.items():
            s = BY_ID[sid]
            try:
                v = validate(s, text)
            except ValueError as e:
                errors.append(str(e))
                continue
            try:
                same = validate(s, base.get(s.section, s.key)) == v
            except ValueError:
                same = False
            if not same or sid == "session.name":
                clean[sid] = v
        if errors:
            return errors
        write_overrides(self.overrides_path, clean)
        return []

    def reset_settings(self) -> None:
        """config.ini 값으로 되돌린다(시험 이름은 유지)."""
        keep = {k: v for k, v in read_overrides(self.overrides_path).items() if k == "session.name"}
        write_overrides(self.overrides_path, keep)

    # ------------------------------------------------------------ 가져오기 탭
    def import_cfg(self) -> Config:
        cfg = Config(self.config_path)
        PlanSettings(cfg, self.overrides_path).apply()
        return cfg

    def import_preview(self):
        """(picks, unmatched, cfg). 파일은 쓰지 않는다."""
        cfg = self.import_cfg()
        found = plan_import.scan(self.base, plan_import.load_missing_pins(self.root.root))
        picks, unmatched = plan_import.plan_import(self.rows, self.root, cfg, found, order=[_norm(t) for t in self.configured],
                                                   samples=Samples(self.root.root))
        return picks, unmatched, cfg

    def import_apply(self, picks, unmatched) -> tuple[list[Path], Path, Path]:
        if self.running():
            raise RuntimeError("CLI 가 계획을 진행 중이라 적용할 수 없습니다. CLI 를 끝낸(q) 뒤 적용하세요.")
        samples = Samples(self.root.root)
        migrate_legacy(self.root, samples)
        samples.sync(p.found.name for p in picks if p.found and p.found.name)
        written = plan_import.apply(picks, self.root)
        log = plan_import.write_log(picks, unmatched, self.root.dir / f"import_{datetime.now():%Y%m%d_%H%M%S}.csv")
        every, _ = rebuild_all(self.rows, self.layout, self.root, self.configured)
        return written, log, every


def _norm(t: str) -> str:
    try:
        return norm_temp(t)
    except ValueError:
        return t


# ---------------------------------------------------------------- GUI 한 개만
def gui_alive(plan_dir: Path, now: float | None = None) -> bool:
    try:
        data = json.loads((Path(plan_dir) / GUI_FILE).read_text(encoding="utf-8"))
        return (now if now is not None else time.time()) - float(data.get("alive", 0)) < STALE_S
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def mark_gui(plan_dir: Path, closing: bool = False) -> None:
    try:
        write_atomic(Path(plan_dir) / GUI_FILE, json.dumps({"pid": os.getpid(), "alive": 0 if closing else time.time()}))
    except OSError:
        pass


def launch_gui(script: Path, config: Path, base: Path, plan_dir: Path, echo=print) -> bool:
    """CLI 와 별도인 GUI 창을 띄운다(실패해도 측정은 계속). 띄웠으면 True."""
    if gui_alive(plan_dir):
        echo("결과·설정 창(GUI)이 이미 열려 있습니다.")
        return False
    if importlib.util.find_spec("tkinter") is None:
        echo("GUI 를 띄우지 못했습니다(tkinter 없음). CLI 로만 진행합니다.")
        return False
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    cmd = [str(pyw if pyw.is_file() else exe), str(script), "--config", str(config), "--dir", str(base)]
    flags = 0 if pyw.is_file() else getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(cmd, cwd=str(Path(script).parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError as e:
        echo(f"GUI 를 띄우지 못했습니다: {e}. CLI 로만 진행합니다.")
        return False
    echo("결과·설정 창(GUI)을 띄웠습니다. 측정은 이 창(CLI)에서 합니다.")
    return True

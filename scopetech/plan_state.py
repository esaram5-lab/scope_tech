"""계획 모드의 CLI <-> GUI 연결 파일 (결과 폴더/plan/ 아래).

state.json      CLI 가 쓴다: 지금 온도·시험 이름·대기 중인 항목·측정 중인지, 몇 초마다 살아 있다는 표시(alive)
overrides.ini   GUI 설정 탭이 쓴다(CLI 의 시험 이름 입력도 여기에 기록): config.ini 대신 쓸 값. config.ini 는 건드리지 않는다.
                CLI 는 측정 직전마다 다시 읽어 반영하고, 그 측정에 쓴 값을 result.json 의 plan_settings 에 남긴다.
"""
from __future__ import annotations

import configparser
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STATE_FILE = "state.json"
OVERRIDES_FILE = "overrides.ini"
ALIVE_S = 2.0      # CLI 가 살아 있다는 표시를 고치는 간격
STALE_S = 10.0     # 이보다 오래 표시가 없으면 CLI 가 끝난 것으로 본다


@dataclass(frozen=True)
class Setting:
    section: str
    key: str
    label: str
    kind: str  # float / int / bool / text
    unit: str = ""
    lo: float | None = None
    hi: float | None = None
    group: str = ""

    @property
    def id(self) -> str:
        return f"{self.section}.{self.key}"


# GUI 설정 탭에서 바꿀 수 있는 값 (온도는 CLI 에서 정한다)
SETTINGS = (
    Setting("session", "name", "시험 이름", "text", group="진행"),
    Setting("capture", "enabled", "화면 캡처", "bool", group="진행"),
    Setting("measure", "single_timeout_s", "Single 대기 시간", "float", "s", 1, 600, group="진행"),
    Setting("manual", "vdd_v", "VDD", "float", "V", 0.5, 10, group="계산 (Rpd / Rpu)"),
    Setting("manual", "r_ext_ohm", "외부 저항", "float", "Ω", 1e3, 1e9, group="계산 (Rpd / Rpu)"),
    Setting("manual", "probe_r_ohm", "프로브 입력 저항", "float", "Ω", 1e5, 1e10, group="계산 (Rpd / Rpu)"),
    Setting("plan", "ioh_v", "IOH 부하 조건 핀 전압", "float", "V", 0, 10, group="부하 조건 (IOH / IOL)"),
    Setting("plan", "ioh_tol_v", "IOH 허용 오차", "float", "V", 0.001, 5, group="부하 조건 (IOH / IOL)"),
    Setting("plan", "iol_v", "IOL 부하 조건 핀 전압", "float", "V", 0, 10, group="부하 조건 (IOH / IOL)"),
    Setting("plan", "iol_tol_v", "IOL 허용 오차", "float", "V", 0.001, 5, group="부하 조건 (IOH / IOL)"),
    Setting("measure", "out_cross", "출력 전환 기준", "float", "(0~1, 출력 진폭 비율)", 0.05, 0.95, group="분석 (VIL / VIH)"),
    Setting("measure", "vin_avg_points", "입력 평균 점수", "int", "점", 1, 10000, group="분석 (VIL / VIH)"),
)
BY_ID = {s.id: s for s in SETTINGS}
TRUE = ("1", "yes", "true", "on", "y")


def validate(setting: Setting, text: str) -> str:
    """입력 문자열을 저장할 값으로 바꾼다. 잘못되면 ValueError(이유)."""
    v = str(text).strip()
    if setting.kind == "text":
        if not v:
            raise ValueError(f"{setting.label}: 비워 둘 수 없습니다.")
        if any(c in v for c in "\r\n"):
            raise ValueError(f"{setting.label}: 한 줄로 입력하세요.")
        return v
    if setting.kind == "bool":
        return "yes" if v.lower() in TRUE else "no"
    try:
        x = float(v)
    except ValueError:
        raise ValueError(f"{setting.label}: 숫자를 입력하세요 ('{v}').") from None
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError(f"{setting.label}: 숫자를 입력하세요 ('{v}').")
    if setting.kind == "int":
        if not x.is_integer():
            raise ValueError(f"{setting.label}: 정수를 입력하세요 ('{v}').")
        x = int(x)
    if setting.lo is not None and x < setting.lo or setting.hi is not None and x > setting.hi:
        raise ValueError(f"{setting.label}: {setting.lo:g} ~ {setting.hi:g} {setting.unit} 범위로 입력하세요 ('{v}').")
    return str(x) if setting.kind == "int" else f"{x:.12g}"


def replace_retry(tmp: Path, dst: Path, tries: int = 20, wait: float = 0.05) -> None:
    """tmp -> dst 교체. Windows 에서는 다른 프로그램(GUI)이 dst 를 읽는 순간 교체가 막힐 수 있어 잠깐 기다렸다 다시 한다."""
    for n in range(tries):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if n == tries - 1:
                raise
            time.sleep(wait)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        replace_retry(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- overrides.ini
def read_overrides(path: Path) -> dict[str, str]:
    """{"manual.vdd_v": "3.25", ...}. 모르는 항목·읽을 수 없는 파일은 무시."""
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_string(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, configparser.Error, UnicodeDecodeError):
        return {}
    out = {}
    for s in SETTINGS:
        if cp.has_option(s.section, s.key):
            try:
                out[s.id] = validate(s, cp.get(s.section, s.key))
            except ValueError:
                continue
    return out


def write_overrides(path: Path, values: dict[str, str]) -> None:
    lines = ["; 계획 모드 설정 (GUI 설정 탭 / CLI 시험 이름). config.ini 대신 이 값을 쓴다. 지우면 config.ini 값으로 돌아간다.", ""]
    for section in dict.fromkeys(s.section for s in SETTINGS):
        items = [(s.key, values[s.id]) for s in SETTINGS if s.section == section and s.id in values]
        if items:
            lines.append(f"[{section}]")
            lines += [f"{k} = {v}" for k, v in items]
            lines.append("")
    write_atomic(Path(path), "\n".join(lines))


class PlanSettings:
    """config.ini 값(기준) 위에 overrides.ini 를 얹어 Config 에 반영한다."""

    def __init__(self, cfg, path: Path):
        self.cfg, self.path = cfg, Path(path)
        self.base = {s.id: cfg.get(s.section, s.key) for s in SETTINGS}

    def current(self) -> dict[str, str]:
        return {s.id: self.cfg.get(s.section, s.key) for s in SETTINGS}

    def apply(self) -> list[tuple[Setting, str, str]]:
        """overrides.ini 를 다시 읽어 반영. 바뀐 항목 [(설정, 이전, 새 값)]."""
        over = read_overrides(self.path)
        changed = []
        for s in SETTINGS:
            new = over.get(s.id, self.base[s.id])
            old = self.cfg.get(s.section, s.key)
            if new != old:
                self.cfg.cp.set(s.section, s.key, new)
                changed.append((s, old, new))
        return changed

    def set(self, sid: str, value: str) -> None:
        """한 항목을 overrides.ini 에 기록(다른 항목은 그대로)."""
        over = read_overrides(self.path)
        over[sid] = validate(BY_ID[sid], value)
        write_overrides(self.path, over)
        self.cfg.cp.set(BY_ID[sid].section, BY_ID[sid].key, over[sid])

    def snapshot(self) -> dict[str, str]:
        """측정에 쓴 값 (result.json 의 plan_settings)."""
        return {s.id: self.cfg.get(s.section, s.key) for s in SETTINGS if s.id != "session.name"}


def describe_change(s: Setting, old: str, new: str) -> str:
    if s.kind == "bool":
        old, new = ("켬" if old.lower() in TRUE else "끔"), ("켬" if new.lower() in TRUE else "끔")
    return f"{s.label} {old} -> {new}" + (f" {s.unit}" if s.unit and s.kind in ("float", "int") else "")


class SettingsView:
    """기록에 남은 설정(plan_settings)을 먼저 보고, 없으면 원래 Config 를 쓰는 읽기 전용 설정 (다시 계산용)."""

    def __init__(self, cfg, values: dict | None):
        self.cfg, self.values = cfg, dict(values or {})

    def get(self, section: str, key: str, fallback: str = "") -> str:
        v = self.values.get(f"{section}.{key}")
        return str(v).strip() if v is not None else self.cfg.get(section, key, fallback)

    def getint(self, section: str, key: str) -> int:
        return int(float(self.get(section, key)))

    def getfloat(self, section: str, key: str) -> float:
        return float(self.get(section, key))

    def getbool(self, section: str, key: str) -> bool:
        return self.get(section, key).lower() in TRUE

    def __getattr__(self, name):
        return getattr(self.cfg, name)


# ---------------------------------------------------------------- state.json
def read_state(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cli_running(state: dict, now: float | None = None) -> bool:
    """CLI 가 지금 계획을 진행 중인지 (끝났거나 비정상 종료로 표시가 멈췄으면 False)."""
    if not state.get("running"):
        return False
    try:
        return (now if now is not None else time.time()) - float(state.get("alive", 0)) < STALE_S
    except (TypeError, ValueError):
        return False


class StateWriter:
    """CLI 쪽: state.json 을 고치고, 입력을 기다리는 동안에도 살아 있다는 표시를 계속 고친다."""

    def __init__(self, path: Path, interval: float = ALIVE_S):
        self.path, self.interval = Path(path), interval
        self.data: dict = {"running": False}
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.failed = False

    def update(self, **kw) -> None:
        with self.lock:
            self.data.update(kw)
            self._write()

    def _write(self) -> None:
        self.data["alive"] = time.time()
        self.data["updated"] = datetime.now().isoformat(timespec="seconds")
        try:
            write_atomic(self.path, json.dumps(self.data, ensure_ascii=False, indent=2))
        except OSError:
            self.failed = True  # GUI 용 표시일 뿐이라 측정은 계속한다

    def _beat(self) -> None:
        while not self._stop.wait(self.interval):
            with self.lock:
                self._write()

    def start(self, **kw) -> None:
        self.update(running=True, pid=os.getpid(), started=datetime.now().isoformat(timespec="seconds"), **kw)
        self._stop.clear()
        self._thread = threading.Thread(target=self._beat, name="plan-state", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        self.update(running=False, measuring=False, current=None)

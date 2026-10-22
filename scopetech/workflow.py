"""측정 작업대(Bench): 시험 조건·현재 기록 폴더 같은 공통 상태와 도구를 들고, 각 동작은 steps/ 모듈에 맡긴다.

    동작(steps/)       reset / vih / voh / capture / save / manual / snap
    화면 캡처 방법     capture/ (config [capture] method: aoe | off | 직접 만든 모듈)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import capture as capture_methods
from . import gallery, results
from .config import Config
from .mso import MSO5B
from .steps import capture as capture_step
from .steps import manual as manual_step
from .steps import por as por_step
from .steps import reset as reset_step
from .steps import save as save_step
from .steps import snap as snap_step
from .steps import vih as vih_step
from .steps import voh as voh_step


@dataclass
class Record:
    """측정 1건(폴더 1개). capture/save 는 현재 Record 폴더에 저장된다."""

    item: str
    folder: Path
    channels: list[int]
    meas: list[tuple[str, str, int]] = field(default_factory=list)  # (이름, 종류, 채널)
    cursor: dict | None = None
    result: dict = field(default_factory=dict)


def _safe(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_") or "X"


class Bench:
    def __init__(self, scope: MSO5B, cfg: Config, out_dir: Path, echo: Callable[[str], None] = print):
        self.scope = scope
        self.cfg = cfg
        self.out_dir = Path(out_dir)
        self.echo = echo
        self.temp = cfg.get("session", "temp")
        self.name = cfg.get("session", "name")
        self.spec_name = cfg.get("session", "spec")
        self.out_type = cfg.get("session", "out_type").upper() or "PP"
        self.dut_pin = ""
        self.out_pin = ""  # VIL/VIH 측정 시 출력 DUT 핀
        self.record: Record | None = None
        self._capturer = None

    # ------------------------------------------------------------------ 동작 (steps/)
    def reset(self) -> None:
        reset_step.run(self)

    def vih(self, ch_in: int, ch_out: int, hold: bool = False) -> Record:
        return vih_step.run(self, ch_in, ch_out, hold)

    def voh(self, chans: list[int], hold: bool = False) -> Record:
        return voh_step.run(self, chans, hold)

    def por(self, ch_vdd: int, ch_out: int) -> Record:
        return por_step.run(self, ch_vdd, ch_out)

    def capture(self) -> list[Path]:
        return capture_step.run(self)

    def save(self, chans: list[int] | None = None) -> list[Path]:
        return save_step.run(self, chans)

    def save_manual(self, label: str, chans: list[int], note: str = "") -> Record:
        return manual_step.run(self, label, chans, note)

    def snap(self, label: str, note: str = "", cursor: bool = True) -> Record:
        return snap_step.run(self, label, note, cursor)

    # ------------------------------------------------------------------ 화면 캡처 방법 (capture/)
    @property
    def capturer(self) -> capture_methods.Capture:
        if self._capturer is None:
            self._capturer = capture_methods.load(self)
        return self._capturer

    def take(self, local: Path) -> Path:
        """화면 1장 (캡처 방법에 맡김). 바로 저장되면 결과 폴더에 이름 붙인 복사본도 만든다."""
        path = self.capturer.take(local)
        self.collect(path)
        return path

    def collect(self, png: Path) -> list[Path]:
        """캡처 PNG 를 [output] result_dir 에 '시험_온도_핀_항목.png' 로 복사 (미뤄 둔 캡처는 가져올 때)."""
        sub = self.cfg.get("output", "result_dir").strip()
        if not sub:
            return []
        try:
            return gallery.collect(png, self.out_dir / sub)
        except OSError as e:  # 복사 실패로 측정을 멈추지 않는다
            self.echo(f"  [경고] 캡처 복사본을 만들지 못했습니다: {e}")
            return []

    def capture_enabled(self) -> bool:
        return self.capturer.enabled

    def _pending_owner(self) -> capture_methods.Capture:
        """미뤄 둔 캡처를 가진 방법. 다른 방법으로 바꾼 뒤에도 예전 aoe 캡처가 남아 있으면 그것을 가져온다."""
        if self.capturer.pending_count() or not (self.out_dir / "_pending_images.json").exists():
            return self.capturer
        from .capture import aoe
        return aoe.Capture(self)

    def pending_count(self) -> int:
        return self._pending_owner().pending_count()

    def fetch_pending(self) -> tuple[int, int]:
        """미뤄 둔 캡처 PNG 를 한 번에 가져온다. (가져온 수, 남은 수)"""
        return self._pending_owner().fetch_pending()

    # ------------------------------------------------------------------ 공통 도구 (steps 가 사용)
    def new_record(self, item: str, channels: list[int]) -> Record:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        chs = "-".join(f"CH{c}" for c in channels)
        base = f"{_safe(self.name)}_{_safe(self.temp)}C" + (f"_{chs}" if chs else "") + f"_{ts}"
        if self.dut_pin:
            out = f"_OUT{_safe(self.out_pin)}" if self.out_pin and item == "VIH" else ""
            base = f"PIN{_safe(self.dut_pin)}{out}_{base}"
        folder = self.out_dir / item / base
        n = 2
        while folder.exists():
            folder = self.out_dir / item / f"{base}_{n}"
            n += 1
        folder.mkdir(parents=True)
        return Record(item, folder, channels)

    def base_result(self, item: str) -> dict:
        return {
            "dut_pin": self.dut_pin,
            "out_pin": self.out_pin if item == "VIH" else "",
            "item": item,
            "name": self.name,
            "temp": self.temp,
            "out_type": self.out_type,
            "spec": self.spec_name,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "scope": self.scope.idn,
        }

    def finish(self, rec: Record) -> None:
        """측정 결과를 result.json 과 results.csv 에 기록하고 판정을 보여준다."""
        results.write_json(rec.folder / "result.json", rec.result)
        rows = results.rows_for(rec.result, self.cfg.spec(self.spec_name), rec.folder.relative_to(self.out_dir).as_posix())
        for r in rows:
            if r["judge"]:
                self.echo(f"  판정 {r['metric']:<14} worst {r['worst_V']:>8} V  {r['rule']} {r['spec_V'] or '-':>6}  -> {r['judge']}")
        self.echo(f"  폴더: {rec.folder}")
        try:
            results.append_csv(self.out_dir / "results.csv", rows)
        except OSError as e:
            # 엑셀에서 results.csv 를 열어두면 쓰기가 막힌다. 측정을 중단하지 않고 별도 파일에 남긴다.
            pending = self.out_dir / f"results_pending_{datetime.now():%Y%m%d_%H%M%S}.csv"
            try:
                results.write_csv(pending, rows)
                where = f"{pending.name} 에 대신 저장"
            except OSError:
                where = "result.json 에만 저장(analyze.py 로 요약 재생성 가능)"
            self.echo(f"  [경고] results.csv 에 기록하지 못했습니다(엑셀에서 열려 있나요?): {e}\n         -> {where}")

    def ensure_record(self) -> Record:
        if self.record is None:
            self.record = self.new_record("MISC", [])
            self.record.result = self.base_result("MISC")
            results.write_json(self.record.folder / "result.json", self.record.result)
        return self.record

    def label_folder(self, rec: Record, label: str) -> None:
        """폴더에 시험 항목도 표시하며 기존 폴더를 덮어쓰지 않는다."""
        target = rec.folder.with_name(rec.folder.name + "_" + _safe(label))
        n = 2
        while target.exists():
            target = rec.folder.with_name(rec.folder.name + "_" + _safe(label) + f"_{n}")
            n += 1
        rec.folder.rename(target)
        rec.folder = target

    def prepare_overlay(self) -> None:
        """새 측정 전에 이전 측정/커서를 정리한다."""
        if self.cfg.getbool("measure", "clear_existing_measurements"):
            self.scope.delete_all_measurements()
        elif self.record:
            for name, _, _ in self.record.meas:
                self.scope.delete_measurement(name)
        self.scope.cursor_on(False)
        self.scope.check("이전 측정 정리", raise_=False)

    def hide_overlay(self, rec: Record) -> None:
        # 측정을 지우면 통계가 사라지고 TekScope 가 불안정해질 수 있어 결과 바만 숨긴다
        self.scope.results_bar(False)
        if rec.cursor:
            self.scope.cursor_on(False)
        self.scope.t.sync()

    def restore_overlay(self, rec: Record) -> None:
        self.scope.results_bar(True)
        if rec.cursor:
            self.scope.cursor_on(True)
        self.scope.t.sync()
        err = self.scope.check("측정/커서 복원", raise_=False)
        if err:
            self.echo(f"  [경고] {err}")

    def clear_overlay(self) -> None:
        if self.record:
            self.hide_overlay(self.record)
            self.record.meas.clear()
            self.record.cursor = None
        else:
            self.scope.cursor_on(False)
        self.scope.check("측정/커서 삭제", raise_=False)

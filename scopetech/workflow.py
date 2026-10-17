"""측정 작업 흐름: 1) 측정/커서  2) 화면 캡처(측정 有/無)  3) 파형 저장."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import results, wavefile
from .analysis import find_thresholds, summarize
from .config import Config
from .mso import MSO5B

VOH_TYPES = ("TOP", "BASE", "MAXIMUM", "MINIMUM")


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


def _v(x: float | None, unit: str = "V") -> str:
    return "-" if x is None else f"{x:.4f} {unit}"


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
        self.record: Record | None = None

    # ------------------------------------------------------------------ 공통
    def _new_record(self, item: str, channels: list[int]) -> Record:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        chs = "-".join(f"CH{c}" for c in channels)
        base = f"{_safe(self.name)}_{_safe(self.temp)}C" + (f"_{chs}" if chs else "") + f"_{ts}"
        if self.dut_pin:
            base = f"PIN{_safe(self.dut_pin)}_{base}"
        folder = self.out_dir / item / base
        n = 2
        while folder.exists():
            folder = self.out_dir / item / f"{base}_{n}"
            n += 1
        folder.mkdir(parents=True)
        return Record(item, folder, channels)

    def _base_result(self, item: str) -> dict:
        return {
            "dut_pin": self.dut_pin,
            "item": item,
            "name": self.name,
            "temp": self.temp,
            "out_type": self.out_type,
            "spec": self.spec_name,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "scope": self.scope.idn,
        }

    def _finish(self, rec: Record) -> None:
        results.write_json(rec.folder / "result.json", rec.result)
        rows = results.rows_for(rec.result, self.cfg.spec(self.spec_name), rec.folder.relative_to(self.out_dir).as_posix())
        results.append_csv(self.out_dir / "results.csv", rows)
        for r in rows:
            if r["judge"]:
                self.echo(f"  판정 {r['metric']:<14} worst {r['worst_V']:>8} V  {r['rule']} {r['spec_V'] or '-':>6}  -> {r['judge']}")
        self.echo(f"  폴더: {rec.folder}")

    def _prepare_overlay(self) -> None:
        """새 측정 전에 이전 측정/커서를 정리한다."""
        if self.cfg.getbool("measure", "clear_existing_measurements"):
            self.scope.delete_all_measurements()
        elif self.record:
            for name, _, _ in self.record.meas:
                self.scope.delete_measurement(name)
        self.scope.cursor_on(False)
        self.scope.check("이전 측정 정리", raise_=False)

    def _ensure_record(self) -> Record:
        if self.record is None:
            self.record = self._new_record("MISC", [])
            self.record.result = self._base_result("MISC")
            results.write_json(self.record.folder / "result.json", self.record.result)
        return self.record

    # ------------------------------------------------------------------ 1) 측정: VIL/VIH
    def vih(self, ch_in: int, ch_out: int, hold: bool = False) -> Record:
        s, cfg = self.scope, self.cfg
        self._prepare_overlay()
        if hold:
            s.stop()
            self.echo("  현재 화면 파형으로 분석합니다(새 획득 없음).")
        else:
            if cfg.getbool("measure", "hires_for_vih") and not s.acq_mode().startswith("HIR"):
                s.set_acq_mode("HIRes")
                self.echo("  획득 모드를 High Res 로 변경했습니다.")
            self.echo("  단일 획득(Single) 대기 중...")
            s.single(cfg.getfloat("measure", "single_timeout_s"))
        win = s.fetch_waveform(ch_in)
        wout = s.fetch_waveform(ch_out)
        th = find_thresholds(
            win, wout,
            avg_points=cfg.getint("measure", "vin_avg_points"),
            cross=cfg.getfloat("measure", "out_cross"),
        )

        rec = self._new_record("VIH", [ch_in, ch_out])
        r = rec.result = self._base_result("VIH")
        r["channels"] = {"in": f"CH{ch_in}", "out": f"CH{ch_out}"}
        r["channel_names"] = {f"CH{c}": cfg.channel_name(c) for c in (ch_in, ch_out)}
        r["acq_mode"] = s.acq_mode()
        r["params"] = {"vin_avg_points": cfg.getint("measure", "vin_avg_points"), "out_cross": cfg.getfloat("measure", "out_cross")}
        r["analysis"] = th.to_dict()

        # 커서: A = 첫 VT+ 시점, B = 첫 VT- 시점 (입력 채널 전압이 화면에 표시됨)
        ta = th.vt_plus[0].time if th.vt_plus else None
        tb = th.vt_minus[0].time if th.vt_minus else None
        if ta is not None or tb is not None:
            ta = ta if ta is not None else tb
            tb = tb if tb is not None else ta
            s.cursor_waveform(ch_in, ta, tb)
            rec.cursor = {"kind": "waveform", "ch": ch_in, "a": ta, "b": tb}
            err = s.check("커서 설정", raise_=False)
            if err:
                self.echo(f"  [경고] {err}")
        r["cursor"] = rec.cursor

        p, m = summarize(th.vt_plus_v), summarize(th.vt_minus_v)
        pair = f"CH{ch_in}->CH{ch_out}"
        sig = f"{cfg.channel_name(ch_in)}->{cfg.channel_name(ch_out)}"
        r["metrics"] = results.vih_metrics(th, pair, sig)
        self.record = rec

        pol = {"inverting": "반전(인버터)", "non-inverting": "비반전(버퍼/AND/레벨시프터)"}.get(th.polarity, "판별 불가")
        self.echo(f"[VIH/VIL] 입력 CH{ch_in} -> 출력 CH{ch_out}, 출력 극성: {pol}")
        for label, st in (("VT+ (상승, VIH측)", p), ("VT- (하강, VIL측)", m)):
            if st:
                self.echo(f"  {label}: 평균 {st['mean']:.4f} V  (n={st['n']}, min {st['min']:.4f} / max {st['max']:.4f})")
            else:
                self.echo(f"  {label}: 없음")
        self.echo(f"  히스테리시스: {_v(th.hysteresis)}")
        if th.out_levels:
            self.echo(f"  출력 레벨(참고): High {th.out_levels.top:.4f} V / Low {th.out_levels.base:.4f} V")
        if rec.cursor:
            self.echo(f"  커서 A={ta * 1e3:+.4f} ms (VT+), B={tb * 1e3:+.4f} ms (VT-)  [소스 CH{ch_in}]")
        for w in th.warnings:
            self.echo(f"  [주의] {w}")
        self._finish(rec)
        return rec

    # ------------------------------------------------------------------ 1) 측정: VOH/VOL
    def voh(self, chans: list[int], hold: bool = False) -> Record:
        s, cfg = self.scope, self.cfg
        self._prepare_overlay()
        names = s.free_measurement_names(len(chans) * len(VOH_TYPES))
        meas = []
        for ch in chans:
            for mtype in VOH_TYPES:
                name = names.pop(0)
                s.add_measurement(name, mtype, ch)
                meas.append((name, mtype, ch))
        s.check("측정 추가")
        if hold:
            s.stop()
            n_acq = 1
            self.echo("  현재 화면 파형으로 측정합니다(새 획득 없음).")
        else:
            n = cfg.getint("measure", "stats_acquisitions")
            self.echo(f"  통계 수집 중... ({n}회 획득)")
            n_acq = s.acquire_stats(n, cfg.getfloat("measure", "stats_timeout_s"))
        values = {name: s.measurement_results(name) for name, _, _ in meas}

        rec = self._new_record("VOH", chans)
        rec.meas = meas
        r = rec.result = self._base_result("VOH")
        r["channels"] = [f"CH{c}" for c in chans]
        r["channel_names"] = {f"CH{c}": cfg.channel_name(c) for c in chans}
        r["acquisitions"] = n_acq
        r["measurements"] = {f"{mtype}_CH{ch}": {"scope_name": name, **values[name]} for name, mtype, ch in meas}

        self.echo(f"[VOH/VOL] 출력 타입 {self.out_type}, 획득 {n_acq}회")
        metrics = []
        for ch in chans:
            v = {mtype: values[name] for name, mtype, c in meas if c == ch}
            top, base, vmax, vmin = v["TOP"], v["BASE"], v["MAXIMUM"], v["MINIMUM"]
            label, sig = f"CH{ch}", cfg.channel_name(ch)
            metrics += [
                results.metric(label, sig, "VOH (TOP)", "VOH", top["mean"], top["min"], top["population"]),
                results.metric(label, sig, "VOL (BASE)", "VOL", base["mean"], base["max"], base["population"]),
                results.metric(label, sig, "MAX", None, vmax["mean"], vmax["max"], vmax["population"]),
                results.metric(label, sig, "MIN", None, vmin["mean"], vmin["min"], vmin["population"]),
            ]
            self.echo(
                f"  CH{ch}({sig}): VOH {_v(top['mean'])} (worst {_v(top['min'])}) | "
                f"VOL {_v(base['mean'])} (worst {_v(base['max'])}) | MAX {_v(vmax['max'])} | MIN {_v(vmin['min'])}"
            )
        r["metrics"] = metrics

        first = values[meas[0][0]], values[meas[1][0]]  # 첫 채널 TOP, BASE
        if cfg.getbool("measure", "voh_cursor") and first[0]["mean"] is not None and first[1]["mean"] is not None:
            s.cursor_hbars(chans[0], first[0]["mean"], first[1]["mean"])
            rec.cursor = {"kind": "hbars", "ch": chans[0], "a": first[0]["mean"], "b": first[1]["mean"]}
            err = s.check("커서 설정", raise_=False)
            if err:
                self.echo(f"  [경고] {err}")
        r["cursor"] = rec.cursor
        self.record = rec
        self._finish(rec)
        return rec

    # ------------------------------------------------------------------ 2) 화면 캡처
    def _hide_overlay(self, rec: Record) -> None:
        # 측정을 지우면 통계가 사라지고 TekScope 가 불안정해질 수 있어 결과 바만 숨긴다
        self.scope.results_bar(False)
        if rec.cursor:
            self.scope.cursor_on(False)
        self.scope.t.sync()

    def _restore_overlay(self, rec: Record) -> None:
        self.scope.results_bar(True)
        if rec.cursor:
            self.scope.cursor_on(True)
        self.scope.t.sync()
        err = self.scope.check("측정/커서 복원", raise_=False)
        if err:
            self.echo(f"  [경고] {err}")

    def capture(self) -> list[Path]:
        rec = self._ensure_record()
        s = self.scope
        s.freeze(self.cfg.getfloat("measure", "single_timeout_s"))  # 두 캡처와 저장 파형이 같은 획득이 되도록
        ink = self.cfg.getbool("capture", "ink_saver")
        self.echo("  화면 캡처: Single 획득으로 스코프가 직접 저장(Act On Event)")
        out = []
        if rec.meas or rec.cursor:
            p = rec.folder / "meas.png"
            p.write_bytes(s.screenshot(ink))
            out.append(p)
            self._hide_overlay(rec)
            try:
                p = rec.folder / "clean.png"
                p.write_bytes(s.screenshot(ink))
                out.append(p)
            finally:
                self._restore_overlay(rec)
        else:
            p = rec.folder / "clean.png"
            p.write_bytes(s.screenshot(ink))
            out.append(p)
            self.echo("  측정/커서가 없어 clean.png 만 저장했습니다.")
        self.echo("[캡처] " + ", ".join(x.name for x in out) + f"  -> {rec.folder}")
        return out

    # ------------------------------------------------------------------ 3) 파형 저장
    def save(self, chans: list[int] | None = None) -> list[Path]:
        rec = self._ensure_record()
        s, cfg = self.scope, self.cfg
        s.freeze(cfg.getfloat("measure", "single_timeout_s"))
        if not chans:
            setting = cfg.get("save", "channels").lower()
            if setting in ("", "displayed", "all"):
                chans = s.displayed_channels()
            else:
                chans = [int(x) for x in re.split(r"[,\s]+", setting) if x]
        if not chans:
            raise ValueError("저장할 채널이 없습니다(화면에 켜진 채널 없음).")
        out: list[Path] = []
        if cfg.getbool("save", "csv"):
            self.echo(f"  파형 데이터 전송 중... ({', '.join(f'CH{c}' for c in chans)})")
            wfs = [s.fetch_waveform(c) for c in chans]
            names = {f"CH{c}": cfg.channel_name(c) for c in chans}
            out += wavefile.write_csv(rec.folder, wfs, names)
        if cfg.getbool("save", "wfm"):
            for c in chans:
                p = rec.folder / f"CH{c}.wfm"
                p.write_bytes(s.save_wfm(c))
                out.append(p)
        if cfg.getbool("save", "setup"):
            p = rec.folder / "setup.set"
            p.write_bytes(s.save_setup())
            out.append(p)
        rec.result.setdefault("saved_channels", [])
        rec.result["saved_channels"] = sorted(set(rec.result["saved_channels"]) | {f"CH{c}" for c in chans})
        results.write_json(rec.folder / "result.json", rec.result)
        self.echo("[저장] " + ", ".join(x.name for x in out) + f"  -> {rec.folder}")
        return out

    # ------------------------------------------------------------------ 기타
    def clear_overlay(self) -> None:
        if self.record:
            self._hide_overlay(self.record)
            self.record.meas.clear()
            self.record.cursor = None
        else:
            self.scope.cursor_on(False)
        self.scope.check("측정/커서 삭제", raise_=False)


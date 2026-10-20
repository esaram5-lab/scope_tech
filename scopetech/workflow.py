"""측정 작업 흐름: 1) 측정/커서  2) 화면 캡처(측정 有/無)  3) 파형 저장."""
from __future__ import annotations

import json
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
        self.out_pin = ""  # VIL/VIH 측정 시 출력 DUT 핀
        self.record: Record | None = None

    # ------------------------------------------------------------------ 공통
    def _new_record(self, item: str, channels: list[int]) -> Record:
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

    def _base_result(self, item: str) -> dict:
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

    def _finish(self, rec: Record) -> None:
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
        # 화면에도 출력 VOH/VOL 이 보이도록 출력 채널에 TOP/BASE 측정 추가.
        # 획득이 끝난 뒤에 추가한다(획득 중 측정 계산 부하/이벤트 방지). 판정 값은 위 파형에서 계산.
        badges = []
        if cfg.getbool("measure", "vih_output_meas"):
            for name, mtype in zip(s.free_measurement_names(2), ("TOP", "BASE")):
                s.add_measurement(name, mtype, ch_out)
                badges.append((name, mtype, ch_out))
            err = s.check("출력 측정 추가", raise_=False)
            if err:
                self.echo(f"  [경고] {err}")

        rec = self._new_record("VIH", [ch_in, ch_out])
        rec.meas = badges
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
        r["metrics"] = results.vih_metrics(th, pair, sig) + results.vih_output_metrics(
            th, f"CH{ch_out}", cfg.channel_name(ch_out))
        self.record = rec

        pol = {"inverting": "반전(인버터)", "non-inverting": "비반전(버퍼/AND/레벨시프터)"}.get(th.polarity, "판별 불가")
        self.echo(f"[VIH/VIL] 입력 CH{ch_in} -> 출력 CH{ch_out}, 출력 극성: {pol}")
        for label, st in (("VT+ (상승, VIH측)", p), ("VT- (하강, VIL측)", m)):
            if st:
                self.echo(f"  {label}: 평균 {st['mean']:.4f} V  (n={st['n']}, min {st['min']:.4f} / max {st['max']:.4f})")
            else:
                self.echo(f"  {label}: 없음")
        self.echo(f"  히스테리시스: {_v(th.hysteresis)}")
        hs, ls = summarize(th.out_highs), summarize(th.out_lows)
        self.echo(f"  출력 VOH: {_v(hs and hs['mean'])} (worst {_v(hs and hs['min'])}, n={hs['n'] if hs else 0})"
                  f" | VOL: {_v(ls and ls['mean'])} (worst {_v(ls and ls['max'])}, n={ls['n'] if ls else 0})"
                  f"  [출력 {self.out_type}{', 핀 ' + self.out_pin if self.out_pin else ''}]")
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

    def _shot(self, ink: bool) -> bytes:
        c = self.cfg
        return self.scope.screenshot(
            ink, timeout=c.getfloat("measure", "single_timeout_s"),
            arm_wait=c.getfloat("capture", "aoe_arm_wait_s"),
            save_wait=c.getfloat("capture", "aoe_save_wait_s"),
            post_wait=c.getfloat("capture", "aoe_post_wait_s"),
        )

    # ---- 캡처 파일 나중에 한 번에 가져오기 (config [capture] image_fetch = end | now)
    @property
    def _pending_path(self) -> Path:
        return self.out_dir / "_pending_images.json"

    def _load_pending(self) -> list[dict]:
        try:
            return json.loads(self._pending_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save_pending(self, items: list[dict]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if items:
            self._pending_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        elif self._pending_path.exists():
            self._pending_path.unlink()

    def _deferred(self) -> bool:
        return self.cfg.get("capture", "image_fetch").lower() != "now"

    def _take(self, ink: bool, local: Path) -> Path:
        """화면 캡처 1장. 기본(end)은 스코프에 저장만 하고 가져올 목록에 기록, now 는 바로 가져온다."""
        if not self._deferred():
            local.write_bytes(self._shot(ink))
            return local
        c = self.cfg
        dest, prefix = self.scope.capture_deferred(
            ink, timeout=c.getfloat("measure", "single_timeout_s"),
            arm_wait=c.getfloat("capture", "aoe_arm_wait_s"),
            save_wait=c.getfloat("capture", "aoe_save_wait_s"),
            post_wait=c.getfloat("capture", "aoe_post_wait_s"),
        )
        items = self._load_pending()
        items.append({"dest": dest, "prefix": prefix, "local": str(local), "time": datetime.now().isoformat(timespec="seconds"),
                      **getattr(self.scope, "last_capture", {})})
        self._save_pending(items)
        return local

    def pending_count(self) -> int:
        return len(self._load_pending())

    def fetch_pending(self) -> tuple[int, int]:
        """스코프에 저장해 둔 캡처 PNG 를 한 번에 가져온다. (가져온 수, 남은 수)"""
        items = self._load_pending()
        if not items:
            return 0, 0
        self.echo(f"[캡처 파일 가져오기] {len(items)}개 (파일 사이 {self.cfg.get('capture', 'fetch_gap_s')}초 간격)")
        self.scope.stop()
        got = self.scope.fetch_images(items, delete=self.cfg.getbool("capture", "delete_after_fetch"),
                                      gap=self.cfg.getfloat("capture", "fetch_gap_s"))
        left = []
        for it in items:
            data = got.get(it["prefix"])
            if data is None:
                left.append(it)
                self.echo(f"  [못 가져옴] {it['local']} (스코프 파일 {it['prefix']}*.png)")
                continue
            Path(it["local"]).parent.mkdir(parents=True, exist_ok=True)
            Path(it["local"]).write_bytes(data)
            self.echo(f"  {it['local']}")
        self._save_pending(left)
        return len(items) - len(left), len(left)

    def capture(self) -> list[Path]:
        rec = self._ensure_record()
        s = self.scope
        s.freeze(self.cfg.getfloat("measure", "single_timeout_s"))  # 두 캡처와 저장 파형이 같은 획득이 되도록
        ink = self.cfg.getbool("capture", "ink_saver")
        self.echo("  화면 캡처: Single 획득으로 스코프가 직접 저장(Act On Event)")
        out = []
        if rec.meas or rec.cursor:
            out.append(self._take(ink, rec.folder / "meas.png"))
            self._hide_overlay(rec)
            try:
                out.append(self._take(ink, rec.folder / "clean.png"))
            finally:
                self._restore_overlay(rec)
        else:
            out.append(self._take(ink, rec.folder / "clean.png"))
            self.echo("  측정/커서가 없어 clean.png 만 저장했습니다.")
        later = " (스코프에 저장, 종료 시 한 번에 가져옴)" if self._deferred() else ""
        self.echo("[캡처] " + ", ".join(x.name for x in out) + later + f"  -> {rec.folder}")
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

    def reset(self) -> None:
        """매 측정 시작 전 스코프 통신/상태 정리 (config [measure] reset_each_run)."""
        if self.cfg.getbool("measure", "reset_each_run"):
            self.scope.reset_state()
            self.echo("  스코프 상태 정리 완료(통신 버퍼·오류·Act On Event)")

    def save_manual(self, label: str, chans: list[int], note: str = "") -> Record:
        """수동 시험: 기존 측정/커서는 유지하고 CSV와 화면 1장을 저장한다."""
        if not label.strip() or not chans or any(c < 1 or c > self.scope.num_channels for c in chans):
            raise ValueError("시험 이름과 저장 채널을 확인해주세요.")
        s = self.scope
        s.stop()
        rec = self._new_record("MANUAL", chans)
        rec.result = self._base_result("MANUAL")
        rec.result.update({"manual_item": label, "note": note,
                           "channels": [f"CH{c}" for c in chans],
                           "metrics": [], "status": "incomplete",
                           "capture_note": "CSV 저장 후 화면 캡처를 위해 추가 Single 획득. 동일 획득 아님."})
        self._label_folder(rec, label)
        self.record = rec
        results.write_json(rec.folder / "result.json", rec.result)
        self.echo("[1/2] 선택한 채널의 파형 CSV 저장")
        wfs = [s.fetch_waveform(c) for c in chans]
        wavefile.write_csv(rec.folder, wfs, {f"CH{c}": self.cfg.channel_name(c) for c in chans})
        self.echo("[2/2] 기존 측정·커서를 표시한 화면 저장 (추가 Single 획득)")
        self._take(self.cfg.getbool("capture", "ink_saver"), rec.folder / "screen.png")
        rec.result.update({"status": "complete", "saved_channels": [f"CH{c}" for c in chans]})
        results.write_json(rec.folder / "result.json", rec.result)
        return rec

    def _label_folder(self, rec: Record, label: str) -> None:
        """폴더에 시험 항목도 표시하며 기존 폴더를 덮어쓰지 않는다."""
        target = rec.folder.with_name(rec.folder.name + "_" + _safe(label))
        n = 2
        while target.exists():
            target = rec.folder.with_name(rec.folder.name + "_" + _safe(label) + f"_{n}")
            n += 1
        rec.folder.rename(target)
        rec.folder = target

    def snap(self, label: str, note: str = "", cursor: bool = True) -> Record:
        """화면만 저장: 파형 데이터·측정·설정 파일 없이 커서를 켜고 지금 화면을 캡처한다.
        화면 캡처는 Act On Event 라 Single 1회 획득이 일어난다(원격 SAVe:IMAGe 는 TekScope 를 멈춤)."""
        if not label.strip():
            raise ValueError("캡처 이름을 입력해주세요.")
        rec = self._new_record("SNAP", [])
        rec.result = self._base_result("SNAP")
        rec.result.update({"snap_item": label, "note": note, "cursor": cursor, "metrics": [],
                           "status": "incomplete", "capture_note": "Act On Event Single 1회 획득 화면. 파형 데이터 없음."})
        self._label_folder(rec, label)
        self.record = rec
        results.write_json(rec.folder / "result.json", rec.result)
        if cursor:
            self.echo("[1/2] 커서 표시 (스코프에 설정된 커서 위치 그대로)")
            self.scope.cursor_on(True)
            self.scope.pace()
        self.echo(f"[{2 if cursor else 1}/{2 if cursor else 1}] 화면 캡처 (Single 1회, 스코프에 저장)")
        self._take(self.cfg.getbool("capture", "ink_saver"), rec.folder / "screen.png")
        rec.result["status"] = "complete"
        results.write_json(rec.folder / "result.json", rec.result)
        return rec

    # ------------------------------------------------------------------ 기타
    def clear_overlay(self) -> None:
        if self.record:
            self._hide_overlay(self.record)
            self.record.meas.clear()
            self.record.cursor = None
        else:
            self.scope.cursor_on(False)
        self.scope.check("측정/커서 삭제", raise_=False)


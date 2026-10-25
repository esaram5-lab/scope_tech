"""온도를 잘못 넣고 측정한 기록 고치기 (fix_temp.py / run_fix_temp.bat 가 사용).

고치는 것 (샘플·잘못된 온도·측정 묶음으로 고른 기록만)
  - 측정 폴더의 result.json 의 temp (원본은 처음 한 번 result.orig.json, 고친 내역은 temp_fixed)
  - 진행 기록: plan/<샘플>/progress_잘못C.json 의 그 항목을 progress_원래C.json 으로 옮김
    (원래 온도에 이미 완료된 같은 항목이 있으면 replace=True 일 때만 바꿈). 비게 된 진행 기록 파일은 지움
  - 사진 모음: 그 기록에서 만든 잘못된 온도 이름의 사진을 지우고 새 온도 이름으로 다시 복사
폴더 이름(…_85C_…)은 그대로 둔다(도구들은 result.json 의 temp 를 먼저 읽음). 요약표·results.csv 는 CLI 가 다시 만든다.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import gallery, results
from .plan import DONE, PlanStore, norm_temp, write_progress_csv
from .plan_import import _time_of, name_from_folder
from .samples import Samples

IMAGES = ("meas.png", "clean.png", "screen.png")


@dataclass
class Rec:
    folder: Path
    rel: str
    result: dict
    name: str
    temp: str
    time: str
    sample: str = ""


@dataclass
class Report:
    fixed: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)       # 진행 기록에서 옮긴 항목 (샘플 온도 key)
    kept: list[str] = field(default_factory=list)        # 원래 온도에 완료된 항목이 있어 옮기지 않음
    removed_files: list[str] = field(default_factory=list)
    photos_removed: int = 0
    photos_copied: int = 0


def scan(base: Path, skip: tuple[str, ...] = ("plan", "result")) -> list[Rec]:
    """결과 폴더의 모든 측정 기록(VIH / VOH / MANUAL / POR / SNAP / MISC)."""
    out = []
    for meta in sorted(Path(base).glob("*/*/result.json")):
        folder = meta.parent
        if folder.parent.name in skip:
            continue
        try:
            r = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(r, dict):
            continue
        try:
            temp = norm_temp(str(r.get("temp", "")))
        except ValueError:
            temp = str(r.get("temp", ""))
        name = str(r.get("name") or name_from_folder(folder.name)).strip()
        out.append(Rec(folder, folder.relative_to(base).as_posix(), r, name, temp, _time_of(r, folder)))
    return out


def by_sample(recs: list[Rec], samples: Samples) -> dict[str, list[Rec]]:
    out: dict[str, list[Rec]] = {}
    for r in recs:
        r.sample = samples.id_of(r.name) if r.name else ""
        out.setdefault(r.sample, []).append(r)
    return out


def sessions(recs: list[Rec], gap_min: float = 30) -> list[list[Rec]]:
    """측정 시각으로 묶음(gap_min 분 넘게 비면 새 묶음)."""
    def t(r):
        try:
            return datetime.fromisoformat(r.time)
        except ValueError:
            return None
    groups: list[list[Rec]] = []
    last = None
    for r in sorted(recs, key=lambda r: r.time):
        now = t(r)
        if not groups or last is None or now is None or (now - last).total_seconds() > gap_min * 60:
            groups.append([])
        groups[-1].append(r)
        last = now or last
    return groups


def conflicts(root: PlanStore, recs: list[Rec], from_t: str, to_t: str) -> list[str]:
    """원래 온도 진행 기록에 이미 다른 기록으로 완료된 항목 key (옮길 항목과 겹치는 것)."""
    rels = {r.rel for r in recs}
    out = []
    for sid in sorted({r.sample for r in recs}):
        st = root.of(sid)
        src = st.load(from_t).get("rows", {})
        dst = st.load(to_t).get("rows", {})
        for key, e in src.items():
            d = dst.get(key, {})
            if e.get("folder") in rels and d.get("status") == DONE and d.get("folder") not in rels:
                out.append(f"{sid} {key}")
    return out


def _remove_photos(rec: Rec, result_dir: Path) -> int:
    """그 기록의 사진으로 만든 복사본(이름 + 같은 내용)을 지운다."""
    n = 0
    for img in IMAGES:
        png = rec.folder / img
        if not png.is_file():
            continue
        data = png.read_bytes()
        for stem in gallery.image_names(rec.result, img):
            for cand in [result_dir / f"{stem}.png"] + sorted(result_dir.glob(f"{stem}_[0-9]*.png")):
                try:
                    if cand.is_file() and cand.stat().st_size == len(data) and cand.read_bytes() == data:
                        cand.unlink()
                        n += 1
                except OSError:
                    continue
    return n


def apply(base: Path, root: PlanStore, rows, recs: list[Rec], from_t: str, to_t: str, replace: bool = False,
          result_dir: str = "result") -> Report:
    rep = Report()
    now = datetime.now().isoformat(timespec="seconds")
    rels = {r.rel for r in recs}
    photo_dir = Path(base) / result_dir if result_dir else None
    for rec in recs:  # 1) 사진 복사본(잘못된 온도 이름)을 먼저 지운다 — 이름이 result.json 의 temp 로 만들어지므로
        if photo_dir and photo_dir.is_dir():
            rep.photos_removed += _remove_photos(rec, photo_dir)
    for rec in recs:  # 2) result.json
        backup = rec.folder / "result.orig.json"
        if not backup.exists():
            shutil.copy2(rec.folder / "result.json", backup)
        rec.result["temp"] = to_t
        rec.result.setdefault("temp_fixed", []).append({"from": from_t, "to": to_t, "at": now})
        results.write_json(rec.folder / "result.json", rec.result)
        rep.fixed.append(rec.rel)
    for sid in sorted({r.sample for r in recs}):  # 3) 진행 기록
        st = root.of(sid)
        src = st.load(from_t)
        if not src.get("rows"):
            continue
        dst = st.load(to_t)
        dst.setdefault("rows", {})
        dst.setdefault("temp", to_t)
        if src.get("name"):
            dst.setdefault("name", src["name"])
        for key in list(src["rows"]):
            e = src["rows"][key]
            if e.get("folder") not in rels:
                continue
            d = dst["rows"].get(key, {})
            if d.get("status") == DONE and d.get("folder") not in rels and not replace:
                rep.kept.append(f"{sid} {key}")
            else:
                dst["rows"][key] = {**e, "temp_fixed": f"{from_t} -> {to_t} ({now})"}
                rep.moved.append(f"{sid} {key}")
            del src["rows"][key]
        st.save(to_t, dst)
        write_progress_csv(rows, st, to_t)
        if src["rows"]:
            st.save(from_t, src)
            write_progress_csv(rows, st, from_t)
        else:  # 잘못된 온도 기록이 모두 옮겨졌으면 파일을 지운다
            for p in (st.progress_path(from_t), st.dir / f"plan_progress_{from_t}C.csv"):
                if p.exists():
                    p.unlink()
                    rep.removed_files.append(p.relative_to(base).as_posix())
    if photo_dir:  # 4) 새 온도 이름으로 사진 다시 복사
        for rec in recs:
            for img in IMAGES:
                png = rec.folder / img
                if png.is_file():
                    rep.photos_copied += len(gallery.collect(png, photo_dir, skip_same=True))
    return rep


def write_log(rep: Report, recs: list[Rec], from_t: str, to_t: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"온도 고치기 {from_t}°C -> {to_t}°C  ({datetime.now():%Y-%m-%d %H:%M:%S})", ""]
    lines += [f"result.json: {r.rel} ({r.time}, {r.name})" for r in recs]
    lines += [""] + [f"진행 기록 옮김: {m}" for m in rep.moved] + [f"옮기지 않음(원래 온도에 완료된 기록 있음): {k}" for k in rep.kept]
    lines += [f"지운 파일: {f}" for f in rep.removed_files]
    lines.append(f"사진: 지움 {rep.photos_removed}장, 다시 복사 {rep.photos_copied}장")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

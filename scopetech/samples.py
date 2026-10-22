"""시험 이름 -> 샘플(fab·번호) 대응표 plan/samples.csv. 같은 계획을 샘플마다 따로 진행·요약한다.

    시험이름,fab,샘플
    TSMC_1,TSMC,1
    VIS-2 재측정,VIS,2       <- 이름이 달라도 fab·샘플이 같으면 같은 샘플
샘플 ID = fab_샘플 (예: TSMC_1). fab·샘플 칸이 비면 시험 이름 그대로 한 샘플로 본다.
표에 없는 이름은 이름에서 추정해(TSMC / VIS·VANGUARD·뱅가드 + 마지막 숫자) 표에 추가한다. 엑셀로 고칠 수 있다.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

SAMPLES_FILE = "samples.csv"
FABS = (("TSMC", ("TSMC",)), ("VIS", ("VANGUARD", "VIS", "뱅가드")))
HEADER = ["시험이름", "fab", "샘플"]


def safe(text: str) -> str:
    """폴더 이름으로 쓸 수 있게 (workflow 의 폴더 이름 규칙과 같음)."""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", str(text)).strip("_.") or "X"


def guess(name: str) -> tuple[str, str]:
    """(fab, 샘플 번호) 추정. 모르면 빈칸."""
    up = str(name).upper()
    fab = ""
    for key, words in FABS:
        hit = next((w for w in words if w.upper() in up), None)
        if hit:
            fab = key
            up = up.replace(hit.upper(), " ")
            break
    nums = re.findall(r"\d+", up)
    return (fab, str(int(nums[-1]))) if fab and nums else (fab, "")


def sample_id(fab: str, no: str, name: str) -> str:
    fab, no = str(fab).strip().upper(), str(no).strip()
    return safe(f"{fab}_{no}") if fab and no else safe(name)


def order_key(sid: str):
    """VIS -> TSMC 순(그 외 이름은 뒤), 같은 fab 안에서는 샘플 번호 순."""
    fab, _, no = sid.partition("_")
    rank = {f: i for i, (f, _) in enumerate(reversed(FABS))}.get(fab, len(FABS))
    return (rank, fab if fab in dict(FABS) else sid, int(no) if no.isdigit() else 0, sid)


class Samples:
    def __init__(self, plan_root: Path):
        self.path = Path(plan_root) / SAMPLES_FILE
        self.rows: dict[str, tuple[str, str]] = {}
        try:
            with self.path.open(encoding="utf-8-sig", newline="") as f:
                for rec in csv.DictReader(f):
                    name = (rec.get("시험이름") or "").strip()
                    if name:
                        self.rows[name] = ((rec.get("fab") or "").strip().upper(), (rec.get("샘플") or "").strip())
        except (OSError, UnicodeDecodeError, csv.Error):
            pass

    def id_of(self, name: str) -> str:
        name = str(name).strip()
        fab, no = self.rows[name] if name in self.rows else guess(name)
        return sample_id(fab, no, name)

    def describe(self, name: str) -> str:
        sid = self.id_of(name)
        fab, no = self.rows.get(str(name).strip(), guess(name))
        if fab and no:
            return f"샘플 {sid} (fab {fab}, 샘플 {no})"
        return f"샘플 {sid} — {self.path.name} 에 fab·샘플을 적으면 다른 이름과 묶을 수 있습니다"

    def sync(self, names) -> list[str]:
        """표에 없는 이름을 추정값으로 추가하고 저장. 추가한 이름 목록."""
        added = [n for n in dict.fromkeys(str(x).strip() for x in names) if n and n not in self.rows]
        if not added:
            return []
        for n in added:
            self.rows[n] = guess(n)
        self.save()
        return added

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            for name, (fab, no) in self.rows.items():
                w.writerow([name, fab, no])

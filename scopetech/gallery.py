"""캡처 사진 모아 두기: 측정 폴더의 PNG 를 알아보기 쉬운 이름으로 결과 폴더 한곳에 복사한다.

    captures/result/INV_3V3_25C_PIN2_VIL-VIH.png         VIL/VIH 측정 표시 화면(meas.png) - 입력 핀
    captures/result/INV_3V3_25C_PIN12_VOH-VOL.png        같은 화면 - 출력 핀(같은 파형에서 VOH/VOL 측정)
    captures/result/INV_3V3_25C_PIN2_VIL-VIH_clean.png   측정 숨긴 화면(clean.png)
    captures/result/INV_3V3_85C_PIN20_VOH-VOL.png
    captures/result/INV_3V3_25C_PIN5_R_pu.png                  메뉴 3 (항목 이름)
    captures/result/INV_3V3_25C_PIN7_edge.png                  메뉴 4 (캡처 이름)
같은 이름이 있으면 _2, _3 ... 을 붙인다. 원본(측정 폴더의 PNG)은 그대로 둔다.
[output] result_dir 로 폴더 이름을 바꾸고, 비우면 복사하지 않는다.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ITEM = {"VIH": "VIL-VIH", "VOH": "VOH-VOL"}


def _safe(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", str(text)).strip("_") or "X"


def image_names(result: dict, image: str) -> list[str]:
    """복사본 이름들(확장자 제외). VIL/VIH 측정은 출력 핀 VOH/VOL 도 같은 화면에서 재므로 출력 핀 이름도 만든다."""
    item = result.get("item", "")
    head = [_safe(result.get("name", "")), f"{_safe(result.get('temp', ''))}C"]
    tail = ["clean"] if Path(image).stem == "clean" and item in ITEM else []

    def name(pin, label):
        return "_".join(head + ([f"PIN{_safe(pin)}"] if pin else []) + [_safe(label)] + tail)
    label = ITEM.get(item) or result.get("manual_item") or result.get("snap_item") or item or "SCREEN"
    names = [name(result.get("dut_pin"), label)]
    if item == "VIH" and result.get("out_pin"):
        names.append(name(result["out_pin"], ITEM["VOH"]))
    return names


def image_name(result: dict, image: str) -> str:
    return image_names(result, image)[0]


def collect(png: Path, result_dir: Path, skip_same: bool = False) -> list[Path]:
    """png 옆 result.json 을 읽어 result_dir 에 이름 붙인 복사본(들)을 만든다. 정보가 없으면 건너뛴다.
    skip_same: 내용이 같은 사진이 이미 있으면 복사하지 않음(기존 사진 모으기용. 측정 중에는 재측정을 모두 남김)."""
    png = Path(png)
    meta = png.parent / "result.json"
    if not png.exists() or not meta.exists():
        return []
    try:
        result = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    result_dir.mkdir(parents=True, exist_ok=True)
    data = png.read_bytes()
    out = []
    for stem in image_names(result, png.name):
        target = result_dir / f"{stem}{png.suffix}"
        n = 2
        while target.exists():
            if skip_same and target.stat().st_size == len(data) and target.read_bytes() == data:
                break  # 같은 사진이 이미 있음(다시 모으기를 여러 번 실행해도 중복 없음)
            target = result_dir / f"{stem}_{n}{png.suffix}"
            n += 1
        else:
            shutil.copy2(png, target)
            out.append(target)
    return out


def collect_all(base: Path, result_dir: Path) -> tuple[int, int]:
    """base 아래 모든 측정 폴더의 캡처 사진을 result_dir 에 모은다. (새로 복사한 수, 살펴본 사진 수)"""
    copied = seen = 0
    for meta in sorted(Path(base).glob("*/*/result.json"), key=lambda p: p.parent.name):
        for name in ("meas.png", "clean.png", "screen.png"):
            png = meta.parent / name
            if png.exists():
                seen += 1
                copied += len(collect(png, result_dir, skip_same=True))
    return copied, seen

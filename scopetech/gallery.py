"""캡처 사진 모아 두기: 측정 폴더의 PNG 를 알아보기 쉬운 이름으로 결과 폴더 한곳에 복사한다.

    captures/result/INV_3V3_25C_PIN2_OUT12_VIL-VIH.png         측정 표시 화면(meas.png)
    captures/result/INV_3V3_25C_PIN2_OUT12_VIL-VIH_clean.png   측정 숨긴 화면(clean.png)
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


def image_name(result: dict, image: str) -> str:
    """result.json 내용과 원본 파일 이름(meas.png 등)으로 복사본 이름을 만든다(확장자 제외)."""
    item = result.get("item", "")
    parts = [_safe(result.get("name", "")), f"{_safe(result.get('temp', ''))}C"]
    if result.get("dut_pin"):
        parts.append(f"PIN{_safe(result['dut_pin'])}")
        if item == "VIH" and result.get("out_pin"):
            parts.append(f"OUT{_safe(result['out_pin'])}")
    label = ITEM.get(item) or result.get("manual_item") or result.get("snap_item") or item or "SCREEN"
    parts.append(_safe(label))
    if Path(image).stem == "clean" and item in ITEM:
        parts.append("clean")
    return "_".join(parts)


def collect(png: Path, result_dir: Path) -> Path | None:
    """png 옆 result.json 을 읽어 result_dir 에 이름 붙인 복사본을 만든다. 정보가 없으면 건너뛴다."""
    png = Path(png)
    meta = png.parent / "result.json"
    if not png.exists() or not meta.exists():
        return None
    try:
        result = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    result_dir.mkdir(parents=True, exist_ok=True)
    stem = image_name(result, png.name)
    target = result_dir / f"{stem}{png.suffix}"
    n = 2
    while target.exists():
        target = result_dir / f"{stem}_{n}{png.suffix}"
        n += 1
    shutil.copy2(png, target)
    return target

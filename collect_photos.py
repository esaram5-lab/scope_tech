"""이미 찍어 둔 캡처 사진도 결과 폴더(captures/result/)에 '시험_온도C_PIN핀_항목.png' 로 모은다 (스코프 연결 불필요).

    python collect_photos.py                 captures/ 의 모든 측정 폴더
    python collect_photos.py --dir captures_sim
여러 번 실행해도 같은 사진은 다시 복사하지 않는다. 원본은 그대로 둔다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scopetech.config import Config  # noqa: E402
from scopetech.gallery import collect_all  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="기존 캡처 사진을 결과 폴더에 모으기")
    ap.add_argument("--config", default=str(HERE / "config.ini"))
    ap.add_argument("--dir", help="결과 폴더 (기본: config.ini 의 base_dir)")
    args = ap.parse_args(argv)
    cfg = Config(args.config)
    base = Path(args.dir) if args.dir else HERE / cfg.get("output", "base_dir")
    sub = cfg.get("output", "result_dir").strip() or "result"
    if not base.is_dir():
        print(f"결과 폴더가 없습니다: {base}")
        return 1
    try:
        copied, seen = collect_all(base, base / sub)
    except OSError as e:
        print(f"[오류] 복사 실패: {e}")
        return 1
    print(f"사진 {seen}장 확인, 새로 복사 {copied}장 -> {base / sub}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

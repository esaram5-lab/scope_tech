"""화면 캡처 안 함 (run_nocap.bat, [capture] enabled = no 또는 method = off)."""
from __future__ import annotations

from pathlib import Path

from .base import Capture as Base


class Capture(Base):
    label = "끔"
    enabled = False

    def take(self, local: Path) -> Path:
        raise ValueError("화면 캡처를 끈 상태입니다(run_nocap / [capture] enabled = no).")

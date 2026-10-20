"""기본 화면 캡처: Act On Event (Single 1회 획득 때 스코프가 직접 PNG 저장).

실장비(MSO58B FV 2.24.4)에서 원격 SAVe:IMAGe 와 캡처 직후 파일 읽기가 TekScope 를 멈춰서 이 방식을 쓴다.
[capture] image_fetch = end(기본): 측정 중에는 스코프에 저장만 하고 가져올 목록(_pending_images.json)에 적어 두었다가
fetch_pending() 에서 한 번에 가져온다. now: 캡처할 때마다 바로 가져온다(이전 방식).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .base import Capture as Base


class Capture(Base):
    label = "Single 획득으로 스코프가 직접 저장(Act On Event)"

    def _waits(self) -> dict:
        c = self.cfg
        return {"timeout": c.getfloat("measure", "single_timeout_s"),
                "arm_wait": c.getfloat("capture", "aoe_arm_wait_s"),
                "save_wait": c.getfloat("capture", "aoe_save_wait_s"),
                "post_wait": c.getfloat("capture", "aoe_post_wait_s")}

    def deferred(self) -> bool:
        return self.cfg.get("capture", "image_fetch").lower() != "now"

    def take(self, local: Path) -> Path:
        if not self.deferred():
            local.write_bytes(self.scope.screenshot(self.ink, **self._waits()))
            return local
        dest, prefix = self.scope.capture_deferred(self.ink, **self._waits())
        items = self.load_pending()
        items.append({"dest": dest, "prefix": prefix, "local": str(local), "time": datetime.now().isoformat(timespec="seconds"),
                      **getattr(self.scope, "last_capture", {})})
        self.save_pending(items)
        return local

    # ---- 나중에 한 번에 가져오기
    @property
    def pending_path(self) -> Path:
        return self.bench.out_dir / "_pending_images.json"

    def load_pending(self) -> list[dict]:
        try:
            return json.loads(self.pending_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def save_pending(self, items: list[dict]) -> None:
        self.bench.out_dir.mkdir(parents=True, exist_ok=True)
        if items:
            self.pending_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        elif self.pending_path.exists():
            self.pending_path.unlink()

    def pending_count(self) -> int:
        return len(self.load_pending())

    def fetch_pending(self) -> tuple[int, int]:
        items = self.load_pending()
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
        self.save_pending(left)
        return len(items) - len(left), len(left)

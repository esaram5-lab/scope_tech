"""화면 캡처 방식의 기본형. 새 방식은 Capture 를 상속해 take() 를 구현한다."""
from __future__ import annotations

from pathlib import Path


class Capture:
    """화면 캡처 1장을 만드는 방법. 측정 흐름(steps/capture.py, manual.py, snap.py)은 이 인터페이스만 쓴다.

    - take(local): 지금 스코프 화면 1장을 local(PNG 경로)에 저장하고 local 을 돌려준다.
      나중에 한 번에 가져오는 방식이면 여기서는 예약만 하고 pending_count()/fetch_pending() 를 구현한다.
    - 쓸 수 있는 것: self.scope(MSO5B 드라이버), self.cfg(config.ini), self.echo(화면 출력), self.ink(잉크 절약 설정)
    """

    label = "화면 캡처"  # 캡처할 때 화면에 보여줄 방식 이름
    enabled = True        # False 면 캡처 단계를 건너뛴다(off.py)

    def __init__(self, bench):
        self.bench = bench

    @property
    def scope(self):
        return self.bench.scope

    @property
    def cfg(self):
        return self.bench.cfg

    def echo(self, text: str) -> None:
        self.bench.echo(text)

    @property
    def ink(self) -> bool:
        return self.cfg.getbool("capture", "ink_saver")

    def take(self, local: Path) -> Path:
        raise NotImplementedError(f"{type(self).__module__}.{type(self).__name__}.take() 를 구현하세요.")

    def pending_count(self) -> int:
        """아직 PC 로 가져오지 않은 캡처 수 (바로 저장하는 방식이면 0)."""
        return 0

    def fetch_pending(self) -> tuple[int, int]:
        """미뤄 둔 캡처를 가져온다. (가져온 수, 남은 수)"""
        return 0, 0

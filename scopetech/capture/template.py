"""화면 캡처 직접 구현용 틀.

사용법
1. 이 파일을 scope.py 옆에 복사해 이름을 바꾼다 (예: my_capture.py).
2. take() 를 구현한다: 스코프 화면 1장을 local(PNG 경로)에 저장하고 local 을 돌려준다.
3. config.ini [capture] method = my_capture  (클래스 이름이 Capture 가 아니면 my_capture:클래스이름)
4. py scope.py --sim 으로 흐름 확인 -> run_seq_test.bat --only capture 로 실장비 반복 시험.

쓸 수 있는 것
- self.scope : MSO5B 드라이버 (write/query/check/pace/stop/single/wait_idle/freeze/abort_capture ...)
               실장비 주의: 원격 SAVe:IMAGe, 캡처 직후 READFile/DELEte 는 TekScope 를 멈추게 한 적이 있음(HANDOFF.md)
- self.cfg   : config.ini (예: self.cfg.getfloat("capture", "aoe_save_wait_s"))
- self.echo  : 화면/로그 출력
- self.ink   : [capture] ink_saver
- 측정이 끝난 뒤 한 번에 가져오는 방식이면 pending_count()/fetch_pending() 도 구현 (aoe.py 참고).
- 예외를 올리면 그 측정은 중단·미완료로 표시된다. 스코프에 켜 둔 것이 있으면 정리한 뒤 올린다.
"""
from __future__ import annotations

from pathlib import Path

from scopetech.capture.base import Capture as Base


class Capture(Base):
    label = "직접 구현 (template)"

    def take(self, local: Path) -> Path:
        # 예)
        # self.scope.stop()
        # ... 스코프에 화면 이미지를 만들고 PC 로 가져오기 ...
        # local.parent.mkdir(parents=True, exist_ok=True)
        # local.write_bytes(png_bytes)
        # return local
        raise NotImplementedError("scopetech/capture/template.py 를 복사해 take() 를 구현하세요.")

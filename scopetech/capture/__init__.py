"""화면 캡처 방식 (교체 가능).

config.ini [capture] method 로 고른다.
    aoe        기본. Act On Event 로 스코프가 직접 저장, 측정 끝난 뒤 한 번에 가져오기 (aoe.py)
    off        캡처 안 함 (off.py). [capture] enabled = no / scope.py --no-capture 도 이것
    이름        직접 만든 방식: scopetech/capture/이름.py 또는 scope.py 옆 이름.py 의 Capture 클래스
    이름:클래스  클래스 이름이 Capture 가 아닐 때
새 방식 만드는 법은 template.py 참고.
"""
from __future__ import annotations

import importlib

from .base import Capture


def method_name(cfg) -> str:
    if not cfg.getbool("capture", "enabled"):
        return "off"
    return cfg.get("capture", "method").strip() or "aoe"


def load(bench) -> Capture:
    method = method_name(bench.cfg)
    mod_name, _, cls_name = method.partition(":")
    module = None
    for full in (f"{__name__}.{mod_name}", mod_name):
        try:
            module = importlib.import_module(full)
            break
        except ModuleNotFoundError as e:
            if e.name != full:  # 모듈 안에서 다른 import 가 실패한 경우는 그대로 알린다
                raise
    if module is None:
        raise ValueError(f"[capture] method = {method}: 캡처 모듈을 찾을 수 없습니다 "
                         f"(scopetech/capture/{mod_name}.py 또는 scope.py 옆 {mod_name}.py)")
    cls = getattr(module, cls_name or "Capture", None)
    if not (isinstance(cls, type) and issubclass(cls, Capture)):
        raise ValueError(f"[capture] method = {method}: {module.__name__} 에 Capture 를 상속한 "
                         f"{cls_name or 'Capture'} 클래스가 없습니다")
    return cls(bench)

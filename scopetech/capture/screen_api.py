"""스코프 Windows 화면 캡처 서버(esaram5-lab/scope-capture-api)로 지금 화면을 PNG 로 받는다.

TekScope 에는 화면 저장·파일 명령을 하나도 보내지 않는다(Act On Event, SAVe:IMAGe, READFile 없음).
스코프에서 scope_capture_server.py 가 돌고 있어야 하고, PC 와 스코프가 LAN 으로도 연결돼 있어야 한다
(측정 명령은 지금처럼 USB, 화면은 LAN).

준비 (config.ini [capture] method = screen_api, 설정은 [screen_api])
- 클라이언트는 scope_tech 에 동봉된 scope_capture/ 의 것을 쓴다(client_dir 로 다른 폴더 지정 가능)
- 스코프 서버가 처음 만든 capture_token.txt 를 PC 의 scope.py 옆(또는 scope_capture/)에 복사
- host 에 스코프 IP. 비우면 자동 검색(서버 --discovery, PC 에 psutil 필요)

캡처 순서 (take 1회)
1. acquire: stop(기본)=실행 중이면 Single 1회 받고 정지 / single=매번 새 Single / keep=건드리지 않음
2. *OPC? 로 앞 명령(결과 바 숨김 등) 처리 확인 후 settle_s 만큼 화면 갱신 시작 대기
3. 서버에 캡처 요청 -> PNG 확인 후 저장 (자동 재시도 없음)
   stable_ms > 0 이면 서버가 화면이 멈출 때까지(stable_frames 장 연속 동일) 기다렸다 찍는다(서버 v1.2 이상).
   TekScope 창만 찍으려면 서버를 --window TekScope 로 실행한다(연결 시 창을 찾았는지 표시).
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

from ..transport import ScopeError
from .base import Capture as Base

ROOT = Path(__file__).resolve().parents[2]  # scope.py 가 있는 폴더


def _import_client(client_dir: str):
    """scope_capture_client 모듈을 찾는다: client_dir -> 동봉된 scope_capture/ -> scope.py 옆 -> scope-capture-api 폴더."""
    candidates = [Path(client_dir)] if client_dir else []
    candidates += [ROOT / "scope_capture", ROOT, ROOT / "scope-capture-api", ROOT.parent / "scope-capture-api"]
    for d in candidates:
        d = (d if d.is_absolute() else ROOT / d).resolve()
        path = d / "scope_capture_client.py"
        if not path.exists():
            continue
        loaded = sys.modules.get("scope_capture_client")
        if loaded is not None and Path(getattr(loaded, "__file__", "")).resolve() == path:
            return loaded
        # 찾은 파일을 그대로 읽는다(다른 폴더의 같은 이름 파일이 먼저 import 되지 않도록).
        # auto_connect 가 import 하는 scope_discovery 도 같은 폴더 것을 쓰게 한다.
        disc = sys.modules.get("scope_discovery")
        if disc is not None and Path(getattr(disc, "__file__", "")).resolve().parent != d:
            del sys.modules["scope_discovery"]
        if str(d) in sys.path:
            sys.path.remove(str(d))
        sys.path.insert(0, str(d))
        spec = importlib.util.spec_from_file_location("scope_capture_client", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["scope_capture_client"] = module
        spec.loader.exec_module(module)
        return module
    raise ValueError("scope_capture_client.py 를 찾을 수 없습니다. scope.py 옆에 복사하거나 "
                     "config.ini [screen_api] client_dir 에 폴더를 적어주세요. 찾은 곳: "
                     + ", ".join(str(d) for d in candidates))


class Capture(Base):
    label = "LAN 화면 캡처 서버 (TekScope 명령 없음)"

    def __init__(self, bench):
        super().__init__(bench)
        self._client = None
        self._mod = None

    def opt(self, key: str) -> str:
        return self.cfg.get("screen_api", key).strip()

    def _errors(self) -> tuple:
        errs = [OSError, ValueError, getattr(self._mod, "CaptureError", RuntimeError)]
        disc = sys.modules.get("scope_discovery")
        if disc is not None and hasattr(disc, "DiscoveryError"):
            errs.append(disc.DiscoveryError)
        return tuple(errs)

    def client(self):
        if self._client is not None:
            return self._client
        self._mod = _import_client(self.opt("client_dir"))
        token = Path(self.opt("token_file") or "capture_token.txt")
        if not token.is_absolute():
            token = next((p for p in (ROOT / token, ROOT / "scope_capture" / token) if p.exists()), ROOT / token)
        if not token.exists():
            raise ValueError(f"캡처 서버 토큰 파일이 없습니다: {token} (스코프 서버의 capture_token.txt 를 복사)")
        timeout = float(self.opt("timeout_s") or 10)
        host = self.opt("host")
        try:
            if host:
                client = self._mod.ScopeCaptureClient(host, token_file=token, port=int(self.opt("port") or 8765),
                                                      timeout=timeout)
            else:
                client = self._mod.ScopeCaptureClient.auto_connect(
                    token_file=token, name=self.opt("name") or None, timeout=timeout,
                    discovery_timeout=float(self.opt("discovery_timeout_s") or 2))
            health = client.health()
            if health.get("status") != "ok":
                raise ValueError("캡처 서버 health 응답이 올바르지 않습니다.")
        except self._errors() as e:
            raise ScopeError(f"화면 캡처 서버 연결 실패({host or '자동 검색'}): {e}\n"
                             "  스코프에서 scope_capture_server.py 실행, LAN 연결, 방화벽, 토큰을 확인하세요.") from e
        if self.stable_ms() and "stable" not in health.get("features", []):
            raise ValueError("스코프의 scope_capture_server.py 가 화면 안정 대기(stable)를 지원하지 않습니다. "
                             "서버를 v1.2 이상으로 바꾸거나 config.ini [screen_api] stable_ms = 0")
        window = health.get("window")
        where = ""
        if window:
            where = (f", 창 '{window.get('matched')}'" + ("" if window.get("foreground") else " (맨 앞 아님)")
                     if window.get("found") else f", 창 '{window.get('title')}' 못 찾음")
        self.echo(f"  화면 캡처 서버 연결: {client.host}:{client.port}{where}")
        self._client = client
        return client

    def stable_ms(self) -> int:
        return int(self.opt("stable_ms") or 0)

    def take(self, local: Path) -> Path:
        client = self.client()
        s = self.scope
        timeout = self.cfg.getfloat("measure", "single_timeout_s")
        acquire = (self.opt("acquire") or "stop").lower()
        if acquire == "single":
            s.single(timeout)
        elif acquire == "stop" and s.is_running():
            s.freeze(timeout)
        elif acquire not in ("stop", "keep", "single"):
            raise ValueError(f"[screen_api] acquire = {acquire}: stop / single / keep 중 하나")
        s.pace()  # 결과 바 숨김 등 앞 명령이 처리된 뒤에
        time.sleep(float(self.opt("settle_s") or 0))  # 화면이 다시 그려질 시간
        try:
            if self.stable_ms():
                png = client.capture_png(stable_ms=self.stable_ms(), interval_ms=int(self.opt("stable_interval_ms") or 100),
                                         frames=int(self.opt("stable_frames") or 3))
            else:
                png = client.capture_png()
        except TypeError as e:  # stable 을 모르는 옛 scope_capture_client.py
            raise ValueError("PC 의 scope_capture_client.py 가 오래돼 stable_ms 를 모릅니다. "
                             "scope-capture-api v1.2 의 파일로 바꾸거나 stable_ms = 0") from e
        except self._errors() as e:
            hint = ("\n  화면이 계속 바뀝니다. 획득이 실행 중인지([screen_api] acquire) 확인하세요."
                    if type(e).__name__ == "CaptureUnstable" else "")
            raise ScopeError(f"화면 캡처 서버 캡처 실패: {e}{hint}") from e
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(png)
        return local

"""스코프 Windows 화면 캡처 서버(esaram5-lab/scope-capture-api)로 지금 화면을 PNG 로 받는다.

TekScope 에는 화면 저장·파일 명령을 하나도 보내지 않는다(Act On Event, SAVe:IMAGe, READFile 없음).
스코프에서 scope_capture_server.py 가 돌고 있어야 하고, PC 와 스코프가 LAN 으로도 연결돼 있어야 한다
(측정 명령은 지금처럼 USB, 화면은 LAN).

준비 (config.ini [capture] method = screen_api, 설정은 [screen_api])
- scope_capture_client.py (자동 검색을 쓰면 scope_discovery.py 도) 를 scope.py 옆에 복사하거나 client_dir 지정
- 스코프 서버가 처음 만든 capture_token.txt 를 PC 로 복사해 token_file 에 지정
- host 에 스코프 IP. 비우면 자동 검색(서버 --discovery, PC 에 psutil 필요)

캡처 순서 (take 1회)
1. acquire: stop(기본)=실행 중이면 Single 1회 받고 정지 / single=매번 새 Single / keep=건드리지 않음
2. *OPC? 로 앞 명령(결과 바 숨김 등) 처리 확인 후 settle_s 만큼 화면 갱신 대기
3. 서버에 캡처 요청 -> PNG 확인 후 저장 (자동 재시도 없음)
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

from ..transport import ScopeError
from .base import Capture as Base

ROOT = Path(__file__).resolve().parents[2]  # scope.py 가 있는 폴더


def _import_client(client_dir: str):
    """scope_capture_client 모듈을 찾는다: client_dir -> scope.py 옆 -> scope-capture-api 폴더."""
    candidates = [Path(client_dir)] if client_dir else []
    candidates += [ROOT, ROOT / "scope-capture-api", ROOT.parent / "scope-capture-api"]
    for d in candidates:
        d = d if d.is_absolute() else ROOT / d
        if (d / "scope_capture_client.py").exists():
            if str(d) not in sys.path:
                sys.path.append(str(d))  # auto_connect 가 같은 폴더의 scope_discovery 를 import 한다
            return importlib.import_module("scope_capture_client")
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
        token = token if token.is_absolute() else ROOT / token
        if not token.exists():
            raise ValueError(f"캡처 서버 토큰 파일이 없습니다: {token} (스코프 서버의 capture_token.txt 를 복사)")
        timeout = float(self.opt("timeout_s") or 10)
        host = self.opt("host")
        try:
            if host:
                client = self._mod.ScopeCaptureClient(host, token_file=token, port=int(self.opt("port") or 8765),
                                                      timeout=timeout)
                if client.health().get("status") != "ok":
                    raise ValueError("캡처 서버 health 응답이 올바르지 않습니다.")
            else:
                client = self._mod.ScopeCaptureClient.auto_connect(
                    token_file=token, name=self.opt("name") or None, timeout=timeout,
                    discovery_timeout=float(self.opt("discovery_timeout_s") or 2))
        except self._errors() as e:
            raise ScopeError(f"화면 캡처 서버 연결 실패({host or '자동 검색'}): {e}\n"
                             "  스코프에서 scope_capture_server.py 실행, LAN 연결, 방화벽, 토큰을 확인하세요.") from e
        self.echo(f"  화면 캡처 서버 연결: {client.host}:{client.port}")
        self._client = client
        return client

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
            png = client.capture_png()
        except self._errors() as e:
            raise ScopeError(f"화면 캡처 서버 캡처 실패: {e}") from e
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(png)
        return local

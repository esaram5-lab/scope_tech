"""스코프 통신 계층.

- SocketTransport : LAN 소켓 서버(기본 포트 4000). 추가 설치 불필요.
- VisaTransport   : USB(USBTMC). TekVISA 또는 NI-VISA 의 visa64.dll 을 ctypes 로 직접 호출
                    (pyvisa 불필요, 단 VISA 드라이버 설치는 필요).
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import socket
import sys
import time
from typing import Callable

Logger = Callable[[str, str], None]


class ScopeError(Exception):
    """스코프 통신/명령 오류."""


class ScopeTimeout(ScopeError):
    """응답 시간 초과."""


class Transport:
    description = ""

    def __init__(self, timeout: float = 10.0, logger: Logger | None = None):
        self.timeout = timeout
        self.logger = logger

    def _log(self, direction: str, text: str) -> None:
        if self.logger:
            self.logger(direction, text)

    # --- 하위 클래스 구현 ---
    def write(self, cmd: str) -> None:
        raise NotImplementedError

    def read_line(self) -> str:
        raise NotImplementedError

    def read_block(self) -> bytes:
        """IEEE 488.2 definite-length block (#<n><len><data>)."""
        raise NotImplementedError

    def read_file(self, size: int | None) -> bytes:
        """FILESystem:READFile 뒤에 오는 원시 파일 데이터."""
        raise NotImplementedError

    def recover(self) -> None:
        """시간 초과 등으로 어긋난 송수신 상태를 정리한다."""

    def close(self) -> None:
        pass

    def _apply_timeout(self, seconds: float) -> None:
        pass

    # --- 공통 ---
    def query(self, cmd: str) -> str:
        self.write(cmd)
        resp = self.read_line()
        self._log("<", resp)
        return resp

    def sync(self) -> None:
        if self.query("*OPC?") != "1":
            raise ScopeError("*OPC? 동기화 실패")

    @contextlib.contextmanager
    def temp_timeout(self, seconds: float):
        old = self.timeout
        if seconds > old:
            self.timeout = seconds
            self._apply_timeout(seconds)
        try:
            yield
        finally:
            if self.timeout != old:
                self.timeout = old
                self._apply_timeout(old)


def parse_block_header(data: bytes) -> tuple[int, int]:
    """(데이터 시작 위치, 길이) 반환."""
    start = 0
    while start < len(data) and data[start : start + 1] in (b" ", b"\r", b"\n"):
        start += 1
    if data[start : start + 1] != b"#":
        raise ScopeError(f"바이너리 블록 헤더 오류: {data[start:start + 16]!r}")
    nd = int(data[start + 1 : start + 2])
    if nd == 0:
        raise ScopeError("길이 미지정(#0) 블록은 지원하지 않습니다.")
    length = int(data[start + 2 : start + 2 + nd])
    return start + 2 + nd, length


class StreamTransport(Transport):
    """바이트 스트림(소켓, 시뮬레이터) 공통 파서."""

    def __init__(self, timeout: float = 10.0, logger: Logger | None = None):
        super().__init__(timeout, logger)
        self._buf = bytearray()

    def _recv(self) -> bytes:
        raise NotImplementedError

    def _send(self, data: bytes) -> None:
        raise NotImplementedError

    def write(self, cmd: str) -> None:
        self._log(">", cmd)
        self._send(cmd.encode("latin-1") + b"\n")

    def _fill(self) -> None:
        data = self._recv()
        if not data:
            raise ScopeError("스코프와의 연결이 끊어졌습니다.")
        self._buf += data

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            self._fill()
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def read_line(self) -> str:
        while True:
            i = self._buf.find(b"\n")
            if i >= 0:
                break
            self._fill()
        line = bytes(self._buf[:i])
        del self._buf[: i + 1]
        return line.decode("latin-1").strip()

    def read_block(self) -> bytes:
        while True:
            if not self._buf:
                self._fill()
            if self._buf[:1] in (b" ", b"\r", b"\n"):
                del self._buf[0]
                continue
            break
        head = self._read_exact(2)
        if head[:1] != b"#":
            raise ScopeError(f"바이너리 블록 헤더 오류: {head!r}")
        nd = int(head[1:2])
        if nd == 0:
            raise ScopeError("길이 미지정(#0) 블록은 지원하지 않습니다.")
        length = int(self._read_exact(nd))
        data = self._read_exact(length)
        self._log("<", f"<binary {length} bytes>")
        self._drop_terminator()
        return data

    def _drop_terminator(self) -> None:
        if not self._buf:
            old = self.timeout
            self._apply_timeout(0.2)
            try:
                self._fill()
            except ScopeTimeout:
                return
            finally:
                self._apply_timeout(old)
        if self._buf[:1] == b"\n":
            del self._buf[0]

    def read_file(self, size: int | None) -> bytes:
        if size is not None:
            data = self._read_exact(size)
        else:
            data = self._read_until_idle(1.0)
        self._log("<", f"<file {len(data)} bytes>")
        return data

    def _read_until_idle(self, idle: float) -> bytes:
        out = bytearray(self._buf)
        self._buf.clear()
        old = self.timeout
        self._apply_timeout(idle)
        try:
            while True:
                try:
                    data = self._recv()
                except ScopeTimeout:
                    break
                if not data:
                    break
                out += data
        finally:
            self._apply_timeout(old)
        return bytes(out)

    def sync(self) -> None:
        # 파일 전송 뒤 남은 종료문자(빈 줄)는 건너뛴다
        self.write("*OPC?")
        for _ in range(100):
            line = self.read_line()
            if line == "1":
                self._log("<", line)
                return
        raise ScopeError("*OPC? 동기화 실패")

    def recover(self) -> None:
        self._read_until_idle(0.5)


class SocketTransport(StreamTransport):
    def __init__(self, host: str, port: int = 4000, timeout: float = 10.0, logger: Logger | None = None):
        super().__init__(timeout, logger)
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            raise ScopeError(
                f"LAN 연결 실패 ({host}:{port}): {e}\n"
                "  - 스코프 Utility > I/O > Socket Server 가 On 인지, 포트 번호가 맞는지 확인\n"
                "  - PC 와 스코프 IP 가 같은 대역인지 확인 (예: PC 192.168.0.100 / 스코프 192.168.0.10)"
            ) from e
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.description = f"LAN {host}:{port}"
        banner = self._read_until_idle(0.3)
        if banner.strip():
            raise ScopeError(
                "스코프 소켓 서버가 Terminal 프로토콜로 설정되어 있습니다. "
                "Utility > I/O > Socket Server 에서 Protocol 을 None 으로 바꾸세요."
            )

    def _recv(self) -> bytes:
        try:
            return self.sock.recv(1 << 20)
        except TimeoutError as e:
            raise ScopeTimeout("스코프 응답 시간 초과") from e

    def _send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def _apply_timeout(self, seconds: float) -> None:
        self.sock.settimeout(seconds)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


# --------------------------------------------------------------------------- VISA (USB)
VI_SUCCESS_MAX_CNT = 0x3FFF0006
VI_ATTR_TMO_VALUE = 0x3FFF001A
VI_ERROR_TMO = -1073807339  # 0xBFFF0015
VI_ERROR_RSRC_NFOUND = -1073807343  # 0xBFFF0011
TEK_VENDOR_ID = "0x0699"


def _load_visa_library(path: str = ""):
    is64 = ctypes.sizeof(ctypes.c_void_p) == 8
    if path:
        candidates = [path]
    elif os.name == "nt":
        candidates = ["visa64.dll", "visa32.dll"] if is64 else ["visa32.dll"]
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        candidates += [os.path.join(sysroot, "System32", c) for c in list(candidates)]
    elif sys.platform == "darwin":
        candidates = ["/Library/Frameworks/VISA.framework/VISA"]
    else:
        candidates = ["libvisa.so", "libvisa.so.0"]
    loader = getattr(ctypes, "WinDLL", ctypes.CDLL) if os.name == "nt" else ctypes.CDLL
    for c in candidates:
        try:
            return loader(c)
        except OSError:
            continue
    raise ScopeError(
        "VISA 라이브러리(visa64.dll)를 찾을 수 없습니다.\n"
        "  USB 로 연결하려면 TekVISA 또는 NI-VISA 를 설치해야 합니다(인터넷 PC 에서 설치 파일을 받아 복사).\n"
        "  설치 없이 쓰려면 LAN 케이블로 연결하고 config.ini 에서 interface = lan 으로 바꾸세요."
    )


class VisaTransport(Transport):
    def __init__(self, resource: str = "", library: str = "", timeout: float = 10.0, logger: Logger | None = None,
                 progress=None, clear: bool = True):
        super().__init__(timeout, logger)
        step = progress or (lambda text: None)  # 멈춘 단계를 알 수 있도록 각 VISA 호출 전에 알림
        step("VISA 라이브러리 불러오기")
        self.lib = lib = _load_visa_library(library)
        u32, i32 = ctypes.c_uint32, ctypes.c_int32
        attr = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32
        pu32 = ctypes.POINTER(u32)
        protos = {
            "viOpenDefaultRM": [pu32],
            "viFindRsrc": [u32, ctypes.c_char_p, pu32, pu32, ctypes.c_char_p],
            "viFindNext": [u32, ctypes.c_char_p],
            "viOpen": [u32, ctypes.c_char_p, u32, u32, pu32],
            "viClose": [u32],
            "viClear": [u32],
            "viSetAttribute": [u32, u32, attr],
            "viWrite": [u32, ctypes.c_char_p, u32, pu32],
            "viRead": [u32, ctypes.POINTER(ctypes.c_char), u32, pu32],
            "viStatusDesc": [u32, i32, ctypes.c_char_p],
        }
        for name, args in protos.items():
            fn = getattr(lib, name)
            fn.argtypes = args
            fn.restype = i32
        self.rm = u32()
        step("VISA 시작")
        self._check(lib.viOpenDefaultRM(ctypes.byref(self.rm)), "viOpenDefaultRM")
        if not resource:
            step("USB 장치 찾기")
            resource = self._find_resource()
        self.vi = u32()
        step(f"장치 열기 {resource}")
        self._check(lib.viOpen(self.rm, resource.encode(), 0, 0, ctypes.byref(self.vi)), f"viOpen({resource})")
        self.description = f"USB {resource}"
        self._readbuf = ctypes.create_string_buffer(1 << 20)
        # 이전 실행이 강제 종료되며 스코프에 남긴 USB 전송/읽지 않은 응답을 버린다(USBTMC Device Clear)
        if clear:
            self._apply_timeout(5)
            step("USB Device Clear")
            st = lib.viClear(self.vi)
            self._log(">", "(USB Device Clear) " + ("OK" if st >= 0 else f"실패: {self._status_text(st)}"))
            time.sleep(0.2)
        self._apply_timeout(timeout)

    def _status_text(self, status: int) -> str:
        buf = ctypes.create_string_buffer(256)
        self.lib.viStatusDesc(self.rm, status, buf)
        return buf.value.decode("latin-1", "replace")

    def _check(self, status: int, what: str) -> int:
        if status == VI_ERROR_TMO:
            raise ScopeTimeout(f"{what}: 스코프 응답 시간 초과")
        if status < 0:
            raise ScopeError(f"{what} 실패: {self._status_text(status)} ({status & 0xFFFFFFFF:#010x})")
        return status

    def _find_resource(self) -> str:
        flist, count = ctypes.c_uint32(), ctypes.c_uint32()
        desc = ctypes.create_string_buffer(256)
        st = self.lib.viFindRsrc(self.rm, b"USB?*INSTR", ctypes.byref(flist), ctypes.byref(count), desc)
        found = []
        if st >= 0:
            found.append(desc.value.decode())
            for _ in range(count.value - 1):
                if self.lib.viFindNext(flist, desc) >= 0:
                    found.append(desc.value.decode())
            self.lib.viClose(flist)
        tek = [r for r in found if TEK_VENDOR_ID in r.lower()]
        if not tek:
            raise ScopeError(
                "USB 에서 Tektronix 스코프를 찾지 못했습니다"
                + (f" (발견된 장치: {', '.join(found)})" if found else "")
                + ".\n  - 스코프 뒷면 USB Device 포트(USB 3.0 B 타입)에 연결했는지 확인\n"
                "  - Utility > I/O > USB Device Port 가 켜져 있는지 확인"
            )
        if len(tek) == 1:
            return tek[0]
        # Tek 함수발생기 등 다른 Tek 장비가 같이 연결된 경우 *IDN? 으로 스코프를 고른다
        idns = []
        for r in tek:
            idn = self._probe_idn(r)
            idns.append(f"{r} = {idn or '응답 없음'}")
            if idn and idn.split(",")[1:2] and idn.split(",")[1].strip().upper().startswith(("MSO", "DPO", "LPD")):
                return r
        raise ScopeError("USB 에 Tektronix 장비가 여러 대인데 스코프를 고르지 못했습니다:\n  " + "\n  ".join(idns)
                         + "\n  config.ini 의 usb_resource 에 스코프 주소를 적어주세요.")

    def _probe_idn(self, resource: str) -> str:
        vi = ctypes.c_uint32()
        if self.lib.viOpen(self.rm, resource.encode(), 0, 0, ctypes.byref(vi)) < 0:
            return ""
        try:
            self.lib.viSetAttribute(vi, VI_ATTR_TMO_VALUE, 2000)
            ret = ctypes.c_uint32()
            if self.lib.viWrite(vi, b"*IDN?\n", 6, ctypes.byref(ret)) < 0:
                return ""
            buf = ctypes.create_string_buffer(512)
            if self.lib.viRead(vi, buf, len(buf), ctypes.byref(ret)) < 0:
                return ""
            return ctypes.string_at(buf, ret.value).decode("latin-1").strip()
        finally:
            self.lib.viClose(vi)

    def _apply_timeout(self, seconds: float) -> None:
        self.lib.viSetAttribute(self.vi, VI_ATTR_TMO_VALUE, int(seconds * 1000))

    def write(self, cmd: str) -> None:
        self._log(">", cmd)
        data = cmd.encode("latin-1") + b"\n"
        ret = ctypes.c_uint32()
        self._check(self.lib.viWrite(self.vi, data, len(data), ctypes.byref(ret)), f"쓰기({cmd})")

    def _read_message(self) -> bytes:
        chunks = []
        ret = ctypes.c_uint32()
        buf = self._readbuf
        while True:
            st = self.lib.viRead(self.vi, buf, len(buf), ctypes.byref(ret))
            self._check(st, "읽기")
            chunks.append(ctypes.string_at(buf, ret.value))
            if st != VI_SUCCESS_MAX_CNT:
                return b"".join(chunks)

    def read_line(self) -> str:
        return self._read_message().decode("latin-1").strip()

    def read_block(self) -> bytes:
        msg = self._read_message()
        start, length = parse_block_header(msg)
        while len(msg) < start + length:  # 드물게 여러 메시지로 나뉘는 경우
            msg += self._read_message()
        self._log("<", f"<binary {length} bytes>")
        return msg[start : start + length]

    def read_file(self, size: int | None) -> bytes:
        data = self._read_message()
        if size is not None:
            while len(data) < size:
                data += self._read_message()
            data = data[:size]
        self._log("<", f"<file {len(data)} bytes>")
        return data

    def recover(self) -> None:
        self.lib.viClear(self.vi)
        time.sleep(0.2)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.lib.viClose(self.vi)
            self.lib.viClose(self.rm)

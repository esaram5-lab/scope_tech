"""스코프 없이 흐름을 확인하기 위한 MSO58B 시뮬레이터 (python scope.py --sim).

CH1: 0~3.3 V 삼각파(100 Hz) / CH2: 슈미트 인버터 출력(VT+ 1.90 V, VT- 1.40 V)
CH3: 1 V 비교기 OD 출력(1.8 V 풀업) / CH4~CH7: 기타 신호
알 수 없는 명령은 실제 스코프처럼 오류 이벤트를 남긴다(명령 오타 확인용).
"""
from __future__ import annotations

import math
import random
import re
import struct
import zlib
from array import array

from .analysis import Waveform, levels
from .transport import ScopeTimeout, StreamTransport

VT_PLUS, VT_MINUS, CMP_REF = 1.90, 1.40, 1.00


def _png(rgb: tuple[int, int, int], size: int = 16) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    row = b"\x00" + bytes(rgb) * size
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )


class SimScope:
    def __init__(self, reclen: int = 20000, seed: int = 1):
        self.reclen = reclen
        self.rng = random.Random(seed)
        self.xincr = 30e-3 / reclen  # 3주기(30 ms)
        self.xzero = -15e-3
        self.ymult, self.yoff = 1e-4, 0.0
        self.yzero = 1.65
        self.on = {ch: ch <= 7 for ch in range(1, 9)}
        self.acq_state, self.stopafter, self.acq_mode, self.numacq = 1, "RUNSTOP", "SAMPLE", 0
        self.data = {"SOURCE": "CH1", "ENCDG": "SRIBINARY", "START": 1, "STOP": reclen, "BYT_NR": 2}
        self.meas: dict[str, dict] = {}
        self.cursor = {"STATE": "0"}
        self.files: dict[str, bytes] = {}
        self.dirs = {"C:"}
        self.cwd = "C:/"
        self.composition = "NORMAL"
        self.rrb = True
        self.aoe = {"ENABLE": "0", "SAVEIMAGE": "OFF", "FILEDEST": "C:/", "FILENAME": "img"}
        self.esr = 0
        self.events: list[str] = []
        self.log: list[str] = []
        self.error_log: list[str] = []
        self._make_waves()

    # ------------------------------------------------------------------ 파형
    def _make_waves(self) -> None:
        n, rng = self.reclen, self.rng
        period = 10e-3
        t = [(i * self.xincr) + self.xzero for i in range(n)]
        vin = [3.3 * (1 - abs(((ti / period + 0.25) % 1) * 2 - 1)) + rng.gauss(0, 0.002) for ti in t]
        inv, cmp_, ck = [], [], []
        state = vin[0] > VT_PLUS
        y2, y3 = (0.04 if state else 3.28), (1.80 if vin[0] > CMP_REF else 0.12)
        for i, v in enumerate(vin):
            if not state and v >= VT_PLUS:
                state = True
            elif state and v <= VT_MINUS:
                state = False
            y2 += ((0.04 if state else 3.28) - y2) / 3  # 빠른 에지
            target3 = 1.80 if v > CMP_REF else 0.12
            y3 += (target3 - y3) / (8 if target3 > y3 else 2)  # OD: 풀업으로 느린 상승
            inv.append(y2 + rng.gauss(0, 0.003))
            cmp_.append(y3 + rng.gauss(0, 0.002))
            ck.append((3.3 if (t[i] // 0.5e-3) % 2 else 0.0) + rng.gauss(0, 0.003))
        self.volts = {
            1: vin,
            2: inv,
            3: cmp_,
            4: ck,
            5: [1.8 + rng.gauss(0, 0.002) for _ in t],
            6: [rng.gauss(0, 0.002) for _ in t],
            7: [3.3 + rng.gauss(0, 0.002) for _ in t],
            8: [0.0] * n,
        }

    def _screen_png(self) -> bytes:
        overlay = int(self.cursor.get("STATE", "0") == "1") + (len(self.meas) if self.rrb else 0)
        return _png((min(255, 40 * overlay), 128, 255 if self.composition.startswith("INV") else 0))

    def _codes(self, ch: int) -> array:
        return array("h", (max(-32768, min(32767, round((v - self.yzero) / self.ymult))) for v in self.volts[ch]))

    def _wave(self, ch: int) -> Waveform:
        return Waveform(f"CH{ch}", self.volts[ch], self.xincr, self.xzero)

    # ------------------------------------------------------------------ 오류
    def _error(self, code: int, text: str) -> None:
        self.esr |= 0x20 if code < 200 else 0x10
        self.events.append(f'{-abs(code)},"{text}"')
        self.error_log.append(text)

    # ------------------------------------------------------------------ 명령 처리
    def handle(self, line: str) -> bytes | None:
        self.log.append(line)
        m = re.match(r"^\s*(\S+)\s*(.*)$", line)
        if not m:
            return None
        head, arg = m.group(1).upper(), m.group(2).strip()
        q = head.endswith("?")
        h = head.rstrip("?")

        def resp(v) -> bytes:
            return f"{v}\n".encode()

        if h == "*IDN":
            return resp("TEKTRONIX,MSO58B,SIM0001,CF:91.1CT FV:2.4.6")
        if h == "*CLS":
            self.esr, self.events = 0, []
            return None
        if h == "*ESR":
            v, self.esr = self.esr, 0
            return resp(v)
        if h == "ALLEV":
            v = ",".join(self.events) or '0,"No events to report - queue empty"'
            self.events = []
            return resp(v)
        if h == "*OPC":
            return resp(1) if q else None
        if q and h in ("*OPT", "HORIZONTAL:MODE", "TRIGGER:A:MODE", "ACQUIRE:STOPAFTER"):
            return resp({"*OPT": "NONE", "HORIZONTAL:MODE": "AUTO", "TRIGGER:A:MODE": "AUTO",
                         "ACQUIRE:STOPAFTER": self.stopafter}[h])
        aoe_keys = {"ACTONEVENT:ENABLE": "ENABLE", "ACTONEVENT:TRIGGER:ACTION:SAVEIMAGE:STATE": "SAVEIMAGE",
                    "SAVEONEVENT:FILEDEST": "FILEDEST", "SAVEONEVENT:FILENAME": "FILENAME"}
        if h in aoe_keys and not q:
            self.aoe[aoe_keys[h]] = arg.upper() if aoe_keys[h] in ("ENABLE", "SAVEIMAGE") else arg
            return None
        if h in ("ACTONEVENT:LIMIT", "SAVEONEVENT:IMAGE:FILEFORMAT") and not q:
            return None
        if h == "MAINWINDOW:RRBDISPLAYSTATE" and not q:
            self.rrb = arg.upper() in ("ON", "1")
            return None
        if h == "BUSY" and q:
            return resp(0)
        if h in ("HEADER", "VERBOSE"):
            return None
        if h == "CLEAR":
            self.numacq = 0
            return None

        if h.startswith("ACQUIRE:"):
            return self._acquire(h[8:], q, arg)
        if h == "HORIZONTAL:RECORDLENGTH" and q:
            return resp(self.reclen)
        mm = re.fullmatch(r"DISPLAY:GLOBAL:CH(\d):STATE", h)
        if mm and q:
            return resp(int(self.on[int(mm.group(1))]))
        if h.startswith("DATA:"):
            key = h[5:]
            if key in self.data and not q:
                self.data[key] = int(arg) if key in ("START", "STOP") else arg.upper()
                return None
        if h.startswith("WFMOUTPRE:"):
            return self._wfmoutpre(h[10:], q, arg)
        if h == "CURVE" and q:
            ch = int(self.data["SOURCE"].removeprefix("CH"))
            if not self.on[ch]:
                self._error(200, "Execution error; source off")
                return b"#10\n"
            body = self._codes(ch).tobytes()
            ln = str(len(body))
            return f"#{len(ln)}{ln}".encode() + body + b"\n"
        if h.startswith("MEASUREMENT:"):
            return self._measurement(h[12:], q, arg)
        if h.startswith("DISPLAY:WAVEVIEW1:CURSOR:CURSOR1:"):
            key = h[33:]
            allowed = {"STATE", "FUNCTION", "SPLITMODE", "ASOURCE", "BSOURCE", "MODE",
                       "WAVEFORM:APOSITION", "WAVEFORM:BPOSITION", "HBARS:APOSITION", "HBARS:BPOSITION"}
            if key in allowed:
                if q:
                    return resp(self.cursor.get(key, "0"))
                self.cursor[key] = {"ON": "1", "OFF": "0"}.get(arg.upper(), arg.upper())
                return None
        if h.startswith("SAVE:") or h.startswith("FILESYSTEM:"):
            return self._files(h, q, arg)
        self._error(113, f"Undefined header; Command not found; {line}")
        return None

    def _acquire(self, key: str, q: bool, arg: str) -> bytes | None:
        if key == "STATE":
            if q:
                return f"{self.acq_state}\n".encode()
            a = arg.upper()
            if a in ("STOP", "OFF", "0"):
                self.acq_state = 0
            elif self.stopafter == "SEQUENCE":
                self.acq_state = 0  # 즉시 트리거된 것으로 처리
                self.numacq += 1
                self._make_waves()
                if self.aoe["ENABLE"] == "1" and self.aoe["SAVEIMAGE"] in ("ON", "1"):
                    dest = self.aoe["FILEDEST"].strip('"').rstrip("/")
                    name = self.aoe["FILENAME"].strip('"') + f"_{self.numacq:017d}.png"
                    self.files[f"{dest}/{name}".upper()] = self._screen_png()
            else:
                self.acq_state = 1
                self.numacq = 0
            return None
        if key == "STOPAFTER" and not q:
            self.stopafter = "SEQUENCE" if arg.upper().startswith("SEQ") else "RUNSTOP"
            return None
        if key == "MODE":
            if q:
                return f"{self.acq_mode}\n".encode()
            self.acq_mode = {"HIRES": "HIRES", "SAMPLE": "SAMPLE"}.get(arg.upper(), arg.upper())
            return None
        if key == "NUMACQ" and q:
            if self.acq_state:
                self.numacq += 25
            return f"{self.numacq}\n".encode()
        self._error(113, f"Undefined header; ACQuire:{key}")
        return None

    def _wfmoutpre(self, key: str, q: bool, arg: str) -> bytes | None:
        if key == "BYT_NR" and not q:
            self.data["BYT_NR"] = int(arg)
            return None
        vals = {"NR_PT": self.reclen, "XINCR": f"{self.xincr:.6E}", "XZERO": f"{self.xzero:.6E}",
                "PT_OFF": 0, "YMULT": f"{self.ymult:.6E}", "YOFF": f"{self.yoff:.6E}", "YZERO": f"{self.yzero:.6E}"}
        if q and key in vals:
            return f"{vals[key]}\n".encode()
        self._error(113, f"Undefined header; WFMOutpre:{key}")
        return None

    def _measurement(self, key: str, q: bool, arg: str) -> bytes | None:
        name = arg.strip('"').upper()
        if key == "ADDNEW" and not q:
            self.meas[name] = {"TYPE": "UNDEFINED", "SOURCE1": "CH1", "STAT": "0"}
            return None
        if key == "DELETE" and not q:
            if self.meas.pop(name, None) is None:
                self._error(200, "Execution error; no such measurement")
            return None
        if key == "DELETEALL" and not q:
            self.meas.clear()
            return None
        if key == "LIST" and q:
            return (",".join(sorted(self.meas, key=lambda s: int(s[4:]))) or "NONE").encode() + b"\n"
        mm = re.fullmatch(r"(MEAS\d+):(.+)", key)
        if mm and mm.group(1) in self.meas:  # 동적 인터페이스: 측정이 있어야 명령이 존재
            m, sub = self.meas[mm.group(1)], mm.group(2)
            if sub in ("TYPE", "SOURCE1") and not q:
                m[sub] = arg.upper()
                return None
            if sub == "DISPLAYSTAT:ENABLE" and not q:
                m["STAT"] = arg
                return None
            mr = re.fullmatch(r"RESULTS:(ALLACQS|CURRENTACQ):(MEAN|MINIMUM|MAXIMUM|POPULATION)", sub)
            if mr and q:
                return f"{self._result(m, mr.group(1), mr.group(2)):.6E}\n".encode()
        self._error(113, f"Undefined header; MEASUrement:{key}")
        return None

    def _result(self, m: dict, scope: str, stat: str) -> float:
        wf = self._wave(int(m["SOURCE1"].removeprefix("CH")))
        lv = levels(wf)
        value = {"TOP": lv.top, "BASE": lv.base, "MAXIMUM": lv.vmax, "MINIMUM": lv.vmin}.get(m["TYPE"], math.nan)
        if stat == "POPULATION":
            return float(max(self.numacq, 1))
        spread = 0.004 if scope == "ALLACQS" else 0.0
        return value + {"MEAN": 0.0, "MINIMUM": -spread, "MAXIMUM": spread}[stat]

    def _files(self, h: str, q: bool, arg: str) -> bytes | None:
        def path_of(s: str) -> str:
            s = s.strip().strip('"')
            return s if re.match(r"^[A-Za-z]:", s) else self.cwd.rstrip("/") + "/" + s

        def parent(p: str) -> str:
            return p.rsplit("/", 1)[0]

        if h == "SAVE:IMAGE:COMPOSITION" and not q:
            self.composition = arg.upper()
            return None
        if h in ("SAVE:IMAGE", "SAVE:SETUP", "SAVE:WAVEFORM") and not q:
            if h == "SAVE:WAVEFORM":
                src, _, target = arg.partition(",")
                p = path_of(target)
                data = b"WFM#" + src.encode() + b"\x00" * 1500
            elif h == "SAVE:IMAGE":
                p = path_of(arg)
                data = self._screen_png()
            else:
                p = path_of(arg)
                data = b":HEADER 1;:ACQUIRE:MODE " + self.acq_mode.encode() + b"\n"
            if parent(p).upper() not in self.dirs:
                self._error(200, f"Execution error; directory not found {parent(p)}")
                return None
            self.files[p.upper()] = data
            return None
        if h == "FILESYSTEM:MKDIR" and not q:
            p = path_of(arg).upper().rstrip("/")
            if p in self.dirs:
                self._error(200, "Execution error; directory exists")
            elif parent(p) not in self.dirs:
                self._error(200, "Execution error; parent directory not found")
            else:
                self.dirs.add(p)
            return None
        if h == "FILESYSTEM:DIR" and q:
            d = self.cwd.rstrip("/").upper()
            names = [k.rsplit("/", 1)[1].lower() for k in self.files if parent(k) == d]
            return (",".join(f'"{n}"' for n in names) or '""').encode() + b"\n"
        if h == "FILESYSTEM:HOMEDIR" and q:
            return b'"C:/Users/Public"\n'
        if h == "FILESYSTEM:CWD":
            if q:
                return f'"{self.cwd}"\n'.encode()
            p = path_of(arg)
            if p.upper().rstrip("/") not in self.dirs:
                self._error(200, "Execution error; no such directory")
                return None
            self.cwd = p
            return None
        if h == "FILESYSTEM:LDIR" and q:
            d = self.cwd.rstrip("/").upper()
            items = [f'"{k.rsplit("/", 1)[1]};FILE;{len(v)};2026-10-17;12:00:00"'
                     for k, v in self.files.items() if parent(k) == d]
            return (",".join(items) or '""').encode() + b"\n"
        if h == "FILESYSTEM:READFILE" and not q:
            data = self.files.get(path_of(arg).upper())
            if data is None:
                self._error(200, "Execution error; file not found")
                return None
            return data + b"\n"  # 실제 장비의 종료문자 유무가 불확실하므로 붙여서 견고성 확인
        if h == "FILESYSTEM:DELETE" and not q:
            if self.files.pop(path_of(arg).upper(), None) is None:
                self._error(200, "Execution error; file not found")
            return None
        self._error(113, f"Undefined header; {h}")
        return None


class SimTransport(StreamTransport):
    description = "SIMULATOR"

    def __init__(self, scope: SimScope | None = None, timeout: float = 10.0, logger=None):
        super().__init__(timeout, logger)
        self.sim = scope or SimScope()
        self._out = bytearray()

    def _send(self, data: bytes) -> None:
        for line in data.decode("latin-1").splitlines():
            r = self.sim.handle(line)
            if r:
                self._out += r

    def _recv(self) -> bytes:
        if not self._out:
            raise ScopeTimeout("시뮬레이터 응답 없음")
        data = bytes(self._out[:65536])
        del self._out[:65536]
        return data

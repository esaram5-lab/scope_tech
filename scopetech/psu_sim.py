"""N6705C 시뮬레이터 (시험·--sim 용). 모듈: 1 N6763A, 2 N6781A, 3 N6791A. 모르는 명령은 오류 대기열에 -113."""
from __future__ import annotations

import re

from .transport import ScopeTimeout, StreamTransport

ARB_KEYS = ("ARB:FUNC:TYPE", "ARB:FUNC:SHAP", "ARB:COUN", "ARB:TERM:LAST")
ARB_PREFIX = ("ARB:VOLT:TRAP:", "ARB:VOLT:RAMP:", "ARB:VOLT:STA:", "ARB:VOLT:UDEF:")
CHANNEL_KEYS = ("OUTP", "VOLT", "CURR", "VOLT:MODE", "EMUL", "SENS:CURR:RANG:AUTO", "CURR:LIM", "CURR:LIM:NEG", "FUNC",
                "INIT:TRAN", "TRIG:TRAN", "ABOR:TRAN", "OUTP:PROT:CLE")


class PsuSim:
    def __init__(self, modules=("N6763A", "N6781A", "N6791A", "")):
        self.modules = list(modules)
        self.log: list[str] = []
        self.errors: list[str] = []
        self.ch: dict[int, dict] = {i: {"OUTP": "0", "VOLT": "0", "CURR": "0.1", "VOLT:MODE": "FIX"}
                                    for i in range(1, len(self.modules) + 1)}
        self.no_arb: set[int] = {i + 1 for i, m in enumerate(self.modules) if m.upper().startswith("N679")}
        self.arb_running: set[int] = set()
        self.ques: dict[int, int] = {}
        self.trig_source = "IMM"

    def _err(self, text: str) -> None:
        self.errors.append(f'-113,"Undefined header; {text}"')

    @staticmethod
    def _chans(text: str) -> list[int]:
        m = re.search(r"\(@([\d,:]+)\)", text)
        if not m:
            return []
        out = []
        for part in m.group(1).split(","):
            if ":" in part:
                a, b = part.split(":")
                out += list(range(int(a), int(b) + 1))
            elif part:
                out.append(int(part))
        return out

    def handle(self, line: str) -> bytes | None:
        self.log.append(line)
        m = re.match(r"^\s*(\S+)\s*(.*)$", line)
        if not m:
            return None
        head, arg = m.group(1).upper(), m.group(2).strip()
        q = head.endswith("?")
        h = head.rstrip("?")
        chans = self._chans(arg)
        value = re.sub(r",?\s*\(@[\d,:]+\)", "", arg).strip()

        def resp(v) -> bytes:
            return f"{v}\n".encode()

        if h == "*IDN":
            return resp("Keysight Technologies,N6705C,SIM0001,D.01.00")
        if h == "*OPT":
            return resp("054")
        if h == "*CLS":
            self.errors = []
            return None
        if h == "SYST:ERR":
            return resp(self.errors.pop(0) if self.errors else '+0,"No error"')
        if h == "SYST:CHAN:COUN" and q:
            return resp(len(self.modules))
        if h == "SYST:CHAN:MOD" and q:
            return resp(",".join(self.modules[c - 1] for c in chans))
        if h == "SYST:CHAN:OPT" and q:
            return resp('""')
        if h == "TRIG:ARB:SOUR":
            self.trig_source = value
            return None
        bad = [c for c in chans if c not in self.ch or not self.modules[c - 1]]
        if bad:
            self.errors.append(f'-222,"Data out of range; channel {bad}"')
            return resp("0") if q else None
        if h in ARB_KEYS or h.startswith(ARB_PREFIX):
            if any(c in self.no_arb for c in chans):
                self._err(h)
                return resp("0") if q else None
            for c in chans:
                if q:
                    return resp(self.ch[c].get(h, {"ARB:FUNC:TYPE": "VOLT", "ARB:FUNC:SHAP": "NONE"}.get(h, "0")))
                self.ch[c][h] = value
            return None
        if h == "MEAS:VOLT" and q:
            c = chans[0]
            st = self.ch[c]
            return resp(float(st["VOLT"]) if st["OUTP"] == "1" else 0.0)
        if h == "MEAS:CURR" and q:
            return resp(1e-6 if self.ch[chans[0]]["OUTP"] == "1" else 0.0)
        if h == "STAT:QUES:COND" and q:
            return resp(self.ques.get(chans[0], 0))
        if h in CHANNEL_KEYS:
            if q:
                v = self.ch[chans[0]].get(h, "0")
                return resp({"ON": "1", "OFF": "0"}.get(v, v))
            for c in chans:
                v = {"ON": "1", "OFF": "0"}.get(value.upper(), value) if h == "OUTP" else value
                self.ch[c][h] = v
                if h == "TRIG:TRAN" and self.ch[c].get("VOLT:MODE", "FIX").upper() == "ARB":
                    self.arb_running.add(c)
                if h == "ABOR:TRAN":
                    self.arb_running.discard(c)
            return None
        self._err(h)
        return resp("0") if q else None


class PsuSimTransport(StreamTransport):
    description = "N6705C SIMULATOR"

    def __init__(self, sim: PsuSim | None = None, timeout: float = 10.0, logger=None):
        super().__init__(timeout, logger)
        self.sim = sim or PsuSim()
        self._out = bytearray()

    def _send(self, data: bytes) -> None:
        for line in data.decode("latin-1").splitlines():
            r = self.sim.handle(line)
            if r:
                self._out += r

    def _recv(self) -> bytes:
        if not self._out:
            raise ScopeTimeout("N6705C 시뮬레이터 응답 없음")
        data = bytes(self._out)
        self._out.clear()
        return data

    def close(self) -> None:
        pass

"""메뉴 3(데이터·화면 저장)의 수동 시험 값 계산: 내부 풀업/풀다운 저항(Rpu/Rpd), 출력 전류(IOH/IOL).

시험 항목 이름으로 종류를 정한다(대소문자·_·공백 무시): R_pd/Rpd, R_pu/Rpu, IOH, IOL. 그 외 이름은 계산하지 않는다.
채널 순서: 저장 채널의 첫 번째가 측정 대상(Rpd/Rpu 는 핀 전압, IOH/IOL 은 전류 프로브), 두 번째가 있으면 핀 전압(IOH/IOL).
값은 레코드 전체 평균(DC).

회로 ([manual] 설정: vdd_v, r_ext_ohm = 1 MΩ, probe_r_ohm = TPP1000 입력 10 MΩ)
- Rpd: 외부 R_ext 를 핀 -> VDD, 핀 전압 V 를 프로브로 측정. 핀 아래쪽 = Rpd ‖ R_probe
       Rpd ‖ R_probe = R_ext · V / (VDD − V)  ->  Rpd = 1 / (1/(Rpd‖R_probe) − 1/R_probe)
- Rpu: 외부 R_ext 를 핀 -> GND, 프로브가 R_ext 와 병렬. R_low = R_ext ‖ R_probe
       Rpu = R_low · (VDD − V) / V
"""
from __future__ import annotations

import re

KINDS = {"RPD": "Rpd", "RPU": "Rpu", "IOH": "IOH", "IOL": "IOL"}


def kind_of(label: str) -> str | None:
    key = re.sub(r"[\s_\-]+", "", label).upper()
    return key if key in KINDS else None


def parallel(a: float, b: float) -> float:
    return a * b / (a + b)


def pull_resistance(kind: str, v_pin: float, vdd: float, r_ext: float, r_probe: float) -> tuple[float | None, str]:
    """(저항 Ω 또는 None, 설명). 측정 범위를 벗어나면 None 과 이유."""
    if kind == "RPD":
        if v_pin >= vdd:
            return None, f"핀 전압 {v_pin:.4f} V ≥ VDD {vdd} V: 풀다운이 없거나 연결 확인"
        if v_pin <= 0:
            return None, f"핀 전압 {v_pin:.4f} V ≤ 0: 외부 저항·전원 연결 확인"
        low = r_ext * v_pin / (vdd - v_pin)  # Rpd ‖ R_probe
        if low >= r_probe:
            return None, f"Rpd‖프로브 {low:.4g} Ω ≥ 프로브 {r_probe:.4g} Ω: 풀다운 없음(프로브만 보임)"
        return 1 / (1 / low - 1 / r_probe), f"Rpd = 1/(1/(R_ext·V/(VDD−V)) − 1/R_probe), Rpd‖R_probe = {low:.4g} Ω"
    if kind == "RPU":
        if v_pin <= 0:
            return None, f"핀 전압 {v_pin:.4f} V ≤ 0: 풀업이 없거나 연결 확인"
        if v_pin >= vdd:
            return None, f"핀 전압 {v_pin:.4f} V ≥ VDD {vdd} V: 외부 저항·전원 연결 확인"
        low = parallel(r_ext, r_probe)  # R_ext ‖ R_probe
        return low * (vdd - v_pin) / v_pin, f"Rpu = (R_ext‖R_probe)·(VDD−V)/V, R_ext‖R_probe = {low:.4g} Ω"
    raise ValueError(kind)


def mean(wf) -> float:
    return sum(wf.to_volts(x) for x in wf.raw) / len(wf.raw)


def compute(label: str, wfs: list, cfg) -> dict | None:
    """수동 시험 계산 결과(result.json 의 calc) 또는 None(계산하지 않는 항목)."""
    kind = kind_of(label)
    if kind is None or not wfs:
        return None
    vdd = cfg.getfloat("manual", "vdd_v")
    r_ext = cfg.getfloat("manual", "r_ext_ohm")
    r_probe = cfg.getfloat("manual", "probe_r_ohm")
    first = mean(wfs[0])
    if kind in ("RPD", "RPU"):
        value, note = pull_resistance(kind, first, vdd, r_ext, r_probe)
        return {"kind": kind, "name": KINDS[kind], "value": value, "unit": "ohm", "note": note,
                "inputs": {"channel": wfs[0].source, "v_pin": first, "vdd": vdd, "r_ext": r_ext, "r_probe": r_probe}}
    calc = {"kind": kind, "name": KINDS[kind], "value": first, "unit": "A",
            "note": f"{wfs[0].source} 전류 프로브 평균", "inputs": {"channel": wfs[0].source}}
    if len(wfs) > 1:
        v = mean(wfs[1])
        calc["inputs"].update(v_channel=wfs[1].source, v_pin=v)
        calc["note"] += f", 그때 핀 전압 {wfs[1].source} {v:.4f} V"
    return calc


def display(calc: dict) -> tuple[str, str]:
    """(보고서·표에 쓸 값 문자열, 단위)."""
    v = calc.get("value")
    if calc["unit"] == "ohm":
        return ("-" if v is None else f"{v / 1e3:.1f}"), "kΩ"
    return ("-" if v is None else f"{v * 1e3:.3f}"), "mA"

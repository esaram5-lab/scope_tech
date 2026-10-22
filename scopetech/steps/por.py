"""전원 인가(POR) 후 출력 레벨: VDD 상승을 트리거로 Single, POR 지연 동안 Low(VOL) -> High(VOH), 지연 시간."""
from __future__ import annotations

from .. import results
from ..analysis import power_on_levels
from . import volts


def run(bench, ch_vdd: int, ch_out: int):
    s, cfg = bench.scope, bench.cfg
    bench.prepare_overlay()
    timeout = cfg.getfloat("measure", "single_timeout_s")
    bench.echo(f"  단일 획득(Single) 대기 중... {timeout:.0f}초 안에 VDD 를 켜세요(CH{ch_vdd} 상승 트리거).")
    s.single(timeout)
    wvdd = s.fetch_waveform(ch_vdd)
    wout = s.fetch_waveform(ch_out)
    po = power_on_levels(wvdd, wout)

    rec = bench.new_record("POR", [ch_vdd, ch_out])
    r = rec.result = bench.base_result("POR")
    r["channels"] = {"vdd": f"CH{ch_vdd}", "out": f"CH{ch_out}"}
    r["channel_names"] = {f"CH{c}": cfg.channel_name(c) for c in (ch_vdd, ch_out)}
    r["analysis"] = po.to_dict()
    label, sig = f"CH{ch_out}", cfg.channel_name(ch_out)
    r["metrics"] = [
        results.metric(label, sig, "VOL (POR 지연 중 Low)", "VOL", po.vol, po.vol, 1 if po.vol is not None else None),
        results.metric(label, sig, "VOH (POR 후 High)", "VOH", po.voh, po.voh, 1 if po.voh is not None else None),
    ]
    bench.record = rec

    delay = "-" if po.delay_s is None else f"{po.delay_s * 1e3:.3f} ms"
    bench.echo(f"[POR] VDD CH{ch_vdd} {volts(po.vdd)} -> 출력 CH{ch_out}: VOL {volts(po.vol)} | VOH {volts(po.voh)}"
               f" | 지연 {delay}  [핀 {bench.dut_pin or '-'}]")
    for w in po.warnings:
        bench.echo(f"  [주의] {w}")
    bench.finish(rec)
    return rec

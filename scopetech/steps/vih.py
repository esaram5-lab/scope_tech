"""VIL/VIH 측정: 삼각파 입력의 출력 전환 지점에서 VT+/VT- 계산, 같은 파형의 출력 VOH/VOL, 커서·측정 배지."""
from __future__ import annotations

from .. import results
from ..analysis import find_thresholds, summarize
from . import volts


def run(bench, ch_in: int, ch_out: int, hold: bool = False):
    s, cfg = bench.scope, bench.cfg
    bench.prepare_overlay()
    if hold:
        s.stop()
        bench.echo("  현재 화면 파형으로 분석합니다(새 획득 없음).")
    else:
        if cfg.getbool("measure", "hires_for_vih") and not s.acq_mode().startswith("HIR"):
            s.set_acq_mode("HIRes")
            bench.echo("  획득 모드를 High Res 로 변경했습니다.")
        bench.echo("  단일 획득(Single) 대기 중...")
        s.single(cfg.getfloat("measure", "single_timeout_s"))
    win = s.fetch_waveform(ch_in)
    wout = s.fetch_waveform(ch_out)
    th = find_thresholds(
        win, wout,
        avg_points=cfg.getint("measure", "vin_avg_points"),
        cross=cfg.getfloat("measure", "out_cross"),
    )
    # 화면에도 출력 VOH/VOL 이 보이도록 출력 채널에 TOP/BASE 측정 추가.
    # 획득이 끝난 뒤에 추가한다(획득 중 측정 계산 부하/이벤트 방지). 판정 값은 위 파형에서 계산.
    badges = []
    if cfg.getbool("measure", "vih_output_meas"):
        for name, mtype in zip(s.free_measurement_names(2), ("TOP", "BASE")):
            s.add_measurement(name, mtype, ch_out)
            badges.append((name, mtype, ch_out))
        err = s.check("출력 측정 추가", raise_=False)
        if err:
            bench.echo(f"  [경고] {err}")

    rec = bench.new_record("VIH", [ch_in, ch_out])
    rec.meas = badges
    r = rec.result = bench.base_result("VIH")
    r["channels"] = {"in": f"CH{ch_in}", "out": f"CH{ch_out}"}
    r["channel_names"] = {f"CH{c}": cfg.channel_name(c) for c in (ch_in, ch_out)}
    r["acq_mode"] = s.acq_mode()
    r["params"] = {"vin_avg_points": cfg.getint("measure", "vin_avg_points"), "out_cross": cfg.getfloat("measure", "out_cross")}
    r["analysis"] = th.to_dict()

    # 커서: A = 첫 VT+ 시점, B = 첫 VT- 시점 (입력 채널 전압이 화면에 표시됨)
    ta = th.vt_plus[0].time if th.vt_plus else None
    tb = th.vt_minus[0].time if th.vt_minus else None
    if ta is not None or tb is not None:
        ta = ta if ta is not None else tb
        tb = tb if tb is not None else ta
        s.cursor_waveform(ch_in, ta, tb)
        rec.cursor = {"kind": "waveform", "ch": ch_in, "a": ta, "b": tb}
        err = s.check("커서 설정", raise_=False)
        if err:
            bench.echo(f"  [경고] {err}")
    r["cursor"] = rec.cursor

    p, m = summarize(th.vt_plus_v), summarize(th.vt_minus_v)
    pair = f"CH{ch_in}->CH{ch_out}"
    sig = f"{cfg.channel_name(ch_in)}->{cfg.channel_name(ch_out)}"
    r["metrics"] = results.vih_metrics(th, pair, sig) + results.vih_output_metrics(
        th, f"CH{ch_out}", cfg.channel_name(ch_out))
    bench.record = rec

    pol = {"inverting": "반전(인버터)", "non-inverting": "비반전(버퍼/AND/레벨시프터)"}.get(th.polarity, "판별 불가")
    bench.echo(f"[VIH/VIL] 입력 CH{ch_in} -> 출력 CH{ch_out}, 출력 극성: {pol}")
    for label, st in (("VT+ (상승, VIH측)", p), ("VT- (하강, VIL측)", m)):
        if st:
            bench.echo(f"  {label}: 평균 {st['mean']:.4f} V  (n={st['n']}, min {st['min']:.4f} / max {st['max']:.4f})")
        else:
            bench.echo(f"  {label}: 없음")
    bench.echo(f"  히스테리시스: {volts(th.hysteresis)}")
    hs, ls = summarize(th.out_highs), summarize(th.out_lows)
    bench.echo(f"  출력 VOH: {volts(hs and hs['mean'])} (worst {volts(hs and hs['min'])}, n={hs['n'] if hs else 0})"
              f" | VOL: {volts(ls and ls['mean'])} (worst {volts(ls and ls['max'])}, n={ls['n'] if ls else 0})"
              f"  [출력 {bench.out_type}{', 핀 ' + bench.out_pin if bench.out_pin else ''}]")
    if rec.cursor:
        bench.echo(f"  커서 A={ta * 1e3:+.4f} ms (VT+), B={tb * 1e3:+.4f} ms (VT-)  [소스 CH{ch_in}]")
    for w in th.warnings:
        bench.echo(f"  [주의] {w}")
    bench.finish(rec)
    return rec

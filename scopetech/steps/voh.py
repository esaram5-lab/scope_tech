"""VOH/VOL 측정: 출력 채널에 TOP/BASE/MAX/MIN 측정을 걸고 통계 수집, 커서."""
from __future__ import annotations

from .. import results
from . import volts

VOH_TYPES = ("TOP", "BASE", "MAXIMUM", "MINIMUM")


def run(bench, chans: list[int], hold: bool = False):
    s, cfg = bench.scope, bench.cfg
    bench.prepare_overlay()
    names = s.free_measurement_names(len(chans) * len(VOH_TYPES))
    meas = []
    for ch in chans:
        for mtype in VOH_TYPES:
            name = names.pop(0)
            s.add_measurement(name, mtype, ch)
            meas.append((name, mtype, ch))
    s.check("측정 추가")
    if hold:
        s.stop()
        n_acq = 1
        bench.echo("  현재 화면 파형으로 측정합니다(새 획득 없음).")
    else:
        n = cfg.getint("measure", "stats_acquisitions")
        bench.echo(f"  통계 수집 중... ({n}회 획득)")
        n_acq = s.acquire_stats(n, cfg.getfloat("measure", "stats_timeout_s"))
    values = {name: s.measurement_results(name) for name, _, _ in meas}

    rec = bench.new_record("VOH", chans)
    rec.meas = meas
    r = rec.result = bench.base_result("VOH")
    r["channels"] = [f"CH{c}" for c in chans]
    r["channel_names"] = {f"CH{c}": cfg.channel_name(c) for c in chans}
    r["acquisitions"] = n_acq
    r["measurements"] = {f"{mtype}_CH{ch}": {"scope_name": name, **values[name]} for name, mtype, ch in meas}

    bench.echo(f"[VOH/VOL] 출력 타입 {bench.out_type}, 획득 {n_acq}회")
    metrics = []
    for ch in chans:
        v = {mtype: values[name] for name, mtype, c in meas if c == ch}
        top, base, vmax, vmin = v["TOP"], v["BASE"], v["MAXIMUM"], v["MINIMUM"]
        label, sig = f"CH{ch}", cfg.channel_name(ch)
        metrics += [
            results.metric(label, sig, "VOH (TOP)", "VOH", top["mean"], top["min"], top["population"]),
            results.metric(label, sig, "VOL (BASE)", "VOL", base["mean"], base["max"], base["population"]),
            results.metric(label, sig, "MAX", None, vmax["mean"], vmax["max"], vmax["population"]),
            results.metric(label, sig, "MIN", None, vmin["mean"], vmin["min"], vmin["population"]),
        ]
        bench.echo(
            f"  CH{ch}({sig}): VOH {volts(top['mean'])} (worst {volts(top['min'])}) | "
            f"VOL {volts(base['mean'])} (worst {volts(base['max'])}) | MAX {volts(vmax['max'])} | MIN {volts(vmin['min'])}"
        )
    r["metrics"] = metrics

    first = values[meas[0][0]], values[meas[1][0]]  # 첫 채널 TOP, BASE
    if cfg.getbool("measure", "voh_cursor") and first[0]["mean"] is not None and first[1]["mean"] is not None:
        s.cursor_hbars(chans[0], first[0]["mean"], first[1]["mean"])
        rec.cursor = {"kind": "hbars", "ch": chans[0], "a": first[0]["mean"], "b": first[1]["mean"]}
        err = s.check("커서 설정", raise_=False)
        if err:
            bench.echo(f"  [경고] {err}")
    r["cursor"] = rec.cursor
    bench.record = rec
    bench.finish(rec)
    return rec

"""저장된 원시 파형(waveform*.csv)으로 결과를 다시 계산한다 (스코프 연결 불필요).

analyze.py --recompute 와 plan_import.py(예전 측정 가져오기)가 함께 쓴다.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import manual_calc, results, wavefile
from .analysis import Waveform, find_thresholds


def load_waves(folder: Path, sources: list[str]) -> dict[str, Waveform]:
    waves: dict[str, Waveform] = {}
    for f in sorted(Path(folder).glob("waveform*.csv")):
        for w in wavefile.read_csv(f):
            waves.setdefault(w.source, w)
    missing = [s for s in sources if s not in waves]
    if missing:
        raise FileNotFoundError(f"{folder}: 파형 CSV 에 {', '.join(missing)} 가 없습니다(save 를 했는지 확인).")
    return waves


def recompute_vih(folder: Path, result: dict, cfg) -> None:
    """VIL/VIH 결과를 파형으로 다시 계산(입력 VT+/VT-, 같은 파형의 출력 VOH/VOL 포함)."""
    ch_in, ch_out = result["channels"]["in"], result["channels"]["out"]
    waves = load_waves(folder, [ch_in, ch_out])
    th = find_thresholds(waves[ch_in], waves[ch_out], avg_points=cfg.getint("measure", "vin_avg_points"),
                         cross=cfg.getfloat("measure", "out_cross"))
    names = result.get("channel_names", {})
    result["analysis"] = th.to_dict()
    result["metrics"] = (results.vih_metrics(th, f"{ch_in}->{ch_out}", f"{names.get(ch_in, ch_in)}->{names.get(ch_out, ch_out)}")
                         + results.vih_output_metrics(th, ch_out, names.get(ch_out, ch_out)))
    result["params"] = {"vin_avg_points": cfg.getint("measure", "vin_avg_points"), "out_cross": cfg.getfloat("measure", "out_cross")}
    result["recomputed"] = datetime.now().isoformat(timespec="seconds")


def recompute_manual(folder: Path, result: dict, cfg, label: str | None = None, order: list[str] | None = None) -> None:
    """메뉴 3(R_pd/R_pu/IOH/IOL) 계산값을 파형으로 다시 계산. order: 쓸 채널 순서(예: ['CH8', 'CH2'])."""
    saved = list(result.get("channels") or [])
    use = order if order and all(c in saved for c in order) else saved
    waves = load_waves(folder, use)
    calc = manual_calc.compute(label or result.get("manual_item", ""), [waves[c] for c in use], cfg)
    if calc is None:
        raise ValueError(f"{folder}: 항목 이름 '{label or result.get('manual_item')}' 은 계산할 수 없습니다(R_pd/R_pu/IOH/IOL).")
    result["calc"] = calc
    result["recomputed"] = datetime.now().isoformat(timespec="seconds")

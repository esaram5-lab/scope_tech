"""config.ini 읽기. 파일에 없는 항목은 아래 기본값을 쓴다."""
from __future__ import annotations

import configparser
from pathlib import Path

DEFAULTS = {
    "connection": {
        "interface": "usb",
        "usb_resource": "",
        "visa_library": "",
        "usb_clear": "yes",
        "ip": "192.168.0.10",
        "port": "4000",
        "timeout_s": "10",
    },
    "session": {"temp": "25", "name": "TEST", "spec": "", "out_type": "PP"},
    "defaults": {"vih_in": "1", "vih_out": "2", "voh": "2"},
    "measure": {
        "hires_for_vih": "yes",
        "single_timeout_s": "30",
        "stats_acquisitions": "50",
        "stats_timeout_s": "30",
        "clear_existing_measurements": "yes",
        "vin_avg_points": "10",
        "out_cross": "0.5",
        "voh_cursor": "yes",
        "vih_output_meas": "yes",
        "reset_each_run": "yes",
    },
    "capture": {"enabled": "yes", "method": "screen_api", "ink_saver": "no", "settle_s": "1.0", "aoe_arm_wait_s": "1", "aoe_save_wait_s": "2", "aoe_post_wait_s": "2",
                "image_fetch": "end", "fetch_gap_s": "1", "delete_after_fetch": "yes"},
    "screen_api": {"host": "", "port": "8765", "token_file": "capture_token.txt", "client_dir": "", "name": "",
                   "timeout_s": "10", "discovery_timeout_s": "2", "acquire": "stop", "settle_s": "0.3",
                   "stable_ms": "2000", "stable_interval_ms": "100", "stable_frames": "3"},
    "save": {"channels": "displayed", "csv": "yes", "wfm": "yes", "setup": "yes"},
    "scope": {"temp_dir": "C:/scope_tech_tmp", "num_channels": "8"},
    "output": {"base_dir": "captures", "sim_dir": "captures_sim", "log_dir": "logs"},
    "channels": {},
}

SPEC_KEYS = ("vih_min", "vil_max", "voh_min", "vol_max")


class Config:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), interpolation=None)
        self.cp.read_dict(DEFAULTS)
        if self.path and self.path.exists():
            self.cp.read(self.path, encoding="utf-8-sig")

    def get(self, section: str, key: str, fallback: str = "") -> str:
        return self.cp.get(section, key, fallback=fallback).strip()

    def getint(self, section: str, key: str) -> int:
        return int(float(self.get(section, key)))

    def getfloat(self, section: str, key: str) -> float:
        return float(self.get(section, key))

    def getbool(self, section: str, key: str) -> bool:
        return self.get(section, key).lower() in ("1", "yes", "true", "on", "y")

    def channel_name(self, ch: int) -> str:
        return self.get("channels", f"ch{ch}") or f"CH{ch}"

    def spec_profiles(self) -> list[str]:
        return [s.split(":", 1)[1] for s in self.cp.sections() if s.lower().startswith("spec:")]

    def spec(self, profile: str | None) -> dict[str, float | None]:
        out: dict[str, float | None] = {k: None for k in SPEC_KEYS}
        if not profile:
            return out
        for sec in self.cp.sections():
            if sec.lower() == f"spec:{profile.lower()}":
                for k in SPEC_KEYS:
                    v = self.cp.get(sec, k, fallback="").strip()
                    out[k] = float(v) if v else None
                return out
        raise KeyError(profile)

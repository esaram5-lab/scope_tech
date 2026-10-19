# 테스트에서는 화면 캡처 대기 시간을 0 으로 (실장비용 기본값은 수 초)
from scopetech import config as _config

for _k in ("aoe_arm_wait_s", "aoe_save_wait_s", "aoe_post_wait_s"):
    _config.DEFAULTS["capture"][_k] = "0"
_orig_init = _config.Config.__init__


def _init(self, path=None):
    _orig_init(self, path)
    for k in ("aoe_arm_wait_s", "aoe_save_wait_s", "aoe_post_wait_s"):
        self.cp.set("capture", k, "0")


_config.Config.__init__ = _init

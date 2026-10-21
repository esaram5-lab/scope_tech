# 테스트에서는 화면 캡처 대기 시간을 0 으로 (실장비용 기본값은 수 초)
from scopetech import config as _config

# 실장비 기본 캡처(screen_api)는 스코프 서버가 필요해, 시뮬레이터 시험은 aoe 로 한다(개별 시험에서 바꿈)
_config.DEFAULTS["capture"]["method"] = "aoe"
for _k in ("aoe_arm_wait_s", "aoe_save_wait_s", "aoe_post_wait_s"):
    _config.DEFAULTS["capture"][_k] = "0"
_orig_init = _config.Config.__init__


def _init(self, path=None):
    _orig_init(self, path)
    for k in ("aoe_arm_wait_s", "aoe_save_wait_s", "aoe_post_wait_s"):
        self.cp.set("capture", k, "0")
    self.cp.set("capture", "method", "aoe")


_config.Config.__init__ = _init

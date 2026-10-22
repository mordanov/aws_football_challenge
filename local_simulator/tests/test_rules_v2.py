from __future__ import annotations

import pytest

from football_lab.rules_v2 import RulesV2Config


def test_config_defaults_and_validation() -> None:
    cfg = RulesV2Config()
    assert (cfg.foul_scale, cfg.p_foul, cfg.p_win_slide, cfg.stun_steps) == (1.0, 0.25, 0.55, 40)
    assert cfg.send_off_fouls == 3 and cfg.p_penalty_goal == 0.75
    with pytest.raises(ValueError):
        RulesV2Config(foul_scale=-0.1)

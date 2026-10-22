import pytest

from football_lab.evolution.bounds import load_bounds, validate_settings
from football_lab.evolution.contracts import PARAM_NAMES


def test_bounds_cover_exactly_the_four_parameters():
    bounds = load_bounds()
    assert set(bounds) == set(PARAM_NAMES)
    for lo, hi in bounds.values():
        assert lo < hi


def test_validate_accepts_defaults_and_rejects_out_of_range():
    ok = {"pass_margin": 3.0, "shot_range": 29.0, "press_radius": 22.0, "support_width": 15.0}
    validate_settings(ok)
    with pytest.raises(ValueError):
        validate_settings({**ok, "shot_range": 99.0})
    with pytest.raises(ValueError):
        validate_settings({**ok, "extra": 1.0})

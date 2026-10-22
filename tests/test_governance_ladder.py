"""The governance ladder, checked where it is defined.

AUTO_EXECUTE_MAX_RISK and BLOCK_MIN_RISK are the two ends of the rule the
governance documentation states as a fixed promise:

    risk <= 0.30  -> auto-execute
    risk >  0.85  -> hard block (never auto-run, always review)

The block was the one threshold hardcoded while its own config section
claimed every threshold was env-overridable. It is now a knob like the rest
— and a knob that can be turned until the rule contradicts itself, which is
worse than not having the rule, so config refuses to import when it is.

Both checks run at import time, so these tests reload the module with the
environment applied rather than calling a function: that is where the check
lives, and it is where a misconfigured deployment would hit it — at boot,
with a message, instead of at run time with a silent wrong answer.
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402


def _reload(**env: str):
    """Reload backend.config with `env` applied, restoring the environment.

    config reads the environment once at import, so a fresh import is the
    only honest way to exercise what a real boot would do.
    """
    previous = {key: os.environ.get(key) for key in env}
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        return importlib.reload(config)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_the_default_ladder_still_imports_and_holds_its_documented_shape():
    """The defaults are the promise the docs make; if reloading with nothing
    set breaks them, everything downstream is measuring a different rule."""
    reloaded = _reload(AIOPS_BLOCK_MIN_RISK=None, AIOPS_AUTO_EXECUTE_MAX_RISK=None)
    assert reloaded.BLOCK_MIN_RISK == 0.85
    assert reloaded.AUTO_EXECUTE_MAX_RISK == 0.30
    assert reloaded.BLOCK_MIN_RISK > reloaded.AUTO_EXECUTE_MAX_RISK
    # risk is clamped to 1.0 before comparison (pipeline.decide), so the
    # default must be reachable — a block above the clamp never fires.
    assert reloaded.BLOCK_MIN_RISK <= 1.0


def test_a_block_at_or_below_the_auto_threshold_refuses_to_boot():
    """The band the ladder promises to run would be blocked instead — the
    documented rule inverted, with nothing to say so."""
    with pytest.raises(RuntimeError) as exc:
        _reload(AIOPS_BLOCK_MIN_RISK="0.20", AIOPS_AUTO_EXECUTE_MAX_RISK="0.30")
    assert "AIOPS_BLOCK_MIN_RISK" in str(exc.value)
    assert "AIOPS_AUTO_EXECUTE_MAX_RISK" in str(exc.value)

    # The failed reload left the module half-initialized; the next import in
    # this session must still get a working config.
    assert _reload(AIOPS_BLOCK_MIN_RISK=None,
                   AIOPS_AUTO_EXECUTE_MAX_RISK=None).BLOCK_MIN_RISK == 0.85


def test_a_block_above_the_risk_clamp_refuses_to_boot():
    """risk is min(1.0, ...) before it is compared, so a block above 1.0
    never fires: the hard block would stand armed and inert while every
    receipt kept recording it as in force."""
    with pytest.raises(RuntimeError) as exc:
        _reload(AIOPS_BLOCK_MIN_RISK="1.5")
    assert "clamped to 1.0" in str(exc.value)
    assert _reload(AIOPS_BLOCK_MIN_RISK=None).BLOCK_MIN_RISK == 0.85


def test_a_knob_between_the_two_ends_is_accepted():
    """The point of the knob: a deployment that wants a tighter block can
    have one, as long as the ladder still means what it says."""
    reloaded = _reload(AIOPS_BLOCK_MIN_RISK="0.60",
                       AIOPS_AUTO_EXECUTE_MAX_RISK="0.20")
    assert reloaded.BLOCK_MIN_RISK == 0.60
    assert reloaded.AUTO_EXECUTE_MAX_RISK == 0.20
    restored = _reload(AIOPS_BLOCK_MIN_RISK=None, AIOPS_AUTO_EXECUTE_MAX_RISK=None)
    assert (restored.BLOCK_MIN_RISK, restored.AUTO_EXECUTE_MAX_RISK) == (0.85, 0.30)

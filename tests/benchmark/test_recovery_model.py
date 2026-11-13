import pytest

from discovery_engine.benchmark.recovery_model import (
    BAND, DEFAULT_BASE, STORELESS_EVALUATOR_SRC, toy_observable)


def test_toy_feedback_inhibition_in_band():
    v = toy_observable("feedback_inhibition", base=0.95)
    assert 0.3 <= v <= 0.8


def test_toy_default_base_computes_target_by_arithmetic():
    assert toy_observable("feedback_inhibition", DEFAULT_BASE) == pytest.approx(0.55)


def test_toy_substrate_depletion_out_of_band():
    v = toy_observable("substrate_depletion", base=0.95)
    assert not (BAND["low"] <= v <= BAND["high"])


def test_toy_unknown_mechanism_returns_base():
    assert toy_observable("nope", base=0.95) == 0.95


def test_toy_params_are_honored():
    assert toy_observable("feedback_inhibition", 1.0, {"gain": 3.0}) == pytest.approx(0.25)
    assert toy_observable("substrate_depletion", 1.0, {"rate": 0.0}) == pytest.approx(1.0)


def test_evaluator_src_compiles_and_embeds_constants():
    src = STORELESS_EVALUATOR_SRC("k", 1.1)
    compile(src, "evaluators.py", "exec")
    assert "_KIND = 'k'" in src and "measure.get(\"study\")" in src

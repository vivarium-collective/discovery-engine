"""A small-magnitude measured value must survive grading: round(x,6) zeroed any
value below ~1e-6, so ~1e-22 flux findings could never match observed and never
verify. _sig6 keeps significant figures instead."""
from discovery_engine.study_evaluator import _sig6


def test_sig6_preserves_tiny_magnitude():
    assert _sig6(3.34e-22) == 3.34e-22          # not zeroed
    assert _sig6(4.005e-22) == 4.005e-22
    assert _sig6(0.5362) == 0.5362              # normal values unchanged
    assert _sig6(0.0) == 0.0
    assert _sig6(None) is None
    # round(3.34e-22, 6) would have been 0.0:
    assert round(3.34e-22, 6) == 0.0 and _sig6(3.34e-22) != 0.0

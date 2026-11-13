"""Recovery-bridge spike (Task 1): the toy model + the store-less evaluator source.

Slice 2 of the discovery-engine benchmark proves the engine's REAL loop can *recover*
a concealed mechanism: build a model from a capability gap, run it, and measure whether
the observable lands in the acceptance band. This module supplies the toy model whose
observable the store-less evaluator re-derives from the BUILT model's *implemented*
mechanism (not the gap's declared mechanism, not a config name).

"""
from __future__ import annotations

import math

# Shared contract between the build hook, the evaluator source, and the chain test.
TOY_KIND = "recovery_toy"                               # the store-less measure kind
DEFAULT_BASE = 1.1                                      # feedback: 1.1/2 = 0.55 (in band)
BAND = {"op": "range", "low": 0.3, "high": 0.8}         # acceptance band for the recovered observable


def toy_observable(mechanism: str, base: float, params: dict | None = None) -> float:
    """The toy observable a recovered mechanism produces, by *genuine arithmetic*.

    - ``feedback_inhibition`` -> steady state of a product under negative feedback:
      ``base / (1 + gain)``.  With ``base=1.1`` and the default ``gain=1.0`` this is
      ``1.1 / 2.0 = 0.55`` — inside the acceptance band ``[0.3, 0.8]`` (computed, never
      hardcoded to the target).
    - ``substrate_depletion`` -> product left after exponential substrate run-down:
      ``base * exp(-rate)``.  With ``base=1.1`` and the default ``rate=2.5`` this is
      ``1.1 * exp(-2.5) = 0.0903`` — genuinely OUT of the band (the decoy half of the
      chain test): the number is small because depletion really drives it there, not
      because anything was pinned.

    ``params`` is live: the evaluator threads the test's optional ``measure.params``
    (e.g. ``{"gain": 1.0}`` / ``{"rate": 2.5}``) into this function; when absent the
    defaults above are used, and they are intentional (they are what places the
    faithful mechanism in band and the decoy out of it).  An unknown mechanism returns
    ``base`` unchanged (no mechanism effect applied), so it cannot masquerade as a recovery
    unless ``base`` itself happens to be in band.

    The value is a pure function of the mechanism string + base + params, so the
    evaluator that reads the built model's ``implements:`` field computes an honest
    observable: faithful build lands in band, decoy build lands out of band.
    """
    params = params or {}
    if mechanism == "feedback_inhibition":
        gain = float(params.get("gain", 1.0))
        return base / (1.0 + gain)
    if mechanism == "substrate_depletion":
        rate = float(params.get("rate", 2.5))
        return base * math.exp(-rate)
    return base


# The body of the generated ``pbg_<ws>/evaluators.py``.  ``STORELESS_EVALUATOR_SRC``
# prepends the two module constants (``_BASE``, ``_KIND``) so no brace-escaping is needed.
_EVALUATOR_BODY = '''
from pathlib import Path

import yaml

from discovery_engine.study.evaluator import storeless
from discovery_engine.benchmark.recovery_model import toy_observable


def _implemented_mechanism(ws_root, study):
    """The mechanism the build_hook ACTUALLY implemented, read from the built model on
    disk: the ``implements:`` field of ``studies/<study>/study.yaml``.  ``study`` is the
    slug the test under evaluation names in its own ``measure.study`` (so several
    build-materialized studies never collide).  Never the gap's declared mechanism and
    never a config name.  Returns None when the study or field is absent."""
    if ws_root is None or not study:
        return None
    path = Path(ws_root) / "studies" / str(study) / "study.yaml"
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    impl = doc.get("implements")
    return str(impl) if impl else None


@storeless
def _recover(test, reader, ws_root):
    # reader is ignored: the grade is re-derived from the built model, store-less by design.
    measure = test.get("measure") or {}
    mech = _implemented_mechanism(ws_root, measure.get("study"))
    if not mech:
        return {"result": "SKIP", "evaluated_by": "code", "measured_value": None,
                "status": "needs_rerun",
                "detail": "no built study named by measure.study with an implements: field"}
    value = toy_observable(mech, _BASE, measure.get("params"))
    pass_if = test.get("pass_if") or {}
    lo, hi = pass_if.get("low"), pass_if.get("high")
    in_band = (lo is None or value >= lo) and (hi is None or value <= hi)
    return {"result": "PASS" if in_band else "FAIL", "evaluated_by": "code",
            "measured_value": value,
            "status": "supported" if in_band else "contradicts",
            "detail": "toy_observable(%r, base=%s) = %.4f" % (mech, _BASE, value)}


def register_evaluators(registry):
    registry[_KIND] = _recover
'''


def STORELESS_EVALUATOR_SRC(test_kind: str, base: float) -> str:
    """Source text for a ``pbg_<ws>/evaluators.py`` that registers a ``@storeless``
    evaluator of ``test_kind``.  The evaluator reads the built model's *implemented*
    mechanism from ``ws_root`` and returns ``{result, measured_value, evaluated_by:"code",
    status, detail}`` computed by :func:`toy_observable`."""
    header = f"_BASE = {base!r}\n_KIND = {test_kind!r}\n"
    return header + _EVALUATOR_BODY

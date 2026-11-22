"""R4 — model adequacy + causal diagnosis (§4).

``model_error.diagnose_refutation`` already triages WHY a prediction failed (revise-model vs
revise-hypothesis). This adds the claim-specific **adequacy gate** §4 asks for: it gates the
scientific CLAIM, not merely model execution. A model can reproduce a measured effect yet still
not establish the stronger claim built on it — because a mechanism the strong claim *requires* was
never shown.

``assess_model_adequacy`` decides, for one claim under a model+evidence state, which inference the
evidence SUPPORTS versus which it must EXCLUDE: the strong claim is adequate only if every mechanism
it requires has been established (each backed by an observation). Otherwise the strong claim is
rejected, the supported weaker inference is named, the missing mechanisms are identified, and a
model revision + a test to establish them are proposed. Pure.

§4's acceptance: a model reproduces a metabolite-exchange increase but lacks heritable transmission;
the engine must reject the stronger claim that endosymbiosis has evolved, identify inheritance as
missing, and propose a model extension + a multigenerational test.
"""
from __future__ import annotations

ADEQUATE = "adequate"
INADEQUATE = "inadequate"


def _established_from_observations(observations) -> set:
    """Mechanisms an observation actually establishes: an observation names a ``mechanism`` it
    backs and is ``supported`` (a measured, in-band result — not merely present)."""
    out: set = set()
    for o in observations or []:
        if not isinstance(o, dict):
            continue
        mech = o.get("mechanism")
        supported = o.get("supported")
        if supported is None:
            supported = str(o.get("verdict", "")).lower() in ("supported", "within_tol", "pass")
        if mech and supported:
            out.add(mech)
    return out


def assess_model_adequacy(claim, *, required_mechanisms=None, established_mechanisms=None,
                          observations=None, proposed_revision=None) -> dict:
    """Claim-specific adequacy gate (§4). The claim's STRONG form needs ``required_mechanisms``; the
    model+evidence has ESTABLISHED ``established_mechanisms`` (or, if not passed explicitly, the
    mechanisms backed by a *supported* observation in ``observations``). The claim is **adequate**
    only when every required mechanism is established; otherwise it is **inadequate** — the strong
    claim is excluded, the supported weaker inference is named, the missing mechanisms are
    identified, and a model revision + a test to establish them are proposed.

    ``claim`` is a string or ``{statement, requires}`` (``requires`` is used when
    ``required_mechanisms`` is not given). Returns a ModelAdequacyAssessment dict."""
    statement = claim.get("statement") if isinstance(claim, dict) else str(claim)
    if required_mechanisms is None and isinstance(claim, dict):
        required_mechanisms = claim.get("requires")
    required = set(required_mechanisms or [])

    established = set(established_mechanisms or [])
    established |= _established_from_observations(observations)

    missing = sorted(required - established)
    supported = sorted(required & established)

    if not required:
        return {"claim": statement, "verdict": ADEQUATE, "supported_inference": statement,
                "excluded_inference": None, "missing_mechanisms": [], "established_mechanisms": sorted(established),
                "assumptions": [], "proposed_revision": None, "proposed_test": None,
                "note": "claim declares no required mechanisms — nothing to gate"}

    if not missing:
        return {"claim": statement, "verdict": ADEQUATE, "supported_inference": statement,
                "excluded_inference": None, "missing_mechanisms": [],
                "established_mechanisms": sorted(required & established),
                "assumptions": sorted(established - required), "proposed_revision": None,
                "proposed_test": None}

    return {
        "claim": statement,
        "verdict": INADEQUATE,
        "supported_inference": (f"the evidence supports only: {', '.join(supported)}" if supported
                                else "no required mechanism of the claim is established"),
        "excluded_inference": statement,
        "missing_mechanisms": missing,
        "established_mechanisms": supported,
        "assumptions": sorted(established - required),
        "proposed_revision": (proposed_revision
                              or f"extend the model to add the missing mechanism(s): {', '.join(missing)}"),
        "proposed_test": (f"a test that establishes {', '.join(missing)} "
                          "(e.g. a held-out / multigenerational experiment)"),
    }

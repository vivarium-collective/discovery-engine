"""Investigation-level rigor roll-up (:func:`investigation_rigor`).

Rolls per-study rigor up to the investigation and adds investigation-level
dimensions: adversarial coverage, methodology strength, falsification exposure,
comparative framing, hypothesis competition, and aggregate per-study gaps.
"""
from __future__ import annotations

from discovery_engine.reporting.rigor.common import (
    GAP,
    OK,
    WARN,
    _as_list,
    _dim,
    _nonempty,
    _study_type,
)
from discovery_engine.reporting.rigor.study_rigor import study_rigor


def investigation_rigor(inv_spec: dict, study_specs: list[dict]) -> dict:
    """Roll study rigor up to the investigation, plus investigation-level
    dimensions (adversarial coverage, methodology strength).

    ``study_specs`` is the list of member study specs (any order). Returns
    ``{per_study: {slug: scorecard}, dimensions: [...], score: {...}, summary}``.
    """
    inv_spec = inv_spec or {}
    study_specs = study_specs or []

    per_study: dict[str, dict] = {}
    for s in study_specs:
        slug = (s or {}).get("name") or (s or {}).get("slug") or f"study-{len(per_study)}"
        per_study[slug] = study_rigor(s or {})

    dims: list[dict] = []

    # Adversarial coverage [C10] — generalized through _study_type (critique #10).
    adversarial = [s for s in study_specs if _study_type(s) == "adversarial"]
    if adversarial:
        dims.append(_dim("adversarial_coverage", "Adversarial testing", OK,
                         f"{len(adversarial)} adversarial study(ies) designed to break the framework", ["C10", "C12", "C15"]))
    else:
        dims.append(_dim("adversarial_coverage", "Adversarial testing", GAP,
                         "no adversarial study — add one that tries to BREAK the criteria: "
                         "mimic / parasitic-or-dependent / externally-maintained / random-cyclic "
                         "systems that should NOT qualify", ["C10", "C12", "C15"]))

    # Methodology strength [C9, C2, C14] — informational positive headline; the
    # reusable methodological contribution the reviewers single out.
    has_dag = any("pipeline_gate" in (s or {}) for s in study_specs)
    has_ac = bool(_as_list(inv_spec.get("acceptance_criteria")))
    if has_dag and has_ac:
        dims.append(_dim("methodology", "Traceable methodology", OK,
                         "capability ladder (study DAG) + explicit acceptance criteria + pass/fail "
                         "gates + traceable findings — the reusable methodological contribution", ["C9", "C2", "C14"]))

    # Falsification exposure [C1] — has the framework ever been seen to reject
    # something? All-pass with no failing control reads as confirmation-only.
    def _passed(s):
        pg = (s or {}).get("pipeline_gate") or {}
        ge = (pg.get("gate_evaluator") or {}) if isinstance(pg, dict) else {}
        res = str(ge.get("result") or (s or {}).get("gate_status") or "").lower()
        return res in ("passed", "pass")

    def _has_discriminating_negative(s):
        for c in _as_list((s or {}).get("controls")):
            if (isinstance(c, dict)
                    and (c.get("kind") or "").lower() in ("negative", "adversarial")
                    and str(c.get("result", "")).upper() == "PASS"
                    and _nonempty(c.get("observed"))):  # item 15: must have run
                return True
        return False

    # An exploratory study OBSERVES rather than tests a hypothesis (critique
    # #10): its passing tests are observations, not falsification credit, so it
    # is excluded from the "did everything just pass?" reasoning.
    non_exploratory = [s for s in study_specs if _study_type(s) != "exploratory"]
    all_passed = bool(non_exploratory) and all(_passed(s) for s in non_exploratory)
    visible_failure = (bool(adversarial)
                       or any(_has_discriminating_negative(s) for s in study_specs)
                       or (bool(non_exploratory) and not all_passed))
    if visible_failure:
        dims.append(_dim("falsification_exposure", "Falsification exposure", OK,
                         "the framework has been shown to reject at least one system (a discriminating "
                         "negative control, an adversarial study, or a non-passing result)", ["C1"]))
    else:
        dims.append(_dim("falsification_exposure", "Falsification exposure", GAP,
                         "every study passes and nothing was shown to fail — the framework was not "
                         "visibly exposed to falsification (add a control that fails, an adversarial "
                         "study, or report a non-passing result)", ["C1"]))

    # Competing theoretical frameworks [C13]
    cf = _as_list(inv_spec.get("competing_frameworks"))
    if cf:
        dims.append(_dim("comparative_framing", "Comparative framing", OK,
                         f"{len(cf)} competing theoretical framework(s) compared", ["C13"]))
    else:
        dims.append(_dim("comparative_framing", "Comparative framing", GAP,
                         "no competing theoretical frameworks compared (viability theory, organizational / "
                         "constraint closure, active inference) — show the findings uniquely support this lens", ["C13"]))

    # Hypothesis competition (critique #6 + #16) — did the investigation put
    # forward ≥2 competing hypotheses AND accumulate evidence against each? An
    # investigation with one (or zero) hypotheses is not adjudicating between
    # rival explanations. Uses the deterministic support roll-up (defensive
    # import so a missing module degrades to GAP, not a crash).
    hyps = [h for h in _as_list(inv_spec.get("hypotheses")) if isinstance(h, dict)]
    if not hyps:
        dims.append(_dim("hypothesis_competition", "Hypothesis competition", GAP,
                         "no competing hypotheses[] declared — state ≥2 rival explanations with "
                         "predictions so the evidence can adjudicate between them", ["C6", "C16"]))
    else:
        n_with_support = 0
        try:
            from discovery_engine.phases.hypotheses import compute_support_log
            for h in hyps:
                if compute_support_log(h, study_specs):
                    n_with_support += 1
        except Exception:  # noqa: BLE001 — defensive cross-module import
            n_with_support = 0
        if len(hyps) >= 2 and n_with_support >= 2:
            dims.append(_dim("hypothesis_competition", "Hypothesis competition", OK,
                             f"{len(hyps)} competing hypotheses, {n_with_support} with evidence "
                             "in their support_log", ["C6", "C16"]))
        elif len(hyps) >= 2:
            dims.append(_dim("hypothesis_competition", "Hypothesis competition", WARN,
                             f"{len(hyps)} hypotheses declared but only {n_with_support} carry "
                             "support_log evidence — link study findings / alternate_hypotheses to them",
                             ["C6", "C16"]))
        else:
            dims.append(_dim("hypothesis_competition", "Hypothesis competition", WARN,
                             "only one hypothesis declared — add a competitor so the evidence "
                             "adjudicates between rivals", ["C6", "C16"]))

    # Aggregate the worst per-study gap count as an investigation signal.
    study_gaps = sum(sc["score"]["gap"] for sc in per_study.values())
    if study_gaps:
        dims.append(_dim("study_rigor_gaps", "Per-study rigor gaps", WARN if study_gaps < 4 else GAP,
                         f"{study_gaps} rigor gap(s) across {len(per_study)} member study(ies)", ["C2", "C4", "C6"]))
    elif per_study:
        dims.append(_dim("study_rigor_gaps", "Per-study rigor gaps", OK,
                         "member studies have no rigor gaps", []))

    score = {GAP: 0, WARN: 0, OK: 0}
    for d in dims:
        score[d["severity"]] = score.get(d["severity"], 0) + 1
    return {
        # Critique #1: this scorecard measures the METHOD, not the model. Make
        # that intent explicit and distinct from the per-study model verdicts.
        "intent": "how well the METHOD defends its claims (method-level, "
                  "distinct from the per-study model verdicts)",
        "per_study": per_study,
        "dimensions": dims,
        "score": {"gap": score[GAP], "warn": score[WARN], "ok": score[OK], "total": len(dims)},
        "summary": f"{score[OK]}/{len(dims)} investigation rigor dimensions addressed"
                   + (f" · {score[GAP]} gap(s)" if score[GAP] else ""),
    }

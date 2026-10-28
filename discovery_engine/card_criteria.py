"""Typed grading criteria for report cards.

A *criterion* says how to compare a measured axis against a reference, and what
verdict that earns. One dispatch (`grade_axis`) covers every axis type so the
organism-behavior card and the v1<->v2 equivalence card share one grader:

  - ``rel_tol``  — scalar within a relative tolerance of a reference scalar.
  - ``ttest``    — population stats: Welch's t-test of the candidate per-cell
                   values vs the reference per-cell values; a *significant*
                   difference is drift/mismatch. (Cell-level n, never timepoints.)
  - ``r2``       — vector (transcriptome/proteome): R^2 of candidate vs
                   reference ensemble-mean vector.
  - ``literature`` — scalar vs an EXPERIMENTAL reference: a band of measured
                   values (multiple sources) plus an optional first-principles
                   ``theoretical_max`` ceiling. Deviation from the measured band
                   grades within_tol/drift/mismatch; crossing the ceiling is a
                   mismatch *flagged as a first-principles violation* (the model
                   exceeds a stoichiometric/thermodynamic limit — wrong, not just
                   off).
  - ``pearson`` — vector concordance by log-log Pearson r (proteome/abundance
                   vs experiment); robust to a systematic scale offset, unlike
                   the identity-based ``r2``.
  - ``composition`` — a branch-point flux split (a composition on a simplex,
                   e.g. the G6P fate EMP/oxPPP/ED) vs a reference composition:
                   total-variation distance grades the routing, paired with a
                   closure residual (unaccounted node influx, expected small)
                   that guards against carbon leaving by an unmodeled route.
  - ``boolean``  — a behavioral assertion that must hold.
  - ``threshold_linear`` — a swept response curve fit to
                   ``y = max(0, slope·(x − x0))``: grades how LINEAR the rise
                   above the threshold is and how its SLOPE compares to a
                   reference curve. The threshold position ``x0`` is reported
                   but does not gate. The qualitative check is *reference-aware*:
                   the reference establishes that a response is expected over
                   the swept range, so its ABSENCE is the mismatch.

Verdicts use a 4-state band (adopted from the vEcoli<->v2ecoli report):
``within_tol`` (pass) / ``drift`` (amber warning) / ``mismatch`` (fail) /
``ungraded`` (no reference). Each grade returns a compact ``meter`` string and a
human-readable ``criterion`` string for the report.

Generic (no organism/sim_data imports); the canonical home is ``discovery_engine``
so any workspace's cards share one grader. The verdict vocabulary is sourced from
``discovery_engine.test_vocab`` (the single home for CANONICAL/COLOR/GLYPH/RANK).
"""
from __future__ import annotations

import math
from typing import Any

from discovery_engine.test_vocab import CANONICAL as VERDICTS

# Severity order matches pbg_v2ecoli/evaluators.py::_SEVERITY — used to take the
# worst-of across the sub-verdicts that make up a single axis grade.
_SEVERITY = {"mismatch": 3, "drift": 2, "within_tol": 1, "ungraded": 0}


def _band(value: float, good: float, warn: float, *, higher_is_better: bool) -> str:
    """Map a scalar to within_tol/drift/mismatch given good/warn thresholds."""
    if higher_is_better:
        if value >= good:
            return "within_tol"
        return "drift" if value >= warn else "mismatch"
    else:
        if value <= good:
            return "within_tol"
        return "drift" if value <= warn else "mismatch"


def _welch(a: list[float], b: list[float]) -> dict[str, float]:
    """Welch's t-test (unequal variance) p-value + Cohen's d, no scipy needed
    for the inputs but uses scipy for the t-distribution survival function."""
    from scipy import stats
    na, nb = len(a), len(b)
    res = stats.ttest_ind(a, b, equal_var=False)
    ma, mb = (sum(a) / na), (sum(b) / nb)
    # pooled sd for an effect-size readout
    va = sum((x - ma) ** 2 for x in a) / (na - 1) if na > 1 else 0.0
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1) if nb > 1 else 0.0
    sp = math.sqrt(((na - 1) * va + (nb - 1) * vb) / max(na + nb - 2, 1)) or 1.0
    return {"p": float(res.pvalue), "t": float(res.statistic),
            "cohens_d": (ma - mb) / sp, "delta_rel": (ma - mb) / mb if mb else 0.0}


def _r2(cand: list[float], ref: list[float]) -> float:
    """Coefficient of determination of cand vs ref (ref as the predictor line
    y=x). Computed in log10 space on positive entries (omics are log-normal);
    falls back to linear if too few positive points."""
    pairs = [(c, r) for c, r in zip(cand, ref) if c > 0 and r > 0]
    if len(pairs) < 3:
        pairs = list(zip(cand, ref))
        xs = [r for _, r in pairs]
        ys = [c for c, _ in pairs]
    else:
        xs = [math.log10(r) for _, r in pairs]
        ys = [math.log10(c) for c, _ in pairs]
    if len(xs) < 2:
        return 0.0
    my = sum(ys) / len(ys)
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - x) ** 2 for x, y in zip(xs, ys))  # residual vs identity y=x
    return 1.0 - ss_res / ss_tot if ss_tot else 1.0


def _fit_threshold_linear(x: list, y: list, floor: float) -> dict | None:
    """Fit a threshold-linear response ``y = max(0, slope·(x − x0))`` to a swept
    ``(x, y)`` curve — ``x`` the driver, ``y`` the response.

    Linear-regress the SUPRA-FLOOR points (``y > floor``) and read ``slope`` =
    regression slope, ``onset`` = the line's x-intercept (the threshold ``x0``),
    and ``r2`` = the coefficient of determination of that fit (how LINEAR the
    supra-threshold rise is — the shape signal). ``r2`` is trivially 1.0 with only
    2 supra points, so a consumer should treat linearity as assessable only at
    ``n_supra >= 3``. Returns ``{onset, slope, r2, n_supra}``; ``onset`` is
    ``None`` when there is no positive rise (slope <= 0). Returns ``None`` when
    fewer than 2 supra-floor points exist (no curve to fit). A proper hinge
    least-squares (using the sub-floor zeros as constraints) is a later
    refinement; ``onset`` from the supra-floor intercept is a sound v1 estimate.
    """
    pts = [(float(xi), float(yi)) for xi, yi in zip(x, y) if yi is not None and yi > floor]
    if len(pts) < 2:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx == 0:
        return None
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    slope = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 1.0
    if slope <= 0:
        return {"onset": None, "slope": slope, "r2": r2, "n_supra": n}
    onset = mx - my / slope  # x where the regression line y = my + slope·(x − mx) = 0
    return {"onset": onset, "slope": slope, "r2": r2, "n_supra": n}


def grade_axis(measured: dict | bool | None, criterion: dict) -> dict[str, Any]:
    """Grade one axis. ``measured`` is the card node for the axis; ``criterion``
    is the reference's spec for it. Returns
    ``{verdict, value, meter, criterion, detail}``.
    """
    ctype = criterion.get("type")

    if ctype == "status":
        # Verdict is carried by the measured node (pre-decided upstream, e.g. a
        # study test's recorded pass/fail). No numeric grading is done here.
        node = measured if isinstance(measured, dict) else {}
        v = node.get("verdict", "ungraded")
        if v not in VERDICTS:
            v = "ungraded"
        return {"verdict": v, "value": node.get("value"),
                "criterion_str": criterion.get("criterion_str", ""),
                "meter": node.get("meter", "—"), "detail": node.get("detail", {})}

    if ctype == "rel_tol":
        ref = criterion.get("reference")
        tol = criterion.get("tol_rel", 0.05)
        got = measured.get("mean") if isinstance(measured, dict) else measured
        if ref is None or got is None:
            return _ungraded(f"within {tol:.0%}")
        rel = abs(got - ref) / abs(ref) if ref else float("inf")
        verdict = _band(rel, tol, 2 * tol, higher_is_better=False)
        return {"verdict": verdict, "value": got,
                "criterion_str": f"within {tol:.0%} of {ref:.4g}",
                "meter": f"Δ = {got - ref:+.4g} ({rel:.1%} rel)", "detail": {"rel": rel}}

    if ctype == "ttest":
        # Magnitude-first: the verdict bands on the relative mean shift |Δ|, with
        # the Welch p-value as a significance GUARD (a large shift is only a
        # mismatch if it is also statistically significant; a large-but-noisy
        # shift stays a drift warning). This keeps a meaningful graded band — a
        # pure-p-value gate goes near-binary at small n (the t-test detects
        # *difference*, not *magnitude*).
        ref_vals = criterion.get("ref_values")
        within_pct = criterion.get("within_pct", 0.05)
        mismatch_pct = criterion.get("mismatch_pct", 0.10)
        p_min = criterion.get("p_min", 0.05)
        inactive_eps = criterion.get("inactive_eps", 1e-6)
        cand_vals = measured.get("values") if isinstance(measured, dict) else None
        if not ref_vals or not cand_vals or len(cand_vals) < 2 or len(ref_vals) < 2:
            return _ungraded(f"|Δ| < {within_pct:.0%}")
        ma, mb = sum(cand_vals) / len(cand_vals), sum(ref_vals) / len(ref_vals)
        # Near-zero reference (e.g. an acetate sentinel): a relative-shift t-test
        # is ill-defined (Δrel divides by ~0). Handle by activity instead — if
        # the reference is inactive (~0), the only question is whether the
        # candidate stayed inactive (pass) or became active (the sentinel
        # tripped — overflow onset = mismatch).
        if abs(mb) < inactive_eps:
            tripped = abs(ma) >= inactive_eps
            return {"verdict": "mismatch" if tripped else "within_tol", "value": ma,
                    "criterion_str": f"stays inactive (|mean| < {inactive_eps:g})",
                    "meter": (f"became active: {ma:+.3g} (ref ≈ 0)" if tripped
                              else f"both ≈ 0 (ref {mb:+.2g}, meas {ma:+.2g})"),
                    "detail": {"inactive_ref": True, "tripped": tripped}}
        w = _welch(cand_vals, ref_vals)
        eff = abs(w["delta_rel"])
        if eff < within_pct:
            verdict = "within_tol"
        elif eff < mismatch_pct:
            verdict = "drift"
        else:
            verdict = "mismatch" if w["p"] < p_min else "drift"
        return {"verdict": verdict, "value": (measured or {}).get("mean"),
                "criterion_str": (f"|Δ| < {within_pct:.0%} (drift {within_pct:.0%}–"
                                  f"{mismatch_pct:.0%}; mismatch >{mismatch_pct:.0%} & p<{p_min})"),
                "meter": f"Δ = {w['delta_rel']:+.1%} · p = {w['p']:.3f} · d = {w['cohens_d']:+.2f}",
                "detail": w}

    if ctype == "r2":
        ref_vec = criterion.get("ref_vector")
        r2_min = criterion.get("r2_min", 0.99)
        r2_drift = criterion.get("r2_drift", 0.95)
        cand_vec = measured.get("vector") if isinstance(measured, dict) else None
        if not ref_vec or not cand_vec:
            return _ungraded(f"R² ≥ {r2_min}")
        r2 = _r2(cand_vec, ref_vec)
        verdict = _band(r2, r2_min, r2_drift, higher_is_better=True)
        n = sum(1 for c, r in zip(cand_vec, ref_vec) if c > 0 and r > 0)
        return {"verdict": verdict, "value": r2,
                "criterion_str": f"log-log R² ≥ {r2_min} ({n} genes)",
                "meter": f"R² = {r2:.4f}", "detail": {"r2": r2, "n": n}}

    if ctype == "flux_scatter":
        # Whole exchange-flux vector vs the pinned reference. Two failure modes:
        # (1) QUALITATIVE — an exchange appears (ref~0 -> active) or disappears
        # (active -> ~0); any such change is a mismatch regardless of R² (the
        # cell started/stopped using a metabolite). (2) QUANTITATIVE — matched
        # (both active) fluxes drift; graded by linear R² vs identity.
        ref_vec = criterion.get("ref_vector")
        eps = criterion.get("active_eps", 1e-6)
        # Significance floor for the QUALITATIVE appeared/disappeared verdict.
        # `active_eps` (~1e-6) is the detection floor for pairing/R²; many FBA
        # exchanges jitter just across it at ~1e-6 with CV > 100% (fitting slack,
        # not metabolism). A flip whose active-side magnitude is below `qual_eps`
        # is reported but does NOT count toward mismatch — only a flux that turns
        # on/off at a metabolically meaningful level is a qualitative change.
        qual_eps = criterion.get("qual_eps", 1e-3)
        r2_min = criterion.get("r2_min", 0.99)
        r2_drift = criterion.get("r2_drift", 0.95)
        # ``qualitative=False`` (internal fluxes): a near-zero flux is a low value,
        # not a categorical on/off, so the appeared/disappeared gate is dropped —
        # every pair active on either side counts toward the identity-R², and the
        # verdict comes from R² alone (no "lost"/"appeared" callouts).
        qualitative = criterion.get("qualitative", True)
        cand_vec = measured.get("vector") if isinstance(measured, dict) else None
        if not ref_vec or not cand_vec:
            return _ungraded(f"R² ≥ {r2_min}, no appeared/disappeared")
        appeared, disappeared, matched, sub_floor = [], [], [], []
        for i, (c, r) in enumerate(zip(cand_vec, ref_vec)):
            ca, ra = abs(c) > eps, abs(r) > eps
            if not qualitative:
                if ca or ra:
                    matched.append((c, r))
            elif ca and ra:
                matched.append((c, r))
            elif ca and not ra:  # appeared: active side is the candidate
                (appeared if abs(c) >= qual_eps else sub_floor).append(i)
            elif ra and not ca:  # disappeared: active side is the reference
                (disappeared if abs(r) >= qual_eps else sub_floor).append(i)
        # linear R² vs identity on matched (signed) pairs
        if len(matched) >= 2:
            ys = [c for c, _ in matched]
            my = sum(ys) / len(ys)
            ss_tot = sum((c - my) ** 2 for c, _ in matched)
            ss_res = sum((c - r) ** 2 for c, r in matched)
            r2 = 1.0 - ss_res / ss_tot if ss_tot else 1.0
        else:
            r2 = 1.0
        n_qual = len(appeared) + len(disappeared)
        if n_qual:
            verdict = "mismatch"
        else:
            verdict = _band(r2, r2_min, r2_drift, higher_is_better=True)
        sub = f" · {len(sub_floor)} sub-floor" if sub_floor else ""
        if not qualitative:
            return {"verdict": verdict, "value": r2,
                    "criterion_str": f"identity R² ≥ {r2_min} (drift ≥ {r2_drift})",
                    "meter": f"R² = {r2:.4f} · {len(matched)} reactions",
                    "detail": {"r2": r2, "n_matched": len(matched)}}
        return {"verdict": verdict, "value": r2,
                "criterion_str": (f"R² ≥ {r2_min} on matched fluxes; "
                                  f"no appeared/disappeared ≥ {qual_eps:g}"),
                "meter": (f"R² = {r2:.4f} · {len(matched)} matched · "
                          f"{len(appeared)} appeared · {len(disappeared)} lost{sub}"),
                "detail": {"r2": r2, "appeared": appeared,
                           "disappeared": disappeared, "sub_floor": sub_floor,
                           "n_matched": len(matched), "qual_eps": qual_eps}}

    if ctype == "threshold_linear":
        # A swept response graded SHAPE-FIRST against a reference curve of the same
        # shape: the response is ~0 below a threshold, then rises ~linearly. Two
        # primary readouts (worst-of):
        #   (1) LINEARITY — R² of the supra-threshold rise (is it "0-then-linear"?).
        #   (2) SLOPE — relative |Δ| of the rise slope vs the reference.
        # The threshold position is SECONDARY: graded for the record but NOT
        # included in the verdict, because it is the feature most likely to shift
        # with conditions that leave the shape intact.
        # The qualitative check is REFERENCE-AWARE: the reference establishes that
        # a rise is expected above its threshold, so ABSENCE of the rise across a
        # sweep that spans past it is the mismatch — not a pass.
        #
        # Worked example (the study this criterion was written for): acetate
        # overflow graded as a dimensionless carbon yield Y_ac = (2·acetate)/
        # (6·glucose) vs glucose uptake rate, against the Vemuri 2006 chemostat
        # curve — exercised by the `overflow-acetate-vs-growth` study in the
        # `metabolism-overflow` investigation. Nothing about the criterion is
        # specific to that phenomenon.
        #
        #   measured  = {x: [driver], y: [response], ...}
        #   criterion = {ref_x, ref_y, active_eps, lin_good/lin_warn,
        #                slope_tol/slope_warn, onset_tol/onset_warn (reported)}
        ref_x, ref_y = criterion.get("ref_x"), criterion.get("ref_y")
        mx = measured.get("x") if isinstance(measured, dict) else None
        my = measured.get("y") if isinstance(measured, dict) else None
        # Response floor separating "flat" from "rising". The 5e-3 default assumes
        # a NORMALIZED, dimensionless y (e.g. a yield fraction); callers whose y
        # carries units must set this explicitly.
        floor = criterion.get("active_eps", 5e-3)
        lin_good = criterion.get("lin_good", 0.90)        # R² of the supra-threshold rise
        lin_warn = criterion.get("lin_warn", 0.70)
        slope_good = criterion.get("slope_tol", 0.30)     # |Δslope| relative
        slope_warn = criterion.get("slope_warn", 0.60)
        # Threshold tolerances are RELATIVE to the reference threshold, so the
        # criterion is invariant to the units of x. Reported, never gating.
        onset_good = criterion.get("onset_tol", 0.25)
        onset_warn = criterion.get("onset_warn", 0.50)
        if not ref_x or not ref_y or not mx or not my:
            return _ungraded("threshold-linear shape + slope vs reference curve")
        ref_fit = _fit_threshold_linear(ref_x, ref_y, floor)
        if ref_fit is None or ref_fit["onset"] is None:
            return _ungraded("reference is not threshold-linear")
        onset_ref, slope_ref = ref_fit["onset"], ref_fit["slope"]
        cstr = (f"shape-first: linear-rise R² ≥ {lin_good} (drift ≥ {lin_warn}); "
                f"slope within {slope_good:.0%} (drift {slope_warn:.0%}); "
                f"threshold reported (secondary)")
        # Reference-aware gate (1): must sweep past the reference threshold to
        # assess a shape you never entered.
        if max(mx) <= onset_ref:
            return {"verdict": "ungraded",
                    "value": {"slope": None, "slope_ref": slope_ref, "onset_ref": onset_ref},
                    "criterion_str": cstr,
                    "meter": f"sweep max x={max(mx):.3g} ≤ threshold x_ref={onset_ref:.3g} — extend sweep",
                    "detail": {"reason": "sweep below reference threshold", "onset_ref": onset_ref}}
        fit = _fit_threshold_linear(mx, my, floor)
        # Reference-aware gate (2): spans past the threshold but stays flat → the
        # expected response is absent → wrong shape → mismatch.
        if fit is None or fit["onset"] is None:
            return {"verdict": "mismatch",
                    "value": {"slope": 0.0, "slope_ref": slope_ref, "onset_ref": onset_ref},
                    "criterion_str": cstr,
                    "meter": (f"no threshold-linear rise (y ≤ {floor:g} through "
                              f"x={max(mx):.3g}; reference rises)"),
                    "detail": {"reason": "no rise in model (flat) — wrong shape",
                               "onset_ref": onset_ref, "slope_ref": slope_ref}}
        onset_m, slope_m, r2_m, n_supra = fit["onset"], fit["slope"], fit["r2"], fit["n_supra"]
        # (1) LINEARITY — only assessable with ≥3 supra-threshold points (R² is
        # trivially 1.0 with 2); otherwise report it but don't gate on it.
        v_lin = _band(r2_m, lin_good, lin_warn, higher_is_better=True) if n_supra >= 3 else "ungraded"
        # (2) SLOPE — relative difference of the rise slope.
        d_slope = abs(slope_m - slope_ref) / abs(slope_ref) if slope_ref else float("inf")
        v_slope = _band(d_slope, slope_good, slope_warn, higher_is_better=False)
        # THRESHOLD — secondary: graded for the record, NOT included in the verdict.
        d_onset = abs(onset_m - onset_ref) / abs(onset_ref) if onset_ref else float("inf")
        v_onset = _band(d_onset, onset_good, onset_warn, higher_is_better=False)
        primary = [v for v in (v_lin, v_slope) if v != "ungraded"]
        verdict = max(primary, key=_SEVERITY.get) if primary else "ungraded"
        return {"verdict": verdict,
                "value": {"slope": slope_m, "slope_ref": slope_ref, "r2": r2_m,
                          "onset": onset_m, "onset_ref": onset_ref},
                "criterion_str": cstr,
                "meter": (f"rise R²={r2_m:.2f} · slope {slope_m:.3g} "
                          f"(ref {slope_ref:.3g}, {d_slope:+.0%}) · threshold {onset_m:.3g} "
                          f"(ref {onset_ref:.3g}, {d_onset:+.0%}, 2°)"),
                "detail": {"slope": slope_m, "slope_ref": slope_ref, "d_slope_rel": d_slope,
                           "r2_linear": r2_m, "onset": onset_m, "onset_ref": onset_ref,
                           "d_onset_rel": d_onset, "lin_verdict": v_lin,
                           "slope_verdict": v_slope, "onset_verdict": v_onset,
                           "onset_gating": False, "n_supra": n_supra}}

    if ctype == "literature":
        # Grade a scalar model value against an EXPERIMENTAL reference: a band of
        # measured values (multiple curated sources) plus an optional
        # first-principles ceiling. Two deliberately differentiated regimes:
        #   - deviation from the measured band -> within_tol / drift / mismatch
        #     (a tracked gap; adequacy is a judgment call).
        #   - crossing a ``theoretical_max`` -> mismatch, flagged as a
        #     first-principles violation (the model exceeds a stoichiometric /
        #     thermodynamic limit — wrong, no judgment required).
        got = measured.get("mean") if isinstance(measured, dict) else measured
        meas = criterion.get("measured") or []
        tmax = criterion.get("theoretical_max")
        tol = criterion.get("tol_rel", 0.10)
        if got is None or not meas:
            return _ungraded("vs measured literature")
        lo, hi = min(meas), max(meas)
        mid = sum(meas) / len(meas)
        if lo * (1 - tol) <= got <= hi * (1 + tol):
            band = "within_tol"
        elif lo * (1 - 2 * tol) <= got <= hi * (1 + 2 * tol):
            band = "drift"
        else:
            band = "mismatch"
        violates = tmax is not None and got > tmax
        detail = {"measured_lo": lo, "measured_hi": hi, "measured_mid": mid,
                  "n_sources": len(meas), "theoretical_max": tmax,
                  "first_principles_violation": bool(violates),
                  "rel_to_mid": (got - mid) / mid if mid else None}
        if violates:
            return {"verdict": "mismatch", "value": got,
                    "criterion_str": f"≤ theoretical max {tmax:.3g}; within measured {lo:.3g}–{hi:.3g}",
                    "meter": f"{got:.3g} > theoretical max {tmax:.3g} — first-principles violation",
                    "detail": detail}
        return {"verdict": band, "value": got,
                "criterion_str": (f"within measured {lo:.3g}–{hi:.3g} (±{tol:.0%})"
                                  + (f"; ≤ max {tmax:.3g}" if tmax is not None else "")),
                "meter": (f"{got:.4g} vs measured {lo:.3g}–{hi:.3g} "
                          f"(Δmid {((got - mid) / mid if mid else 0):+.0%})"),
                "detail": detail}

    if ctype == "pearson":
        # Concordance of a candidate vector vs a reference vector by log-log
        # Pearson r — the literature convention for proteome/abundance
        # comparisons. Unlike identity-R² (the ``r2`` criterion, right for
        # same-lineage v1↔v2 omics), Pearson r is robust to a systematic scale
        # offset between model and experiment, which is expected for absolute
        # protein copy numbers.
        ref_vec = criterion.get("ref_vector")
        r_min = criterion.get("r_min", 0.9)
        r_drift = criterion.get("r_drift", 0.7)
        cand_vec = measured.get("vector") if isinstance(measured, dict) else None
        if not ref_vec or not cand_vec:
            return _ungraded(f"log-log Pearson r ≥ {r_min}")
        pairs = [(c, r) for c, r in zip(cand_vec, ref_vec) if c > 0 and r > 0]
        if len(pairs) < 3:
            return _ungraded(f"log-log Pearson r ≥ {r_min}")
        xs = [math.log10(r) for _, r in pairs]
        ys = [math.log10(c) for c, _ in pairs]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        r = sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0
        verdict = _band(r, r_min, r_drift, higher_is_better=True)
        return {"verdict": verdict, "value": r,
                "criterion_str": f"log-log Pearson r ≥ {r_min} (drift ≥ {r_drift}; {n} genes)",
                "meter": f"r = {r:.3f}", "detail": {"r": r, "n": n}}

    if ctype == "composition":
        # Grade a branch-point flux split — a COMPOSITION on a simplex (e.g. the
        # glucose-6-P fate: EMP / oxidative-PPP / ED) — against a reference
        # composition, paired with a closure residual. Two readouts, worst-of
        # (mirrors flux_scatter's qualitative+quantitative gate):
        #   (1) ROUTING — total-variation distance TV = ½·Σ|p−q| between the
        #       model and reference compositions (each renormalized over the
        #       known branches). TV ∈ [0,1] reads directly as "fraction of flux
        #       misrouted"; zero-safe (an absent branch, e.g. ED≈0, is fine).
        #   (2) CLOSURE residual — the fraction of node influx NOT accounted by
        #       the known branches. Expected small and positive (a biomass
        #       drain). A residual past its band means carbon leaves the node by
        #       an unmodeled/unexpected route (or the branch set over-draws), and
        #       caps the verdict regardless of how well the known branches match
        #       — the spurious-route guard.
        #   measured:  {"branches": {name: flux}, "influx": flux_in}
        #   criterion: {"type":"composition", "ref_fractions": {name: frac},
        #               "tv_good","tv_warn","residual_max","residual_warn"}
        branches = measured.get("branches") if isinstance(measured, dict) else None
        influx = measured.get("influx") if isinstance(measured, dict) else None
        ref_fr = criterion.get("ref_fractions")
        if not branches or not ref_fr or not influx:
            return _ungraded("flux composition vs reference")
        names = list(ref_fr)
        ref_sum = sum(ref_fr.values()) or 1.0
        ref_norm = {n: ref_fr[n] / ref_sum for n in names}
        of_in = {n: branches.get(n, 0.0) / influx for n in names}
        catab = sum(of_in.values())
        residual = 1.0 - catab
        mod_norm = ({n: of_in[n] / catab for n in names} if catab > 0
                    else {n: 0.0 for n in names})
        tv = 0.5 * sum(abs(mod_norm[n] - ref_norm[n]) for n in names)
        tv_good = criterion.get("tv_good", 0.05)
        tv_warn = criterion.get("tv_warn", 0.15)
        routing = _band(tv, tv_good, tv_warn, higher_is_better=False)
        res_max = criterion.get("residual_max", 0.05)
        res_warn = criterion.get("residual_warn", 0.10)
        resid_v = _band(abs(residual), res_max, res_warn, higher_is_better=False)
        rank = {"within_tol": 0, "drift": 1, "mismatch": 2, "ungraded": 3}
        verdict = max(routing, resid_v, key=lambda v: rank[v])

        def _pct(d):
            return ", ".join(f"{n} {100 * d[n]:.0f}%" for n in names)

        return {"verdict": verdict, "value": tv,
                "criterion_str": (f"routing TV ≤ {tv_good:.2f} (drift ≤ {tv_warn:.2f}); "
                                  f"closure residual ≤ {res_max:.0%}"),
                "meter": (f"TV = {tv:.3f} · model [{_pct(mod_norm)}] vs "
                          f"ref [{_pct(ref_norm)}] · residual {residual:+.0%}"),
                "detail": {"tv": tv, "residual": residual,
                           "model_fractions": mod_norm, "ref_fractions": ref_norm,
                           "routing_verdict": routing, "residual_verdict": resid_v,
                           "branch_names": names}}

    if ctype == "boolean":
        ok = bool(measured) if measured is not None else None
        if ok is None:
            return _ungraded("assertion holds")
        return {"verdict": "within_tol" if ok else "mismatch", "value": ok,
                "criterion_str": "assertion holds", "meter": "pass" if ok else "FAIL",
                "detail": {}}

    return _ungraded("(unknown criterion)")


def _ungraded(criterion_str: str) -> dict[str, Any]:
    return {"verdict": "ungraded", "value": None, "criterion_str": criterion_str,
            "meter": "—", "detail": {}}

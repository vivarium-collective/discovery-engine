"""Evidence authority — who owns which study field, and a guard that an applied
(AI- or human-authored) artifact never rewrites a *measured* or *derived* value.

The map's honesty rule, made mechanical. A filler supplies interpretation (prose),
decisions and proposals; the *measurement* — a finding's ``evidence.observed`` and a run's
``computed_outcomes`` — is the engine's, produced by grading real runs. An apply step must
never let an authored/AI artifact overwrite it. The native apply handlers already write only
their own prose/test slots, so this is defense-in-depth that also covers future handlers:
snapshot the measured fields before the write, and raise if any changed after.

Pure; AI-free.
"""
from __future__ import annotations

MEASURED = "measured"
AUTHORED = "authored"
DERIVED = "derived"
INTERPRETED = "interpreted"

# Study field (dotted; ``[]`` = per-list-item) -> its provenance owner. An authored/AI
# apply may touch AUTHORED / INTERPRETED fields; it may NEVER change a MEASURED or DERIVED
# one (those are :data:`PROTECTED`).
OWNERSHIP = {
    "findings[].evidence.observed": MEASURED,
    "findings[].evidence.measured_value": MEASURED,
    "runs[].computed_outcomes": DERIVED,
    "findings[].expected.range": AUTHORED,
    "findings[].statement": INTERPRETED,
    "findings[].explanation": INTERPRETED,
    "findings[].status": INTERPRETED,
}
PROTECTED = (MEASURED, DERIVED)

_MISSING = object()


def _finding_key(f: object):
    return (f.get("id") or f.get("name")) if isinstance(f, dict) else None


def measured_fingerprint(study: dict) -> dict:
    """The MEASURED + DERIVED values an apply step must leave unchanged, keyed by a stable
    path. Pure read; tolerant of missing blocks (returns ``{}`` for a study with no evidence)."""
    study = study or {}
    fp: dict = {}
    for f in study.get("findings") or []:
        key = _finding_key(f)
        if key is None:
            continue
        ev = f.get("evidence") or {}
        if "observed" in ev:
            fp[f"findings[{key}].evidence.observed"] = ev.get("observed")
        if "measured_value" in ev:
            fp[f"findings[{key}].evidence.measured_value"] = ev.get("measured_value")
    for r in study.get("runs") or []:
        if not isinstance(r, dict):
            continue
        rk = r.get("name") or r.get("run_id") or r.get("id")
        if rk is None:
            continue
        if "computed_outcomes" in r:
            fp[f"runs[{rk}].computed_outcomes"] = r.get("computed_outcomes")
    return fp


def assert_no_authority_leak(before_study: dict, after_study: dict, *, writer: str = "ai") -> None:
    """Raise ``ValueError`` if any measured/derived value changed between ``before_study``
    and ``after_study`` (a writer that is not the measurement engine rewrote measured data).
    An added or removed measured key also counts as a leak. Pure."""
    before = measured_fingerprint(before_study)
    after = measured_fingerprint(after_study)
    changed = sorted(k for k in set(before) | set(after)
                     if before.get(k, _MISSING) != after.get(k, _MISSING))
    if changed:
        raise ValueError(
            f"evidence authority leak: a {writer!r} write changed measured/derived "
            f"field(s) {changed} — interpretation is never evidence")

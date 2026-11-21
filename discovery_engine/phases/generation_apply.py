"""Apply a filled GenerationResult through the engine's canonical write op.

The engine EMITS GenerationRequests (``generation_emit``); once a filler (a
``/viva-*`` skill, or an AI filler behind ``$VIVA_FILLER_CMD``) has produced the
artifact, it is APPLIED here by routing to the EXISTING invariant-preserving
write op for the request's kind — ``record_resolution`` for a decision, the
finding writers for an interpretation, and so on. The generation (the artifact)
happens in the filler across the seam; this module only routes and writes, so the
engine stays pure and LLM-free. Phase 3 of the AI-drivable discovery cycle spec
(human fillers + apply routing).

Wired kinds: ``resolve_decision``, ``interpret_finding``, ``author_tests``,
``explain``. Others return ``queued_for_review`` (no apply op yet) rather than
raising, so a runner can drain the wired work and leave the rest for review.
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path

from discovery_engine.phases.generation import GenerationRequest, GenerationResult


def _inv_path(ws_root, inv_slug: str) -> Path:
    from discovery_engine.investigation.import_ import investigations_dir
    return investigations_dir(Path(ws_root)) / inv_slug / "investigation.yaml"


def _study_dir(ws_root, inv_slug: str, study: str) -> Path | None:
    from discovery_engine.investigation.import_ import investigations_dir, studies_dir
    flat = studies_dir(Path(ws_root)) / study
    if (flat / "study.yaml").exists():
        return flat
    nested = investigations_dir(Path(ws_root)) / inv_slug / "studies" / study
    return nested if (nested / "study.yaml").exists() else None


def _apply_finding_interpretation(sdir: Path, finding_id: str, art: dict) -> list[str]:
    """Write the authored interpretation (statement / explanation / status) onto
    the named finding, comment-preserving + atomic. Only those prose slots — never
    the numeric evidence, which is ``finding_observations``' job."""
    from ruamel.yaml import YAML

    from discovery_engine.study import io as study_io
    sp = sdir / "study.yaml"
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    doc = y.load(sp.read_text(encoding="utf-8"))
    target = next((f for f in (doc.get("findings") or [])
                   if isinstance(f, dict) and (f.get("id") or f.get("name")) == finding_id), None)
    if target is None:
        raise ValueError(f"no finding {finding_id!r} in {sp}")
    applied: list[str] = []
    for key in ("statement", "explanation", "status"):
        val = art.get(key)
        if isinstance(val, str) and val.strip():
            target[key] = val
            applied.append(f"findings[{finding_id}].{key}")
    if not applied:
        raise ValueError("interpret_finding artifact set no statement/explanation/status")
    buf = StringIO()
    y.dump(doc, buf)
    study_io.atomic_write(sp, buf.getvalue())
    return applied


def _apply_author_tests(sdir: Path, art: dict) -> list[str]:
    """Append the authored behavior test(s) onto the study's ``behavior_tests``
    list, comment-preserving + atomic. Fill-absent BY TEST NAME: an authored test
    whose name already exists is skipped (never duplicated, never clobbered), so a
    second apply of the same artifact adds nothing (idempotent). Returns the list of
    newly-appended test names; an artifact that names no tests raises ``ValueError``."""
    from ruamel.yaml import YAML

    from discovery_engine.study import io as study_io
    tests = art.get("tests") or art.get("behavior_tests") or []
    if isinstance(tests, dict):          # a single authored test, un-listed
        tests = [tests]
    authored = [t for t in tests
                if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"].strip()]
    if not authored:
        raise ValueError("author_tests artifact set no named behavior_tests")
    sp = sdir / "study.yaml"
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    doc = y.load(sp.read_text(encoding="utf-8"))
    if not isinstance(doc.get("behavior_tests"), list):
        doc["behavior_tests"] = []
    existing = doc["behavior_tests"]
    have = {t.get("name") for t in existing if isinstance(t, dict)}
    applied: list[str] = []
    for t in authored:
        name = t["name"]
        if name in have:
            continue                     # fill-absent: never duplicate / clobber
        existing.append(t)
        have.add(name)
        applied.append(f"behavior_tests[{name}]")
    if not applied:
        return []                        # idempotent: everything already present, no write
    buf = StringIO()
    y.dump(doc, buf)
    study_io.atomic_write(sp, buf.getvalue())
    return applied


def _apply_explanation(sdir: Path, finding_id: str, art: dict) -> list[str]:
    """Write the authored explanation prose onto the named finding's
    ``explanation``, comment-preserving + atomic. Derive-first: an empty/missing
    explanation is filled; a non-empty existing one is NOT clobbered (returns an
    empty list, no write). An artifact carrying no prose raises ``ValueError``."""
    from ruamel.yaml import YAML

    from discovery_engine.study import io as study_io
    prose = art.get("explanation") or art.get("prose")
    if not isinstance(prose, str) or not prose.strip():
        raise ValueError("explain artifact set no explanation prose")
    sp = sdir / "study.yaml"
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    doc = y.load(sp.read_text(encoding="utf-8"))
    target = next((f for f in (doc.get("findings") or [])
                   if isinstance(f, dict) and (f.get("id") or f.get("name")) == finding_id), None)
    if target is None:
        raise ValueError(f"no finding {finding_id!r} in {sp}")
    existing = target.get("explanation")
    if isinstance(existing, str) and existing.strip():
        return []                        # derive-first: never clobber an authored explanation
    target["explanation"] = prose
    buf = StringIO()
    y.dump(doc, buf)
    study_io.atomic_write(sp, buf.getvalue())
    return [f"findings[{finding_id}].explanation"]


# investigation.yaml block each registry skill's recorder writes to.
_SKILL_BLOCKS = {"hypothesize": "hypothesis_proposals", "critique": "critique_reports",
                 "acquire_model": "model_acquisition_proposals"}


def _filler_id(result: GenerationResult) -> str:
    f = result.filler or {}
    return f.get("id") or f.get("kind") or "filler"


def _require_study_dir(art: dict, kind: str, *, ws_root, inv_slug: str) -> Path:
    """Resolve the artifact's ``study`` to its dir, or raise — the shared preamble
    of every study-writing handler."""
    study = art.get("study")
    if not study:
        raise ValueError(f"{kind} artifact needs {{study}}")
    sdir = _study_dir(ws_root, inv_slug, str(study))
    if sdir is None:
        raise ValueError(f"no study.yaml for {study!r}")
    return sdir


# --- per-kind handlers -------------------------------------------------------
# Every handler has the SAME signature — ``(art, *, ws_root, inv_slug, request,
# result) -> {status, applied_to, ...}`` — and raises ``ValueError`` on a
# malformed artifact. ``apply_result`` is a thin dispatcher over the table below;
# wiring a new kind is one entry, not another ``if`` branch.

def _apply_resolve_decision(art, *, ws_root, inv_slug, request, result):
    from discovery_engine.discover import record_resolution
    rationale = (art.get("rationale") or result.rationale or "").strip()
    if not rationale:
        raise ValueError("resolve_decision artifact needs a rationale")
    rec = record_resolution(_inv_path(ws_root, inv_slug), "open_decision",
                            request.target, rationale, by=_filler_id(result))
    return {"status": "applied", "applied_to": [f"{inv_slug}/resolutions"], "detail": rec}


def _apply_interpret_finding(art, *, ws_root, inv_slug, request, result):
    finding_id = art.get("finding") or art.get("finding_id")
    if not finding_id:
        raise ValueError("interpret_finding artifact needs {study, finding}")
    sdir = _require_study_dir(art, "interpret_finding", ws_root=ws_root, inv_slug=inv_slug)
    return {"status": "applied",
            "applied_to": _apply_finding_interpretation(sdir, str(finding_id), art)}


def _apply_author_tests_kind(art, *, ws_root, inv_slug, request, result):
    sdir = _require_study_dir(art, "author_tests", ws_root=ws_root, inv_slug=inv_slug)
    return {"status": "applied", "applied_to": _apply_author_tests(sdir, art)}


def _apply_explain_kind(art, *, ws_root, inv_slug, request, result):
    finding_id = art.get("finding") or art.get("finding_id")
    if not finding_id:
        raise ValueError("explain artifact needs {study, finding}")
    sdir = _require_study_dir(art, "explain", ws_root=ws_root, inv_slug=inv_slug)
    return {"status": "applied",
            "applied_to": _apply_explanation(sdir, str(finding_id), art)}


def _apply_registry_skill(art, *, ws_root, inv_slug, request, result):
    """Handler for the SkillRegistry-backed kinds (hypothesize / critique /
    acquire_model): coerce the artifact through the kind's honesty gate, then
    record it into its investigation.yaml proposal block."""
    from discovery_engine.agents.skill_registry import default_registry  # lazy: avoid cycles
    sk = default_registry().get(request.kind)
    block = _SKILL_BLOCKS.get(sk.kind, f"{sk.kind}_proposals")
    coerced = sk.coerce(art)              # ValueError propagates on a malformed artifact
    rec = sk.record(_inv_path(ws_root, inv_slug), coerced)
    return {"status": "applied", "applied_to": [f"{inv_slug}/{block}"], "detail": rec}


# The native (study- and decision-writing) handlers, keyed by request kind. The
# registry-backed kinds are resolved lazily in ``_handler_for`` so this module
# never imports the skill writers at import time (cycle-safe).
_NATIVE_HANDLERS = {
    "resolve_decision": _apply_resolve_decision,
    "interpret_finding": _apply_interpret_finding,
    "author_tests": _apply_author_tests_kind,
    "explain": _apply_explain_kind,
}


def _handler_for(kind: str):
    """The single apply handler for ``kind`` — a native one, else the registry
    adapter when the SkillRegistry knows the kind, else ``None`` (not wired)."""
    native = _NATIVE_HANDLERS.get(kind)
    if native is not None:
        return native
    from discovery_engine.agents.skill_registry import default_registry  # lazy: avoid cycles
    if default_registry().get(kind) is not None:
        return _apply_registry_skill
    return None


def _no_handler_outcome(kind: str) -> dict:
    """The ``queued_for_review`` outcome for a kind with no apply handler.

    A KNOWN generation kind with no handler is a DELIBERATE *CLI-verb* kind
    (``visualize`` / ``propose_study``): it is authored through its ``/viva-*``
    skill — which renders a figure / seeds a study, things the pure engine does
    not do — not auto-applied here. Say so and name the skill (``next_action``),
    so a drive's "queued" outcome reads as "ready to apply with /viva:viz", not a
    missing-feature TODO (the "...not wired yet" wording that misled a reviewer
    into thinking apply was an unimplemented stub). An UNKNOWN kind is a real gap.
    """
    from discovery_engine.phases.generation import KINDS, skill_for
    if kind in KINDS:
        skill = skill_for(kind)
        return {
            "status": "queued_for_review", "applied_to": [],
            "reason": (f"kind {kind!r} is authored via its /{skill} skill, not "
                       f"auto-applied by the engine — apply it with /{skill}"),
            "next_action": {"type": "skill", "skill": skill, "kind": kind},
        }
    return {"status": "queued_for_review", "applied_to": [],
            "reason": f"no apply handler for unknown kind {kind!r}"}


# Kinds whose apply op writes a study.yaml — guarded for evidence-authority leaks.
_STUDY_WRITING_KINDS = ("interpret_finding", "explain", "author_tests")


def _target_study(ws_root, inv_slug: str, art: dict):
    """The ``(sdir, study_dict)`` a study-writing kind targets, or ``None`` — used to
    snapshot the target study's measured fields before/after an apply (authority guard)."""
    study = art.get("study")
    if not study:
        return None
    sdir = _study_dir(ws_root, inv_slug, str(study))
    if sdir is None:
        return None
    from discovery_engine.study import io as study_io
    return sdir, (study_io.load_yaml(sdir / "study.yaml") or {})


def apply_result(request: GenerationRequest, result: GenerationResult, *,
                 ws_root, inv_slug: str) -> dict:
    """Route ``(request, result)`` to the canonical write op for ``request.kind``.

    One dispatch over :data:`_NATIVE_HANDLERS` + the SkillRegistry (via
    :func:`_handler_for`): every applicable kind resolves to one uniform handler.
    Returns ``{status, applied_to, ...}`` — ``status`` is ``applied`` when a wired
    op ran, else ``queued_for_review``. Raises ``ValueError`` on a malformed
    artifact for a wired kind. Pure routing over existing writers; no generation.

    A study-writing kind is wrapped by the evidence-authority guard: the target study's
    measured/derived fields are snapshotted before the write and re-checked after, so an
    applied artifact can never overwrite a finding's ``observed`` or a run's
    ``computed_outcomes`` (a leak raises ``ValueError`` → recorded as an apply error)."""
    handler = _handler_for(request.kind)
    if handler is None:
        return _no_handler_outcome(request.kind)
    art = result.artifact or {}
    guard = _target_study(ws_root, inv_slug, art) if request.kind in _STUDY_WRITING_KINDS else None
    out = handler(art, ws_root=ws_root, inv_slug=inv_slug, request=request, result=result)
    if guard is not None:
        from discovery_engine.phases.evidence_authority import assert_no_authority_leak
        from discovery_engine.study import io as study_io
        sdir, before = guard
        after = study_io.load_yaml(sdir / "study.yaml") or {}
        assert_no_authority_leak(before, after)
    return out


def run_requests(requests, *, fill, ws_root, inv_slug: str, budget=None,
                 apply_cli_verb=None) -> list[dict]:
    """Dispatch typed GenerationRequests to a filler and apply the auto-gated ones.

    ``fill`` is the injected filler seam — ``fill(request) -> GenerationResult | None``
    (a ``/viva-*`` skill wrapper, the ``$VIVA_FILLER_CMD`` subprocess, or a test
    stub). It is the ONLY place generation (an LLM) runs; the engine stays pure.

    ``apply_cli_verb`` is an OPTIONAL second seam for the CLI-verb kinds
    (``visualize`` / ``propose_study``) that have no native apply handler because
    applying them renders a figure / seeds a study — things the pure engine does
    not do. ``apply_cli_verb(request) -> {status, applied_to} | None`` shells out
    to the kind's ``/viva-*`` skill (e.g. ``$VIVA_CLI_APPLIER_CMD`` →
    ``claude -p /viva:viz``), which does that impure work and reports what it
    landed; returning ``None`` declines (the request falls back to queued). When
    it is omitted (the default), a CLI-verb kind is queued, NOT filled — so a
    ``--free-run`` drive without an applier never starves its budget on kinds it
    cannot auto-apply. With it, a drive lands rendered viz + seeded studies
    end-to-end. The engine stays pure either way: the impure work lives in the
    injected command, exactly like ``fill``.

    Gate discipline (spec §3 / review-focus): a ``review``-gated request is QUEUED,
    never auto-filled; an ``auto`` request is filled and applied via
    :func:`apply_result`. A filler that raises, returns nothing, or yields an
    artifact the apply op rejects becomes an ``error``/``queued_for_review``
    outcome — never a silent apply — and never blocks the rest (the runner drains
    the auto work and leaves the rest). An optional :class:`~discovery_engine.discover.budget.Budget`
    charges one ``generation_calls`` per auto request; when it is exhausted the drain records a
    typed ``stop`` (``budget_exhausted``) and halts, so a bounded run never fills past its budget.
    Returns one outcome dict per request:
    ``{request_id, kind, gate, outcome: applied|queued_for_review|error|stop, ...}``."""
    outcomes: list[dict] = []
    for req in requests:
        base = {"request_id": req.id, "kind": req.kind, "gate": req.gate}
        if req.gate != "auto":
            outcomes.append({**base, "outcome": "queued_for_review"})
            continue
        if _handler_for(req.kind) is None:
            # A kind with no apply handler is a CLI-verb kind authored via its
            # /viva-* skill (visualize -> /viva:viz, propose_study -> /viva:study),
            # or an unknown kind.
            if apply_cli_verb is not None:
                # ORCHESTRATE: invoke the skill seam, which renders / seeds and
                # reports what it applied. Charges one generation_call (the skill
                # generates + applies, like a fill).
                if budget is not None and not budget.charge("generation_calls"):
                    from discovery_engine.discover._policy import BUDGET_EXHAUSTED, stop as _stop
                    outcomes.append({**base, **_stop(BUDGET_EXHAUSTED,
                                                     detail="generation_calls budget exhausted")})
                    break
                try:
                    res = apply_cli_verb(req)
                except Exception as e:  # noqa: BLE001 — never crash the drain
                    outcomes.append({**base, "outcome": "error",
                                     "reason": f"cli-verb applier raised: {e}"})
                    continue
                if res:
                    outcomes.append({**base, "outcome": res.get("status", "applied"),
                                     "applied_to": res.get("applied_to", [])})
                    continue
                # the applier declined (None) -> fall through to the queued outcome
            # No applier (or it declined): queue WITHOUT filling — reserves the
            # budget for kinds that actually apply, so a viz-heavy steering goal no
            # longer starves the wired kinds (the bug a driver hit — a budget of 13
            # consumed by 7 visualize + 6 propose_study before one interpret_finding
            # got a turn -> applied: 0). The named skill applies it.
            nho = _no_handler_outcome(req.kind)
            out = {**base, "outcome": "queued_for_review", "reason": nho["reason"]}
            if "next_action" in nho:
                out["next_action"] = nho["next_action"]
            outcomes.append(out)
            continue
        if budget is not None and not budget.charge("generation_calls"):
            # Bounded run: out of generation budget -> a typed stop, and halt the drain.
            from discovery_engine.discover._policy import BUDGET_EXHAUSTED, stop as _stop
            outcomes.append({**base, **_stop(BUDGET_EXHAUSTED,
                                             detail="generation_calls budget exhausted")})
            break
        try:
            result = fill(req)
        except Exception as e:  # noqa: BLE001 — a filler must never crash the drain
            outcomes.append({**base, "outcome": "error", "reason": f"filler raised: {e}"})
            continue
        if result is None:
            outcomes.append({**base, "outcome": "queued_for_review",
                             "reason": "filler produced nothing"})
            continue
        try:
            applied = apply_result(req, result, ws_root=ws_root, inv_slug=inv_slug)
        except ValueError as e:
            outcomes.append({**base, "outcome": "error", "reason": str(e)})
            continue
        outcomes.append({**base, "outcome": applied.get("status", "applied"),
                         "applied_to": applied.get("applied_to", [])})
    return outcomes


def subprocess_filler(cmd: str | None = None, *, timeout: float = 1800):
    """A filler that shells out to ``$VIVA_FILLER_CMD`` (or an explicit ``cmd``).

    Generalizes the decider seam (``$VIVA_DECIDER_CMD``): the command receives a
    :class:`GenerationRequest` as JSON on stdin and must print a
    :class:`GenerationResult` JSON on stdout. The engine stays LLM-free — the AI
    (or a deterministic filler) lives in that command (e.g. ``claude -p`` with a
    fill skill, or viva-superpowers' filler). Returns a
    ``fill(request) -> GenerationResult | None`` for :func:`run_requests`. With no
    command configured, ``fill`` returns ``None`` (nothing auto-fills). A malformed
    result is rejected (``validate_result``) so a bad artifact never applies."""
    import json
    import os
    import subprocess

    from discovery_engine.phases.generation import GenerationResult, validate_result
    command = cmd or os.environ.get("VIVA_FILLER_CMD")

    def fill(request):
        if not command:
            return None
        proc = subprocess.run(command, shell=True, check=False,
                              input=json.dumps(request.to_dict(), default=str),
                              capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "").strip()
        if not out:
            return None
        doc = json.loads(out)
        validate_result(doc)                       # reject a malformed artifact loudly
        return GenerationResult(
            request_id=doc.get("request_id") or request.id,
            artifact=doc.get("artifact") or {},
            filler=doc.get("filler") or {"kind": "ai", "id": "VIVA_FILLER_CMD"},
            rationale=doc.get("rationale") or "",
            confidence=doc.get("confidence"),
            status=doc.get("status") or "applied",
            applied_to=doc.get("applied_to") or [],
        )

    return fill


def subprocess_cli_applier(cmd: str | None = None, *, timeout: float = 1800):
    """An ``apply_cli_verb`` seam that shells out to ``$VIVA_CLI_APPLIER_CMD``.

    The counterpart to :func:`subprocess_filler` for the CLI-verb kinds
    (``visualize`` / ``propose_study``): the command receives a
    :class:`GenerationRequest` as JSON on stdin, runs the kind's ``/viva-*`` skill
    — which RENDERS a figure / SEEDS a study and writes it into the workspace
    (things the pure engine does not do) — and prints an outcome JSON
    ``{"status": "applied", "applied_to": [...]}`` on stdout. Returns an
    ``apply_cli_verb(request) -> dict | None`` for :func:`run_requests`. With no
    command configured it returns ``None`` (declines → the request is queued), so
    a drive without an applier keeps today's queue-only behaviour; a command that
    produces no/empty output also declines. The engine stays LLM-free — the skill
    (and its render/seed) lives entirely in that command."""
    import json
    import os
    import subprocess

    command = cmd or os.environ.get("VIVA_CLI_APPLIER_CMD")

    def apply_cli_verb(request):
        if not command:
            return None
        proc = subprocess.run(command, shell=True, check=False,
                              input=json.dumps(request.to_dict(), default=str),
                              capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "").strip()
        if not out:
            return None
        doc = json.loads(out)
        applied_to = doc.get("applied_to") or []
        return {
            "status": doc.get("status") or "applied",
            "applied_to": applied_to if isinstance(applied_to, list) else [applied_to],
        }

    return apply_cli_verb

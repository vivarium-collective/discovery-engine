"""Per-run store resolution and the computed-outcomes write-back (B2b).

``compute_outcomes`` evaluates every run in a study and writes a parallel
``computed_outcomes`` block per run — never touching the authored ``outcomes`` —
via a ruamel.yaml round-trip that preserves comments and is idempotent.
"""
from __future__ import annotations

from typing import Any

from discovery_engine.study import io as study_io

from .config import _resolve_run_config
from .workspace_plugins import _grader_stamp, _inputs_hash, _storeless_kinds


# ---------------------------------------------------------------------------
# B2b: per-run store resolution
# ---------------------------------------------------------------------------

def _to_plain(obj: Any) -> Any:
    """Recursively convert ruamel.yaml objects to plain Python types."""
    if hasattr(obj, "items"):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    return obj


def _resolve_run_store(
    run: dict,
    study_dir: Any,
    ws_root: Any = None,
) -> "str | None":
    """Resolve a run entry to a RunReader-openable path.

    Priority: ``emitter.store`` → ``store_path`` → ``store`` → ``run_dir`` → ``parquet``.
    Relative paths are resolved against *study_dir*, then *ws_root*, then cwd.
    For ``parquet`` candidates: if the resolved path lacks a ``history/``
    subdirectory, descend to the first child directory that has one (the
    experiment dir that RunReader can open).

    Args:
        run:       Run dict from study.yaml ``runs[]``.
        study_dir: Directory containing the study.yaml.
        ws_root:   Workspace root for resolving relative paths.

    Returns:
        Resolved absolute path string, or ``None`` if unresolvable.
    """
    import os
    from pathlib import Path as _Path

    study_dir = _Path(study_dir)
    ws_root = _Path(ws_root) if ws_root else None

    # Build (raw_path, is_parquet) candidates in priority order
    candidates: list[tuple[str, bool]] = []
    emitter = run.get("emitter") or {}
    if isinstance(emitter, dict) and emitter.get("store"):
        candidates.append((str(emitter["store"]), False))
    if run.get("store_path"):
        candidates.append((str(run["store_path"]), False))
    if run.get("store"):
        candidates.append((str(run["store"]), False))
    if run.get("run_dir"):
        candidates.append((str(run["run_dir"]), False))
    if run.get("parquet"):
        candidates.append((str(run["parquet"]), True))

    # (No early return on empty candidates: an entry with no explicit store pointer still
    # falls through to the shared-runs.db fallback below.)

    # Bases for relative-path resolution (tried in order)
    bases: list[_Path | None] = [None]  # None = treat path as absolute/cwd-relative
    bases.append(study_dir)
    if ws_root:
        bases.append(ws_root)
    bases.append(_Path(os.getcwd()))

    for raw_path, is_parquet in candidates:
        raw = _Path(raw_path)

        # Find an existing path
        resolved: _Path | None = None
        for base in bases:
            candidate = raw if base is None else base / raw
            if candidate.exists():
                resolved = candidate
                break

        if resolved is None:
            continue

        if is_parquet:
            # If the resolved dir already has history/, use it directly
            if (resolved / "history").exists():
                return str(resolved)
            # Descend to the first subdirectory that contains history/
            try:
                children = sorted(
                    c for c in resolved.iterdir() if c.is_dir()
                )
                for child in children:
                    if (child / "history").exists():
                        return str(child)
            except PermissionError:
                pass
            # No descendant with history/ found — skip this candidate
            continue
        else:
            return str(resolved)

    # Fallback: the study's shared multi-run store. `vivarium-workbench run study` writes a
    # run into <study_dir>/runs.db but does not always record a per-run store pointer
    # (emitter_path can be None in runs_meta), leaving the entry with no candidate above —
    # so the run's real data is present but unresolvable, and the gate it computes never
    # gets recorded. If this entry's run_id is in that runs.db, resolve to it (RunReader
    # opens the shared DB directly). Scoped to a present run_id so stale entries pointing
    # at a gone per-run store don't all collapse onto the shared DB. (A multi-run DB with
    # several present ids still resolves them to the same path; selecting by run_id inside
    # RunReader is a follow-up.)
    shared = study_dir / "runs.db"
    if shared.exists() and run.get("name"):
        try:
            from discovery_engine.study import run_registry
            if any(r.get("run_id") == run["name"] for r in run_registry.list_runs(shared)):
                return str(shared)
        except Exception:  # noqa: BLE001 — a best-effort probe, never block resolution
            pass

    return None


# ---------------------------------------------------------------------------
# B2b: compute_outcomes — write parallel computed_outcomes block per run
# ---------------------------------------------------------------------------

def _select_run_entry(runs: list, selector: dict) -> "dict | None":
    """Map a run selector to a ``runs[]`` entry (cross-run convention, #98 2b).

    - ``{run: variant, variant: X}`` → the entry whose ``variant`` or ``name`` is X.
    - ``{run: baseline}`` (default) → the entry with ``canonical: true``, else the
      first with no ``variant``, else the first entry.
    """
    if (selector or {}).get("run") == "variant":
        want = selector.get("variant")
        for r in runs:
            if isinstance(r, dict) and (r.get("variant") == want or r.get("name") == want):
                return r
        return None
    for r in runs:
        if isinstance(r, dict) and r.get("canonical") is True:
            return r
    for r in runs:
        if isinstance(r, dict) and not r.get("variant"):
            return r
    return runs[0] if runs and isinstance(runs[0], dict) else None


def _build_run_opener(runs: list, study_dir: Any, ws_root: Any):
    """Return ``opener(selector) -> RunReader | None`` over a study's ``runs[]``.

    Resolves a selector to a run entry (``_select_run_entry``), then to a store
    (``_resolve_run_store``), then opens a reader (cached by store path). Returns
    None when the run/store can't be resolved or opened. This is the plugin-side
    resolver that lets cross-run (``run_delta``) tests load their compare run.
    """
    try:
        from viva_emitters import RunReader  # noqa: PLC0415
    except ImportError:
        return None
    cache: dict = {}

    def opener(selector):
        entry = _select_run_entry(runs, selector or {})
        if entry is None:
            return None
        store = _resolve_run_store(entry, study_dir, ws_root)
        if store is None:
            return None
        if store not in cache:
            try:
                cache[store] = RunReader.open(store)
            except Exception:  # noqa: BLE001
                cache[store] = None
        return cache[store]

    return opener


def compute_outcomes(
    study_dir: Any,
    ws_root: Any = None,
) -> dict:
    """Evaluate all study runs and write a parallel ``computed_outcomes`` block.

    Non-destructive: ``run["outcomes"]`` (authored) is **never** modified.
    Writes ``run["computed_outcomes"]`` per run using a ruamel.yaml round-trip
    to preserve comments.  Idempotent: writes the file only when content changed.

    For each test in ``computed_outcomes``, attaches a ``reconcile`` flag:
      - ``agree``       — code result matches authored result.
      - ``divergent``   — code result differs from authored result.
      - ``no_authored`` — no authored entry, or code produced no result
                          (agent / needs_rerun bucket).

    If a run's store cannot be resolved, writes
    ``computed_outcomes = {_status: store_unresolved}`` and never fabricates
    verdicts.

    Args:
        study_dir: Path to the directory containing ``study.yaml``.
        ws_root:   Workspace root for resolving relative paths in run entries.

    Returns:
        Summary dict: ``{"runs_evaluated": int, "tests_code": int, "tests_agent": int}``
    """
    import io
    from pathlib import Path as _Path

    import ruamel.yaml
    import yaml as _yaml

    # evaluate_study/evaluate_test live in the package __init__ (so that
    # se.<helper> monkeypatches are honored); import at call time to avoid an
    # import cycle and to pick up any patched versions, as the flat module did.
    from discovery_engine.study.evaluator import evaluate_study, evaluate_test

    study_dir = _Path(study_dir)
    ws_root = _Path(ws_root) if ws_root else None
    if ws_root is None:
        # Derive the workspace root from study_dir when not given, so workspace-registered
        # evaluators (including the @storeless recovery evaluators) are found. Without this,
        # compute_outcomes(study_dir) with no ws_root silently loads NO workspace evaluators —
        # _storeless_kinds comes back empty and every store-less test bails to store_unresolved
        # (and native workspace-evaluator tests fall to the agent bucket).
        _p = study_dir.resolve()
        for _anc in (_p, *_p.parents):
            if (_anc / "workspace.yaml").is_file():
                ws_root = _anc
                break
    study_yaml_path = study_dir / "study.yaml"

    # Plain load for evaluate_study (reads tests / behavior_tests)
    spec = _yaml.safe_load(study_yaml_path.read_text(encoding="utf-8"))

    # Cross-run (run_delta) tests are study-level, not per-run: split them out of
    # the per-run pass and evaluate them once after the loop (#98 Stage 2b).
    _tests_key = "tests" if spec.get("tests") else "behavior_tests"
    _all_tests = spec.get(_tests_key) or []
    _cross_run_tests = [
        t for t in _all_tests
        if isinstance(t, dict) and (t.get("measure") or {}).get("kind") == "run_delta"
    ]
    per_run_spec = dict(spec)
    per_run_spec[_tests_key] = [t for t in _all_tests if t not in _cross_run_tests]

    # ruamel round-trip load: preserves comments + block style
    ryaml = ruamel.yaml.YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096  # prevent unwanted line-wrapping
    with open(study_yaml_path, encoding="utf-8") as fh:
        doc = ryaml.load(fh)

    # Capture original serialised form for change detection
    _orig_sio = io.StringIO()
    ryaml.dump(doc, _orig_sio)
    original_serialised = _orig_sio.getvalue()

    runs_evaluated = 0
    tests_code = 0
    tests_agent = 0

    for run in doc.get("runs") or []:
        store_path = _resolve_run_store(run, study_dir, ws_root)

        # Phase 2 freshness stamp: the inputs_hash anchors staleness AND idempotency — carry the
        # prior graded_at forward when the inputs are unchanged, so a re-grade never churns the YAML.
        _ih = _inputs_hash(per_run_spec.get(_tests_key), run, ws_root)
        _old_stamp = (run.get("computed_outcomes") or {}).get("_grader") or {}
        _prior_at = _old_stamp.get("graded_at") if _old_stamp.get("inputs_hash") == _ih else None

        store_status = None
        if store_path is None:
            # The run store is unresolved. We can still grade STORE-LESS tests — their
            # evaluator re-derives its result internally (e.g. a recovery search re-runs
            # SINDy+fit) and needs no reader. Only genuinely run-data tests stay ungraded
            # here, so don't blanket-skip when store-less tests are present.
            _sl = _storeless_kinds(ws_root)
            sl_tests = [t for t in (per_run_spec.get(_tests_key) or [])
                        if isinstance(t, dict) and (t.get("measure") or {}).get("kind") in _sl]
            if not sl_tests:
                run["computed_outcomes"] = ruamel.yaml.CommentedMap(
                    {"_status": "store_unresolved",
                     "_grader": _grader_stamp(inputs_hash=_ih, store="unresolved",
                                              graded_at=_prior_at)}
                )
                continue
            try:
                outcomes = evaluate_study({**per_run_spec, _tests_key: sl_tests}, None,
                                          ws_root=ws_root)
            except Exception as exc:  # noqa: BLE001
                run["computed_outcomes"] = ruamel.yaml.CommentedMap(
                    {"_status": f"evaluation_error: {exc}",
                     "_grader": _grader_stamp(inputs_hash=_ih, graded_at=_prior_at)}
                )
                continue
            store_status = "store_less_graded"  # store unresolved; only store-less tests graded
        else:
            # Open reader and evaluate
            try:
                try:
                    from viva_emitters import RunReader  # noqa: PLC0415
                except ImportError as _ie:
                    raise ImportError(
                        "RunReader requires the parquet reader: "
                        "pip install 'viva-emitters[parquet]'"
                    ) from _ie
                reader = RunReader.open(store_path)
                outcomes = evaluate_study(per_run_spec, reader, ws_root=ws_root)
            except Exception as exc:  # noqa: BLE001
                run["computed_outcomes"] = ruamel.yaml.CommentedMap(
                    {"_status": f"evaluation_error: {exc}",
                     "_grader": _grader_stamp(inputs_hash=_ih, graded_at=_prior_at)}
                )
                continue

        # Build computed_outcomes with reconcile flags
        authored = run.get("outcomes") or {}
        new_co = ruamel.yaml.CommentedMap()
        for test_name, outcome in outcomes.items():
            entry = ruamel.yaml.CommentedMap(outcome)

            authored_entry = authored.get(test_name) if isinstance(authored, dict) else None
            authored_result = (
                authored_entry.get("result")
                if isinstance(authored_entry, dict)
                else None
            )
            code_result = outcome.get("result")  # None for agent / needs_rerun
            if code_result == "ungraded":
                code_result = None  # a skip (couldn't grade), not a verdict — reconcile as no_authored, not divergent

            if code_result is not None and authored_result is not None:
                reconcile = "agree" if code_result == authored_result else "divergent"
            else:
                reconcile = "no_authored"

            entry["reconcile"] = reconcile
            new_co[test_name] = entry

            by = outcome.get("evaluated_by", "")
            if by == "code":
                tests_code += 1
            else:
                tests_agent += 1

        # A store-less GRADED block is a REAL result, NOT a stub — it must NOT carry ``_status``
        # (that convention, read by finding_observations, means "skip me" and would blank the
        # grades before they reach findings[].status). The store provenance lives in the grader
        # stamp instead (a meta key, never masking the real grades), now three-valued so a consumer
        # can tell "re-derived by design" (store_less) from a genuinely broken store (unresolved).
        _store = "store_less" if store_status == "store_less_graded" else "resolved"
        stamp = _grader_stamp(inputs_hash=_ih, store=_store, graded_at=_prior_at)
        new_co["_grader"] = ruamel.yaml.CommentedMap(stamp)
        run["computed_outcomes"] = new_co
        runs_evaluated += 1

    # Study-level cross-run pass: evaluate run_delta tests ONCE (not per run) and
    # attach each to its primary run's computed_outcomes (#98 Stage 2b).
    runs_list = doc.get("runs") or []
    if _cross_run_tests:
        opener = _build_run_opener(runs_list, study_dir, ws_root)
        for test in _cross_run_tests:
            name = test.get("name", "run_delta")
            given = test.get("given") or {}
            primary_sel = {k: v for k, v in given.items() if k != "compare_to"} or {"run": "baseline"}
            primary_reader = opener(primary_sel) if opener else None
            outcome = evaluate_test(
                test, primary_reader, ws_root=ws_root,
                config=_resolve_run_config(spec, test), run_opener=opener,
            )
            target = _select_run_entry(runs_list, primary_sel)
            if target is None:
                continue
            co = target.get("computed_outcomes")
            if not isinstance(co, ruamel.yaml.CommentedMap):
                co = ruamel.yaml.CommentedMap()
                target["computed_outcomes"] = co
            authored = target.get("outcomes") or {}
            ae = authored.get(name) if isinstance(authored, dict) else None
            ar = ae.get("result") if isinstance(ae, dict) else None
            cr = outcome.get("result")
            if cr == "ungraded":
                cr = None
            entry = ruamel.yaml.CommentedMap(outcome)
            entry["reconcile"] = (
                ("agree" if cr == ar else "divergent")
                if (cr is not None and ar is not None) else "no_authored"
            )
            co[name] = entry
            if outcome.get("evaluated_by") == "code":
                tests_code += 1
            else:
                tests_agent += 1

    # Write only when something changed (idempotency)
    _new_sio = io.StringIO()
    ryaml.dump(doc, _new_sio)
    new_serialised = _new_sio.getvalue()

    if new_serialised != original_serialised:
        study_io.atomic_write(study_yaml_path, new_serialised)

    return {
        "runs_evaluated": runs_evaluated,
        "tests_code": tests_code,
        "tests_agent": tests_agent,
    }

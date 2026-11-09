"""Peer-review rigor checks: framework-rigor gates (G1 gate-class, G4
config-consumption + seeded-stochastic, G5 units), reproducible-baseline carry,
and the campaign quality-bar checks.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

import yaml

from discovery_engine.study.band_provenance import has_numeric_band as _is_numeric_band
from discovery_engine.bibtex import bib_keys
from discovery_engine.reporting.rigor import run_is_emitter_backed
from discovery_engine.workspace_paths import WorkspacePaths
from discovery_engine.study.observable_lint import reconcile_study_observables
import re as _re
from discovery_engine.reporting.linter.context import *  # noqa: F401,F403




# ---------------------------------------------------------------------------
# Peer-review rigor checks (framework-rigor: G1 gate_class, G4 config
# consumption + seed-all-stochastic, G5 units/time declared)
# ---------------------------------------------------------------------------


# G1 — the canonical per-behavior_test gate classification (design doc:
# "CANONICAL FIELD NAME … `gate_class` on each behavior_test").
_GATE_CLASSES = ("regression_pin", "acceptance_criterion")


def _check_gate_class_declared(ctx: _LintContext) -> None:
    """G1-lint: every ``behavior_tests[]`` entry should DECLARE its
    ``gate_class`` — ``regression_pin`` (a threshold set after seeing the run;
    pins are never counted as acceptance evidence) or ``acceptance_criterion``
    (a pre-stated directional prior committed before the run).

    The linter never infers the classification — only the author knows whether
    the threshold predated the run — it requires it be declared:

    - entry carries a machine-readable gate (a ``pass_if`` block or a numeric
      band per ``band_provenance.has_numeric_band``) and no ``gate_class``
      → WARNING (the verdict counts split on this field; an unclassified gate
      silently inflates the acceptance count).
    - descriptive-only entry (no machine-readable gate yet) and no
      ``gate_class`` → INFO (classify when the gate is authored).
    - ``gate_class`` set to a value outside the enum → WARNING.
    """
    tests = ctx.spec.get("behavior_tests") or []
    if not isinstance(tests, list):
        return
    for idx, t in enumerate(tests):
        if not isinstance(t, dict):
            continue
        name = t.get("name", f"<index-{idx}>")
        gc = t.get("gate_class")
        if isinstance(gc, str) and gc.strip():
            if gc.strip() not in _GATE_CLASSES:
                ctx.add(
                    level="warning",
                    field_path=f"behavior_tests[{idx}].gate_class",
                    message=(
                        f"behavior_test {name!r} declares gate_class {gc.strip()!r}, "
                        f"which is not a recognised value. Expected one of "
                        f"{list(_GATE_CLASSES)}."
                    ),
                    check="gate_class_unknown",
                )
            continue
        carries_gate = isinstance(t.get("pass_if"), dict) or _is_numeric_band(t)
        ctx.add(
            level="warning" if carries_gate else "info",
            field_path=f"behavior_tests[{idx}].gate_class",
            message=(
                f"behavior_test {name!r} has no gate_class. Declare "
                "`gate_class: regression_pin | acceptance_criterion` — a "
                "threshold set AFTER seeing the run is a regression_pin (it "
                "pins current behavior and is never counted as acceptance "
                "evidence); a pre-stated directional prior committed BEFORE "
                "the run is an acceptance_criterion. The linter does not "
                "infer this — only the author knows whether the threshold "
                "predated the run."
            ),
            check="gate_class_missing",
        )


# --- G4 shared machinery: static composite + process-class analysis ---------
#
# LIMITATION (stated up front): the reference implementation of the config-
# consumption audit (meta-modelers-guide tests/test_config_consumption.py)
# INSTANTIATES each process and compares the effective config to the supplied
# one, which also catches bigraph-schema's value-clobbering disease (a pinned
# Float 0.0 refilled from the schema _default — the `mut_sigma: 0.0` bug).
# The linter deliberately never imports or executes arbitrary workspace code
# (same contract as _check_visualization_addresses), so the checks below are a
# LIGHTER, STRUCTURAL version: they parse composite spec files and process
# class sources with `ast` only. They catch supplied config KEYS that no
# declared `config_schema` accepts (silently dropped by bigraph-schema fill),
# but CANNOT catch a schema-declared key whose VALUE is clobbered at
# instantiation time — that stronger guarantee belongs in a workspace test
# modeled on the reference implementation.

_SOURCE_SCAN_SKIP = frozenset({
    "__pycache__", "node_modules", "out", "outputs", "reports",
    "datasets", "references", "tests",
})

_RNG_SIGNAL_RE = re.compile(
    r"default_rng\s*\(|np\.random\.|numpy\.random\.|RandomState\s*\(|"
    r"random\.(?:random|randint|choice|gauss|normalvariate|normal|uniform|"
    r"sample|shuffle|poisson|exponential)\s*\("
)

_STOCHASTIC_NAME_RE = re.compile(
    r"(stochastic|gillespie|monte_?carlo|langevin|brownian|random_?walk|ssa\b)",
    re.IGNORECASE,
)

_SEED_CONFIG_KEYS = frozenset({"seed", "random_seed", "rng_seed", "random_state"})


def _workspace_process_class_index(ws_root: Path) -> dict:
    """Statically index every class defined under the workspace's Python
    package directories: ``{class_name: {"schema_keys": set|None,
    "rng_signal": bool, "file": str}}``.

    ``schema_keys`` is the set of top-level keys of a NON-EMPTY literal-dict
    ``config_schema`` class attribute; ``None`` when the class has no
    ``config_schema``, an empty one, or builds it dynamically (merge/call) —
    in all of which cases the consumption check conservatively skips the
    class rather than guessing. ``rng_signal`` is a source-text randomness
    signal used by the seed-all-stochastic check. Pure ``ast`` analysis —
    no workspace code is imported or executed.
    """
    import ast as _ast

    index: dict = {}
    try:
        children = [c for c in ws_root.iterdir()
                    if c.is_dir() and not c.name.startswith(".")]
    except OSError:
        return index
    pkg_dirs = [c for c in children
                if (c / "__init__.py").is_file() and c.name not in _SOURCE_SCAN_SKIP]
    for pkg in pkg_dirs:
        for py in pkg.rglob("*.py"):
            rel_parts = py.relative_to(pkg).parts
            if any(p in _SOURCE_SCAN_SKIP or p.startswith(".") for p in rel_parts):
                continue
            try:
                src = py.read_text(encoding="utf-8")
                tree = _ast.parse(src)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.ClassDef):
                    continue
                schema_keys = None
                for stmt in node.body:
                    if isinstance(stmt, _ast.Assign):
                        targets = [t.id for t in stmt.targets
                                   if isinstance(t, _ast.Name)]
                        value = stmt.value
                    elif isinstance(stmt, _ast.AnnAssign) and isinstance(
                            stmt.target, _ast.Name):
                        targets = [stmt.target.id]
                        value = stmt.value
                    else:
                        continue
                    if "config_schema" not in targets:
                        continue
                    if isinstance(value, _ast.Dict) and all(
                        isinstance(k, _ast.Constant) and isinstance(k.value, str)
                        for k in value.keys
                    ):
                        keys = {k.value for k in value.keys}
                        schema_keys = keys or None  # empty literal → unknown
                    break  # first config_schema statement decides
                try:
                    seg = _ast.get_source_segment(src, node) or ""
                except Exception:  # noqa: BLE001
                    seg = ""
                index[node.name] = {
                    "schema_keys": schema_keys,
                    "rng_signal": bool(_RNG_SIGNAL_RE.search(seg)),
                    "file": str(py),
                }
    return index


_COMPOSITE_SPEC_EXTS = (
    ".composite.yaml", ".composite.yml", ".composite.json",
    ".yaml", ".yml", ".json",
)


def _composite_spec_dirs(ws_root: Path) -> list:
    """Directories where composite spec files live: the workspace-level
    ``composites/`` dir plus any ``<child>/composites/`` package dir."""
    dirs: list = []
    wp = WorkspacePaths.load(ws_root)
    if wp.composites.is_dir():
        dirs.append(wp.composites)
    try:
        children = list(ws_root.iterdir())
    except OSError:
        return dirs
    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        d = child / "composites"
        if d.is_dir() and d not in dirs:
            dirs.append(d)
    return dirs


def _resolve_composite_file(ctx: _LintContext, ref: str):
    """Best-effort resolution of a study composite reference to a spec FILE
    on disk. Returns ``None`` when the ref doesn't resolve (a registry id,
    a Python-defined composite, …) — the G4 checks stay silent for those
    rather than guessing."""
    ref = (ref or "").strip()
    if ref.startswith("local:"):
        ref = ref[len("local:"):]
    if not ref:
        return None
    # 1. Literal path relative to the workspace root.
    p = ctx.ws_root / ref
    if p.suffix.lower() in {".json", ".yaml", ".yml"} and p.is_file():
        return p
    # 2. <composites-dir>/<stem><ext> for the plausible stems of the ref.
    stems: list = []
    for s in (ref, ref.rsplit("/", 1)[-1], ref.rsplit(".", 1)[-1]):
        if s and s not in stems:
            stems.append(s)
    dirs = _cached(ctx, "composite_spec_dirs",
                   lambda: _composite_spec_dirs(ctx.ws_root))
    for d in dirs:
        for stem in stems:
            for ext in _COMPOSITE_SPEC_EXTS:
                cand = d / f"{stem}{ext}"
                if cand.is_file():
                    return cand
    # 3. Dotted module-ish ref (pkg.composites.x) → path with extensions.
    if "." in ref and "/" not in ref:
        rel = ref.replace(".", "/")
        for ext in _COMPOSITE_SPEC_EXTS:
            cand = ctx.ws_root / f"{rel}{ext}"
            if cand.is_file():
                return cand
    return None


def _composite_process_nodes(state, path: str = "state") -> list:
    """Yield ``(node_path, address, config, node)`` for every process/step
    node in a composite state tree (mirrors the reference audit's walker)."""
    out: list = []
    if isinstance(state, dict):
        t = state.get("_type")
        addr = state.get("address")
        if isinstance(addr, str) and isinstance(t, str) and (
                t.startswith("process") or t.startswith("step")):
            out.append((path, addr, state.get("config") or {}, state))
        for k, v in state.items():
            out += _composite_process_nodes(v, f"{path}.{k}")
    elif isinstance(state, list):
        for i, v in enumerate(state):
            out += _composite_process_nodes(v, f"{path}[{i}]")
    return out


def _composite_class_name(address: str) -> str:
    """``local:!pkg.mod.Cls`` / ``local:Cls`` / ``pkg.mod.Cls`` → ``Cls``."""
    data = address.split(":", 1)[-1].lstrip("!")
    return data.rsplit(".", 1)[-1]


def _study_composite_specs(ctx: _LintContext) -> list:
    """The parsed composite spec files a study references, de-duplicated:
    ``[(composite_name, spec_path, state_tree)]``. Unresolvable refs and
    unparseable files are silently skipped (other checks own those)."""
    out: list = []
    seen: set = set()
    for ref in _collect_composite_refs(ctx.spec):
        f = _resolve_composite_file(ctx, ref)
        if f is None or f in seen:
            continue
        seen.add(f)
        try:
            spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(spec, dict):
            continue
        state = spec.get("state") if isinstance(spec.get("state"), dict) else spec
        name = spec.get("name") if isinstance(spec.get("name"), str) else None
        out.append((name or f.name, f, state))
    return out


def _check_config_consumption(ctx: _LintContext) -> None:
    """G4a — config-consumption audit (structural). Every config key a
    composite supplies to a process should be ACCEPTED by that process's
    declared ``config_schema``; a key the schema doesn't declare is silently
    dropped by bigraph-schema fill (the ``mut_sigma: 0.0`` disease — a
    published parameter that never reaches the process).

    Structural-only: composite specs and class ``config_schema`` literals are
    compared via ``ast`` — no instantiation (see the LIMITATION note above:
    value-level clobbering of a schema-declared key is out of this check's
    reach and needs the workspace-level instantiation test). Conservative:
    classes that aren't found in workspace source, or whose ``config_schema``
    is absent/empty/dynamic, are skipped rather than guessed at. Warning per
    (composite, process, dropped key).
    """
    if ctx.slug == "<workspace>":
        return
    composites = _study_composite_specs(ctx)
    if not composites:
        return
    index = _cached(ctx, "process_class_index",
                    lambda: _workspace_process_class_index(ctx.ws_root))
    if not index:
        return
    for comp_name, _spec_path, state in composites:
        for node_path, addr, config, _node in _composite_process_nodes(state):
            if not isinstance(config, dict) or not config:
                continue
            cls = _composite_class_name(addr)
            info = index.get(cls)
            if not info or not info.get("schema_keys"):
                continue  # unknown/dynamic schema → conservatively skip
            schema_keys = info["schema_keys"]
            for key in config:
                if not isinstance(key, str) or key in schema_keys:
                    continue
                ctx.add(
                    level="warning",
                    field_path=f"composite:{comp_name}:{node_path}.config.{key}",
                    message=(
                        f"Composite {comp_name!r} supplies config key {key!r} "
                        f"to process {cls!r} (at {node_path}), but the class's "
                        f"declared config_schema does not accept it "
                        f"(accepted: {sorted(schema_keys)}). bigraph-schema "
                        "silently drops unknown config keys, so this published "
                        "parameter never reaches the process. Fix the key name, "
                        "add it to the process config_schema, or remove it. "
                        "(Structural check — schema-declared keys whose VALUE "
                        "is clobbered at instantiation, e.g. a pinned 0.0 "
                        "refilled from the schema default, need the "
                        "instantiation-level workspace test.)"
                    ),
                    check="config_consumption",
                )


def _config_declares_seed(config) -> bool:
    """True when a process config carries a seed-ish key at any depth."""
    if not isinstance(config, dict):
        return False
    for k, v in config.items():
        if isinstance(k, str) and k.strip().lower() in _SEED_CONFIG_KEYS:
            return True
        if isinstance(v, dict) and _config_declares_seed(v):
            return True
    return False


def _check_stochastic_unseeded(ctx: _LintContext) -> None:
    """G4b — seed-all-stochastic. A composite process that draws randomness
    must declare a seed in its config, or its runs are unreproducible and
    seed-sweep claims are unfounded.

    Conservative stochasticity detection (better to under-flag than
    false-positive) — a process counts as stochastic only when:

    - it declares ``stochastic: true`` on the node or in its config, OR
    - its class name matches a known-stochastic pattern (Stochastic,
      Gillespie, MonteCarlo, Langevin, Brownian, RandomWalk, SSA), OR
    - its class source (statically located in the workspace package) shows
      an RNG signal (``default_rng(``, ``np.random.``, ``random.choice(`` …).

    Seeded = a ``seed`` / ``random_seed`` / ``rng_seed`` / ``random_state``
    key anywhere in the supplied config. Warning per (composite, process).
    """
    if ctx.slug == "<workspace>":
        return
    composites = _study_composite_specs(ctx)
    if not composites:
        return
    index = _cached(ctx, "process_class_index",
                    lambda: _workspace_process_class_index(ctx.ws_root))
    for comp_name, _spec_path, state in composites:
        for node_path, addr, config, node in _composite_process_nodes(state):
            cls = _composite_class_name(addr)
            declared = (
                node.get("stochastic") is True
                or (isinstance(config, dict) and config.get("stochastic") is True)
            )
            name_signal = bool(_STOCHASTIC_NAME_RE.search(cls))
            info = index.get(cls) or {}
            rng_signal = bool(info.get("rng_signal"))
            if not (declared or name_signal or rng_signal):
                continue
            if _config_declares_seed(config):
                continue
            how = (
                "declares stochastic: true" if declared
                else "has a stochastic-sounding class name" if name_signal
                else "shows an RNG signal in its source"
            )
            ctx.add(
                level="warning",
                field_path=f"composite:{comp_name}:{node_path}.config.seed",
                message=(
                    f"Composite {comp_name!r} process {cls!r} (at {node_path}) "
                    f"{how} but its config declares no seed "
                    f"(none of {sorted(_SEED_CONFIG_KEYS)}). Unseeded "
                    "stochastic processes make runs unreproducible and "
                    "seed-sweep claims unfounded — pin a seed in the "
                    "composite config (and sweep it explicitly for "
                    "replication)."
                ),
                check="stochastic_unseeded",
            )


# --- G5 — units/time declared; no unearned physical unit labels -------------


# A physical-unit label only counts when it follows a number ("4.2 mM",
# "30 seconds"), which keeps prose like "the mM scale" or the word "um" from
# false-positiving. Longest alternatives first so "minutes" wins over "min".
_PHYSICAL_UNIT_RE = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*"
    r"(mmol/gDW/hr|mmol/gDW/h|mmol/L|mol/L|mM|µM|μM|uM|nM|pM|"
    r"µm|μm|um|nm|"
    r"seconds|second|minutes|minute|hours|hour|hrs|hr|min|ms)"
    r"(?![\w/])"
)


def _check_unearned_unit_labels(ctx: _LintContext) -> None:
    """G5 — units/time declared, or no physical unit labels in claims.

    A study whose findings / conclusions print physical unit labels (mM,
    mmol/gDW/hr, µm, seconds, …) implicitly claims a calibrated mapping from
    simulation ticks and arbitrary field values to physical quantities. That
    mapping must be DECLARED in a ``units_and_time`` block (what a tick maps
    to; what unit each field/quantity carries) — otherwise the labels are
    decorative and overstate the model's calibration.

    Fires ONE aggregated WARNING per study when claim/finding text
    (``findings[]`` and ``conclusion_logic`` strings) contains
    number-followed-by-unit labels and the spec has no non-empty
    ``units_and_time`` block. Silent when the block exists (its quality is
    the author's/reviewer's job, not the linter's) or when no unit labels
    appear. Warning, not an error — decorative units are an overclaim
    symptom, not a schema violation.
    """
    if ctx.slug == "<workspace>":
        return
    if _is_nonempty(ctx.spec.get("units_and_time")):
        return
    offenders: list = []
    findings = ctx.spec.get("findings") or []
    if isinstance(findings, list):
        for idx, f in enumerate(findings):
            for path, value in _walk_strings(f, prefix=f"findings[{idx}]"):
                m = _PHYSICAL_UNIT_RE.search(value)
                if m:
                    offenders.append((path, m.group(1)))
    cl = ctx.spec.get("conclusion_logic")
    if cl:
        for path, value in _walk_strings(cl, prefix="conclusion_logic"):
            m = _PHYSICAL_UNIT_RE.search(value)
            if m:
                offenders.append((path, m.group(1)))
    if not offenders:
        return
    shown = ", ".join(f"{p} ({u!r})" for p, u in offenders[:4])
    more = f" (+{len(offenders) - 4} more)" if len(offenders) > 4 else ""
    ctx.add(
        level="warning",
        field_path="units_and_time",
        message=(
            f"Study prints physical unit labels in claim/finding text — "
            f"{shown}{more} — but declares no `units_and_time` block. A "
            "physical unit implies a calibrated tick→time and value→quantity "
            "mapping; without the declaration the labels are decorative and "
            "overstate calibration. Either add `units_and_time:` (what one "
            "tick maps to; the unit each field/quantity carries, with the "
            "calibration source) or strip the unit labels from the claims."
        ),
        check="unearned_unit_labels",
    )


# --- Reproducibility — baseline carries a real run-config -------------------

# Keys that do NOT count as a reproducible run-config on their own. A baseline
# whose params carry only these (or nothing) is indistinguishable from
# composite defaults: {seed, cache_dir} pin reproducibility plumbing but not
# the scientific configuration the viewer/Run needs to reconstruct the study.
_BARE_BASELINE_PARAM_KEYS = frozenset({"seed", "cache_dir"})


def _read_baseline_condition(spec: dict) -> dict | None:
    """The effective baseline condition dict, or ``None`` when absent.

    Mirrors how ``_check_missing_baseline`` reads the baseline: prefer the v4
    ``conditions.baseline`` mapping, fall back to the first entry of the legacy
    v3 ``baseline[]`` list. Returns ``None`` when neither is present (the
    ``missing_baseline`` check owns that gap).
    """
    conditions = spec.get("conditions") or {}
    cond_baseline = conditions.get("baseline") if isinstance(conditions, dict) else None
    if isinstance(cond_baseline, dict) and cond_baseline:
        return cond_baseline
    baseline = spec.get("baseline")
    if isinstance(baseline, list) and baseline and isinstance(baseline[0], dict):
        return baseline[0]
    return None


def _baseline_params_are_rich(params) -> bool:
    """True when inline params add something beyond a bare {seed, cache_dir}."""
    if not isinstance(params, dict) or not params:
        return False
    for key, value in params.items():
        if isinstance(key, str) and key.strip().lower() in _BARE_BASELINE_PARAM_KEYS:
            continue
        if _is_nonempty(value):
            return True
    return False


def _resolve_baseline_config_file(ctx: _LintContext, config_file) -> Path | None:
    """Resolve a baseline ``config_file`` the way the workbench load_spec does.

    Absolute path used as-is; otherwise tried study-dir-relative then
    workspace-root-relative. Returns the first candidate that is an existing
    file, or ``None`` (unset / unresolvable).
    """
    if not isinstance(config_file, str) or not config_file.strip():
        return None
    raw = config_file.strip()
    p = Path(raw)
    if p.is_absolute():
        candidates = [p]
    else:
        try:
            study_dir = WorkspacePaths.load(ctx.ws_root).studies / ctx.slug
        except Exception:  # noqa: BLE001 — tolerate a missing workspace.yaml
            study_dir = ctx.ws_root / "studies" / ctx.slug
        candidates = [study_dir / raw, ctx.ws_root / raw]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _runtime_carries_run_config(runtime) -> bool:
    """True when the separate ``runtime:`` surface pins a real run-config.

    ``runtime:`` (emitter, generation count, post-run scripts) is read by the
    runner INDEPENDENTLY of baseline params, so a study that carries its
    run-config there is reproducible even with bare params — it must not be
    falsely flagged as bare.
    """
    if not isinstance(runtime, dict):
        return False
    for key in (
        "max_generations", "n_generations", "generations",
        "emitter", "default_emitter", "post_run_scripts",
        "subprocess_timeout_s",
    ):
        if _is_nonempty(runtime.get(key)):
            return True
    return False


def _check_baseline_config_carry(ctx: _LintContext) -> None:
    """Reproducibility — the baseline must resolve to a real run-config.

    The workbench viewer/Run reconstructs a study from the EFFECTIVE baseline
    params: composite defaults ⊕ ``conditions.baseline.config_file`` ⊕ inline
    ``conditions.baseline.params`` (config_file resolved absolute →
    study-dir-relative → workspace-root-relative). When that resolution adds
    NOTHING beyond composite defaults / a bare ``{seed, cache_dir}`` — no rich
    inline params, no resolvable config_file — AND the separate ``runtime:``
    surface (emitter, generation count) carries no run-config either, the
    viewer shows composite defaults rather than a reproducible spec, and a Run
    will not reproduce the study.

    Silent when there is no baseline at all (``missing_baseline`` owns that).
    Handles the v4 ``conditions.baseline`` mapping and the legacy ``baseline[]``
    list shape (mirrors ``_check_missing_baseline``).
    """
    if ctx.slug == "<workspace>":
        return
    cond = _read_baseline_condition(ctx.spec)
    if cond is None:
        return  # no baseline declared — missing_baseline handles that gap
    if _baseline_params_are_rich(cond.get("params")):
        return
    if _resolve_baseline_config_file(ctx, cond.get("config_file")) is not None:
        return
    if _runtime_carries_run_config(ctx.spec.get("runtime")):
        return
    ctx.add(
        level=_completeness_level(ctx.spec, warn_at="Build"),
        field_path="conditions.baseline",
        message=(
            "the viewer shows composite defaults, not a reproducible spec; "
            "Run will not reproduce this study — add "
            "conditions.baseline.config_file or fill conditions.baseline.params"
        ),
        check="baseline_config_carry",
    )


# ---------------------------------------------------------------------------
# Campaign quality bar + enrichment-plan design principles
# (campaign lessons, 2026-10 — see feedback_campaign_build_quality_bar +
# the Scientific Enrichment Plan §14 design principles).
# ---------------------------------------------------------------------------

_NUMBER_RE = _re.compile(r"-?\d")


def _finding_is_quantitative(f: dict) -> bool:
    """A finding is quantitative if it pins a number a reader could check.

    Keys on the structured slots the biology-forward scaffold fills:
    ``expected.range`` / ``expected.value`` / ``expected.band`` (an authored
    expectation), a numeric ``divergence_factor``, or an ``evidence.observed``
    string that contains a digit. A purely qualitative observation
    ("the pore concentrates solutes") is NOT quantitative and is left alone.
    """
    if not isinstance(f, dict):
        return False
    expected = f.get("expected")
    if isinstance(expected, dict):
        for key in ("range", "value", "band", "threshold"):
            if expected.get(key) is not None:
                return True
    if isinstance(f.get("divergence_factor"), (int, float)):
        return True
    ev = f.get("evidence")
    if isinstance(ev, dict):
        observed = ev.get("observed")
        if isinstance(observed, str) and _NUMBER_RE.search(observed):
            return True
    return False


def _check_finding_missing_cites(ctx: _LintContext) -> None:
    """A quantitative finding with no provenance → warning.

    Every quantitative claim should name its source: the memory quality bar
    item 4 (provenance on every quantitative claim). A finding that pins a
    number (``expected.range`` / observed value / divergence factor) but
    carries no ``expected.cites`` AND no ``expert_reference`` AND no
    ``provenance`` is making an unsourced quantitative claim. Warning (nudge,
    not blocking) — qualitative findings and findings that already carry any
    provenance handle are silent.
    """
    findings = ctx.spec.get("findings") or []
    if not isinstance(findings, list):
        return
    for idx, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        if not _finding_is_quantitative(f):
            continue
        expected = f.get("expected") if isinstance(f.get("expected"), dict) else {}
        has_cites = bool(expected.get("cites"))
        has_expert_ref = bool(f.get("expert_reference"))
        has_provenance = bool(f.get("provenance"))
        if has_cites or has_expert_ref or has_provenance:
            continue
        fid = f.get("id", f"<index-{idx}>")
        ctx.add(
            level="warning",
            field_path=f"findings[{idx}].expected.cites",
            message=(
                f"Finding {fid!r} makes a quantitative claim but carries no "
                "provenance. Add expected.cites: [bib_key] (→ references/papers.bib), "
                "an expert_reference, or a provenance note so the number is "
                "traceable to its source."
            ),
            check="finding_missing_cites",
        )


_DECISION_VIEW_TAGS = frozenset({"decision", "decide", "phase-map", "phase_map"})


def _viz_is_decision_view(v: dict) -> bool:
    """A visualization entry declared as the study's Decision view.

    Keys on an explicit ``view:``/``role:`` tag (``decision``), or a name/title
    that reads as a phase map. Deliberately narrow so the check only fires on a
    study that has actually opted into the three-view standard.
    """
    if not isinstance(v, dict):
        return False
    for key in ("view", "role"):
        val = str(v.get(key) or "").strip().lower()
        if val in _DECISION_VIEW_TAGS:
            return True
    return False


def _viz_is_phase_map(v: dict) -> bool:
    """True when a Decision-view entry is backed by a grid/phase-map, not a run.

    A phase map identifies PASS and FAILURE regions across a swept grid
    (enrichment-plan principle 1). It is backed by a persisted grid artifact,
    not a single canonical run. Accept any of: ``phase_map: true``, a
    ``grid``/``grid_artifact``/``sweep`` reference, or a ``kind`` that names a
    phase map / heatmap.
    """
    if not isinstance(v, dict):
        return False
    if v.get("phase_map") is True:
        return True
    for key in ("grid", "grid_artifact", "sweep", "sweep_artifact"):
        if v.get(key):
            return True
    kind = str(v.get("kind") or v.get("chart_kind") or "").strip().lower()
    if kind in {"phase-map", "phase_map", "phasemap", "heatmap"}:
        return True
    return False


def _check_decision_viz_single_run(ctx: _LintContext) -> None:
    """A Decision view that is a single run, not a phase map → warning.

    Enrichment-plan principle 1: every major claim identifies PASS and FAILURE
    regions, so the Decision view should be a phase map over a swept grid, not
    a representative trajectory. Fires only when the study declares a Decision
    view (``view: decision``) and none of its Decision-view entries is backed by
    a persisted grid/phase-map artifact. Warning — a nudge toward the standard.
    """
    vizzes = ctx.spec.get("visualizations") or []
    if not isinstance(vizzes, list):
        return
    decision = [v for v in vizzes if _viz_is_decision_view(v)]
    if not decision:
        return
    if any(_viz_is_phase_map(v) for v in decision):
        return
    names = ", ".join(
        str(v.get("name") or f"<index-{i}>") for i, v in enumerate(decision)
    )
    ctx.add(
        level="warning",
        field_path="visualizations",
        message=(
            f"Decision view ({names}) looks like a single run, not a phase map. "
            "A Decision view should identify PASS and FAILURE regions across a "
            "swept grid (phase map), not a representative trajectory. Back it "
            "with a persisted grid artifact and mark it phase_map: true (or "
            "grid_artifact: <path>). See the three-view standard in /viva-study."
        ),
        check="decision_viz_single_run",
    )


def _check_authored_confidence_diverges(ctx: _LintContext) -> None:
    """Authored confidence disagrees with the derived value → warning.

    A hand-set ``confidence:`` that :func:`study_verdict.derive_confidence`
    contradicts (e.g. ``Accepted`` over a ``failed`` verdict) makes the
    graph node, the rail dot, and the badge tell different stories. Warning:
    reconcile the authored value with the evidence, or drop it and let the
    derived value stand.
    """
    authored = str(ctx.spec.get("confidence") or "").strip()
    if not authored:
        return
    try:
        from discovery_engine.study import verdict as study_verdict
    except Exception:  # noqa: BLE001 — never let an import crash the lint run
        return
    if not study_verdict.confidence_diverges_from_derived(ctx.spec):
        return
    derived = study_verdict.derive_confidence(ctx.spec)
    ctx.add(
        level="warning",
        field_path="confidence",
        message=(
            f"Authored confidence {authored!r} diverges from the derived value "
            f"{derived!r} (from the study's verdict/status). Reconcile the "
            "authored confidence with the evidence, or remove it and let the "
            "derived value stand so the graph node, rail dot, and badge agree."
        ),
        check="authored_confidence_diverges",
    )


def _check_contract_observables(ctx: _LintContext) -> None:
    """Warn when a study declares a ``contract.face`` port its latest run never
    emits — a declared observable the simulation does not actually produce.

    Best-effort: resolving the run is wrapped so a missing/unreadable run (or any
    resolver error) leaves the lint run untouched rather than failing it.
    """
    try:
        findings = reconcile_study_observables(ctx.spec, ctx.ws_root)
    except Exception:  # noqa: BLE001 - best-effort; never break the lint run
        return
    for finding in findings:
        ctx.add(
            level=finding.get("severity", "warning"),
            field_path="contract.face",
            message=finding["message"],
            check="contract-observable",
        )


__all__ = [
    '_GATE_CLASSES',
    '_check_gate_class_declared',
    '_SOURCE_SCAN_SKIP',
    '_RNG_SIGNAL_RE',
    '_STOCHASTIC_NAME_RE',
    '_SEED_CONFIG_KEYS',
    '_workspace_process_class_index',
    '_COMPOSITE_SPEC_EXTS',
    '_composite_spec_dirs',
    '_resolve_composite_file',
    '_composite_process_nodes',
    '_composite_class_name',
    '_study_composite_specs',
    '_check_config_consumption',
    '_config_declares_seed',
    '_check_stochastic_unseeded',
    '_PHYSICAL_UNIT_RE',
    '_check_unearned_unit_labels',
    '_BARE_BASELINE_PARAM_KEYS',
    '_read_baseline_condition',
    '_baseline_params_are_rich',
    '_resolve_baseline_config_file',
    '_runtime_carries_run_config',
    '_check_baseline_config_carry',
    '_NUMBER_RE',
    '_finding_is_quantitative',
    '_check_finding_missing_cites',
    '_DECISION_VIEW_TAGS',
    '_viz_is_decision_view',
    '_viz_is_phase_map',
    '_check_decision_viz_single_run',
    '_check_authored_confidence_diverges',
    '_check_contract_observables',
]

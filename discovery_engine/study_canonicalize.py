"""Canonicalize a study's model declaration into the conditions.{baseline,variants}
form. Pure, in-place, comment-preserving-safe (moves value nodes, never reserializes)."""
from __future__ import annotations

import argparse
from io import StringIO
from pathlib import Path
from ruamel.yaml import YAML

from discovery_engine import study_io
from discovery_engine.workspace_paths import WorkspacePaths


def canonicalize_models(spec) -> dict:
    report = {"changed": False, "style": "canonical", "flags": [], "inherited_composites": []}
    top_baseline = spec.get("baseline")
    conditions = spec.get("conditions")

    # --- classify + move top-level baseline/variants into conditions (Style B / both) ---
    if isinstance(top_baseline, list):
        report["style"] = "both" if isinstance(conditions, dict) and conditions.get("baseline") else "B"
        if conditions is None or not isinstance(conditions, dict):
            spec["conditions"] = {}
            conditions = spec["conditions"]
        cond_baseline = conditions.get("baseline")
        cond_has_baseline = ((isinstance(cond_baseline, dict) and cond_baseline.get("composite"))
                             or (isinstance(cond_baseline, list) and cond_baseline))
        if cond_has_baseline:
            report["flags"].append("both_dropped_toplevel")   # conditions wins
        elif len(top_baseline) > 1:
            # Multi-arm study: several baseline arms, each a distinct composite.
            # A first-class, encouraged pattern — keep them ALL as a list rather
            # than collapsing to one baseline or refusing to migrate.
            conditions["baseline"] = top_baseline              # move the whole list (keeps comments)
            report["flags"].append("multi_arm")
        elif top_baseline:
            conditions["baseline"] = top_baseline[0]           # move node (keeps its comments)
        # move a top-level variants list in, if present and conditions lacks one
        top_variants = spec.get("variants")
        if isinstance(top_variants, list):
            if not conditions.get("variants"):
                conditions["variants"] = top_variants
            else:
                report["flags"].append("both_dropped_toplevel_variants")  # conditions wins
        for k in ("baseline", "variants"):
            if k in spec:
                del spec[k]
        report["changed"] = True

    conditions = spec.get("conditions")
    if not isinstance(conditions, dict) or not conditions.get("baseline"):
        return report  # nothing canonical to normalize (e.g. parca / non-model study)

    cond_baseline = conditions["baseline"]
    base_composite = cond_baseline.get("composite") if isinstance(cond_baseline, dict) else None

    # --- normalize variants: inherit composite, rename parameter_overrides -> params ---
    for v in (conditions.get("variants") or []):
        if not isinstance(v, dict):
            continue
        if "parameter_overrides" in v and "params" not in v:
            v["params"] = v.pop("parameter_overrides"); report["changed"] = True
        if not v.get("composite") and base_composite:
            v["composite"] = base_composite
            report["inherited_composites"].append(v.get("name", "?")); report["changed"] = True

    # --- ensure model_settings key exists (kept separate) ---
    if "model_settings" not in conditions:
        conditions["model_settings"] = []; report["changed"] = True

    return report


def _prereq_slugs(pg) -> list[str]:
    out = []
    for p in (pg.get("prerequisites") or []):
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict) and p.get("study"):
            out.append(str(p["study"]))
    return out


def backfill_question(spec) -> dict:
    """Ensure a conditions-form study has a top-level `question` string.

    The workbench routes a schema_version:4 study WITH a `conditions:` block into
    the strict v4-redesign validator, which requires a non-empty top-level
    `question`. When model canonicalization produces conditions form, lift an
    existing authored question into place (purpose.question -> description ->
    title). Never fabricates: if no source exists, flag `no_question_source` and
    leave the study without a question."""
    report = {"changed": False, "source": None, "flags": []}
    if not isinstance(spec.get("conditions"), dict):
        return report
    q = spec.get("question")
    if isinstance(q, str) and q.strip():
        return report
    purpose = spec.get("purpose")
    val = None
    src = None
    if isinstance(purpose, dict) and isinstance(purpose.get("question"), str) and purpose["question"].strip():
        val, src = purpose["question"], "purpose.question"
    elif isinstance(spec.get("description"), str) and spec["description"].strip():
        val, src = spec["description"], "description"
    elif isinstance(spec.get("title"), str) and spec["title"].strip():
        val, src = spec["title"], "title"
    if val is None:
        report["flags"].append("no_question_source")
        return report
    spec["question"] = val
    report["changed"] = True
    report["source"] = src
    return report


def normalize_tests_shape(spec) -> dict:
    """Make a conditions-form study's `tests` a LIST (the v4-redesign validator
    requires it). Legacy studies carry `tests` as a pytest-config dict; preserve a
    non-trivial config under `pytest_tests` and set `tests: []`. Trivial
    auto-discover stubs are dropped outright. No-op unless in conditions form with a
    dict `tests`."""
    report = {"changed": False, "preserved": False, "flags": []}
    if not isinstance(spec.get("conditions"), dict):
        return report
    t = spec.get("tests")
    if not isinstance(t, dict):
        return report
    trivial = (
        t.get("auto_discover", True) is True
        and not (t.get("pytest_args") or [])
        and t.get("last_results") in (None, [], {})
        and t.get("data_source", "latest_run") == "latest_run"
    )
    if not trivial:
        spec["pytest_tests"] = t
        report["preserved"] = True
    spec["tests"] = []
    report["changed"] = True
    return report


def canonicalize_ordering(spec, known_slugs) -> dict:
    report = {"changed": False, "added_inputs": [], "flags": []}
    pg = spec.get("pipeline_gate")
    parents = spec.get("parent_studies") or []
    prereqs = (_prereq_slugs(pg) if isinstance(pg, dict) else []) + [str(p) for p in parents]

    dangling = False
    if prereqs:
        inputs = spec.get("inputs")
        if not isinstance(inputs, list):
            inputs = []
            spec["inputs"] = inputs
        existing = {(e.get("artifact"), e.get("from")) for e in inputs if isinstance(e, dict)}
        for producer in prereqs:
            if producer not in known_slugs:
                report["flags"].append(f"dangling_prereq:{producer}")
                dangling = True
                continue
            if any(frm == producer for (_art, frm) in existing):
                continue
            edge = {"artifact": producer, "from": producer}
            inputs.append(edge)
            existing.add((producer, producer))
            report["added_inputs"].append(producer)
            report["changed"] = True

    if dangling:
        report["flags"].append("pipeline_gate_retained")
    else:
        if isinstance(pg, dict):
            for sub in ("prerequisites", "enables"):
                if sub in pg:
                    del pg[sub]
                    report["changed"] = True
            if not pg:
                del spec["pipeline_gate"]
                report["changed"] = True
        if "parent_studies" in spec:
            del spec["parent_studies"]
            report["changed"] = True
    return report


def _ruamel():
    y = YAML(); y.preserve_quotes = True; y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def migrate_study_file(study_dir, known_slugs, write: bool = False) -> dict:
    study_dir = Path(study_dir)
    path = study_dir / "study.yaml"
    y = _ruamel()
    spec = y.load(path.read_text(encoding="utf-8"))
    if spec is None:
        return {"models": {}, "ordering": {}, "question": {}, "tests": {}, "written": False}
    m = canonicalize_models(spec)
    o = canonicalize_ordering(spec, known_slugs)
    qb = backfill_question(spec)
    tn = normalize_tests_shape(spec)
    changed = bool(m.get("changed") or o.get("changed") or qb.get("changed") or tn.get("changed"))
    written = False
    if write and changed:
        buf = StringIO(); y.dump(spec, buf)
        study_io.atomic_write(path, buf.getvalue()); written = True
    return {"models": m, "ordering": o, "question": qb, "tests": tn, "written": written}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="viva-canonicalize-studies")
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--study", default=None, help="slug; default = all")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    wp = WorkspacePaths.load(args.workspace)
    known = {p.name for p in wp.iter_study_dirs()}
    targets = ([wp.study_dir(args.study)] if args.study
               else list(wp.iter_study_dirs()))
    any_flag = False
    for d in targets:
        rep = migrate_study_file(d, known_slugs=known, write=args.write)
        flags = (rep["models"].get("flags", []) + rep["ordering"].get("flags", [])
                 + rep.get("question", {}).get("flags", []) + rep.get("tests", {}).get("flags", []))
        mark = "WROTE" if rep["written"] else ("would-change" if (rep["models"].get("changed") or rep["ordering"].get("changed") or rep.get("question", {}).get("changed") or rep.get("tests", {}).get("changed")) else "ok")
        print(f"[{mark}] {d.name}" + (f"  flags={flags}" if flags else ""))
        any_flag = any_flag or bool(flags)
    if any_flag:
        print("\nNOTE: flags present (multi_baseline / dangling_prereq) need a human decision.")
    return 0

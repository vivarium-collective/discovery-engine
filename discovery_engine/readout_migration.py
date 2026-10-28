"""readout_migration.py — migrate legacy study readouts to canonical form.

Sub-project #7 of the Readout-coordination design
(``docs/specs/2026-06-09-readout-coordination-design.md`` §5.7).

Converts a study's legacy readouts (the three dialects — ``identifier:`` magic
indices / bulk expressions, ``store_path:``, and authored ``index_by``) to the
canonical structured form, using ``readout_resolver`` as the single source of
truth.  SAFE by construction:

  * ``migrate_readouts(spec)`` is a **pure** function (no I/O).
  * Unresolvable readouts (prose ``·`` groups, ``derived``, ambiguous) are
    KEPT untouched and flagged ``needs_human`` in the report — never guessed.
  * ``migrate_study_file`` defaults to **dry-run** (``write=False``); when
    ``write=True`` it rewrites only the ``readouts:`` block via a ruamel
    round-trip, preserving comments and all hand-authored non-readout content.
    It is a **true no-op** when nothing actually changes (all readouts already
    canonical / ``needs_human``): the file is left byte-for-byte identical.

NOTE: canonicalization rebuilds each migrated readout dict from its resolved
selector, so inline YAML comments attached to an *individual readout entry* are
not preserved across a rewrite — acceptable, since the readout is the unit being
rewritten.  Comments on non-readout content (and on the ``readouts:`` key
itself) survive intact.

Public API:
    migrate_readouts(spec) -> (new_readouts, report)
    migrate_study_file(study_dir, write=False) -> report
"""
from __future__ import annotations

from pathlib import Path

from discovery_engine.readout_resolver import (
    ResolvedReadout,
    UnresolvedReadout,
    resolve_readout,
)

# Fields carried through from the original readout onto the migrated one.
_CARRY_FIELDS = ("units", "description", "status")


# ---------------------------------------------------------------------------
# Pure migration
# ---------------------------------------------------------------------------

def _canonical_readout(original: dict, r: ResolvedReadout) -> tuple[dict, str]:
    """Build a canonical readout dict from a ResolvedReadout.

    Returns ``(new_readout, kind)``.  The new dict always re-resolves to the
    same selector via ``resolve_readout`` (round-trip guarantee).
    """
    out: dict = {"name": r.name}

    # description: prefer the original's, then fold in any resolver note
    # (e.g. a stripped trailing-prose qualifier) so nothing is lost.
    description = original.get("description")
    if r.notes:
        note = r.notes
        description = f"{description} — {note}" if description else note
    if description:
        out["description"] = description

    if r.kind == "element" and r.index_by is not None:
        out["index_by"] = dict(r.index_by)
        if r.observable:
            out["store_path"] = r.observable
        if r.aggregate:
            out["aggregate"] = r.aggregate

    elif r.kind == "scalar":
        # Scalar paths are already canonical (no magic index); store_path is
        # the resolvable canonical carrier.
        out["store_path"] = r.observable

    elif r.kind == "expression":
        # No magic index to migrate; the identifier string IS the canonical
        # human form. Reconstruct it (re-adding the `bulk ` prefix when the
        # expression selects bulk ids) so it re-resolves to kind=expression.
        ident = r.expression
        if r.observable == "bulk":
            ident = f"bulk {ident}"
        out["identifier"] = ident
        if r.aggregate:
            out["aggregate"] = r.aggregate

    # carry units/status (description handled above)
    for f in _CARRY_FIELDS:
        if f == "description":
            continue
        if f in original and original[f] is not None:
            out[f] = original[f]

    if r.units and "units" not in out:
        out["units"] = r.units

    return out, r.kind


def migrate_readouts(spec: dict) -> tuple[list[dict], dict]:
    """Migrate all readouts in ``spec`` to canonical form (pure, no I/O).

    Args:
        spec: parsed study.yaml dict; reads ``spec["readouts"]`` (list).

    Returns:
        ``(new_readouts, report)`` where ``new_readouts`` is a list parallel
        to the input (canonical dict for resolvable readouts; the **original
        dict untouched** for unresolvable ones), and ``report`` is::

            {
              "entries": [{name, status, kind?, source_dialect?, reason?}],
              "migrated": [names...],
              "needs_human": [{name, reason}...],
            }
    """
    readouts: list[dict] = spec.get("readouts") or []
    new_readouts: list[dict] = []
    entries: list[dict] = []
    migrated: list[str] = []
    needs_human: list[dict] = []

    for i, ro in enumerate(readouts):
        # Studies may author readouts as bare prose strings rather than
        # structured dicts (the pre-canonical dialect). Mirror the guard in
        # resolve_study_readouts: treat a string as unresolvable prose and flag
        # it needs_human instead of crashing on ``str.get`` (which otherwise
        # leaks an uncaught AttributeError up into the report linter and
        # silently voids that study's readout check).
        if isinstance(ro, str):
            new_readouts.append(ro)
            needs_human.append({
                "name": ro,
                "reason": (
                    "prose readout authored as a string, not a structured "
                    "readout dict — re-author against the composite's real "
                    "observables (/viva-study check-observables)"
                ),
            })
            entries.append({
                "name": ro,
                "status": "needs_human",
                "reason": "prose readout authored as a string",
            })
            continue

        name = ro.get("name", f"readout_{i}")
        resolved = resolve_readout(ro)

        if isinstance(resolved, UnresolvedReadout):
            # KEEP original untouched; flag for a human.
            new_readouts.append(ro)
            needs_human.append({"name": name, "reason": resolved.reason})
            entries.append({
                "name": name,
                "status": "needs_human",
                "reason": resolved.reason,
            })
            continue

        new_ro, kind = _canonical_readout(ro, resolved)
        new_readouts.append(new_ro)
        migrated.append(name)
        entries.append({
            "name": name,
            "status": "migrated",
            "kind": kind,
            "source_dialect": resolved.source_dialect,
        })

    report = {
        "entries": entries,
        "migrated": migrated,
        "needs_human": needs_human,
    }
    return new_readouts, report


# ---------------------------------------------------------------------------
# Study-file migration (comment-preserving, dry-run by default)
# ---------------------------------------------------------------------------

def _study_yaml_path(study_dir: Path | str) -> Path:
    p = Path(study_dir)
    return p if p.name == "study.yaml" else p / "study.yaml"


# ---------------------------------------------------------------------------
# Pure status classification (dry-run; never writes)
# ---------------------------------------------------------------------------

def readout_migration_status(study_dir: Path | str) -> dict:
    """Classify a study's readouts into migration buckets (PURE read).

    Loads the study spec, runs the **pure** ``migrate_readouts`` (a dry-run —
    no file is ever touched), and sorts every readout into one of three
    buckets:

      * ``needs_human``  — unresolvable (prose ``·`` groups, ``derived``,
        ambiguous). The migration keeps these untouched; a human must
        re-author them against the composite's real observables. Carried
        straight from the report as ``[{name, reason}, ...]``.
      * ``migratable``   — resolvable AND the canonical form DIFFERS from the
        original. A ``migrate_study_file(write=True)`` would safely rewrite
        these (meaning-preserving). Returned as the *original* readout dicts.
      * ``canonical``    — resolvable AND already canonical (a dry-run migrate
        leaves them byte-identical). Nothing to do.

    This function performs NO writes — it only reads ``study.yaml``. The actual
    rewrite is ``migrate_study_file(write=True)``, invoked only by the skills.

    Args:
        study_dir: the study directory (or the ``study.yaml`` path itself).

    Returns:
        ``{"canonical": [readout, ...], "migratable": [readout, ...],
           "needs_human": [{"name": ..., "reason": ...}, ...]}``
    """
    from discovery_engine import study_io

    study_yaml = _study_yaml_path(study_dir)
    spec = study_io.load_yaml_mapping(study_yaml)

    originals: list[dict] = spec.get("readouts") or []
    new_readouts, report = migrate_readouts(spec)

    canonical: list[dict] = []
    migratable: list[dict] = []
    for original, new, entry in zip(originals, new_readouts, report["entries"]):
        if entry.get("status") == "needs_human":
            continue  # already accounted for in report["needs_human"]
        if new == original:
            canonical.append(original)
        else:
            migratable.append(original)

    return {
        "canonical": canonical,
        "migratable": migratable,
        "needs_human": list(report["needs_human"]),
    }


def migrate_study_file(study_dir: Path | str, write: bool = False) -> dict:
    """Migrate a study.yaml's readouts in place (default: dry-run).

    Args:
        study_dir: the study directory (or the study.yaml path itself).
        write:     when ``False`` (default), compute the migration and return
                   the report WITHOUT touching the file.  When ``True``,
                   rewrite ONLY the ``readouts:`` block via a ruamel round-trip
                   (comment- and formatting-preserving); all other
                   hand-authored content is left byte-for-byte intact.  When no
                   readout actually changes (all already canonical /
                   ``needs_human``), this is a **true no-op** — the file is left
                   byte-for-byte identical and nothing is written.

    Returns:
        The ``migrate_readouts`` report, augmented with:

          * ``"study_yaml"`` (path),
          * ``"written"`` (bool — whether the file was actually rewritten),
          * ``"changed"`` (bool — whether canonicalization would change any
            readout; ``False`` ⇒ already-canonical no-op even in dry-run), and
          * ``"canonicalized"`` (list of names actually rewritten this call —
            the *changed* subset of ``"migrated"``, which excludes readouts that
            were already canonical).

    Inline comments on an *individual readout entry* are not preserved across a
    rewrite (the readout dict is rebuilt from its resolved selector); comments
    on non-readout content survive intact.
    """
    from io import StringIO

    from ruamel.yaml import YAML

    study_yaml = _study_yaml_path(study_dir)
    if not study_yaml.is_file():
        raise FileNotFoundError(f"no study.yaml at {study_yaml}")

    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096  # avoid line-wrap reflow on long prose values
    # Match the workspace study.yaml block-seq convention (`-` at offset 2,
    # content at column 4) so a fresh readouts list is NOT reindented to the
    # ruamel default (0-offset) — keeps the readouts block's original shape.
    ryaml.indent(mapping=2, sequence=4, offset=2)

    rt_spec = ryaml.load(study_yaml.read_text(encoding="utf-8"))
    if rt_spec is None:
        rt_spec = {}

    new_readouts, report = migrate_readouts(rt_spec)
    report["study_yaml"] = str(study_yaml)
    report["written"] = False

    # Which readouts actually change (vs. already-canonical / needs_human, which
    # round-trip to the same value).  Drives both the no-op short-circuit (FIX 2)
    # and the accurate "canonicalized" count (FIX 3).
    originals = list(rt_spec.get("readouts") or [])
    canonicalized: list[str] = []
    for new_ro, orig in zip(new_readouts, originals):
        if new_ro != orig and isinstance(new_ro, dict):
            name = new_ro.get("name")
            if name:
                canonicalized.append(name)
    changed = len(new_readouts) != len(originals) or any(
        new_ro != orig for new_ro, orig in zip(new_readouts, originals)
    )
    report["changed"] = changed
    report["canonicalized"] = canonicalized

    # No write requested, or nothing actually changed → true no-op (the file is
    # left byte-for-byte identical; we never re-dump an already-canonical study).
    if not write or not changed:
        return report

    # Replace ONLY the readouts block; everything else stays as loaded.
    rt_spec["readouts"] = new_readouts

    buf = StringIO()
    ryaml.dump(rt_spec, buf)
    # atomic write via the shared helper
    from discovery_engine import study_io
    study_io.atomic_write(study_yaml, buf.getvalue())
    report["written"] = True
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse
    import json

    from discovery_engine.workspace_paths import WorkspacePaths

    ap = argparse.ArgumentParser(
        prog="pbg-migrate-readouts",
        description="Migrate a study's legacy readouts to canonical form "
                    "(dry-run by default).",
    )
    ap.add_argument("--workspace", default=".", help="workspace root")
    ap.add_argument("--study", required=True, help="study slug")
    ap.add_argument(
        "--write", action="store_true",
        help="rewrite study.yaml's readouts block (default: dry-run)",
    )
    args = ap.parse_args(argv)

    paths = WorkspacePaths.load(Path(args.workspace))
    study_dir = paths.study_dir(args.study)
    report = migrate_study_file(study_dir, write=args.write)

    print(json.dumps({
        "study": args.study,
        "study_yaml": report["study_yaml"],
        "written": report["written"],
        "migrated": report["migrated"],
        "needs_human": report["needs_human"],
    }, indent=2))
    if report["needs_human"]:
        print(f"\n{len(report['needs_human'])} readout(s) need human review "
              "(kept untouched).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

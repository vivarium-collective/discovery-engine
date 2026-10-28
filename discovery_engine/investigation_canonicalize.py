"""Canonicalize investigation.yaml: the study-list key is `members:` (not `studies:`)."""
from __future__ import annotations
import argparse
from io import StringIO
from pathlib import Path
from ruamel.yaml import YAML
from discovery_engine import study_io
from discovery_engine.workspace_paths import WorkspacePaths


def canonicalize_investigation(spec) -> dict:
    report = {"changed": False, "flags": []}
    if "studies" not in spec:
        return report
    if "members" in spec:
        report["flags"].append("both_keys_present")
        return report
    # rename studies -> members, preserving order/comments by moving the node
    spec["members"] = spec.pop("studies")
    report["changed"] = True
    return report


def _ruamel():
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def migrate_investigation_file(inv_dir, write: bool = False) -> dict:
    path = Path(inv_dir) / "investigation.yaml"
    y = _ruamel()
    spec = y.load(path.read_text(encoding="utf-8"))
    if spec is None:
        return {"changed": False, "flags": [], "written": False}
    rep = canonicalize_investigation(spec)
    written = False
    if write and rep["changed"]:
        buf = StringIO()
        y.dump(spec, buf)
        study_io.atomic_write(path, buf.getvalue())
        written = True
    rep["written"] = written
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="viva-canonicalize-investigations")
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--investigation", default=None)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    wp = WorkspacePaths.load(args.workspace)
    inv_root = wp.dir("investigations")
    if not inv_root.is_dir():
        print(f"no investigations/ directory at {inv_root}")
        return 0
    if args.investigation:
        d = inv_root / args.investigation
        if not d.is_dir():
            print(f"investigation {args.investigation!r} not found under {inv_root}")
            return 0
        targets = [d]
    else:
        targets = [p for p in sorted(inv_root.iterdir())
                   if (p / "investigation.yaml").is_file()]
    for d in targets:
        rep = migrate_investigation_file(d, write=args.write)
        mark = "WROTE" if rep["written"] else ("would-change" if rep["changed"] else "ok")
        print(f"[{mark}] {d.name}" + (f"  flags={rep['flags']}" if rep["flags"] else ""))
    return 0

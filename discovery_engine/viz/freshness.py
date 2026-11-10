"""Pure run->chart freshness core (single source of truth; vendored into the
dashboard). A chart's <chart>.meta.json records which run produced it; freshness
compares that against the study's latest run."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FRESH, STALE, UNRENDERED, UNTRACKED = "fresh", "stale", "unrendered", "untracked"
BROKEN_STUB = "broken_stub"

_CLASS_SCHEMES = ("local", "dashboard")
_FIGURE_SCHEMES = ("svg", "png", "gif", "image", "file", "html", "threejs")
_FIGURE_SUFFIXES = (".svg", ".png", ".gif", ".jpg", ".jpeg", ".webp", ".html", ".htm")
_POINTER_FIELDS = ("chart", "file", "path", "src")


def _split_address(address) -> tuple[str | None, str]:
    if not isinstance(address, str) or ":" not in address:
        return None, ""
    scheme, _, ref = address.partition(":")
    return scheme.strip().lower(), ref.strip()


def normalize_entry(entry: dict) -> dict:
    """Pure: the derived VizSpec for one study.yaml visualizations[] entry.

    kind: class (local:/dashboard:) | figure (static/iframe address or a
    figure-suffixed pointer field) | command (render: present) | broken_stub
    (no renderable identity; `rendered:` is deliberately NOT a pointer)."""
    entry = entry or {}
    name = entry.get("name")
    address = entry.get("address") or None
    render = entry.get("render") or None
    scheme, ref = _split_address(address)
    chart = entry.get("chart") or entry.get("output") or None
    if not chart:
        for f in _POINTER_FIELDS[1:]:
            v = entry.get(f)
            if isinstance(v, str) and v.lower().endswith(_FIGURE_SUFFIXES):
                chart = v
                break
    default_out = f"viz/{name}.html" if name else None
    if scheme in _CLASS_SCHEMES and not render:
        kind, output = "class", chart or default_out
    elif render:
        kind = "command"
        output = chart or (ref if scheme in _FIGURE_SCHEMES and ref else None) or default_out
    elif scheme in _FIGURE_SCHEMES and ref:
        kind, output = "figure", chart or ref
    elif chart:
        kind, output = "figure", chart
    else:
        kind, output = BROKEN_STUB, None
    requires = entry.get("requires")
    return {
        "name": name,
        "kind": kind,
        "address": address,
        "output": output,
        "render": render,
        "requires": list(requires) if isinstance(requires, (list, tuple)) else [],
        "source_run": entry.get("source_run") or None,
        "covers": list(entry.get("covers") or []),
    }


def classify_viz(spec: dict, row: dict, latest: dict | None, mv: str | None = None) -> str:
    """Pure: fresh | stale | unrendered | broken_stub (decision §2B).

    `row` = {path, exists, meta, mtime, content_hash} (inventory data);
    `latest` = the study's latest run {run_id, completed_at} or None."""
    if spec.get("kind") == BROKEN_STUB:
        return BROKEN_STUB
    row = row or {}
    if not row.get("exists"):
        return UNRENDERED
    completed = (latest or {}).get("completed_at")
    meta = row.get("meta")
    if not meta:
        if not latest or completed is None:
            return FRESH
        mtime = row.get("mtime")
        return FRESH if mtime is not None and float(mtime) >= float(completed) else STALE
    pinned = spec.get("source_run")
    if pinned:
        return FRESH if meta.get("source_run_id") == pinned else STALE
    if meta.get("model_version") and not spec.get("requires") and mv:
        return FRESH if meta["model_version"] == mv else STALE
    if not latest or meta.get("source_run_id") != latest.get("run_id"):
        return STALE
    if completed is not None and (meta.get("rendered_at") or 0) < float(completed):
        return STALE
    return FRESH


def _meta_path(chart: Path) -> Path:
    return chart.with_suffix(chart.suffix + ".meta.json")


def _hash(chart: Path) -> str:
    h = hashlib.sha256(chart.read_bytes()).hexdigest()
    return f"sha256:{h}"


def stamp_meta(chart: Path, *, source_run_id: str | None, generation_id: str | None,
               rendered_at: float, command: str,
               model_version: str | None = None) -> None:
    """Write/overwrite <chart>.meta.json with provenance for `chart`."""
    meta = {
        "source_run_id": source_run_id,
        "generation_id": generation_id,
        "rendered_at": float(rendered_at),
        "command": command,
        "content_hash": _hash(chart) if chart.is_file() else None,
    }
    if model_version:
        meta["model_version"] = model_version
    _meta_path(chart).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def read_meta(chart: Path) -> dict | None:
    p = _meta_path(chart)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def chart_freshness(study_dir: Path, entry: dict, latest: dict | None,
                    mv: str | None = None) -> str:
    """fresh | stale | unrendered (for a declared visualizations[] entry or a
    normalized spec). Absent meta falls back to mtime vs latest.completed_at."""
    spec = normalize_entry(entry)
    if not spec["output"]:
        return UNRENDERED
    chart = Path(study_dir) / spec["output"]
    if not chart.is_file():
        return UNRENDERED
    row = {"exists": True, "meta": read_meta(chart), "mtime": chart.stat().st_mtime}
    return classify_viz(spec, row, latest, mv=mv)


def manifest_diff(study_dir: Path, entries: list[dict]) -> dict:
    """Compare declared entries against charts/*.svg|png on disk.

    Returns {"declared": [...], "untracked": [...]} where untracked are chart
    files present on disk but absent from visualizations[]."""
    declared = {e.get("chart") for e in entries if e.get("chart")}
    charts_dir = Path(study_dir) / "charts"
    on_disk = set()
    if charts_dir.is_dir():
        for p in charts_dir.glob("*.svg"):
            on_disk.add(f"charts/{p.name}")
        for p in charts_dir.glob("*.png"):
            on_disk.add(f"charts/{p.name}")
    return {"declared": sorted(declared), "untracked": sorted(on_disk - declared)}

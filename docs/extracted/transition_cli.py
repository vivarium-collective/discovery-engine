# moved out of the framework — domain analysis belongs in the workspace (viva-genesis).
# Former `discover transition` CLI pieces from discovery_engine/discover.py (verbatim).

_OUTCOME_GLYPH = {"stable-endosymbiosis": "██ stable", "collapse": "░░ collapse",
                  "transient-association": "▒▒ transient", "no-transition": "·· none"}


def _summarize_transition(report: dict) -> str:
    """Render the Cycle N+2 composed model + the conditional transition phase diagram."""
    mv = report["model_version"]
    lines = [f"Proto-Endosymbiosis Composite — {mv['id']}",
             f"  built on Cycle N+1 versions: {mv['parent_ids'] or '(none pinned)'}",
             f"  mechanisms: {', '.join(mv['components'])}"]
    if mv.get("coupling_issues"):
        for i in mv["coupling_issues"]:
            lines.append(f"    ⚑ {i}")
    lines.append("")
    lines.append("conditional transition phase diagram (proximity × pump × heredity):")
    for cell in report["surface"]:
        p = cell["point"]
        coords = f"proximity={p['proximity']:<9} pump={p['pump']:<9} heredity={p['heredity']:<4}"
        lines.append(f"    {coords} → {_OUTCOME_GLYPH.get(cell['outcome'], cell['outcome'])}")
    lines.append("  tally: " + ", ".join(f"{k}×{v}" for k, v in sorted(report["tally"].items())))
    if report["caveats"]:
        lines.append("  conditional on (unresolved upstream):")
        for c in report["caveats"]:
            lines.append(f"    ⚑ {c}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------

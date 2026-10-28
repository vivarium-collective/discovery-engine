"""Reference-driven report-card grading + verdict serialization/rendering.

The reference fixture declares **axes** — each with a presentation block (group,
label, ...) and a typed ``criterion`` (see ``card_criteria``). The measured
*card* supplies the value for each axis path. ``grade_card`` digs each path,
grades it via ``card_criteria.grade_axis``, and rolls up the worst verdict.
``verdict_json`` serializes that into the machine-readable ``report_card_verdict/v1``
schema (axes regrouped by slugged group, group verdict = worst axis).
``render_verdict_html`` renders a stored v1 verdict into a self-contained HTML card.

Generic — no organism/sim_data imports. The verdict vocabulary (RANK/COLOR/GLYPH)
comes from ``discovery_engine.test_vocab``, the single home for it. This is the
shared slice of what used to live in ``v2ecoli.library.report_card``; the
organism-specific card assembly (vector merges, rich plotted renderers) stays in
v2ecoli.
"""
from __future__ import annotations

from typing import Any

from discovery_engine.card_criteria import grade_axis
from discovery_engine.test_vocab import COLOR as _COLOR
from discovery_engine.test_vocab import GLYPH as _GLYPH
from discovery_engine.test_vocab import RANK as _RANK


def dig(card: dict, path: str) -> Any:
    """Follow a dotted ``path`` into a nested dict; ``None`` if any part is absent."""
    node = card
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def grade_card(card: dict, reference: dict) -> dict:
    """Grade every axis the reference declares. Returns
    ``{overall, axes: {path: {...presentation, ...grade}}}``."""
    axes_out: dict[str, dict] = {}
    worst = "ungraded"
    for path, spec in (reference.get("axes") or {}).items():
        measured = dig(card, path)
        g = grade_axis(measured, spec.get("criterion", {}))
        axes_out[path] = {**spec, **g, "path": path, "measured": measured}
        if _RANK[g["verdict"]] > _RANK[worst]:
            worst = g["verdict"]
    return {"overall": worst, "axes": axes_out}


def _slug_group(label: str) -> str:
    """'Exchange fluxes' -> 'exchange_fluxes'; 'Gene expression' -> 'gene_expression'."""
    return (label or "ungrouped").strip().lower().replace("&", "and").replace(" ", "_")


def verdict_json(report: dict, *, model_ref: str = "", reference_model: str = "",
                 generated: str = "") -> dict:
    """Serialize a grade_card() report into the machine-readable v1 verdict schema.

    grade_card returns FLAT axes keyed by path, each carrying a `group` label and
    a `verdict`. This regroups them by slugged group name and computes each
    group's verdict as the worst (most severe) axis verdict in that group.
    """
    groups: dict[str, dict] = {}
    for path, ax in (report.get("axes") or {}).items():
        gslug = _slug_group(ax.get("group", ""))
        g = groups.setdefault(gslug, {"verdict": "ungraded", "axes": []})
        v = ax.get("verdict", "ungraded")
        g["axes"].append({
            "id": path,
            "label": ax.get("label", path),
            "verdict": v,
            "value": ax.get("value"),
            "meter": ax.get("meter"),
            "detail": ax.get("detail") or {},
            "criterion": ax.get("criterion"),
            "measured": ax.get("measured"),
        })
        if _RANK.get(v, 0) > _RANK.get(g["verdict"], 0):
            g["verdict"] = v
    return {
        "schema": "report_card_verdict/v1",
        "model_ref": model_ref,
        "reference_model": reference_model,
        "generated": generated,
        "overall": report.get("overall", "ungraded"),
        "groups": groups,
    }


def render_verdict_html(verdict: dict, *, title: str | None = None) -> str:
    """Render a stored ``report_card_verdict/v1`` dict into a self-contained HTML
    card (no external assets): one section per group, one row per axis. Reuses the
    card colour/glyph vocabulary so it matches grade_card-rendered cards. Emits no
    timestamp (callers commit the output — keep it deterministic)."""
    import html as _html

    overall = verdict.get("overall", "ungraded")
    title = title or verdict.get("title") or "Report card"
    ref_model = verdict.get("reference_model", "")
    meas_model = verdict.get("model_ref", "")

    def chip(v: str, label: str | None = None) -> str:
        c = _COLOR.get(v, _COLOR["ungraded"])
        g = _GLYPH.get(v, _GLYPH["ungraded"])
        txt = _html.escape(label or v.replace("_", " "))
        return (f"<span style='display:inline-block;padding:2px 8px;border-radius:10px;"
                f"background:{c};color:#fff;font-size:12px'>{g} {txt}</span>")

    def val_str(val) -> str:
        if val is None:
            return ""
        if isinstance(val, bool):
            return str(val)
        if isinstance(val, (int, float)):
            return _html.escape(f"{val:.4g}")
        return _html.escape(str(val))

    # Tally axis verdicts across every group for the header summary.
    tally = {"within_tol": 0, "drift": 0, "mismatch": 0, "ungraded": 0}
    for grp in (verdict.get("groups") or {}).values():
        for ax in grp.get("axes", []):
            v = ax.get("verdict", "ungraded")
            tally[v if v in tally else "ungraded"] += 1

    def counts_str(scope=None) -> str:
        # scope = a group dict → count just that group; else the whole card.
        t = {"within_tol": 0, "drift": 0, "mismatch": 0, "ungraded": 0}
        groups = [scope] if scope is not None else list((verdict.get("groups") or {}).values())
        for grp in groups:
            for ax in grp.get("axes", []):
                v = ax.get("verdict", "ungraded")
                t[v if v in t else "ungraded"] += 1
        parts = []
        for v in ("within_tol", "drift", "mismatch", "ungraded"):
            if t[v]:
                parts.append(f"<span style='color:{_COLOR.get(v)}'>{t[v]} {_GLYPH.get(v)}</span>")
        return " &nbsp; ".join(parts)

    sections = []
    for gslug, grp in (verdict.get("groups") or {}).items():
        rows = []
        for i, ax in enumerate(grp.get("axes", [])):
            bg = "#ffffff" if i % 2 == 0 else "#f8fafc"
            rows.append(
                f"<tr style='background:{bg};border-top:1px solid #eef2f7'>"
                f"<td style='padding:7px 12px'>{_html.escape(str(ax.get('label', ax.get('id', ''))))}</td>"
                f"<td style='padding:7px 12px'>{chip(ax.get('verdict', 'ungraded'))}</td>"
                f"<td style='padding:7px 12px;font-variant-numeric:tabular-nums;color:#0f172a'>{val_str(ax.get('value'))}</td>"
                f"<td style='padding:7px 12px;color:#475569;font-size:12px'>{_html.escape(str(ax.get('meter', '') or ''))}</td>"
                "</tr>")
        sections.append(
            "<section style='border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;"
            "margin:0 0 14px;box-shadow:0 1px 2px rgba(15,23,42,0.04)'>"
            "<header style='display:flex;align-items:center;gap:10px;padding:9px 14px;"
            "background:#f1f5f9;border-bottom:1px solid #e2e8f0'>"
            f"<strong style='font-size:14px;color:#0f172a'>{_html.escape(gslug.replace('_', ' ').title())}</strong>"
            f"{chip(grp.get('verdict', 'ungraded'))}"
            f"<span style='margin-left:auto;font-size:12px'>{counts_str(grp)}</span>"
            "</header>"
            "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
            "<thead><tr style='text-align:left;color:#94a3b8;font-size:11px;"
            "text-transform:uppercase;letter-spacing:0.04em'>"
            "<th style='padding:6px 12px'>Axis</th><th style='padding:6px 12px'>Verdict</th>"
            "<th style='padding:6px 12px'>Value</th><th style='padding:6px 12px'>Δ / stats</th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>")

    sub = " &nbsp;·&nbsp; ".join(x for x in [
        f"reference: <code>{_html.escape(ref_model)}</code>" if ref_model else "",
        f"measured: <code>{_html.escape(meas_model)}</code>" if meas_model else ""] if x)
    header = (
        "<header style='background:#0f172a;color:#e2e8f0;padding:16px 20px;"
        "border-radius:12px 12px 0 0'>"
        f"<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap'>"
        f"<h2 style='margin:0;font-size:18px;color:#fff'>{_html.escape(title)}</h2>"
        f"{chip(overall, 'overall: ' + overall.replace('_', ' '))}"
        f"<span style='margin-left:auto;font-size:13px'>{counts_str()}</span>"
        "</div>"
        f"<div style='color:#94a3b8;font-size:12px;margin-top:6px'>{sub}</div>"
        "</header>")
    return (
        "<div style='font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
        "max-width:960px;color:#0f172a'>"
        f"{header}"
        "<div style='border:1px solid #e2e8f0;border-top:0;border-radius:0 0 12px 12px;"
        f"padding:16px 16px 4px'>{''.join(sections)}</div></div>")

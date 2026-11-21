#!/usr/bin/env python3
"""Sync the interactive cycle map's embedded data with the canonical source.

``docs/cycle-spec.json`` is the single source of truth for the cycle map. The
interactive map (``docs/cycle-map.html``) carries a verbatim copy of it in a
``<script type="application/json" id="cycle-spec">`` block and renders from that,
so it works offline (no fetch). This script re-injects the canonical JSON into
that block. Run it after editing ``cycle-spec.json``:

    python docs/build_cycle_map.py          # rewrite the embedded block
    python docs/build_cycle_map.py --check   # exit 1 if the HTML is stale (CI)

``tests/test_cycle_spec_drift.py`` enforces the same equality in the test suite.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_DOCS = Path(__file__).resolve().parent
_SPEC = _DOCS / "cycle-spec.json"
_HTML = _DOCS / "cycle-map.html"
_BLOCK = re.compile(
    r'(<script[^>]*id="cycle-spec"[^>]*>)(.*?)(</script>)', re.DOTALL)


def _canonical_json() -> str:
    spec = json.loads(_SPEC.read_text(encoding="utf-8"))
    return json.dumps(spec, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the embedded block is stale (no write)")
    args = ap.parse_args()

    html = _HTML.read_text(encoding="utf-8")
    m = _BLOCK.search(html)
    if not m:
        print("error: no <script id=\"cycle-spec\"> block in cycle-map.html", file=sys.stderr)
        return 2
    payload = _canonical_json()
    current = m.group(2).strip()
    if current == payload:
        print("cycle-map.html is up to date with cycle-spec.json")
        return 0
    if args.check:
        print("cycle-map.html is STALE — run: python docs/build_cycle_map.py", file=sys.stderr)
        return 1
    new_html = html[:m.start(2)] + "\n" + payload + "\n" + html[m.end(2):]
    _HTML.write_text(new_html, encoding="utf-8")
    print("rewrote the embedded cycle-spec block in cycle-map.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

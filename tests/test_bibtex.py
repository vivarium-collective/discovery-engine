"""Tests for discovery_engine.bibtex — the unified workspace bib-key source.

Locks in the correctness fix: study verify and the report linter must resolve
the SAME bib file with the SAME parser (they used to disagree).
"""
from __future__ import annotations

from pathlib import Path

from discovery_engine import bibtex


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "references").mkdir()
    return tmp_path


def test_parses_keys_with_and_without_trailing_comma(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "references" / "papers.bib").write_text(
        "@article{smith2024,\n  title={X},\n}\n@misc{jones_2023}\n"
    )
    assert bibtex.bib_keys(ws) == {"smith2024", "jones_2023"}


def test_missing_file_returns_empty_set_by_default(tmp_path: Path):
    assert bibtex.bib_keys(_ws(tmp_path)) == set()


def test_missing_file_returns_none_with_missing_ok(tmp_path: Path):
    assert bibtex.bib_keys(_ws(tmp_path), missing_ok=True) is None


def test_filename_precedence_prefers_papers_bib(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "references" / "papers.bib").write_text("@article{canonical,\n}\n")
    (ws / "references" / "references.bib").write_text("@article{fallback,\n}\n")
    assert bibtex.bib_keys(ws) == {"canonical"}


def test_falls_back_to_references_bib(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "references" / "references.bib").write_text("@article{fb,\n}\n")
    assert bibtex.bib_keys(ws) == {"fb"}


# NOTE: the cross-module "verify and findings agree on the same bib file" tests
# moved to the workbench in Phase 2.1k (batch 1) alongside study_verify /
# study_findings — see vivarium_workbench/tests/test_study_{verify,findings}.py.
# This file now covers only discovery_engine.bibtex itself (a STAY core module).

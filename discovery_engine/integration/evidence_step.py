"""``EvidenceStep`` — the post-sim Step that routes a study's graded verdict to the
claims it references, as Evidence records.

Sibling of ``post_sim.TestStep`` in the post-sim composite (``ResultsStep ->
{TestStep -> EvidenceStep}``). Read-only: it emits an ``evidence`` node and writes
nothing — the durable commit is WP4, at the authority boundary. Defined here (its
own module, like the workbench ``KnowledgeStep``); wiring it into the production
run graph is a follow-up, so this never perturbs an existing composite.
"""
from __future__ import annotations

import process_bigraph

from discovery_engine.projection.evidence_routing import study_evidence


class EvidenceStep(process_bigraph.Step):
    """Reads the study context, derives the study's verdict and the claims it
    references (``knowledge_inputs``), and emits Evidence records. Read-only."""

    config_schema: dict = {}

    def inputs(self):
        return {"study": "tree"}

    def outputs(self):
        return {"evidence": "tree"}

    def update(self, state, interval=None):
        study = (state or {}).get("study")
        if study is None:
            return {"evidence": []}
        # accept either a StudyContext (post-sim convention) or a raw spec dict
        spec = getattr(study, "spec", study) or {}
        slug = getattr(study, "study_name", None) or spec.get("name") or ""
        return {"evidence": study_evidence(spec, slug)}

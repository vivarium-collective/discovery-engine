"""SkillRegistry: maps a generation-request ``kind`` to its skill contract.

Each Skill wraps the EXISTING validator (``coerce``, the honesty gate, raises
ValueError on a bad artifact) and canonical writer (``record``) for the kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Skill:
    kind: str
    name: str
    proposal_type: str
    skill_md: str
    produces: str
    consequence: str
    coerce: Callable[[dict], dict]
    record: Callable[[Any, dict], dict]


@dataclass(frozen=True)
class SkillRegistry:
    _skills: dict[str, Skill]

    def get(self, kind: str) -> Skill | None:
        return self._skills.get(kind)

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._skills)


def default_registry() -> SkillRegistry:
    # Imported here to avoid import cycles.
    from discovery_engine.discover.actions import (
        record_critique_report,
        record_hypothesis_proposal,
    )
    from discovery_engine.phases.critique_report import coerce_critique_report
    from discovery_engine.phases.hypothesis_proposal import coerce_hypothesis_proposal

    skills = (
        Skill(
            kind="hypothesize",
            name="hypothesize",
            proposal_type="HypothesisProposal",
            skill_md="discovery_engine/skills/hypothesize/SKILL.md",
            produces="hypothesis_proposal/v1",
            consequence="authoring",
            coerce=coerce_hypothesis_proposal,
            record=record_hypothesis_proposal,
        ),
        Skill(
            kind="critique",
            name="critique",
            proposal_type="CritiqueReport",
            skill_md="discovery_engine/skills/critique/SKILL.md",
            produces="critique_report/v1",
            consequence="authoring",
            coerce=coerce_critique_report,
            record=record_critique_report,
        ),
    )
    return SkillRegistry({s.kind: s for s in skills})

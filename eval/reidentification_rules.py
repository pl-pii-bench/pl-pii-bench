"""Residual-identifiability rule set (methodology §6.4).

Each rule names an exact combination of schema-backed quasi-identifier
labels that, if all are left undetected for the same `entity_id` subject,
is deemed to leave that subject re-identifiable. This is a deterministic,
published, adversary-free rule, not a probabilistic linkage model.

A rule only evaluates for an `entity_id` subject when the ground truth for
that subject contains an entity for every labeled slot in the rule.
Subjects missing a slot are not evaluated against that rule.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReidentificationRule:
    name: str
    labels: tuple[str, ...]
    note: str = ""


REIDENTIFICATION_RULES: tuple[ReidentificationRule, ...] = (
    ReidentificationRule(
        name="DOB+LOC",
        labels=("DOB", "LOC"),
        note="Date of birth combined with a locality.",
    ),
    ReidentificationRule(
        name="DOB+POSTAL",
        labels=("DOB", "POSTAL"),
        note="Date of birth combined with a postal code.",
    ),
)

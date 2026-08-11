"""Metric computation for pl-pii-bench (methodology §6).

`evaluate_corpus` does the matching once per document (strict + relaxed for
every lane, including pdf); every other function in this module is a pure
aggregation over those match results plus entity metadata already carried
on `Entity` (protection, identifier_class, case, id_format, date_format,
entity_id). Nothing here re-touches the corpus text.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from eval.loader import Document, Entity, TRACKED_LABELS
from eval.matching import DocMatchResult, match_document
from eval.predictions import PredictedSpan
from eval.reidentification_rules import REIDENTIFICATION_RULES, ReidentificationRule

MODES = ("strict", "relaxed")


@dataclass
class Counts:
    tp: int = 0
    fn: int = 0
    fp: int = 0

    def add(self, other: "Counts") -> None:
        self.tp += other.tp
        self.fn += other.fn
        self.fp += other.fp

    @property
    def recall(self) -> float | None:
        total = self.tp + self.fn
        return None if total == 0 else self.tp / total

    @property
    def precision(self) -> float | None:
        total = self.tp + self.fp
        return None if total == 0 else self.tp / total

    def f_beta(self, beta: float = 2.0) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None:
            return None
        beta2 = beta * beta
        denom = beta2 * p + r
        if denom == 0:
            return 0.0
        return (1 + beta2) * p * r / denom

    def as_dict(self) -> dict:
        return {
            "tp": self.tp, "fn": self.fn, "fp": self.fp,
            "recall": self.recall, "precision": self.precision,
            "f2": self.f_beta(2.0),
        }


# --------------------------------------------------------------------------
# Matching pass
# --------------------------------------------------------------------------

@dataclass
class DocMatches:
    strict: DocMatchResult | None = None
    relaxed: DocMatchResult | None = None


def _match_key(doc: Document) -> tuple[str, str]:
    return (doc.lane, doc.doc_id)


def evaluate_corpus(
    documents: list[Document],
    predictions: dict[tuple[str | None, str], list[PredictedSpan]],
) -> dict[tuple[str, str], DocMatches]:
    results: dict[tuple[str, str], DocMatches] = {}
    for doc in documents:
        preds = predictions.get(
            _match_key(doc), predictions.get((None, doc.doc_id), [])
        )
        results[_match_key(doc)] = DocMatches(
            strict=match_document(doc.entities, preds, "strict"),
            relaxed=match_document(doc.entities, preds, "relaxed"),
        )
    return results


# --------------------------------------------------------------------------
# Per-lane, per-label rows (§6.2)
# --------------------------------------------------------------------------

def _counts_from_result(result: DocMatchResult, predicate=lambda e: True) -> dict[str, Counts]:
    by_label: dict[str, Counts] = defaultdict(Counts)
    for entity, _pred in result.tp:
        if predicate(entity):
            by_label[entity.label].tp += 1
    for entity in result.fn:
        if predicate(entity):
            by_label[entity.label].fn += 1
    for span in result.fp:
        by_label[span.label].fp += 1
    return by_label


def label_rows(lane_docs: list[Document], matches: dict[tuple[str, str], DocMatches]) -> list[dict]:
    """One row per label present in ground truth or predictions, for one lane."""
    totals: dict[str, dict[str, Counts]] = {
        mode: defaultdict(Counts) for mode in MODES
    }
    protected: dict[str, Counts] = defaultdict(Counts)     # relaxed only
    direct: dict[str, Counts] = defaultdict(Counts)        # relaxed only
    quasi: dict[str, Counts] = defaultdict(Counts)         # relaxed only

    for doc in lane_docs:
        dm = matches[_match_key(doc)]
        if dm.relaxed is None:
            continue
        for label, c in _counts_from_result(dm.strict).items():
            totals["strict"][label].add(c)
        for label, c in _counts_from_result(dm.relaxed).items():
            totals["relaxed"][label].add(c)
        for label, c in _counts_from_result(dm.relaxed, lambda e: e.protection == "protect").items():
            protected[label].add(c)
        for label, c in _counts_from_result(dm.relaxed, lambda e: e.identifier_class == "direct").items():
            direct[label].add(c)
        for label, c in _counts_from_result(dm.relaxed, lambda e: e.identifier_class == "quasi").items():
            quasi[label].add(c)

    labels = sorted(set(totals["strict"]) | set(totals["relaxed"]))
    rows = []
    for label in labels:
        rows.append({
            "label": label,
            "strict": totals["strict"][label].as_dict(),
            "relaxed": totals["relaxed"][label].as_dict(),
            "protected_recall_relaxed": protected[label].recall if label in protected else None,
            "direct_identifier_recall_relaxed": direct[label].recall if label in direct else None,
            "quasi_identifier_coverage_relaxed": quasi[label].recall if label in quasi else None,
        })
    return rows


# --------------------------------------------------------------------------
# Case-stratified recall (inflection lane)
# --------------------------------------------------------------------------

def case_stratified_recall(lane_docs: list[Document], matches: dict[tuple[str, str], DocMatches]) -> list[dict]:
    counts: dict[str, Counts] = defaultdict(Counts)
    for doc in lane_docs:
        dm = matches[_match_key(doc)]
        if dm.relaxed is None:
            continue
        for entity, _pred in dm.relaxed.tp:
            if entity.case:
                counts[entity.case].tp += 1
        for entity in dm.relaxed.fn:
            if entity.case:
                counts[entity.case].fn += 1
    return [
        {"case": case, **c.as_dict()}
        for case, c in sorted(counts.items())
    ]


# --------------------------------------------------------------------------
# Format-stratified recall (identifiers lane: id_format / date_format)
# --------------------------------------------------------------------------

def format_stratified_recall(lane_docs: list[Document], matches: dict[tuple[str, str], DocMatches]) -> list[dict]:
    counts: dict[tuple[str, str], Counts] = defaultdict(Counts)
    for doc in lane_docs:
        dm = matches[_match_key(doc)]
        if dm.relaxed is None:
            continue
        for entity, _pred in dm.relaxed.tp:
            fmt = entity.id_format or entity.date_format
            if fmt:
                counts[(entity.label, fmt)].tp += 1
        for entity in dm.relaxed.fn:
            fmt = entity.id_format or entity.date_format
            if fmt:
                counts[(entity.label, fmt)].fn += 1
    return [
        {"label": label, "format": fmt, **c.as_dict()}
        for (label, fmt), c in sorted(counts.items())
    ]


# --------------------------------------------------------------------------
# Clean-vs-perturbed delta (robustness lane, §6.6)
# --------------------------------------------------------------------------

def robustness_delta(
    all_docs: list[Document],
    matches: dict[tuple[str, str], DocMatches],
) -> list[dict]:
    by_id = {_match_key(d): d for d in all_docs}
    rows = []
    for doc in all_docs:
        if doc.lane != "robustness" or not doc.clean_doc_id:
            continue
        clean_doc = by_id.get(("core", doc.clean_doc_id))
        if clean_doc is None or _match_key(clean_doc) not in matches or _match_key(doc) not in matches:
            continue
        clean_dm = matches[_match_key(clean_doc)].relaxed
        noisy_dm = matches[_match_key(doc)].relaxed
        if clean_dm is None or noisy_dm is None:
            continue
        clean_counts = _counts_from_result(clean_dm)
        noisy_counts = _counts_from_result(noisy_dm)
        labels = sorted(set(clean_counts) | set(noisy_counts))
        for label in labels:
            clean_r = clean_counts[label].recall
            noisy_r = noisy_counts[label].recall
            delta = None
            if clean_r is not None and noisy_r is not None:
                delta = noisy_r - clean_r
            rows.append({
                "doc_id": doc.doc_id,
                "clean_doc_id": doc.clean_doc_id,
                "noise": doc.axes.get("noise"),
                "label": label,
                "clean_recall": clean_r,
                "perturbed_recall": noisy_r,
                "delta": delta,
            })
    return rows


# --------------------------------------------------------------------------
# Negative-lane FP count per category (§6.2)
# --------------------------------------------------------------------------

def negative_lane_fp(lane_docs: list[Document], matches: dict[tuple[str, str], DocMatches]) -> list[dict]:
    counts: dict[str, int] = defaultdict(int)
    tokens: dict[str, int] = defaultdict(int)
    total = 0
    total_tokens = 0
    for doc in lane_docs:
        dm = matches[_match_key(doc)]
        if dm.relaxed is None:
            continue
        n = len(dm.relaxed.fp)
        category = doc.category or "uncategorized"
        token_count = len((doc.text or "").split())
        counts[category] += n
        tokens[category] += token_count
        total += n
        total_tokens += token_count

    def row(category: str, fp_count: int, token_count: int) -> dict:
        return {
            "category": category,
            "fp_count": fp_count,
            "tokens": token_count,
            "fp_per_1000_tokens": (
                None if token_count == 0 else fp_count * 1000 / token_count
            ),
        }

    return [row(category, fp_count, tokens[category]) for category, fp_count in sorted(counts.items())] + [
        row("TOTAL", total, total_tokens)
    ]


# --------------------------------------------------------------------------
# Residual-identifiability (§6.4)
# --------------------------------------------------------------------------

def residual_identifiability(
    span_docs: list[Document],
    matches: dict[tuple[str, str], DocMatches],
    rules: tuple[ReidentificationRule, ...] = REIDENTIFICATION_RULES,
) -> dict:
    """For each `entity_id` subject (grouped within a document), flag
    `re-identifiable` if there exists a published rule whose every labeled
    slot is present in that subject's ground truth AND every one of those
    entities was left undetected (relaxed miss) — i.e. would remain
    unmasked in the system's output. Detection-only harness: "unmasked"
    == "undetected", since there is no masked document to inspect.
    """
    subjects_evaluated = 0
    subjects_flagged = 0
    flags: list[dict] = []

    for doc in span_docs:
        dm = matches[_match_key(doc)]
        if dm.relaxed is None:
            continue
        undetected_ids = {id(e) for e in dm.relaxed.fn}

        by_subject: dict[str, list[Entity]] = defaultdict(list)
        for e in doc.entities:
            if e.entity_id and e.protection == "protect":
                by_subject[e.entity_id].append(e)

        for entity_id, subject_entities in by_subject.items():
            by_label: dict[str, list[Entity]] = defaultdict(list)
            for e in subject_entities:
                by_label[e.label].append(e)

            applicable_rules = [
                rule for rule in rules
                if all(label in by_label for label in rule.labels)
            ]
            if not applicable_rules:
                continue
            subjects_evaluated += 1

            triggered = []
            for rule in applicable_rules:
                slot_all_undetected = all(
                    all(id(e) in undetected_ids for e in by_label[label])
                    for label in rule.labels
                )
                if slot_all_undetected:
                    triggered.append(rule.name)

            if triggered:
                subjects_flagged += 1
                flags.append({
                    "doc_id": doc.doc_id,
                    "entity_id": entity_id,
                    "triggered_rules": triggered,
                })

    rate = None if subjects_evaluated == 0 else subjects_flagged / subjects_evaluated
    return {
        "subjects_evaluated": subjects_evaluated,
        "subjects_flagged": subjects_flagged,
        "rate": rate,
        "flags": flags,
        "rules": [r.name for r in rules],
    }


# --------------------------------------------------------------------------
# Consistency / reversibility (§6.5) — optional, mapping-file driven
# --------------------------------------------------------------------------

def consistency_reversibility(mapping_path) -> dict | str:
    """Returns "n/a" when no mapping file is supplied (detection-only run).

    Mapping-file contract (JSONL, one line per document):
        {"doc_id": "...",
         "entity_to_surrogate": {"p1": "Aleksander Nowicki", ...},
         "original_entity_text": {"p1": "Jan Kowalski", ...}}

    - Consistency: fraction of `entity_id` groups mapped 1:1 (every
      occurrence of an entity_id maps to the same surrogate, and no two
      distinct entity_ids collide onto the same surrogate).
    - Reversibility (round-trip fidelity): fraction of entity_ids whose
      surrogate, looked up in the document's own reverse mapping, restores
      the original entity text exactly. This is a scoped, entity_id-level
      round trip (not full-document reconstruction), documented as such
      because full reconstruction needs the masked document text as well,
      which is out of this harness's minimal contract.
    """
    if mapping_path is None:
        return "n/a"

    import json
    from pathlib import Path

    total_groups = 0
    consistent_groups = 0
    total_restorable = 0
    restored_ok = 0
    surrogate_collisions = 0

    with Path(mapping_path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            entity_to_surrogate: dict[str, str] = row.get("entity_to_surrogate", {})
            original_text: dict[str, str] = row.get("original_entity_text", {})

            seen_surrogates: dict[str, str] = {}
            for entity_id, surrogate in entity_to_surrogate.items():
                total_groups += 1
                if surrogate in seen_surrogates and seen_surrogates[surrogate] != entity_id:
                    surrogate_collisions += 1
                else:
                    consistent_groups += 1
                    seen_surrogates[surrogate] = entity_id

                if entity_id in original_text:
                    total_restorable += 1
                    reverse = {v: k for k, v in entity_to_surrogate.items()}
                    if reverse.get(surrogate) == entity_id:
                        restored_ok += 1

    return {
        "consistency_fraction": None if total_groups == 0 else consistent_groups / total_groups,
        "surrogate_collisions": surrogate_collisions,
        "round_trip_fidelity": None if total_restorable == 0 else restored_ok / total_restorable,
        "groups_evaluated": total_groups,
    }


# --------------------------------------------------------------------------
# Summary block (§6.2 closing note): no single aggregate.
# --------------------------------------------------------------------------

def summary_block(
    all_span_docs: list[Document],
    matches: dict[tuple[str, str], DocMatches],
    negative_docs: list[Document],
    reident: dict,
) -> dict:
    direct = Counts()
    protected = Counts()
    for doc in all_span_docs:
        dm = matches[_match_key(doc)]
        if dm.relaxed is None:
            continue
        for label, c in _counts_from_result(dm.relaxed, lambda e: e.identifier_class == "direct").items():
            direct.add(c)
        for label, c in _counts_from_result(dm.relaxed, lambda e: e.protection == "protect").items():
            protected.add(c)

    negative_total = next(
        row for row in negative_lane_fp(negative_docs, matches)
        if row["category"] == "TOTAL"
    )

    return {
        "direct_identifier_recall_relaxed": direct.recall,
        "protected_recall_relaxed": protected.recall,
        "residual_identifiability_rate": reident["rate"],
        "negative_lane_fp_count": negative_total["fp_count"],
        "negative_lane_fp_per_1000_tokens": negative_total["fp_per_1000_tokens"],
    }

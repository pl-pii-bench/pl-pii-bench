"""Unit tests for the pl-pii-bench scorer against a small hand-made fixture
with known TP/FP/FN (tests/fixtures/mini_corpus/ + mini_predictions.jsonl).

Fixture design (see tests/fixtures/mini_corpus/core/mini-doc1.json):
- PERSON "Jan Kowalski" and PESEL "79030835344": predicted with exact spans
  -> TP under strict and relaxed.
- DOB and LOC (entity_id "p1"): not predicted at all -> FN under both modes.
  Both are `quasi` and belong to the same subject, so this is also the
  residual-identifiability positive case (DOB+LOC rule).
- ORG "Acme Sp. z o.o." (entity_id "org1", protection "keep"): predicted as
  the shorter substring "Acme" -> FN under strict (span mismatch), TP under
  relaxed (>=1 char overlap, same label). Its "keep" protection excludes it
  from protected recall.
- PHONE prediction with no ground-truth match anywhere in the doc -> FP.
- tests/fixtures/mini_corpus/negative/mini-neg1.json has zero ground truth
  and two predicted spans -> both are negative-lane FPs, category
  "lookalike-test".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.loader import Document, Entity, load_corpus
from eval.predictions import load_predictions
from eval.metrics import (
    evaluate_corpus,
    label_rows,
    negative_lane_fp,
    residual_identifiability,
    consistency_reversibility,
    summary_block,
)

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS_DIR = FIXTURES / "mini_corpus"
PREDICTIONS_PATH = FIXTURES / "mini_predictions.jsonl"
MAPPING_PATH = FIXTURES / "mini_mapping.jsonl"


def _load():
    documents = load_corpus(CORPUS_DIR)
    predictions = load_predictions(PREDICTIONS_PATH)
    matches = evaluate_corpus(documents, predictions)
    return documents, matches


def test_loader_invariant_holds_on_fixture():
    # load_corpus itself asserts text[start:end] == entity.text; reaching
    # here without a CorpusError means the fixture is internally consistent.
    documents = load_corpus(CORPUS_DIR)
    doc_ids = {d.doc_id for d in documents}
    assert doc_ids == {"mini-doc1", "mini-neg1"}


def test_strict_vs_relaxed_matching():
    documents, matches = _load()
    core_docs = [d for d in documents if d.lane == "core"]
    rows = {row["label"]: row for row in label_rows(core_docs, matches)}

    # Exact-span matches: TP under both modes.
    assert rows["PERSON"]["strict"] == {"tp": 1, "fn": 0, "fp": 0, "recall": 1.0, "precision": 1.0, "f2": 1.0}
    assert rows["PERSON"]["relaxed"]["tp"] == 1
    assert rows["PESEL"]["strict"]["tp"] == 1
    assert rows["PESEL"]["relaxed"]["tp"] == 1

    # Undetected entirely: FN under both modes.
    assert rows["DOB"]["strict"] == {"tp": 0, "fn": 1, "fp": 0, "recall": 0.0, "precision": None, "f2": None}
    assert rows["DOB"]["relaxed"]["fn"] == 1
    assert rows["LOC"]["strict"]["fn"] == 1
    assert rows["LOC"]["relaxed"]["fn"] == 1

    # Partial-overlap prediction: strict FN, relaxed TP.
    assert rows["ORG"]["strict"]["tp"] == 0
    assert rows["ORG"]["strict"]["fn"] == 1
    assert rows["ORG"]["relaxed"]["tp"] == 1
    assert rows["ORG"]["relaxed"]["fn"] == 0

    # Unmatched prediction: FP under both modes, no ground truth to recall against.
    assert rows["PHONE"]["strict"] == {"tp": 0, "fn": 0, "fp": 1, "recall": None, "precision": 0.0, "f2": None}
    assert rows["PHONE"]["relaxed"]["fp"] == 1


def test_protected_direct_quasi_split():
    documents, matches = _load()
    core_docs = [d for d in documents if d.lane == "core"]
    rows = {row["label"]: row for row in label_rows(core_docs, matches)}

    # direct-identifier recall: PERSON + PESEL, both detected -> 100%.
    assert rows["PERSON"]["direct_identifier_recall_relaxed"] == 1.0
    assert rows["PESEL"]["direct_identifier_recall_relaxed"] == 1.0
    # quasi-identifier fields never populate the direct column.
    assert rows["DOB"]["direct_identifier_recall_relaxed"] is None
    assert rows["ORG"]["direct_identifier_recall_relaxed"] is None

    # quasi-identifier coverage: DOB, LOC, ORG all identifier_class=quasi.
    assert rows["DOB"]["quasi_identifier_coverage_relaxed"] == 0.0
    assert rows["LOC"]["quasi_identifier_coverage_relaxed"] == 0.0
    assert rows["ORG"]["quasi_identifier_coverage_relaxed"] == 1.0

    # protected recall excludes the "keep"-protection ORG entity: PERSON,
    # DOB, LOC, PESEL are all protection=protect -> 2 detected of 4 = 50%.
    assert rows["PERSON"]["protected_recall_relaxed"] == 1.0
    assert rows["ORG"]["protected_recall_relaxed"] is None  # no protect-class ORG here


def test_negative_lane_fp_counting():
    documents, matches = _load()
    negative_docs = [d for d in documents if d.lane == "negative"]
    rows = negative_lane_fp(negative_docs, matches)
    by_category = {row["category"]: row["fp_count"] for row in rows}
    assert by_category["lookalike-test"] == 2
    assert by_category["TOTAL"] == 2


def test_negative_lane_fp_per_1000_tokens():
    documents, matches = _load()
    negative_docs = [d for d in documents if d.lane == "negative"]
    rows = {row["category"]: row for row in negative_lane_fp(negative_docs, matches)}

    # The fixture contains 10 whitespace-delimited tokens and two FPs.
    assert rows["lookalike-test"]["tokens"] == 10
    assert rows["lookalike-test"]["fp_per_1000_tokens"] == 200.0
    assert rows["TOTAL"]["tokens"] == 10
    assert rows["TOTAL"]["fp_per_1000_tokens"] == 200.0


def test_loader_carries_negative_decoys():
    documents = load_corpus(CORPUS_DIR)
    negative_doc = next(d for d in documents if d.doc_id == "mini-neg1")

    assert negative_doc.decoys == ["12345", "identyfikator"]
    assert all(decoy in negative_doc.text for decoy in negative_doc.decoys)


def test_residual_identifiability_flags_dob_loc_subject():
    documents, matches = _load()
    core_docs = [d for d in documents if d.lane == "core"]
    result = residual_identifiability(core_docs, matches)

    # Subject "p1" has DOB and LOC (both quasi, same entity_id), both
    # entirely undetected -> the DOB+LOC rule triggers. Subject "org1" only
    # has ORG (detected, and no LOC under that entity_id) -> not evaluated.
    assert result["subjects_evaluated"] == 1
    assert result["subjects_flagged"] == 1
    assert result["rate"] == 1.0
    assert result["flags"][0]["entity_id"] == "p1"
    assert "DOB+LOC" in result["flags"][0]["triggered_rules"]


def test_identifying_combos_include_dob_loc_and_postal_but_not_org_loc():
    documents = [
        Document(
            doc_id="dob-loc",
            lane="core",
            text="",
            entities=[
                Entity(text="", label="DOB", entity_id="p1"),
                Entity(text="", label="LOC", entity_id="p1"),
            ],
        ),
        Document(
            doc_id="dob-postal",
            lane="core",
            text="",
            entities=[
                Entity(text="", label="DOB", entity_id="p2"),
                Entity(text="", label="POSTAL", entity_id="p2"),
            ],
        ),
        Document(
            doc_id="org-loc",
            lane="core",
            text="",
            entities=[
                Entity(text="", label="ORG", entity_id="p3"),
                Entity(text="", label="LOC", entity_id="p3"),
            ],
        ),
    ]
    matches = evaluate_corpus(documents, {})

    result = residual_identifiability(documents, matches)

    assert result["rules"] == ["DOB+LOC", "DOB+POSTAL"]
    assert result["subjects_evaluated"] == 2
    assert result["subjects_flagged"] == 2
    assert {flag["entity_id"] for flag in result["flags"]} == {"p1", "p2"}


def test_summary_block_no_single_aggregate():
    documents, matches = _load()
    core_docs = [d for d in documents if d.lane == "core"]
    negative_docs = [d for d in documents if d.lane == "negative"]
    reident = residual_identifiability(core_docs, matches)
    summary = summary_block(documents, matches, negative_docs, reident)

    # direct: PERSON + PESEL, both TP -> 100%.
    assert summary["direct_identifier_recall_relaxed"] == 1.0
    # protected: PERSON, DOB, LOC, PESEL protect-class -> 2/4 detected = 50%.
    assert summary["protected_recall_relaxed"] == 0.5
    assert summary["residual_identifiability_rate"] == 1.0
    assert summary["negative_lane_fp_count"] == 2
    assert summary["negative_lane_fp_per_1000_tokens"] == 200.0


def test_consistency_reversibility_optional_block():
    assert consistency_reversibility(None) == "n/a"

    result = consistency_reversibility(MAPPING_PATH)
    assert result["groups_evaluated"] == 2
    assert result["consistency_fraction"] == 1.0
    assert result["surrogate_collisions"] == 0
    assert result["round_trip_fidelity"] == 1.0

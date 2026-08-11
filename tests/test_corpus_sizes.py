"""Regression floors for the published public benchmark corpus."""
from __future__ import annotations

import json
from hashlib import sha256

import pytest

from corpus_stats import (
    ADDRESS_FORMS,
    HOLDOUT_CORPUS,
    IDENTIFIER_LABELS,
    LANE_FLOORS,
    PUBLIC_CORPUS,
    lane_documents,
    lane_spans,
    wilson_half_width,
)


PREPOSITIONS = ("w ", "we ", "z ", "ze ", "do ", "przy ", "pod ", "na ", "od ", "przez ")


def _load_lane(name: str) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((PUBLIC_CORPUS / name).glob("*.json"))
    ]


def _load_holdout_lane(name: str) -> list[dict]:
    if not HOLDOUT_CORPUS.is_dir():
        pytest.skip("private holdout corpus is not available")
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((HOLDOUT_CORPUS / name).glob("*.json"))
    ]


@pytest.mark.parametrize("scope", ("public", "holdout"))
@pytest.mark.parametrize("lane", sorted(LANE_FLOORS))
def test_lane_floor(scope: str, lane: str):
    documents = lane_documents(lane, scope)
    if not documents:
        pytest.skip(f"{scope}/{lane} is not an available corpus lane")

    floor = LANE_FLOORS[lane]
    if lane == "negative":
        word_count = sum(len(document.text.split()) for document in documents if document.text)
        decoy_count = sum(len(document.decoys) for document in documents)
        assert word_count >= floor["words"]
        assert decoy_count >= floor["decoys"]
        density = decoy_count * 1_000 / word_count
        assert density >= floor["decoys_per_1000_words"], (
            f"{scope}/negative: {density:.1f} decoys per 1000 words"
        )
        return

    spans = lane_spans(lane, scope)
    assert len(spans) >= floor["spans"]
    assert wilson_half_width(0.90, len(spans)) <= floor["wilson_max_half_width"]
    if lane == "robustness":
        noise_types = {document.axes.get("noise") for document in documents}
        assert None not in noise_types
        assert len(noise_types) >= floor["noise_types"]


@pytest.mark.parametrize("scope", ("public", "holdout"))
@pytest.mark.parametrize("label", IDENTIFIER_LABELS)
def test_identifier_label_and_format_floors(scope: str, label: str):
    spans = [
        entity
        for _document, entity in lane_spans("identifiers", scope)
        if entity.label == label
    ]
    if not spans and not lane_documents("identifiers", scope):
        pytest.skip(f"{scope}/identifiers is not an available corpus lane")

    floor = LANE_FLOORS["identifiers"]
    assert len(spans) >= floor["per_label"]
    format_counts = {
        id_format: sum(entity.id_format == id_format for entity in spans)
        for id_format in {entity.id_format for entity in spans}
    }
    assert None not in format_counts
    assert len(format_counts) >= floor["id_formats"]
    assert all(count >= floor["per_format"] for count in format_counts.values())


def test_public_address_corpus_size_and_forms():
    documents = _load_lane("address")
    entities = [entity for document in documents for entity in document["entities"]]
    address_forms = {
        entity["address_form"]
        for entity in entities
        if entity["label"] in {"LOC", "POSTAL"}
    }

    assert address_forms == ADDRESS_FORMS


def test_public_address_corpus_keeps_person_street_and_org_boundaries():
    documents = {document["doc_id"]: document for document in _load_lane("address")}
    person_streets = documents["address-person-named-streets"]
    org_names = documents["address-loc-in-org"]

    assert not any(entity["label"] == "PERSON" for entity in person_streets["entities"])
    assert any(entity["label"] == "ORG" for entity in org_names["entities"])
    assert not any(entity["label"] == "LOC" for entity in org_names["entities"])


def test_address_street_spans_exclude_prepositions():
    # Only the bare-city form keeps its preposition (guidelines §5.3, `we
    # Wrocławiu`). Street forms are annotated from the prefix onwards.
    for documents in (_load_lane("address"), _load_holdout_lane("address")):
        for document in documents:
            for entity in document["entities"]:
                if entity.get("address_form") in {None, "bare-city"}:
                    continue
                assert not entity["text"].startswith(PREPOSITIONS), (
                    document["doc_id"],
                    entity["text"],
                )


def test_public_negative_corpus_size_and_decoy_invariants():
    documents = _load_lane("negative")
    decoys = [decoy for document in documents for decoy in document["decoys"]]
    for document in documents:
        assert document["entities"] == []
        assert document["category"]
        assert document["decoys"]
        assert all(decoy in document["text"] for decoy in document["decoys"])


def test_holdout_shares_no_file_with_public():
    if not HOLDOUT_CORPUS.is_dir():
        pytest.skip("private holdout corpus is not available")

    public_hashes = {
        sha256(path.read_bytes()).hexdigest()
        for path in PUBLIC_CORPUS.rglob("*")
        if path.is_file()
    }
    holdout_hashes = {
        sha256(path.read_bytes()).hexdigest()
        for path in HOLDOUT_CORPUS.rglob("*")
        if path.is_file()
    }

    assert not public_hashes & holdout_hashes


def test_holdout_address_corpus_size_and_forms():
    documents = _load_holdout_lane("address")
    entities = [entity for document in documents for entity in document["entities"]]
    address_forms = {
        entity["address_form"]
        for entity in entities
        if entity["label"] in {"LOC", "POSTAL"} and "address_form" in entity
    }

    assert address_forms == ADDRESS_FORMS


def test_holdout_negative_corpus_size_and_decoy_invariants():
    documents = _load_holdout_lane("negative")
    decoys = [decoy for document in documents for decoy in document["decoys"]]
    for document in documents:
        assert document["entities"] == []
        assert document["category"]
        assert document["decoys"]
        assert all(decoy in document["text"] for decoy in document["decoys"])

"""Cross-lane corpus contracts that must hold in every available scope."""
from __future__ import annotations

from collections import defaultdict
import re

import pytest

from corpus_stats import (
    HOLDOUT_CORPUS,
    LANE_FLOORS,
    PUBLIC_CORPUS,
    dominant_surface_ratio,
    iban_valid,
    lane_documents,
    luhn_valid,
    nip_valid,
    pesel_valid,
    private_superseded_iban_surfaces,
    regon_valid,
    scope_documents,
)
from eval.loader import CorpusError, load_corpus


CHECKSUM_VALIDATORS = {
    "PESEL": pesel_valid,
    "NIP": nip_valid,
    "REGON": regon_valid,
    "IBAN": iban_valid,
}
SCOPES = ("public", "holdout")
ALL_LANES = ("address", "core", "identifiers", "inflection", "negative", "pdf", "robustness")
ALLOWED_PUBLIC_CORE_PDF_DUPLICATES = {
    "core-deklaracja-pit-high",
    "core-faktura-vat-high",
    "core-kpir-ledger-high",
    "core-odpis-krs-high",
    "core-pismo-urzedowe-high",
    "core-protokol-zebrania-high",
    "core-umowa-o-prace-high",
    "core-umowa-zlecenie-high",
    "core-wyciag-bankowy-high",
    "core-wyrok-001",
}
SUPERSEDED_IBAN_SURFACES = frozenset(
    {
        "PL-5667-5104-7175-0893-6476-0807-98",
        "77719877883197183561629071",
    }
)

_IDENTIFIER_RUN = re.compile(r"(?:[A-ZĄ-Ż]+[-/])?[\d\s.-]+")


def _valid_identifiers(text: str) -> list[str]:
    """Return checksum-valid identifiers in normalized rendered surfaces."""
    found = []
    for match in _IDENTIFIER_RUN.finditer(text):
        surface = match.group().strip()
        without_prefix = re.sub(r"^[A-ZĄ-Ż]+[-/]", "", surface)
        digits = re.sub(r"[\s.-]", "", without_prefix)
        if not digits:
            continue
        rendered = f"{surface!r} -> {digits}"
        if len(digits) == 11 and pesel_valid(digits):
            found.append(f"PESEL {rendered}")
        if len(digits) == 10 and nip_valid(digits):
            found.append(f"NIP {rendered}")
        if len(digits) in {9, 14} and regon_valid(digits):
            found.append(f"REGON {rendered}")
        if len(digits) == 26 and iban_valid(digits):
            found.append(f"NRB {rendered}")
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            found.append(f"Luhn {rendered}")
    return found


def _documents_for_scope(scope: str):
    if scope == "holdout" and not HOLDOUT_CORPUS.is_dir():
        pytest.skip("private holdout corpus is not available")
    return scope_documents(scope)


def _lane_is_available(lane: str, scope: str) -> bool:
    return bool(lane_documents(lane, scope))


def _superseded_iban_surfaces(scope: str) -> frozenset[str] | None:
    if scope == "public":
        return SUPERSEDED_IBAN_SURFACES
    return private_superseded_iban_surfaces()


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("lane", ALL_LANES)
def test_lane_loading_enforces_span_offsets(lane: str, scope: str, tmp_path):
    """Loading each available lane enforces text[start:end] == entity.text."""
    if scope == "holdout" and not HOLDOUT_CORPUS.is_dir():
        pytest.skip("private holdout corpus is not available")
    if not _lane_is_available(lane, scope):
        pytest.skip(f"{scope}/{lane} is not an available corpus lane")
    corpus_root = PUBLIC_CORPUS if scope == "public" else HOLDOUT_CORPUS
    documents = load_corpus(corpus_root, lanes={lane})
    for document in documents:
        assert document.text is not None
        for entity in document.entities:
            if entity.start is not None and entity.end is not None:
                assert document.text[entity.start:entity.end] == entity.text

    shifted_lane = tmp_path / "core"
    shifted_lane.mkdir()
    (shifted_lane / "shifted.json").write_text(
        '{"doc_id":"shifted","text":"Jan Kowalski","entities":['
        '{"text":"Jan","label":"PERSON","start":1,"end":4}]}',
        encoding="utf-8",
    )
    with pytest.raises(CorpusError):
        load_corpus(tmp_path, lanes={"core"})


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("lane", ALL_LANES)
def test_positive_lanes_use_checksum_valid_identifiers(lane: str, scope: str):
    """Ground truth never punishes a detector for checksum validation.

    Perturbed robustness documents opt out by declaring ``axes.noise``.  The
    test intentionally keys the exemption on that data contract instead of the
    lane name, so a future noisy document cannot silently evade the check.
    """
    invalid = []
    superseded = []
    superseded_surfaces = _superseded_iban_surfaces(scope)
    if not _lane_is_available(lane, scope):
        pytest.skip(f"{scope}/{lane} is not an available corpus lane")
    if superseded_surfaces is None:
        pytest.skip("private superseded-IBAN denylist is not available")
    for document in lane_documents(lane, scope):
        if document.lane == "negative" or document.axes.get("noise"):
            continue
        for entity in document.entities:
            validator = CHECKSUM_VALIDATORS.get(entity.label)
            if validator and not validator(entity.text):
                invalid.append(
                    f"{document.lane}/{document.doc_id}: "
                    f"{entity.label} {entity.text!r}"
                )
            if entity.label == "IBAN" and entity.text in superseded_surfaces:
                superseded.append(f"{document.lane}/{document.doc_id}: {entity.text!r}")
    assert not invalid, "checksum-invalid ground truth:\n" + "\n".join(invalid)
    assert not superseded, "superseded IBAN fixture surface:\n" + "\n".join(superseded)


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("lane", sorted(LANE_FLOORS))
def test_no_rendered_surface_dominates_a_lane(lane: str, scope: str):
    if not _lane_is_available(lane, scope):
        pytest.skip(f"{scope}/{lane} is not an available corpus lane")
    ratio = dominant_surface_ratio(lane, scope)
    assert ratio <= 0.05, f"{scope}/{lane}: dominant surface ratio {ratio:.1%}"


@pytest.mark.parametrize("scope", SCOPES)
def test_doc_ids_are_unique_across_lanes_except_public_core_pdf_pairs(scope: str):
    lanes_by_id: dict[str, set[str]] = defaultdict(set)
    for document in _documents_for_scope(scope):
        lanes_by_id[document.doc_id].add(document.lane)

    duplicates = {
        doc_id: lanes
        for doc_id, lanes in lanes_by_id.items()
        if len(lanes) > 1
    }
    expected = (
        {doc_id: {"core", "pdf"} for doc_id in ALLOWED_PUBLIC_CORE_PDF_DUPLICATES}
        if scope == "public"
        else {}
    )
    assert duplicates == expected


@pytest.mark.parametrize("scope", SCOPES)
def test_negative_lanes_have_no_checksum_valid_identifier_in_any_rendering(scope: str):
    if not _lane_is_available("negative", scope):
        pytest.skip(f"{scope}/negative is not an available corpus lane")
    invalid = [
        f"{document.lane}/{document.doc_id}: {identifier}"
        for document in lane_documents("negative", scope)
        for identifier in _valid_identifiers(document.text)
    ]
    assert not invalid, "checksum-valid identifier in negative text:\n" + "\n".join(invalid)

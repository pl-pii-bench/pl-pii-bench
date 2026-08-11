"""Shared corpus measurements and identifier checksum validators.

The benchmark corpus is authored in the product repository, while the public
copy is synced into this repository.  Keeping path resolution and the checksum
rules here gives every corpus guard one definition of those contracts.
"""
from __future__ import annotations

from collections import Counter
import json
from math import sqrt
from pathlib import Path
import re

from eval.loader import Document, load_corpus


REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_CORPUS = REPO_ROOT / "corpus"
HOLDOUT_CORPUS = (
    REPO_ROOT.parent / "products" / "anonimator" / "bench" / "corpus" / "holdout"
)
PRIVATE_SUPERSEDED_IBAN_SURFACES = HOLDOUT_CORPUS / ".superseded-iban-surfaces.json"

# Final target floors for the lanes expanded by this plan. They intentionally
# live here before the corpus reaches them, so every lane phase shares one
# stable contract instead of introducing a weakened local threshold.
LANE_FLOORS = {
    "address": {
        "spans": 150,
        "wilson_max_half_width": 0.05,
    },
    "identifiers": {
        "spans": 360,
        "per_label": 30,
        "id_formats": 4,
        "per_format": 5,
        "wilson_max_half_width": 0.05,
    },
    "robustness": {
        "spans": 200,
        "noise_types": 5,
        "wilson_max_half_width": 0.05,
    },
    "negative": {
        "words": 10_000,
        "decoys": 700,
        # False alarms are published per 1000 words. Carrier prose that grows
        # faster than the declared bait lowers every engine's rate without any
        # engine improving, so the lane carries a density floor as well as a
        # size floor. 60 sits below the pre-expansion 71 and far above the 39
        # the lane fell to when it was first grown.
        "decoys_per_1000_words": 60,
    },
}

IDENTIFIER_LABELS = (
    "PESEL",
    "NIP",
    "REGON",
    "DOWOD",
    "IBAN",
    "PHONE",
    "EMAIL",
    "PASSPORT",
    "DRIVING_LICENSE",
    "PAYMENT_CARD",
    "VIN",
    "VEHICLE_PLATE",
)

# `corpus/annotation-guidelines.md` §3 defines this closed vocabulary and
# requires every listed form to have corpus coverage.
ADDRESS_FORMS = frozenset(
    {
        "street-only",
        "full",
        "bare-city",
        "street-with-unit",
        "po-box",
        "admin-unit",
        "letterhead-block",
    }
)


def corpus_root(scope: str) -> Path:
    """Return the corpus directory for a public or private holdout scope."""
    roots = {"public": PUBLIC_CORPUS, "holdout": HOLDOUT_CORPUS}
    try:
        return roots[scope]
    except KeyError as exc:
        raise ValueError(f"unknown corpus scope: {scope!r}") from exc


def scope_documents(scope: str) -> list[Document]:
    """Load every available document for ``scope`` and assert span offsets."""
    root = corpus_root(scope)
    if not root.is_dir():
        return []
    return load_corpus(root)


def lane_documents(lane: str, scope: str = "public") -> list[Document]:
    """Return the documents for one lane in one corpus scope."""
    return [document for document in scope_documents(scope) if document.lane == lane]


def lane_spans(lane: str, scope: str = "public") -> list[tuple[Document, object]]:
    """Return every annotated span paired with its source document."""
    return [
        (document, entity)
        for document in lane_documents(lane, scope)
        for entity in document.entities
    ]


def private_superseded_iban_surfaces() -> frozenset[str] | None:
    """Return the holdout-only IBAN denylist without copying it into this repo.

    Maintainers keep the JSON array beside the private holdout corpus.  The
    public benchmark package deliberately does not carry that file, so callers
    can skip the holdout-only assertion when working from a public checkout.
    """
    if not PRIVATE_SUPERSEDED_IBAN_SURFACES.is_file():
        return None
    raw = json.loads(PRIVATE_SUPERSEDED_IBAN_SURFACES.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(surface, str) for surface in raw):
        raise ValueError("private superseded IBAN denylist must be a JSON array of strings")
    return frozenset(raw)


def wilson_half_width(proportion: float, sample_size: int, z: float = 1.96) -> float:
    """Return the two-sided Wilson confidence-interval half-width."""
    if not 0.0 <= proportion <= 1.0:
        raise ValueError("proportion must be between zero and one")
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")

    denominator = 1 + z**2 / sample_size
    centre = (proportion + z**2 / (2 * sample_size)) / denominator
    lower = (
        proportion
        + z**2 / (2 * sample_size)
        - z
        * sqrt((proportion * (1 - proportion) + z**2 / (4 * sample_size)) / sample_size)
    ) / denominator
    return centre - lower


def dominant_surface_ratio(lane: str, scope: str = "public") -> float:
    """Return the largest rendered-surface share for a lane.

    Negative lanes have no entities, so their declared decoys are the rendered
    surfaces whose repetition is measured.  Every other lane uses entity text.
    """
    documents = lane_documents(lane, scope)
    if lane == "negative":
        surfaces = [decoy for document in documents for decoy in document.decoys]
    else:
        surfaces = [entity.text for document in documents for entity in document.entities]
    if not surfaces:
        return 0.0
    return max(Counter(surfaces).values()) / len(surfaces)


def _digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def pesel_valid(value: str) -> bool:
    digits = _digits(value)
    if len(digits) != 11:
        return False
    weights = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
    checksum = (10 - sum(int(digit) * weight for digit, weight in zip(digits, weights)) % 10) % 10
    return checksum == int(digits[10])


def nip_valid(value: str) -> bool:
    digits = _digits(value)
    if len(digits) != 10:
        return False
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    checksum = sum(int(digit) * weight for digit, weight in zip(digits, weights)) % 11
    return checksum != 10 and checksum == int(digits[9])


def regon_valid(value: str) -> bool:
    digits = _digits(value)
    if len(digits) == 9:
        weights = (8, 9, 2, 3, 4, 5, 6, 7)
        checksum = sum(int(digit) * weight for digit, weight in zip(digits, weights)) % 11 % 10
        return checksum == int(digits[8])
    if len(digits) == 14:
        first_weights = (8, 9, 2, 3, 4, 5, 6, 7)
        second_weights = (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)
        first_checksum = sum(
            int(digit) * weight for digit, weight in zip(digits[:8], first_weights)
        ) % 11 % 10
        second_checksum = sum(
            int(digit) * weight for digit, weight in zip(digits[:13], second_weights)
        ) % 11 % 10
        return first_checksum == int(digits[8]) and second_checksum == int(digits[13])
    return False


def iban_valid(value: str) -> bool:
    compact = re.sub(r"[\s-]", "", value).upper()
    for prefix in ("IBAN:", "NRB:"):
        if compact.startswith(prefix):
            compact = compact.removeprefix(prefix)
    if re.fullmatch(r"\d{26}", compact):
        compact = "PL" + compact
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", compact):
        return False
    rotated = compact[4:] + compact[:4]
    numeric = "".join(str(int(character, 36)) for character in rotated)
    return int(numeric) % 97 == 1


def luhn_valid(value: str) -> bool:
    digits = _digits(value)
    if len(digits) < 2:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        number = int(digit)
        if index % 2:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0

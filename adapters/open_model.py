"""Shared clean-room helpers for the public open-model adapters.

Adapters deliberately share only the benchmark's public prediction contract.
They do not import the product or assume a model-specific span object.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from eval.loader import TRACKED_LABELS


# Names emitted by the three public models.  Keep aliases explicit rather than
# guessing from arbitrary model labels: an unmapped label must not become a
# misleading public prediction.
PUBLIC_LABEL_ALIASES = {
    "PERSON": "PERSON", "PER": "PERSON", "PERSNAME": "PERSON",
    "PERSON_NAME": "PERSON", "PERSON_IDENTIFIER": "PERSON", "NAME": "PERSON",
    "SURNAME": "PERSON",
    "ORG": "ORG", "ORGANIZATION": "ORG", "ORGNAME": "ORG",
    "ORGANIZATION_NAME": "ORG", "ORGANIZATION_IDENTIFIER": "ORG", "COMPANY": "ORG",
    "LOC": "LOC", "GPE": "LOC", "LOCATION": "LOC",
    "PLACE": "LOC", "PLACENAME": "LOC", "GEOGNAME": "LOC",
    "GEO_LOCATION": "LOC", "CITY": "LOC", "PESEL": "PESEL", "NIP": "NIP",
    "REGON": "REGON", "DOWOD": "DOWOD", "ID_CARD": "DOWOD",
    "DOCUMENT_NUMBER": "DOWOD", "ID_NUMBER": "DOWOD",
    "IBAN": "IBAN", "BANK_ACCOUNT_IDENTIFIER": "IBAN", "BANK_ACCOUNT": "IBAN",
    "POSTAL": "POSTAL", "POSTAL_CODE": "POSTAL", "POSTAL_ADDRESS": "POSTAL",
    "ADDRESS": "POSTAL",
    "DOB": "DOB", "DATE_OF_BIRTH": "DOB", "PHONE": "PHONE",
    "PHONE_NUMBER": "PHONE", "EMAIL": "EMAIL", "EMAIL_ADDRESS": "EMAIL",
    "PASSPORT": "PASSPORT", "DRIVING_LICENSE": "DRIVING_LICENSE",
    "DRIVER_LICENSE": "DRIVING_LICENSE", "PAYMENT_CARD": "PAYMENT_CARD",
    "VIN": "VIN", "VEHICLE_IDENTIFIER": "VIN",
    "VEHICLE_PLATE": "VEHICLE_PLATE", "LICENSE_PLATE": "VEHICLE_PLATE",
}


def normalize_label(label: object) -> str | None:
    """Return a frozen public label, accepting BIO prefixes only."""
    if not isinstance(label, str):
        return None
    value = label.strip().upper().replace("-", "_").replace(" ", "_")
    if value.startswith(("B_", "I_", "E_", "S_")):
        value = value[2:]
    normalized = PUBLIC_LABEL_ALIASES.get(value)
    return normalized if normalized in TRACKED_LABELS else None


def normalize_spans(text: str, raw_spans: Iterable[dict]) -> list[dict]:
    """Validate, normalize, deduplicate, and order spans for JSONL output.

    Invalid native offsets are discarded.  This is intentionally fail-closed:
    a guessed text offset is worse than a false negative in an auditable
    benchmark prediction file.
    """
    normalized: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for raw in raw_spans:
        label = normalize_label(raw.get("label"))
        start, end = raw.get("start"), raw.get("end")
        if label is None or isinstance(start, bool) or isinstance(end, bool):
            continue
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start < 0 or end <= start or end > len(text):
            continue
        span_text = text[start:end]
        declared_text = raw.get("text")
        if declared_text not in (None, "") and declared_text != span_text:
            continue
        key = (label, start, end)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"label": label, "start": start, "end": end, "text": span_text})
    return sorted(normalized, key=lambda span: (span["start"], span["end"], span["label"]))


def write_predictions(docs, out_path: Path, predict: Callable[[str], Iterable[dict]]) -> None:
    """Emit one normalized envelope per loader document, including empties."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as stream:
        for index, doc in enumerate(docs, start=1):
            text = doc.text or ""
            print(f"  [{index}/{len(docs)}] {doc.doc_id}", file=sys.stderr)
            spans = normalize_spans(text, predict(text))
            stream.write(json.dumps({"doc_id": doc.doc_id, "lane": doc.lane, "spans": spans}, ensure_ascii=False) + "\n")

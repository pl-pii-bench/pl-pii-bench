#!/usr/bin/env python3
"""Presidio baseline adapter — the canonical reproducible baseline row.

Microsoft Presidio (`presidio-analyzer`) with spaCy `pl_core_news_lg` for
PERSON/ORG/LOC (via its NKJP-style `persName`/`orgName`/`placeName`/
`geogName` NER labels), plus custom Polish recognizers with checksum
validation for PESEL, NIP, REGON, and DOWOD (dowód osobisty), and
presidio's built-in recognizers for IBAN, EMAIL, and PHONE. Requires
`pip install -e ".[presidio]"` and `python -m spacy download pl_core_news_lg`.

Checksum algorithms are the public, government-published Polish identifier
standards (PESEL mod-10 weighted, NIP/REGON mod-11 weighted, dowód osobisty
mod-10 weighted with letter values A=10..Z=35) — general public knowledge,
not read from or derived from any product source.

Known, documented gaps (this is a baseline, not a ceiling):
- REGON: validates the 9-digit form only, not the 14-digit extended form.
- IBAN: presidio's built-in recognizer requires the 2-letter country-code
  prefix; the corpus's bare-26-digit-without-prefix `id_format` variant is
  not matched.
- No recognizer for POSTAL, DOB, PASSPORT, DRIVING_LICENSE, PAYMENT_CARD,
  VIN, VEHICLE_PLATE — outside the MVP's required label set.
- OCR/obfuscation-perturbed identifiers (`robustness` lane) are expected to
  fail: this baseline does not normalize `l`->`1`, `O`->`0`, or strip
  inserted spaces before checksum validation. That is what the robustness
  lane measures, not a bug in this adapter.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.loader import load_corpus, TRACKED_LABELS

# --------------------------------------------------------------------------
# Checksum validators (public Polish government algorithms)
# --------------------------------------------------------------------------

def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def valid_pesel(text: str) -> bool:
    d = _digits(text)
    if len(d) != 11:
        return False
    weights = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
    total = sum(int(c) * w for c, w in zip(d[:10], weights))
    control = (10 - (total % 10)) % 10
    return control == int(d[10])


def valid_nip(text: str) -> bool:
    d = _digits(text)
    if len(d) != 10:
        return False
    weights = [6, 5, 7, 2, 3, 4, 5, 6, 7]
    total = sum(int(c) * w for c, w in zip(d[:9], weights))
    return total % 11 == int(d[9])


def valid_regon9(text: str) -> bool:
    d = _digits(text)
    if len(d) != 9:
        return False
    weights = [8, 9, 2, 3, 4, 5, 6, 7]
    total = sum(int(c) * w for c, w in zip(d[:8], weights))
    control = total % 11
    if control == 10:
        control = 0
    return control == int(d[8])


def valid_dowod(text: str) -> bool:
    compact = text.replace(" ", "").upper()
    if len(compact) != 9:
        return False
    letters, check, digits = compact[:3], compact[3], compact[4:]
    if not (letters.isalpha() and check.isdigit() and digits.isdigit()):
        return False
    letter_values = [ord(c) - ord("A") + 10 for c in letters]
    weights = [7, 3, 1, 7, 3, 1, 7, 3]
    values = letter_values + [int(c) for c in digits]
    total = sum(v * w for v, w in zip(values, weights))
    return total % 10 == int(check)


# --------------------------------------------------------------------------
# Presidio wiring
# --------------------------------------------------------------------------

PRESIDIO_TO_PUBLIC = {
    "PERSON": "PERSON",
    "ORGANIZATION": "ORG",
    "LOCATION": "LOC",
    "PESEL": "PESEL",
    "NIP": "NIP",
    "REGON": "REGON",
    "DOWOD": "DOWOD",
    "IBAN_CODE": "IBAN",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
}


def build_analyzer():
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import (
        EmailRecognizer, IbanRecognizer, PhoneRecognizer, SpacyRecognizer,
    )

    class PeselRecognizer(PatternRecognizer):
        def __init__(self):
            super().__init__(
                supported_entity="PESEL",
                supported_language="pl",
                patterns=[Pattern("PESEL (11 digits)", r"\b\d{11}\b", 0.3)],
            )

        def validate_result(self, pattern_text):
            return valid_pesel(pattern_text)

    class NipRecognizer(PatternRecognizer):
        def __init__(self):
            super().__init__(
                supported_entity="NIP",
                supported_language="pl",
                patterns=[
                    Pattern("NIP (10 digits)", r"\b\d{10}\b", 0.3),
                    Pattern("NIP (dashed)", r"\b\d{3}-\d{3}-\d{2}-\d{2}\b", 0.4),
                ],
            )

        def validate_result(self, pattern_text):
            return valid_nip(pattern_text)

    class RegonRecognizer(PatternRecognizer):
        def __init__(self):
            super().__init__(
                supported_entity="REGON",
                supported_language="pl",
                patterns=[Pattern("REGON (9 digits)", r"\b\d{9}\b", 0.3)],
            )

        def validate_result(self, pattern_text):
            return valid_regon9(pattern_text)

    class DowodRecognizer(PatternRecognizer):
        def __init__(self):
            super().__init__(
                supported_entity="DOWOD",
                supported_language="pl",
                patterns=[Pattern("DOWOD (3 letters + 6 digits)", r"\b[A-Z]{3}\d{6}\b", 0.4)],
            )

        def validate_result(self, pattern_text):
            return valid_dowod(pattern_text)

    nlp_configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "pl", "model_name": "pl_core_news_lg"}],
        "ner_model_configuration": {
            # pl_core_news_lg uses NKJP-style labels, not the English
            # PER/ORG/LOC scheme presidio's SpacyRecognizer defaults expect.
            "model_to_presidio_entity_mapping": {
                "persName": "PERSON",
                "orgName": "ORGANIZATION",
                "placeName": "LOCATION",
                "geogName": "LOCATION",
            },
            "low_score_entity_names": [],
        },
    }
    nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()

    registry = RecognizerRegistry(supported_languages=["pl"])
    registry.add_recognizer(SpacyRecognizer(
        supported_language="pl",
        supported_entities=["PERSON", "ORGANIZATION", "LOCATION"],
    ))
    registry.add_recognizer(PeselRecognizer())
    registry.add_recognizer(NipRecognizer())
    registry.add_recognizer(RegonRecognizer())
    registry.add_recognizer(DowodRecognizer())
    registry.add_recognizer(IbanRecognizer(supported_language="pl"))
    registry.add_recognizer(EmailRecognizer(supported_language="pl"))
    registry.add_recognizer(PhoneRecognizer(supported_language="pl", supported_regions=("PL",)))

    return AnalyzerEngine(nlp_engine=nlp_engine, registry=registry, supported_languages=["pl"])


def analyze_document(analyzer, text: str) -> list[dict]:
    results = analyzer.analyze(text=text, language="pl")
    spans = []
    for r in results:
        label = PRESIDIO_TO_PUBLIC.get(r.entity_type)
        if label is None or label not in TRACKED_LABELS:
            continue
        spans.append({"label": label, "start": r.start, "end": r.end, "text": text[r.start:r.end]})
    return spans


def write_predictions(docs, analyzer, out_path: Path) -> None:
    """Write exactly one prediction envelope for each normalized document."""
    with out_path.open("w", encoding="utf-8") as fh:
        for i, doc in enumerate(docs, start=1):
            print(f"  [{i}/{len(docs)}] {doc.doc_id}", file=sys.stderr)
            spans = analyze_document(analyzer, doc.text)
            fh.write(
                json.dumps(
                    {"doc_id": doc.doc_id, "lane": doc.lane, "spans": spans},
                    ensure_ascii=False,
                )
                + "\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Presidio baseline adapter")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lanes", nargs="*", default=None)
    args = parser.parse_args()

    lanes = set(args.lanes) if args.lanes else None
    docs = [d for d in load_corpus(args.corpus, lanes=lanes) if d.text is not None]

    print(f"Loading Presidio analyzer (spaCy pl_core_news_lg) ...", file=sys.stderr)
    analyzer = build_analyzer()

    write_predictions(docs, analyzer, args.out)

    print(f"Wrote {len(docs)} documents to {args.out}")


if __name__ == "__main__":
    main()

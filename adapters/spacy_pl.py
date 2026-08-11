#!/usr/bin/env python3
"""spaCy ``pl_core_news_lg`` adapter for the public benchmark envelope."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.open_model import write_predictions
from eval.loader import load_corpus

MODEL_NAME = "pl_core_news_lg"
# This is the package release selected in pyproject's spacy-pl extra.
MODEL_REVISION = "3.8.0"
SPACY_LABELS = {
    "persName": "PERSON", "orgName": "ORG", "placeName": "LOC",
    "geogName": "LOC", "PER": "PERSON", "ORG": "ORG", "LOC": "LOC",
}


def load_model():
    try:
        import spacy
    except ImportError as error:
        raise SystemExit("error: install the spacy-pl extra to run this adapter") from error
    return spacy.load(MODEL_NAME)


def predict_document(model, text: str) -> list[dict]:
    doc = model(text)
    return [
        {"label": SPACY_LABELS.get(entity.label_, entity.label_), "start": entity.start_char,
         "end": entity.end_char, "text": entity.text}
        for entity in doc.ents
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lanes", nargs="*", default=None)
    args = parser.parse_args()
    docs = load_corpus(args.corpus, lanes=set(args.lanes) if args.lanes else None)
    model = load_model()
    write_predictions(docs, args.out, lambda text: predict_document(model, text))


if __name__ == "__main__":
    main()

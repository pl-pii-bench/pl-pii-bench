#!/usr/bin/env python3
"""GLiNER Polish PII adapter, pinned to an immutable Hugging Face revision."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.open_model import write_predictions
from eval.loader import load_corpus

MODEL_ID = "piotrmaciejbednarski/gliner-pii-polish"
# Immutable commit, not a mutable ``main`` branch.  Update only with a new
# benchmark release and record the change in its manifest.
MODEL_REVISION = "a06dee420506ca62c8948a5d6970b7a64455f15d"
THRESHOLD = 0.5
# These are the model card's fine-tuning labels. GLiNER conditions on the
# supplied strings, so substituting the benchmark schema would not reproduce
# the released model configuration.
MODEL_NATIVE_LABELS = (
    "name", "surname", "city", "address", "postal_code", "company", "phone",
    "email", "pesel", "document_number", "id_number", "bank_account",
)


def load_model():
    try:
        from gliner import GLiNER
    except ImportError as error:
        raise SystemExit("error: install the gliner-pii-polish extra to run this adapter") from error
    return GLiNER.from_pretrained(MODEL_ID, revision=MODEL_REVISION)


def predict_document(model, text: str) -> list[dict]:
    predictions = model.predict_entities(text, list(MODEL_NATIVE_LABELS), threshold=THRESHOLD)
    return [
        {"label": item.get("label"), "start": item.get("start"), "end": item.get("end"),
         "text": item.get("text")}
        for item in predictions
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

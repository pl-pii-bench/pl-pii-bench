#!/usr/bin/env python3
"""Pinned BardsAI multilingual token-classification public adapter."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.open_model import write_predictions
from eval.loader import load_corpus

MODEL_ID = "bardsai/eu-pii-anonimization-multilang-v2-preview"
# This model is a rolling preview. Pin the exact Hugging Face commit so a
# published benchmark run remains reproducible even when the preview changes.
MODEL_REVISION = "8e0b19766bb0dd4916d096b4f540dd46c138c760"


def load_pipeline():
    try:
        from transformers import pipeline
    except ImportError as error:
        raise SystemExit("error: install the bardsai-eu-pii extra to run this adapter") from error
    return pipeline(
        "token-classification", model=MODEL_ID, tokenizer=MODEL_ID,
        revision=MODEL_REVISION, aggregation_strategy="simple",
    )


def predict_document(detector, text: str) -> list[dict]:
    return [
        {"label": item.get("entity_group", item.get("entity")), "start": item.get("start"),
         "end": item.get("end"), "text": item.get("word")}
        for item in detector(text)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lanes", nargs="*", default=None)
    args = parser.parse_args()
    docs = load_corpus(args.corpus, lanes=set(args.lanes) if args.lanes else None)
    detector = load_pipeline()
    write_predictions(docs, args.out, lambda text: predict_document(detector, text))


if __name__ == "__main__":
    main()

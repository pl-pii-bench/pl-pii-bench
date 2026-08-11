#!/usr/bin/env python3
"""Dev helper: emit a predictions JSONL that is exactly the corpus ground
truth, for sanity-checking the scorer against itself (should score ~100%
recall / precision independent of any real detector). Not part of the
published harness surface — a debugging tool.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.loader import load_corpus, TRACKED_LABELS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path("corpus"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lanes", nargs="*", default=None)
    args = parser.parse_args()

    lanes = set(args.lanes) if args.lanes else None
    docs = load_corpus(args.corpus, lanes=lanes)

    with args.out.open("w", encoding="utf-8") as fh:
        for doc in docs:
            spans = []
            for e in doc.entities:
                if e.label not in TRACKED_LABELS:
                    continue
                spans.append({"label": e.label, "start": e.start, "end": e.end, "text": e.text})
            fh.write(json.dumps({"doc_id": doc.doc_id, "spans": spans}, ensure_ascii=False) + "\n")

    print(f"Wrote {len(docs)} documents to {args.out}")


if __name__ == "__main__":
    main()

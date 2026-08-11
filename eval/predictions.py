"""Predictions-file contract for pl-pii-bench.

A predictions file is JSONL, one line per document::

    {"doc_id": "core-umowa-001", "lane": "core", "spans": [
        {"label": "PERSON", "start": 12, "end": 24, "text": "Jan Kowalski"}
    ]}

`lane` is optional for a single-lane predictions file and **required** when one
file covers several lanes, because `doc_id` is unique per lane rather than per
corpus (the `pdf` lane reuses ten `core` document ids for its own extraction of
the same source documents). Tagged rows are keyed by `(lane, doc_id)` and are
honoured when scoring all lanes as well as when a caller filters to one lane.

This is the system-agnostic format `eval/score.py` consumes; any detector
integrates by producing one line like this per corpus document. Labels must
already be normalized to the 17 public labels (annotation-guidelines.md §2)
— predictions with any other label are ignored by the scorer, exactly like
ground-truth entities outside the frozen label set.

For the pdf lane, `start`/`end` are whatever offsets the system's own text
extraction produced (there is no shared canonical extraction across
systems); the scorer only uses `label` + `text` there (multiset matching,
see eval/matching.py), so `start`/`end` may be omitted or approximate for
pdf-lane spans.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PredictedSpan:
    label: str
    text: str
    start: int | None = None
    end: int | None = None


def load_predictions(
    path: Path, lanes: set[str] | None = None
) -> dict[tuple[str | None, str], list[PredictedSpan]]:
    """Returns {(lane, doc_id): [PredictedSpan, ...]}. Documents absent from the
    predictions file are treated by the scorer as having zero predictions
    (every ground-truth entity is a miss) — this is deliberate: a system
    that silently skips a document should not get an "n/a" free pass.

    `doc_id` is unique per lane, not per corpus: ten documents (for example
    `core-faktura-vat-high`) exist in both the `core` and `pdf` lanes with
    different text. When one adapter invocation covers several lanes, its
    predictions file therefore contains that id twice. Rows may carry an
    optional `lane` field; the scorer honours that tag whether or not `lanes`
    restricts the rows being loaded. A duplicate is an error only when both
    `lane` and `doc_id` match an earlier row. Untagged rows retain the `None`
    lane so single-lane legacy files can still be scored.
    """
    predictions: dict[tuple[str | None, str], list[PredictedSpan]] = {}
    seen_lines: dict[tuple[str | None, str], int] = {}
    with Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {e}") from e
            lane = row.get("lane")
            if lanes is not None and lane is not None and lane not in lanes:
                continue
            doc_id = row["doc_id"]
            key = (lane, doc_id)
            if key in predictions:
                raise ValueError(
                    f"{path}:{lineno}: duplicate doc_id {doc_id!r} in lane {lane!r} "
                    f"(first seen on line {seen_lines[key]})."
                )
            seen_lines[key] = lineno
            predictions[key] = [
                PredictedSpan(
                    label=s["label"],
                    text=s.get("text", ""),
                    start=s.get("start"),
                    end=s.get("end"),
                )
                for s in row.get("spans", [])
            ]
    return predictions

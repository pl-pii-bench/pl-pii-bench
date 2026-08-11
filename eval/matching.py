"""Span-matching rules (methodology §6.1) and per-document match resolution.

Two modes, both reported by the scorer:

- strict: exact span (start, end) and label match.
- relaxed: >=1 character of overlap, same label. Partial-span detections
  still flag the PII for a human reviewer, so relaxed recall is the
  privacy-relevant number; strict is the quality number.

Matching is one-to-one per document per label: each ground-truth entity
claims at most one predicted span (the highest-overlap available one), and
each predicted span can satisfy at most one ground-truth entity. This is
stricter than the internal bench's `run_recall.py` (which checks "does any
prediction overlap >=50%" independently per ground-truth entity, without
reserving predictions), and is the standard approach for span-level P/R —
duplicate correct predictions still count as false positives here.

The pdf lane now carries committed, canonical extracted text with real
offsets (see loader.py) and is matched by `match_document` exactly like
every other span lane -- there is no separate count/multiset matcher.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from eval.loader import Document, Entity, TRACKED_LABELS
from eval.predictions import PredictedSpan

MatchMode = Literal["strict", "relaxed"]


@dataclass
class DocMatchResult:
    tp: list[tuple[Entity, PredictedSpan]]
    fn: list[Entity]
    fp: list[PredictedSpan]


def _overlap_chars(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def _is_match(entity: Entity, span: PredictedSpan, mode: MatchMode) -> bool:
    if span.label != entity.label:
        return False
    if span.start is None or span.end is None or entity.start is None or entity.end is None:
        return False
    if mode == "strict":
        return span.start == entity.start and span.end == entity.end
    return _overlap_chars(span.start, span.end, entity.start, entity.end) >= 1


def match_document(
    entities: list[Entity],
    predicted: list[PredictedSpan],
    mode: MatchMode,
) -> DocMatchResult:
    """One-to-one greedy matching, highest-overlap-first, per label."""
    tracked_gt = [e for e in entities if e.label in TRACKED_LABELS]
    tracked_pred = list(enumerate(p for p in predicted if p.label in TRACKED_LABELS))

    used_pred: set[int] = set()
    tp: list[tuple[Entity, PredictedSpan]] = []
    fn: list[Entity] = []

    for entity in tracked_gt:
        candidates = [
            (idx, span) for idx, span in tracked_pred
            if idx not in used_pred and _is_match(entity, span, mode)
        ]
        if not candidates:
            fn.append(entity)
            continue
        if entity.start is not None and entity.end is not None:
            best_idx, best_span = max(
                candidates,
                key=lambda item: _overlap_chars(
                    item[1].start, item[1].end, entity.start, entity.end
                ),
            )
        else:
            best_idx, best_span = candidates[0]
        used_pred.add(best_idx)
        tp.append((entity, best_span))

    fp = [span for idx, span in tracked_pred if idx not in used_pred]
    return DocMatchResult(tp=tp, fn=fn, fp=fp)

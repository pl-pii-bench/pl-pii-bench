#!/usr/bin/env python3
"""pl-pii-bench scorer CLI.

    python eval/score.py --corpus corpus/ --predictions predictions.jsonl \
        --system presidio-pl --out-md results/presidio-core.md --out-json results/presidio-core.json

Emits a stratified Markdown + JSON report (methodology §6) with the harness
version + corpus version stamped in. No single aggregate score; the summary
block is direct-identifier recall + protected recall (relaxed) +
residual-identifiability rate + negative-lane FP count.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import HARNESS_VERSION
from eval.loader import load_corpus, KNOWN_LANES
from eval.predictions import load_predictions
from eval.metrics import (
    evaluate_corpus,
    label_rows,
    case_stratified_recall,
    format_stratified_recall,
    robustness_delta,
    negative_lane_fp,
    residual_identifiability,
    consistency_reversibility,
    summary_block,
)
from eval.report import LANE_NOTES, write_json_report, write_markdown_report, render_markdown


def build_report(
    corpus_dir: Path,
    predictions_path: Path,
    system: str,
    corpus_version: str,
    mapping_path: Path | None,
    lanes: set[str] | None = None,
) -> dict:
    documents = load_corpus(corpus_dir, lanes=lanes)
    predictions = load_predictions(predictions_path, lanes=lanes)
    matches = evaluate_corpus(documents, predictions)

    by_lane: dict[str, list] = {}
    for doc in documents:
        by_lane.setdefault(doc.lane, []).append(doc)

    lanes_report: dict[str, dict] = {}
    for lane, docs in sorted(by_lane.items()):
        lane_report: dict = {"labels": label_rows(docs, matches)}
        if LANE_NOTES.get(lane):
            lane_report["notes"] = list(LANE_NOTES[lane])
        if lane == "inflection":
            lane_report["case_stratified"] = case_stratified_recall(docs, matches)
        if lane == "identifiers":
            lane_report["format_stratified"] = format_stratified_recall(docs, matches)
        if lane == "robustness":
            lane_report["robustness_delta"] = robustness_delta(documents, matches)
        if lane == "negative":
            lane_report["negative_fp"] = negative_lane_fp(docs, matches)
        lanes_report[lane] = lane_report

    # Every loaded lane is span-based now (pdf included -- see loader.py),
    # so the summary/residual-identifiability aggregates run over all of them.
    core_docs = [d for d in documents if d.lane == "core"]
    reident_source = core_docs if core_docs else documents
    reident = residual_identifiability(reident_source, matches)

    negative_docs = [d for d in documents if d.lane == "negative"]
    summary = summary_block(documents, matches, negative_docs, reident)

    report = {
        "meta": {
            "harness_version": HARNESS_VERSION,
            "corpus_version": corpus_version,
            "system": system,
            "predictions_file": str(predictions_path),
            "lanes_evaluated": sorted(by_lane.keys()),
        },
        "summary": summary,
        "lanes": lanes_report,
        "residual_identifiability": reident,
        "consistency_reversibility": consistency_reversibility(mapping_path),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="pl-pii-bench stratified scorer")
    parser.add_argument("--corpus", type=Path, required=True, help="Path to corpus/ directory")
    parser.add_argument("--predictions", type=Path, required=True, help="Predictions JSONL file")
    parser.add_argument("--system", default="unnamed-system", help="Name of the system under evaluation")
    parser.add_argument("--corpus-version", default="unversioned", help="Corpus version to stamp into the report")
    parser.add_argument("--mapping", type=Path, default=None, help="Optional consistency/reversibility mapping JSONL (§6.5)")
    parser.add_argument("--lanes", nargs="*", default=None, help="Restrict to these lanes (default: all present)")
    parser.add_argument("--out-md", type=Path, default=None, help="Markdown report output path")
    parser.add_argument("--out-json", type=Path, default=None, help="JSON report output path")
    args = parser.parse_args()

    lanes = set(args.lanes) if args.lanes else None
    if lanes and not lanes.issubset(KNOWN_LANES):
        parser.error(f"--lanes must be a subset of {sorted(KNOWN_LANES)}")

    report = build_report(
        corpus_dir=args.corpus,
        predictions_path=args.predictions,
        system=args.system,
        corpus_version=args.corpus_version,
        mapping_path=args.mapping,
        lanes=lanes,
    )

    markdown = render_markdown(report)
    print(markdown)

    if args.out_md:
        write_markdown_report(args.out_md, report)
    if args.out_json:
        write_json_report(args.out_json, report)


if __name__ == "__main__":
    main()

"""Markdown + JSON rendering for the stratified pl-pii-bench report."""
from __future__ import annotations

import json
from pathlib import Path


# Lane-level caveats rendered into every report, for every system, so a
# reader of a single report never has to find them in a separate document.
# Keep these system-neutral and factual: they describe how the corpus was
# built, not how any one system performed.
LANE_NOTES: dict[str, list[str]] = {
    "address": [
        "**Boundary-convention provenance.** This lane's span convention -- "
        "`LOC` and `POSTAL` annotated as separate spans, street-type prefix "
        "(`ul.`, `al.`, `pl.`) kept inside the `LOC` span -- was adopted from "
        "the Anonimator maintainer's internal 2026-07-18 measurement practice "
        "rather than derived independently (`corpus/annotation-guidelines.md` "
        "§5.4). Anonimator's native output follows that convention exactly, so "
        "**the strict columns on this lane favour it by construction**: a system "
        "that emits one span per whole address, or that splits the prefix off as "
        "its own span, loses strict credit for detections a human reviewer would "
        "accept. Use the relaxed columns for cross-system comparison here. "
        "Relaxed matching (>=1 char overlap) does not reward boundary agreement, "
        "so it does not carry this advantage.",
        "**One-to-one matching cuts both ways.** Each ground-truth entity claims "
        "at most one predicted span and each predicted span satisfies at most one "
        "entity (`eval/matching.py`). Splitting one gold span into two predictions "
        "yields 1 TP + 1 FP (a precision cost, never double credit); merging two "
        "gold spans into one prediction yields 1 TP + 1 FN (a recall cost). "
        "Segmentation disagreement is penalized in both directions and can never "
        "inflate a score.",
    ],
}


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _per_1000_tokens(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1f}"


def write_json_report(path: Path, report: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def render_markdown(report: dict) -> str:
    lines: list[str] = []
    meta = report["meta"]
    lines.append(f"# pl-pii-bench report: `{meta['system']}`")
    lines.append("")
    lines.append(f"- Harness version: `{meta['harness_version']}`")
    lines.append(f"- Corpus version: `{meta['corpus_version']}`")
    lines.append(f"- Predictions file: `{meta['predictions_file']}`")
    lines.append(f"- Lanes evaluated: {', '.join(meta['lanes_evaluated'])}")
    lines.append(f"- Matching modes: strict (exact span+label), relaxed (>=1 char overlap, same label)")
    lines.append("")

    lines.append("## Summary (no single aggregate — methodology §6.2)")
    lines.append("")
    s = report["summary"]
    lines.append(f"- Direct-identifier recall (relaxed): **{_pct(s['direct_identifier_recall_relaxed'])}**")
    lines.append(f"- Protected recall (relaxed): **{_pct(s['protected_recall_relaxed'])}**")
    lines.append(f"- Residual-identifiability rate: **{_pct(s['residual_identifiability_rate'])}**")
    lines.append(f"- Negative-lane FP count: **{s['negative_lane_fp_count']}**")
    lines.append(
        "- Negative-lane FP per 1,000 tokens: "
        f"**{_per_1000_tokens(s['negative_lane_fp_per_1000_tokens'])}**"
    )
    lines.append("")

    for lane, lane_report in report["lanes"].items():
        lines.append(f"## Lane: `{lane}`")
        lines.append("")

        for note in lane_report.get("notes", []):
            lines.append(f"> {note}")
            lines.append("")

        rows = lane_report.get("labels", [])
        if rows:
            lines.append("| Label | TP(strict) | FN(strict) | FP(strict) | Recall(strict) | Precision(strict) |"
                          " Recall(relaxed) | Precision(relaxed) | F2(relaxed) | Protected recall | Direct recall | Quasi coverage |")
            lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
            for row in rows:
                st, rl = row["strict"], row["relaxed"]
                lines.append(
                    f"| {row['label']} | {st['tp']} | {st['fn']} | {st['fp']} | {_pct(st['recall'])} | {_pct(st['precision'])} "
                    f"| {_pct(rl['recall'])} | {_pct(rl['precision'])} | {_pct(rl['f2'])} "
                    f"| {_pct(row['protected_recall_relaxed'])} | {_pct(row['direct_identifier_recall_relaxed'])} "
                    f"| {_pct(row['quasi_identifier_coverage_relaxed'])} |"
                )
            lines.append("")

        if lane_report.get("case_stratified"):
            lines.append("### Case-stratified recall")
            lines.append("")
            lines.append("| Case | TP | FN | Recall |")
            lines.append("|---|---:|---:|---:|")
            for row in lane_report["case_stratified"]:
                lines.append(f"| {row['case']} | {row['tp']} | {row['fn']} | {_pct(row['recall'])} |")
            lines.append("")

        if lane_report.get("format_stratified"):
            lines.append("### Format-stratified recall")
            lines.append("")
            lines.append("| Label | Format | TP | FN | Recall |")
            lines.append("|---|---|---:|---:|---:|")
            for row in lane_report["format_stratified"]:
                lines.append(f"| {row['label']} | {row['format']} | {row['tp']} | {row['fn']} | {_pct(row['recall'])} |")
            lines.append("")

        if lane_report.get("robustness_delta"):
            lines.append("### Clean-vs-perturbed recall delta")
            lines.append("")
            lines.append("| Doc | Clean doc | Noise | Label | Clean recall | Perturbed recall | Delta |")
            lines.append("|---|---|---|---|---:|---:|---:|")
            for row in lane_report["robustness_delta"]:
                delta = row["delta"]
                delta_text = "n/a" if delta is None else f"{delta * 100:+.1f}pp"
                lines.append(
                    f"| {row['doc_id']} | {row['clean_doc_id']} | {row['noise']} | {row['label']} "
                    f"| {_pct(row['clean_recall'])} | {_pct(row['perturbed_recall'])} "
                    f"| {delta_text} |"
                )
            lines.append("")

        if lane_report.get("negative_fp"):
            lines.append("### False positives per category")
            lines.append("")
            lines.append("| Category | FP count | Tokens | FP / 1,000 tokens |")
            lines.append("|---|---:|---:|---:|")
            for row in lane_report["negative_fp"]:
                lines.append(
                    f"| {row['category']} | {row['fp_count']} | {row['tokens']} "
                    f"| {_per_1000_tokens(row['fp_per_1000_tokens'])} |"
                )
            lines.append("")

    lines.append("## Residual-identifiability (§6.4)")
    lines.append("")
    ri = report["residual_identifiability"]
    lines.append(f"- Rules applied: {', '.join(ri['rules'])} (see `eval/reidentification_rules.py`, provisional pending ratification)")
    lines.append(f"- Subjects evaluated: {ri['subjects_evaluated']}")
    lines.append(f"- Subjects flagged re-identifiable: {ri['subjects_flagged']}")
    lines.append(f"- Rate: {_pct(ri['rate'])}")
    lines.append("")

    lines.append("## Consistency / reversibility (§6.5, optional)")
    lines.append("")
    cr = report["consistency_reversibility"]
    if cr == "n/a":
        lines.append("n/a — detection-only predictions file, no mapping supplied.")
    else:
        lines.append(f"- Consistency (1:1 entity_id -> surrogate): {_pct(cr['consistency_fraction'])}")
        lines.append(f"- Surrogate collisions: {cr['surrogate_collisions']}")
        lines.append(f"- Round-trip fidelity: {_pct(cr['round_trip_fidelity'])}")
    lines.append("")

    return "\n".join(lines) + "\n"


def write_markdown_report(path: Path, report: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(render_markdown(report), encoding="utf-8")

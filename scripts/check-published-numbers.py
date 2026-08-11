#!/usr/bin/env python3
"""Fail when published benchmark numbers drift from the release manifest."""
from __future__ import annotations

import argparse
import json
import re
from fractions import Fraction
from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_LANES = (
    "core",
    "inflection",
    "identifiers",
    "address",
    "negative",
    "robustness",
    "pdf",
)
HOLDOUT_LANES = ("core", "identifiers", "address", "negative")


def normalized(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def percentage(value: float) -> str:
    return f"{value * 100:.1f}".replace(".", ",") + "%"


def fail(location: str, expected: object, actual: object) -> None:
    raise SystemExit(
        f"mismatch: {location}: expected {expected!r}, found {actual!r}"
    )


def release_rows(manifest: dict) -> tuple[tuple[dict, ...], dict[tuple[str, str, str], dict]]:
    engines = manifest.get("engine_registry", {}).get("engines")
    if not isinstance(engines, list) or not engines:
        raise SystemExit("error: release manifest has no engine registry")
    engine_ids = tuple(engine.get("id") for engine in engines)
    if any(not isinstance(engine_id, str) for engine_id in engine_ids):
        raise SystemExit("error: release manifest has an invalid engine id")

    expected = {
        (scope, lane, engine_id)
        for scope, lanes in (("public", PUBLIC_LANES), ("holdout", HOLDOUT_LANES))
        for lane in lanes
        for engine_id in engine_ids
    }
    rows = {}
    for run in manifest.get("runs", []):
        key = (run.get("scope"), run.get("lane"), run.get("engine"))
        if key not in expected:
            continue
        if key in rows:
            raise SystemExit(f"error: release manifest has duplicate run: {key}")
        rows[key] = run
    if set(rows) != expected:
        missing = sorted(expected - set(rows))
        raise SystemExit(f"error: release manifest is missing runs: {missing}")
    return tuple(engines), rows


def public_documents(rows: dict[tuple[str, str, str], dict], engines: tuple[dict, ...]) -> dict[str, int]:
    documents = {}
    for lane in PUBLIC_LANES:
        values = {
            rows[("public", lane, engine["id"])].get("documents")
            for engine in engines
        }
        if len(values) != 1 or not isinstance(next(iter(values)), int):
            raise SystemExit(
                "error: public release manifest has inconsistent document counts "
                f"for lane {lane!r}: {sorted(values, key=str)}"
            )
        documents[lane] = next(iter(values))
    return documents


def address_span_count(rows: dict[tuple[str, str, str], dict], engines: tuple[dict, ...]) -> int:
    """Infer the common scoring denominator from exact manifest recall values."""

    denominators = []
    for engine in engines:
        value = rows[("public", "address", engine["id"])]["summary"].get(
            "relaxed_recall"
        )
        if not isinstance(value, float):
            raise SystemExit("error: manifest address recall must be a float")
        fraction = Fraction(value).limit_denominator(1_000_000)
        if abs(float(fraction) - value) > 1e-12:
            raise SystemExit("error: manifest address recall is not an exact fraction")
        denominators.append(fraction.denominator)
    count = max(denominators)
    if any(count % denominator for denominator in denominators):
        raise SystemExit(
            "error: manifest address recall values do not share a span denominator"
        )
    return count


def markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def check_document_table(path: Path, documents: dict[str, int]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        header = next(index for index, line in enumerate(lines) if line == "| Lane | Documents |")
    except StopIteration:
        fail(f"{path}: public corpus table header", "| Lane | Documents |", None)
    for offset, lane in enumerate(PUBLIC_LANES, start=2):
        if header + offset >= len(lines):
            fail(f"{path}: {lane} row", [f"`{lane}`", str(documents[lane])], None)
        actual = markdown_row(lines[header + offset])
        expected = [f"`{lane}`", str(documents[lane])]
        if actual != expected:
            fail(f"{path}: public/{lane} documents", expected, actual)


def address_resolution_sentence(address_spans: int) -> str:
    resolution = 100 / address_spans
    return (
        f"The public address lane has {address_spans} annotated spans, so one missed "
        f"span changes its span recall by about {resolution:.1f} percentage point."
    )


def check_readme(readme_path: Path, documents: dict[str, int], address_spans: int) -> None:
    check_document_table(readme_path, documents)
    expected_sentence = address_resolution_sentence(address_spans)
    text = normalized(readme_path.read_text(encoding="utf-8"))
    if expected_sentence not in text:
        fail(f"{readme_path}: address resolution", expected_sentence, None)


def check_dataset_card(
    dataset_card_path: Path, documents: dict[str, int], address_spans: int
) -> None:
    check_document_table(dataset_card_path, documents)
    text = normalized(dataset_card_path.read_text(encoding="utf-8"))
    expected_sentence = address_resolution_sentence(address_spans)
    if expected_sentence not in text:
        fail(f"{dataset_card_path}: address resolution", expected_sentence, None)
    identifier_sentence = (
        "Every identifier label carries at least 30 annotated spans across at least "
        "four rendered formats."
    )
    if identifier_sentence not in text:
        fail(f"{dataset_card_path}: identifier coverage", identifier_sentence, None)


class BenchmarkTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: dict[tuple[str, str], list[list[str]]] = {}
        self._key: tuple[str, str] | None = None
        self._cells: list[str] = []
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            scope = attributes.get("data-scope")
            lane = attributes.get("data-lane")
            if scope and lane:
                self._key = (scope, lane)
                self._cells = []
        elif tag == "td" and self._key is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._cell_parts is not None:
            self._cells.append(normalized("".join(self._cell_parts)))
            self._cell_parts = None
        elif tag == "tr" and self._key is not None:
            self.rows.setdefault(self._key, []).append(self._cells)
            self._key = None
            self._cells = []


def score(run: dict, lane: str) -> str:
    summary = run.get("summary", {})
    if lane == "negative":
        value = summary.get("negative_lane_fp_count")
        if not isinstance(value, int):
            raise SystemExit("error: manifest negative false-alarm count must be an integer")
        return str(value)
    value = summary.get("relaxed_recall")
    if not isinstance(value, float):
        raise SystemExit("error: manifest relaxed recall must be a float")
    return percentage(value)


def html_row(parser: BenchmarkTableParser, scope: str, lane: str) -> list[str]:
    rows = parser.rows.get((scope, lane), [])
    if len(rows) != 1:
        fail(f"benchmark.html: {scope}/{lane} row count", 1, len(rows))
    return rows[0]


def check_html(
    html_path: Path,
    documents: dict[str, int],
    engines: tuple[dict, ...],
    rows: dict[tuple[str, str, str], dict],
) -> None:
    parser = BenchmarkTableParser()
    parser.feed(html_path.read_text(encoding="utf-8"))
    parser.close()

    for lane in PUBLIC_LANES:
        actual = html_row(parser, "public", lane)
        expected_scores = [score(rows[("public", lane, engine["id"])], lane) for engine in engines]
        if len(actual) != 7:
            fail(f"{html_path}: public/{lane} cell count", 7, len(actual))
        if actual[0] != str(documents[lane]):
            fail(f"{html_path}: public/{lane} positions", str(documents[lane]), actual[0])
        if actual[2:] != expected_scores:
            fail(f"{html_path}: public/{lane} scores", expected_scores, actual[2:])

    for lane in HOLDOUT_LANES:
        actual = html_row(parser, "holdout", lane)
        expected_scores = [
            score(rows[(scope, lane, engine["id"])], lane)
            for engine in engines
            for scope in ("public", "holdout")
        ]
        if actual != expected_scores:
            fail(f"{html_path}: holdout/{lane} scores", expected_scores, actual)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "results" / "v1.0.0" / "release-manifest.json",
    )
    parser.add_argument("--readme", type=Path, default=REPO_ROOT / "README.md")
    parser.add_argument(
        "--dataset-card",
        type=Path,
        default=REPO_ROOT
        / "dist"
        / "release"
        / "v1.0.0"
        / "pl-pii-bench-v1.0.0-huggingface"
        / "README.md",
        help=(
            "rendered Hugging Face dataset card produced by package-release.py "
            "(not scripts/dataset-card-template.md, which is unrendered)"
        ),
    )
    parser.add_argument(
        "--benchmark-html",
        type=Path,
        default=REPO_ROOT.parent
        / "products"
        / "anonimator"
        / "web-site"
        / "src"
        / "pages"
        / "benchmark.astro",
        help=(
            "the site migrated to Astro; the results table markup is static "
            "HTML inside this .astro source, so HTMLParser can read it directly "
            "without a prior `astro build`"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    engines, rows = release_rows(manifest)
    documents = public_documents(rows, engines)
    address_spans = address_span_count(rows, engines)
    check_readme(args.readme, documents, address_spans)
    check_dataset_card(args.dataset_card, documents, address_spans)
    check_html(args.benchmark_html, documents, engines, rows)
    print("published benchmark numbers match release manifest")


if __name__ == "__main__":
    main()

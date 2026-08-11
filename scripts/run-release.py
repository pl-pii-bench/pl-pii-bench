#!/usr/bin/env python3
"""Plan or execute the complete pl-pii-bench release matrix.

Plan mode is fast and side-effect free. Full mode runs both adapters and
the scorer for every public and private-holdout lane, writing versioned
artifacts locally. This command never publishes or uploads artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MONOREPO_ROOT = REPO_ROOT.parent
HARNESS_VERSION = "0.1.0"

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


@dataclass(frozen=True)
class EngineSpec:
    """A local engine that may appear in public benchmark artifacts."""

    engine_id: str
    display_name: str
    adapter: str
    dependency_group: str
    execution: str
    model_id: str | None = None
    model_revision: str | None = None
    public_lanes: tuple[str, ...] = PUBLIC_LANES
    holdout_lanes: tuple[str, ...] = HOLDOUT_LANES

    def lanes_for(self, scope: str) -> tuple[str, ...]:
        if scope == "public":
            return self.public_lanes
        if scope == "holdout":
            return self.holdout_lanes
        raise ValueError(f"unsupported release scope: {scope}")

    def manifest_row(self) -> dict[str, object]:
        reproducibility: dict[str, str] = {
            "dependency_group": self.dependency_group,
            "execution": self.execution,
        }
        if self.model_id is not None:
            reproducibility["model_id"] = self.model_id
        if self.model_revision is not None:
            reproducibility["model_revision"] = self.model_revision
        return {
            "id": self.engine_id,
            "display_name": self.display_name,
            "adapter": self.adapter,
            "public_lanes": list(self.public_lanes),
            "holdout_lanes": list(self.holdout_lanes),
            "reproducibility": reproducibility,
        }


PUBLIC_ENGINE_REGISTRY_VERSION = 1
PUBLIC_ENGINE_REGISTRY = (
    EngineSpec(
        "anonimator",
        "Anonimator",
        "anonimator",
        "product-attested",
        "maintainer-attested",
    ),
    EngineSpec(
        "presidio",
        "Presidio",
        "presidio",
        "presidio",
        "open-adapter",
    ),
    EngineSpec(
        "spacy-pl",
        "spaCy PL",
        "spacy_pl",
        "spacy-pl",
        "open-adapter",
    ),
    EngineSpec(
        "gliner-pii-polish",
        "GLiNER PII Polish",
        "gliner_pii_polish",
        "gliner-pii-polish",
        "open-adapter",
    ),
    EngineSpec(
        "bardsai-eu-pii",
        "BardsAI EU PII",
        "bardsai_eu_pii",
        "bardsai-eu-pii",
        "open-adapter",
        "bardsai/eu-pii-anonimization-multilang-v2-preview",
        "8e0b19766bb0dd4916d096b4f540dd46c138c760",
    ),
)
PUBLIC_ENGINE_IDS = tuple(spec.engine_id for spec in PUBLIC_ENGINE_REGISTRY)
PUBLIC_ENGINE_BY_ID = {spec.engine_id: spec for spec in PUBLIC_ENGINE_REGISTRY}

# These diagnostics are deliberately private and must never become public rows.
PRIVATE_OR_REMOTE_ENGINE_IDS = frozenset(
    {
        "anthropic-api",
        "claude-terminal-agent",
        "codex-subscription",
        "gemini-api",
        "openai-api",
    }
)

DEFAULT_PUBLIC_CORPUS = REPO_ROOT / "corpus"
DEFAULT_HOLDOUT_CORPUS = (
    MONOREPO_ROOT / "products" / "anonimator" / "bench" / "corpus" / "holdout"
)
DEFAULT_PRODUCT_REPO = MONOREPO_ROOT / "products" / "anonimator"
DEFAULT_PRESIDIO_PYTHON = (
    DEFAULT_PRODUCT_REPO / "bench" / "eval" / ".venv-baselines" / "bin" / "python"
)


def selected_public_engines(selection: str = "all") -> tuple[EngineSpec, ...]:
    """Return a safe public-engine selection without admitting private engines."""

    if selection == "all":
        return PUBLIC_ENGINE_REGISTRY
    try:
        return (PUBLIC_ENGINE_BY_ID[selection],)
    except KeyError as error:
        raise ValueError(f"unsupported public engine selection: {selection}") from error


def release_runs(
    engines: tuple[EngineSpec, ...] = PUBLIC_ENGINE_REGISTRY,
) -> list[dict[str, str]]:
    runs: list[dict[str, str]] = []
    for scope in ("public", "holdout"):
        for engine in engines:
            for lane in engine.lanes_for(scope):
                runs.append(
                    {"scope": scope, "engine": engine.engine_id, "lane": lane}
                )
    return runs


def release_plan(version: str, engines: tuple[EngineSpec, ...] = PUBLIC_ENGINE_REGISTRY) -> dict:
    runs = release_runs(engines)
    return {
        "version": version,
        "matrix": {
            "public_lanes": list(PUBLIC_LANES),
            "holdout_lanes": list(HOLDOUT_LANES),
            "engine_registry_version": PUBLIC_ENGINE_REGISTRY_VERSION,
            "engines": [spec.manifest_row() for spec in engines],
        },
        "counts": {
            "public_lanes": len(PUBLIC_LANES),
            "holdout_lanes": len(HOLDOUT_LANES),
            "engines": len(engines),
            "public_runs": sum(
                len(engine.public_lanes) for engine in engines
            ),
            "holdout_runs": sum(
                len(engine.holdout_lanes) for engine in engines
            ),
            "runs": len(runs),
        },
        "runs": runs,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(paths: list[Path], relative_to: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest.update(path.relative_to(relative_to).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _run(command: list[str]) -> None:
    print("+ " + shlex.join(command), flush=True)
    subprocess.run(command, check=True)


def _validate_lane(corpus: Path, lane: str):
    sys.path.insert(0, str(REPO_ROOT))
    from eval.loader import load_corpus

    documents = load_corpus(corpus, lanes={lane})
    if not documents:
        raise SystemExit(f"error: no documents loaded for {corpus} lane {lane}")
    if any(document.doc_id == "" for document in documents):
        raise SystemExit(f"error: blank document ID in {corpus} lane {lane}")
    if len({document.doc_id for document in documents}) != len(documents):
        raise SystemExit(f"error: duplicate document ID in {corpus} lane {lane}")
    return documents


def _adapter_command(
    engine: EngineSpec,
    corpus: Path,
    lanes: list[str],
    predictions: Path,
    product_repo: Path,
    presidio_python: Path,
) -> list[str]:
    python = (
        presidio_python if engine.engine_id == "presidio" else Path(sys.executable)
    )
    command = [
        "arch",
        "-arm64",
        str(python),
        str(REPO_ROOT / "adapters" / f"{engine.adapter}.py"),
        "--corpus",
        str(corpus),
    ]
    # Every adapter declares `--lanes` with `nargs="*"`, so repeating the flag
    # keeps only the LAST occurrence. All lanes of the group must be passed
    # after a single flag, or the adapter silently emits predictions for one
    # lane and every other lane in the group scores 0.0 recall.
    command.append("--lanes")
    command.extend(lanes)
    command.extend(["--out", str(predictions)])
    if engine.engine_id == "anonimator":
        command.extend(["--product-repo", str(product_repo)])
    return command


def _score_command(
    corpus: Path,
    lane: str,
    predictions: Path,
    report_md: Path,
    report_json: Path,
    engine: str,
    corpus_version: str,
) -> list[str]:
    return [
        sys.executable,
        str(REPO_ROOT / "eval" / "score.py"),
        "--corpus",
        str(corpus),
        "--predictions",
        str(predictions),
        "--lanes",
        lane,
        "--system",
        engine,
        "--corpus-version",
        corpus_version,
        "--out-md",
        str(report_md),
        "--out-json",
        str(report_json),
    ]


def _git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(MONOREPO_ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _presidio_versions(python: Path) -> dict[str, str]:
    code = (
        "import importlib.metadata as m,json;"
        "print(json.dumps({"
        "'presidio_analyzer_version':m.version('presidio-analyzer'),"
        "'spacy_version':m.version('spacy'),"
        "'spacy_model_version':m.version('pl-core-news-lg')"
        "}))"
    )
    result = subprocess.run(
        ["arch", "-arm64", str(python), "-B", "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _relative_artifact(path: Path) -> dict[str, str | int]:
    return {
        "path": path.relative_to(REPO_ROOT).as_posix(),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def _atomic_write_json(path: Path, value: dict) -> None:
    """Write a release record without exposing a partially written JSON file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _artifact_is_verified(artifact: dict[str, object]) -> bool:
    """Return whether an artifact record still matches the file it describes."""

    path_value = artifact.get("path")
    digest = artifact.get("sha256")
    bytes_count = artifact.get("bytes")
    if not isinstance(path_value, str) or not isinstance(digest, str):
        return False
    if not isinstance(bytes_count, int):
        return False
    path = REPO_ROOT / path_value
    return path.is_file() and path.stat().st_size == bytes_count and _sha256(path) == digest


def _harness_sha256() -> str:
    """Content hash of the scoring harness and adapters.

    `HARNESS_VERSION` is hand-maintained, so a bug fix in `eval/` or
    `adapters/` does not change it. Without this hash the resume gate would
    keep reusing run records produced by the code that had the bug.
    """

    paths = sorted(
        [
            *(REPO_ROOT / "eval").glob("*.py"),
            *(REPO_ROOT / "adapters").glob("*.py"),
            Path(__file__).resolve(),
        ]
    )
    return _tree_sha256(paths, REPO_ROOT)


def _lane_corpus_state(documents: list, corpus_path: Path) -> tuple[int, str]:
    """(document count, content hash) of one lane, as recorded in a run record."""

    return (
        len(documents),
        _tree_sha256(
            sorted(
                {
                    document.source_file.resolve()
                    for document in documents
                    if document.source_file is not None
                }
            ),
            Path(corpus_path).resolve(),
        ),
    )


def _run_record_is_verified(
    record: object,
    expected: dict[str, str],
    corpus_state: tuple[int, str] | None = None,
) -> bool:
    """Accept only a completed, hash-verified record for the exact requested run.

    `corpus_state` is the (document count, corpus sha256) of the lane as it
    exists now. A record scored against different corpus content is not a
    result for this corpus, however intact its own artifacts are: without this
    check, growing a lane leaves every prior run "verified" and the release
    resumes stale numbers under a manifest that advertises the new corpus.
    """

    if not isinstance(record, dict):
        return False
    if any(record.get(key) != value for key, value in expected.items()):
        return False
    if corpus_state is not None:
        documents, corpus_sha256 = corpus_state
        if record.get("documents") != documents:
            return False
        if record.get("corpus_sha256") != corpus_sha256:
            return False
        # Records written before this field existed cannot be shown to come
        # from the current harness, so they are not resumable.
        if record.get("harness_sha256") != _harness_sha256():
            return False
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict):
        return False
    return all(
        isinstance(artifact, dict) and _artifact_is_verified(artifact)
        for artifact in artifacts.values()
    )


def _artifact_tree_sha256(run_records: list[dict], *, scope: str | None = None) -> str:
    """Hash run artifacts using their recorded repository-relative paths."""

    paths = [
        REPO_ROOT / artifact["path"]
        for run in run_records
        if scope is None or run["scope"] == scope
        for artifact in run["artifacts"].values()
    ]
    return _tree_sha256(paths, REPO_ROOT)


def _publish_run_directory(staging_dir: Path, final_run_dir: Path) -> None:
    """Atomically publish one completed run after creating its scope directory."""

    final_run_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_run_dir.exists():
        shutil.rmtree(final_run_dir)
    os.replace(staging_dir, final_run_dir)


def _micro_summary(report: dict, lane: str) -> dict:
    rows = report["lanes"][lane]["labels"]
    relaxed_tp = sum(row["relaxed"]["tp"] for row in rows)
    relaxed_fn = sum(row["relaxed"]["fn"] for row in rows)
    relaxed_fp = sum(row["relaxed"]["fp"] for row in rows)
    recall_denominator = relaxed_tp + relaxed_fn
    precision_denominator = relaxed_tp + relaxed_fp
    recall = relaxed_tp / recall_denominator if recall_denominator else None
    precision = relaxed_tp / precision_denominator if precision_denominator else None
    if recall is None or precision is None or 4 * precision + recall == 0:
        f2 = None
    else:
        f2 = 5 * precision * recall / (4 * precision + recall)
    summary = report["summary"]
    return {
        "relaxed_recall": recall,
        "relaxed_precision": precision,
        "relaxed_f2": f2,
        "direct_identifier_recall_relaxed": summary[
            "direct_identifier_recall_relaxed"
        ],
        "protected_recall_relaxed": summary["protected_recall_relaxed"],
        "residual_identifiability_rate": summary[
            "residual_identifiability_rate"
        ],
        "negative_lane_fp_count": summary["negative_lane_fp_count"],
        "negative_lane_fp_per_1000_tokens": summary[
            "negative_lane_fp_per_1000_tokens"
        ],
    }


def _corpus_manifest(
    corpus: Path,
    lanes: tuple[str, ...],
    documents_by_lane: dict[tuple[str, str], list],
    scope: str,
    validated_at: str,
) -> dict:
    source_files = {
        document.source_file.resolve()
        for lane in lanes
        for document in documents_by_lane[(scope, lane)]
        if document.source_file is not None
    }
    return {
        "path": os.path.relpath(corpus.resolve(), REPO_ROOT),
        "sha256": _tree_sha256(list(source_files), corpus.resolve()),
        "documents": sum(len(documents_by_lane[(scope, lane)]) for lane in lanes),
        "lanes": {
            lane: len(documents_by_lane[(scope, lane)])
            for lane in lanes
        },
        "validated_at": validated_at,
    }


def _render_release_summary(manifest: dict) -> str:
    registry = {
        engine["id"]: engine for engine in manifest["engine_registry"]["engines"]
    }
    selected_ids = manifest.get("matrix", {}).get("engine_ids", registry)
    engine_rows = [registry[engine_id] for engine_id in selected_ids]
    lines = [
        f"# pl-pii-bench {manifest['benchmark_version']} release summary",
        "",
        f"Generated from `release-manifest.json` at {manifest['generated_at']}.",
        "",
        "| Scope | Lane | "
        + " | ".join(f"{engine['display_name']} recall" for engine in engine_rows)
        + " |",
        "|---|---|" + "|".join("---:" for _engine in engine_rows) + "|",
    ]
    lookup = {
        (run["scope"], run["lane"], run["engine"]): run
        for run in manifest["runs"]
    }
    for scope, lanes in (
        ("public", PUBLIC_LANES),
        ("holdout", HOLDOUT_LANES),
    ):
        for lane in lanes:
            values = []
            for engine in engine_rows:
                summary = lookup[(scope, lane, engine["id"])]["summary"]
                recall = summary["relaxed_recall"]
                values.append(
                    f"n/a, {summary['negative_lane_fp_count']} FP"
                    if recall is None
                    else f"{recall * 100:.1f}%"
                )
            lines.append(f"| {scope} | `{lane}` | " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "The machine-readable manifest is authoritative. Every run records its",
            "commands, timestamps, corpus hash, dependency versions, and artifact hashes.",
            "",
        ]
    )
    return "\n".join(lines)


def execute_release(
    version: str,
    public_corpus: Path,
    holdout_corpus: Path,
    output_root: Path,
    product_repo: Path,
    presidio_python: Path,
    engines: tuple[EngineSpec, ...] = PUBLIC_ENGINE_REGISTRY,
) -> None:
    corpora = {
        "public": public_corpus.resolve(),
        "holdout": holdout_corpus.resolve(),
    }
    if not presidio_python.is_file():
        raise SystemExit(
            f"error: Presidio baseline Python not found: {presidio_python}"
        )
    presidio_versions = _presidio_versions(presidio_python)
    validated_at = _utc_now()
    documents_by_lane: dict[tuple[str, str], list] = {}
    for scope, lanes in (
        ("public", PUBLIC_LANES),
        ("holdout", HOLDOUT_LANES),
    ):
        corpus = corpora[scope]
        if not corpus.is_dir():
            raise SystemExit(f"error: {scope} corpus directory not found: {corpus}")
        for lane in lanes:
            documents_by_lane[(scope, lane)] = _validate_lane(corpus, lane)

    release_dir = output_root.resolve() / version
    release_dir.mkdir(parents=True, exist_ok=True)
    plan_path = release_dir / "release-plan.json"
    _atomic_write_json(plan_path, release_plan(version, engines))

    run_records = []

    # Group runs by (scope, engine) so that adapters that use a daemon
    # (e.g. Anonimator with kpwr-n82) pay the ~10s model-load cost once
    # per engine instead of once per lane.  One adapter invocation covers
    # all lanes in the group; the scorer then runs per lane against the
    # combined predictions file (unmatched predictions are ignored).
    runs = release_runs(engines)
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for r in runs:
        grouped.setdefault((r["scope"], r["engine"]), []).append(r)

    for (scope, engine), group in sorted(grouped.items()):
        engine_spec = PUBLIC_ENGINE_BY_ID[engine]
        lanes = [r["lane"] for r in group]

        # Resume check: every run in the group must have a verified record.
        all_verified = True
        for r in group:
            rrp = release_dir / "run-records" / f'{r["scope"]}-{r["engine"]}-{r["lane"]}.json'
            if rrp.is_file():
                try:
                    prior = json.loads(rrp.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    all_verified = False
                    break
                corpus_state = _lane_corpus_state(
                    documents_by_lane[(r["scope"], r["lane"])], corpora[r["scope"]]
                )
                if not _run_record_is_verified(prior, r, corpus_state):
                    all_verified = False
                    break
            else:
                all_verified = False
                break
        if all_verified:
            print(f"= resume verified {scope}/{engine} ({len(lanes)} lanes)", flush=True)
            for r in group:
                rrp = release_dir / "run-records" / f'{r["scope"]}-{r["engine"]}-{r["lane"]}.json'
                run_records.append(json.loads(rrp.read_text(encoding="utf-8")))
            continue

        final_group_dir = release_dir / scope / engine
        staging_dir = release_dir / ".staging" / scope / engine
        shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        predictions = staging_dir / "predictions.jsonl"
        corpus_path = corpora[scope]

        # One adapter call for all lanes of this (scope, engine) group.
        adapter_command = _adapter_command(
            engine_spec,
            corpus_path,
            lanes,
            predictions,
            product_repo.resolve(),
            presidio_python,
        )
        started_at = _utc_now()
        started = time.monotonic()
        _run(adapter_command)

        public_version = version.removeprefix("v")

        # Score each lane individually from the combined predictions file.
        for r in group:
            lane = r["lane"]
            report_md = staging_dir / f"report-{lane}.md"
            report_json = staging_dir / f"report-{lane}.json"
            corpus_version = (
                public_version
                if scope == "public"
                else f"{public_version}-holdout"
            )
            scorer_command = _score_command(
                corpus_path,
                lane,
                predictions,
                report_md,
                report_json,
                engine,
                corpus_version,
            )
            _run(scorer_command)

        completed_at = _utc_now()
        duration_seconds = round(time.monotonic() - started, 3)

        _publish_run_directory(staging_dir, final_group_dir)

        for r in group:
            lane = r["lane"]
            report_path = final_group_dir / f"report-{lane}.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            corpus_version = (
                public_version
                if scope == "public"
                else f"{public_version}-holdout"
            )
            record: dict[str, object] = {
                "scope": scope,
                "engine": engine,
                "lane": lane,
                "corpus_version": corpus_version,
                "command": {
                    "adapter": adapter_command,
                    "scorer": _score_command(
                        corpus_path,
                        lane,
                        predictions,
                        final_group_dir / f"report-{lane}.md",
                        final_group_dir / f"report-{lane}.json",
                        engine,
                        corpus_version,
                    ),
                },
                "started_at": started_at,
                "completed_at": completed_at,
                "duration_seconds": duration_seconds,
                "documents": _lane_corpus_state(
                    documents_by_lane[(scope, lane)], corpus_path
                )[0],
                "corpus_sha256": _lane_corpus_state(
                    documents_by_lane[(scope, lane)], corpus_path
                )[1],
                "harness_sha256": _harness_sha256(),
                "summary": _micro_summary(report, lane),
                "artifacts": {
                    "predictions": _relative_artifact(final_group_dir / "predictions.jsonl"),
                    "report_json": _relative_artifact(final_group_dir / f"report-{lane}.json"),
                    "report_markdown": _relative_artifact(final_group_dir / f"report-{lane}.md"),
                },
            }
            run_record_path = release_dir / "run-records" / f"{scope}-{engine}-{lane}.json"
            _atomic_write_json(run_record_path, record)
            run_records.append(record)

    commit = _git_value("rev-parse", "HEAD")
    dirty = bool(_git_value("status", "--porcelain"))
    manifest = {
        "schema_version": 2,
        "benchmark_version": version,
        "generated_at": _utc_now(),
        "repository": {
            "commit": commit,
            "dirty": dirty,
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "harness_version": HARNESS_VERSION,
            "anonimator_version": "0.1.0",
            "presidio_analyzer_version": presidio_versions[
                "presidio_analyzer_version"
            ],
            "spacy_version": presidio_versions["spacy_version"],
            "spacy_model": "pl_core_news_lg",
            "spacy_model_version": presidio_versions[
                "spacy_model_version"
            ],
        },
        "engine_registry": {
            "version": PUBLIC_ENGINE_REGISTRY_VERSION,
            "engines": [spec.manifest_row() for spec in PUBLIC_ENGINE_REGISTRY],
        },
        "corpora": {
            "public": _corpus_manifest(
                corpora["public"],
                PUBLIC_LANES,
                documents_by_lane,
                "public",
                validated_at,
            ),
            "holdout": _corpus_manifest(
                corpora["holdout"],
                HOLDOUT_LANES,
                documents_by_lane,
                "holdout",
                validated_at,
            ),
        },
        "matrix": {
            "public_lanes": list(PUBLIC_LANES),
            "holdout_lanes": list(HOLDOUT_LANES),
            "engine_ids": [engine.engine_id for engine in engines],
            "run_count": len(run_records),
        },
        "runs": run_records,
        "public_artifacts_sha256": _artifact_tree_sha256(
            run_records, scope="public"
        ),
        "artifacts_sha256": _artifact_tree_sha256(run_records),
    }
    manifest_path = release_dir / "release-manifest.json"
    _atomic_write_json(manifest_path, manifest)
    summary_path = release_dir / "release-summary.md"
    summary_path.write_text(_render_release_summary(manifest), encoding="utf-8")

    print(f"Release artifacts written under {release_dir}")
    print(f"Authoritative manifest: {manifest_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--plan",
        action="store_true",
        help="Print the complete release matrix without running engines",
    )
    mode.add_argument(
        "--full",
        action="store_true",
        help="Run the complete matrix and write local versioned artifacts",
    )
    parser.add_argument("--version", default="v1.0.0")
    parser.add_argument(
        "--engines",
        choices=("all", *PUBLIC_ENGINE_IDS),
        default="all",
        help="Run every public engine or select one public engine by ID",
    )
    parser.add_argument("--public-corpus", type=Path, default=DEFAULT_PUBLIC_CORPUS)
    parser.add_argument("--holdout-corpus", type=Path, default=DEFAULT_HOLDOUT_CORPUS)
    parser.add_argument("--output-root", type=Path, default=REPO_ROOT / "results")
    parser.add_argument("--product-repo", type=Path, default=DEFAULT_PRODUCT_REPO)
    parser.add_argument(
        "--presidio-python",
        type=Path,
        default=DEFAULT_PRESIDIO_PYTHON,
        help="Python from the pinned native-arm64 Presidio baseline environment",
    )
    args = parser.parse_args()
    engines = selected_public_engines(args.engines)

    if args.plan:
        print(json.dumps(release_plan(args.version, engines), ensure_ascii=False, indent=2))
        return

    execute_release(
        version=args.version,
        public_corpus=args.public_corpus,
        holdout_corpus=args.holdout_corpus,
        output_root=args.output_root,
        product_repo=args.product_repo,
        presidio_python=args.presidio_python,
        engines=engines,
    )


if __name__ == "__main__":
    main()

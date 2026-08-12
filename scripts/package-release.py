#!/usr/bin/env python3
"""Build deterministic public GitHub and Hugging Face release packages.

The source release manifest remains local because it includes reproduction
commands and maintainer paths. This script derives a public-only summary and
never copies predictions, detailed reports, or private evaluation material.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_SPLITS = (
    "core",
    "inflection",
    "identifiers",
    "address",
    "negative",
    "robustness",
    "pdf",
)
PUBLIC_ENGINE_IDS = (
    "anonimator",
    "presidio",
    "spacy-pl",
    "gliner-pii-polish",
    "bardsai-eu-pii",
)
GITHUB_DIRS = ("adapters", "corpus", "eval", "scripts", "tests", ".github")
GITHUB_FILES = (
    "README.md",
    "LICENSE",
    "DATA_LICENSE",
    "pyproject.toml",
    "release-manifest.schema.json",
)
IGNORED_NAMES = {
    ".DS_Store",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "site-packages",
    "uv.lock",
    "venv",
}
TEXT_SUFFIXES = {
    "",
    ".bib",
    ".cfg",
    ".ini",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = (
    re.compile("/" + "Users" + "/"),
    re.compile(r"[A-Za-z]:\\\\" + "Users" + r"\\\\"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
PRIVATE_ARTIFACT_MARKERS = (
    "codex",
    "holdout",
    "prompt",
    "event",
    "session",
    "response",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_dump(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def ignored(path: Path) -> bool:
    return any(part in IGNORED_NAMES for part in path.parts) or any(
        marker in part.lower()
        for part in path.parts
        for marker in PRIVATE_ARTIFACT_MARKERS
    )


def copy_tree(source: Path, destination: Path) -> None:
    def _ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in IGNORED_NAMES
            or any(marker in name.lower() for marker in PRIVATE_ARTIFACT_MARKERS)
        }

    shutil.copytree(source, destination, ignore=_ignore)


def source_commit(manifest: dict) -> str:
    commit = manifest.get("repository", {}).get(
        "commit", manifest.get("source_commit", "")
    )
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        commit = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SystemExit("error: could not determine a 40-character source commit")
    return commit


def public_registry(manifest: dict) -> tuple[dict, ...]:
    """Validate and return the complete, local-only public engine registry."""

    rows = manifest.get("engine_registry", {}).get("engines")
    if not isinstance(rows, list):
        raise SystemExit("error: release manifest has no public engine registry")
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    if len(rows) != len(PUBLIC_ENGINE_IDS) or set(ids) != set(PUBLIC_ENGINE_IDS):
        raise SystemExit(
            "error: release manifest engine registry must contain exactly the "
            f"public engines: {list(PUBLIC_ENGINE_IDS)}"
        )
    return tuple(rows)


def public_runs(manifest: dict) -> dict[tuple[str, str], dict]:
    engine_rows = public_registry(manifest)
    engine_ids = tuple(row["id"] for row in engine_rows)
    public_rows = [run for run in manifest.get("runs", []) if run.get("scope") == "public"]
    unexpected_engines = sorted(
        {
            run.get("engine")
            for run in public_rows
            if run.get("engine") not in engine_ids
        },
        key=str,
    )
    if unexpected_engines:
        raise SystemExit(
            "error: public release contains a non-public engine: "
            f"{unexpected_engines}"
        )
    runs = {}
    for run in public_rows:
        key = (run.get("engine"), run.get("lane"))
        if key[1] not in PUBLIC_SPLITS:
            raise SystemExit(f"error: public release contains an invalid lane: {key[1]}")
        if key in runs:
            raise SystemExit(f"error: public release has duplicate run: {key}")
        runs[key] = run
    expected = {(engine, split) for engine in engine_ids for split in PUBLIC_SPLITS}
    if set(runs) != expected:
        missing = sorted(expected - set(runs))
        unexpected = sorted(set(runs) - expected)
        raise SystemExit(
            "error: public release must contain exactly the full public matrix; "
            f"missing={missing}, unexpected={unexpected}"
        )
    return runs


def public_artifacts_sha256(runs: dict[tuple[str, str], dict]) -> str:
    digest = hashlib.sha256()
    records = []
    for (engine, split), run in runs.items():
        for kind, artifact in run["artifacts"].items():
            records.append((engine, split, kind, artifact["sha256"]))
    for record in sorted(records):
        digest.update("\0".join(record).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def resolved_public_artifacts_sha256(
    source: dict, runs: dict[tuple[str, str], dict]
) -> str:
    recorded = source.get("public_artifacts_sha256")
    if isinstance(recorded, str) and re.fullmatch(r"[0-9a-f]{64}", recorded):
        return recorded
    return public_artifacts_sha256(runs)


def percentage(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def results_table(
    runs: dict[tuple[str, str], dict], engine_rows: tuple[dict, ...]
) -> str:
    lines = [
        "| Split | Documents | "
        + " | ".join(
            f"{engine['display_name']} relaxed recall" for engine in engine_rows
        )
        + " |",
        "|---|---:|" + "|".join("---:" for _engine in engine_rows) + "|",
    ]
    for split in PUBLIC_SPLITS:
        rows = [runs[(engine["id"], split)] for engine in engine_rows]
        values = []
        for row in rows:
            summary = row["summary"]
            values.append(
                f"{summary['negative_lane_fp_count']} FP"
                if split == "negative"
                else percentage(summary["relaxed_recall"])
            )
        label = f"`{split}`*" if split == "negative" else f"`{split}`"
        lines.append(f"| {label} | {rows[0]['documents']} | " + " | ".join(values) + " |")
    lines.append(
        "\n\\* The `negative` split has no planted entities, so relaxed recall "
        "is undefined; the value shown is the false-positive count instead."
    )
    return "\n".join(lines)


def _engine_version_text(engine: dict, environment: dict) -> str:
    """Render the pinned package/model versions an engine's row was produced with."""

    engine_id = engine.get("id")
    reproducibility = engine.get("reproducibility", {})
    model_id = reproducibility.get("model_id")
    model_revision = reproducibility.get("model_revision")
    if engine_id == "anonimator":
        return f"version {environment.get('anonimator_version', 'n/a')}"
    if engine_id == "presidio":
        return (
            f"presidio-analyzer {environment.get('presidio_analyzer_version', 'n/a')}, "
            f"spaCy {environment.get('spacy_version', 'n/a')}, "
            f"pl_core_news_lg {environment.get('spacy_model_version', 'n/a')}"
        )
    if engine_id == "spacy-pl":
        return (
            f"spaCy {environment.get('spacy_version', 'n/a')}, "
            f"pl_core_news_lg {environment.get('spacy_model_version', 'n/a')}"
        )
    if engine_id == "gliner-pii-polish":
        package_text = f"gliner {environment.get('gliner_version', 'n/a')}"
    elif engine_id == "bardsai-eu-pii":
        package_text = (
            f"transformers {environment.get('transformers_version', 'n/a')}, "
            f"torch {environment.get('torch_version', 'n/a')}"
        )
    else:
        package_text = None
    if model_id and model_revision:
        model_text = f"model `{model_id}` @ `{model_revision}`"
        return f"{package_text}, {model_text}" if package_text else model_text
    return package_text or "n/a"


def environment_table(environment: dict, engine_rows: tuple[dict, ...]) -> str:
    lines = ["| Engine | Reproduction versions |", "|---|---|"]
    for engine in engine_rows:
        display_name = engine.get("display_name", engine.get("id", "unknown"))
        lines.append(f"| {display_name} | {_engine_version_text(engine, environment)} |")
    return "\n".join(lines)


def sanitized_results(
    version: str,
    commit: str,
    artifact_sha: str,
    manifest: dict,
    runs: dict[tuple[str, str], dict],
) -> str:
    environment = manifest["environment"]
    engine_rows = public_registry(manifest)
    return (
        f"# pl-pii-bench {version} public results\n\n"
        "This summary is generated from the authoritative release manifest. "
        "It contains public split aggregates only. Predictions, detailed "
        "reports, reproduction commands, and maintainer paths are excluded.\n\n"
        f"{results_table(runs, engine_rows)}\n\n"
        f"{environment_table(environment, engine_rows)}\n\n"
        f"Source commit: `{commit}`\n\n"
        f"Public artifact-set SHA-256: `{artifact_sha}`\n"
    )


def sanitized_summary_json(
    version: str,
    commit: str,
    artifact_sha: str,
    manifest: dict,
    runs: dict[tuple[str, str], dict],
) -> dict:
    environment = manifest["environment"]
    engine_rows = public_registry(manifest)
    return {
        "schema_version": 1,
        "benchmark_version": version,
        "source_commit": commit,
        "public_artifacts_sha256": artifact_sha,
        "engine_registry": {
            "version": manifest["engine_registry"]["version"],
            "engines": list(engine_rows),
        },
        "environment": {
            "harness_version": environment["harness_version"],
            "anonimator_version": environment["anonimator_version"],
            "presidio_analyzer_version": environment["presidio_analyzer_version"],
            "spacy_version": environment["spacy_version"],
            "spacy_model": environment["spacy_model"],
            "spacy_model_version": environment["spacy_model_version"],
            "gliner_version": environment.get("gliner_version"),
            "transformers_version": environment.get("transformers_version"),
            "torch_version": environment.get("torch_version"),
        },
        "runs": [
            {
                "scope": "public",
                "engine": engine["id"],
                "lane": split,
                "documents": runs[(engine["id"], split)]["documents"],
                "summary": runs[(engine["id"], split)]["summary"],
            }
            for engine in engine_rows
            for split in PUBLIC_SPLITS
        ],
    }


def public_document_counts(runs: dict[tuple[str, str], dict]) -> dict[str, int]:
    """Return the manifest-recorded public document count for each split."""

    counts = {}
    for split in PUBLIC_SPLITS:
        documents = {
            run["documents"]
            for (_engine, lane), run in runs.items()
            if lane == split
        }
        if len(documents) != 1:
            raise SystemExit(
                "error: public release manifest has inconsistent document counts "
                f"for split {split!r}: {sorted(documents)}"
            )
        counts[split] = documents.pop()
    return counts


def split_rows(runs: dict[tuple[str, str], dict]) -> str:
    """Public splits only.

    The dataset card ships with the public release, so its document counts
    must come from the authoritative release manifest rather than a fresh
    recount of the corpus files. The holdout corpus is private and its per-lane
    document counts are not published here.
    """

    lines = [
        "| Lane | Documents |",
        "|---|---:|",
    ]
    counts = public_document_counts(runs)
    for split in PUBLIC_SPLITS:
        lines.append(f"| `{split}` | {counts[split]} |")
    return "\n".join(lines)


def data_files_yaml() -> str:
    lines = []
    for split in PUBLIC_SPLITS:
        extension = "jsonl" if split == "inflection" else "json"
        lines.extend(
            [
                f"      - split: {split}",
                f"        path: data/{split}/*.{extension}",
            ]
        )
    return "\n".join(lines)


def render_card(
    version: str,
    commit: str,
    artifact_sha: str,
    runs: dict[tuple[str, str], dict],
    engine_rows: tuple[dict, ...],
    environment: dict | None = None,
) -> str:
    template = (REPO_ROOT / "scripts" / "dataset-card-template.md").read_text(
        encoding="utf-8"
    )
    replacements = {
        "{{VERSION}}": version.removeprefix("v"),
        "{{DATA_FILES}}": data_files_yaml(),
        "{{SPLIT_ROWS}}": split_rows(runs),
        "{{RESULTS_TABLE}}": results_table(runs, engine_rows),
        "{{ENVIRONMENT_TABLE}}": environment_table(environment or {}, engine_rows),
        "{{SOURCE_COMMIT}}": commit,
        "{{PUBLIC_ARTIFACTS_SHA256}}": artifact_sha,
    }
    for token, value in replacements.items():
        if token not in template:
            raise SystemExit(f"error: dataset-card template is missing token: {token}")
        template = template.replace(token, value)
    unresolved = re.findall(r"\{\{[A-Z0-9_]+\}\}", template)
    if unresolved:
        raise SystemExit(f"error: unresolved dataset-card tokens: {unresolved}")
    return template


def citation(version: str) -> str:
    return (
        "@misc{pl-pii-bench,\n"
        "  title  = {pl-pii-bench: An Open Benchmark for Polish PII "
        "Detection and Anonymization},\n"
        "  author = {Anonimator.pl},\n"
        "  year   = {2026},\n"
        f"  note   = {{Version {version.removeprefix('v')}}}\n"
        "}\n"
    )


def payload_manifest(package_dir: Path, package: str, version: str, commit: str) -> dict:
    records = []
    for path in sorted(package_dir.rglob("*")):
        if path.is_file() and path.name != "PACKAGE-MANIFEST.json":
            records.append(
                {
                    "path": path.relative_to(package_dir).as_posix(),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    return {
        "schema_version": 1,
        "package": package,
        "benchmark_version": version,
        "source_commit": commit,
        "payload_file_count": len(records),
        "payload_files": records,
    }


def tree_sha256(package_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(package_dir.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(package_dir).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(sha256_file(path).encode("ascii"))
            digest.update(b"\n")
    return digest.hexdigest()


def archive_tree(package_dir: Path, archive: Path) -> None:
    root_name = package_dir.name
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                paths = [package_dir, *sorted(package_dir.rglob("*"))]
                for path in paths:
                    relative = path.relative_to(package_dir)
                    arcname = Path(root_name) / relative
                    info = tar.gettarinfo(str(path), arcname=arcname.as_posix())
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    if path.is_dir():
                        info.mode = 0o755
                        tar.addfile(info)
                    elif path.is_file():
                        executable = path.suffix in {".py", ".sh"}
                        info.mode = 0o755 if executable else 0o644
                        with path.open("rb") as stream:
                            tar.addfile(info, stream)
                    else:
                        raise SystemExit(f"error: unsupported package entry: {path}")


def scan_release_tree(root: Path) -> None:
    violations = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        lowered = {part.lower() for part in relative.parts}
        if lowered & {".venv", "venv", "site-packages", "__pycache__", ".git"}:
            violations.append(f"environment path: {relative}")
        if "holdout" in lowered:
            violations.append(f"private evaluation path: {relative}")
        if any(
            marker in part.lower()
            for part in relative.parts
            for marker in PRIVATE_ARTIFACT_MARKERS
        ):
            violations.append(f"private diagnostic path: {relative}")
        if path.is_symlink():
            violations.append(f"symlink: {relative}")
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                violations.append(f"sensitive text {pattern.pattern!r}: {relative}")
    if violations:
        raise SystemExit("error: release package scan failed:\n" + "\n".join(violations))


def prepare_github(
    destination: Path,
    version: str,
    commit: str,
    results: str,
    public_summary: dict,
) -> None:
    destination.mkdir(parents=True)
    for name in GITHUB_DIRS:
        copy_tree(REPO_ROOT / name, destination / name)
    for name in GITHUB_FILES:
        shutil.copy2(REPO_ROOT / name, destination / name)
    (destination / "RESULTS.md").write_text(results, encoding="utf-8")
    json_dump(destination / "release-summary.json", public_summary)
    json_dump(
        destination / "PACKAGE-MANIFEST.json",
        payload_manifest(destination, "github", version, commit),
    )


def prepare_huggingface(
    destination: Path,
    version: str,
    commit: str,
    artifact_sha: str,
    results: str,
    runs: dict[tuple[str, str], dict],
    engine_rows: tuple[dict, ...],
    environment: dict,
) -> None:
    destination.mkdir(parents=True)
    data_dir = destination / "data"
    data_dir.mkdir()
    document_counts = public_document_counts(runs)
    for split in PUBLIC_SPLITS:
        copy_tree(REPO_ROOT / "corpus" / split, data_dir / split)
    shutil.copy2(
        REPO_ROOT / "corpus" / "annotation-guidelines.md",
        destination / "annotation-guidelines.md",
    )
    shutil.copy2(REPO_ROOT / "DATA_LICENSE", destination / "DATA_LICENSE")
    (destination / "README.md").write_text(
        render_card(
            version, commit, artifact_sha, runs, engine_rows, environment
        ),
        encoding="utf-8",
    )
    (destination / "RESULTS.md").write_text(results, encoding="utf-8")
    (destination / "CITATION.bib").write_text(citation(version), encoding="utf-8")
    split_metadata = {}
    for split in PUBLIC_SPLITS:
        split_dir = data_dir / split
        split_metadata[split] = {
            "documents": document_counts[split],
            "files": [
                {
                    "path": path.relative_to(destination).as_posix(),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
                for path in sorted(split_dir.iterdir())
                if path.is_file()
            ],
        }
    json_dump(
        destination / "dataset-manifest.json",
        {
            "schema_version": 1,
            "benchmark_version": version,
            "source_commit": commit,
            "license": "CC-BY-4.0",
            "splits": split_metadata,
            "pdf_contract": {
                "source_artifacts": "data/pdf/*.pdf",
                "scored_inputs": "data/pdf/*.json",
            },
        },
    )
    json_dump(
        destination / "PACKAGE-MANIFEST.json",
        payload_manifest(destination, "huggingface", version, commit),
    )


def build(version: str, manifest_path: Path, output_dir: Path) -> dict:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise SystemExit("error: version must have the form vMAJOR.MINOR.PATCH")
    source_record = manifest_path
    if not source_record.is_file():
        packaged_summary = REPO_ROOT / "release-summary.json"
        if not packaged_summary.is_file():
            raise SystemExit(
                f"error: release source record not found: {manifest_path}"
            )
        source_record = packaged_summary
    manifest = json.loads(source_record.read_text(encoding="utf-8"))
    if manifest.get("benchmark_version") != version:
        raise SystemExit(
            "error: requested version does not match the source release manifest"
        )
    runs = public_runs(manifest)
    engine_rows = public_registry(manifest)
    commit = source_commit(manifest)
    artifact_sha = resolved_public_artifacts_sha256(manifest, runs)
    results = sanitized_results(version, commit, artifact_sha, manifest, runs)
    public_summary = sanitized_summary_json(
        version, commit, artifact_sha, manifest, runs
    )

    resolved_output = output_dir.resolve()
    resolved_repo = REPO_ROOT.resolve()
    if resolved_output == resolved_repo:
        raise SystemExit("error: refusing to replace the repository root")
    if resolved_repo in resolved_output.parents:
        allowed_root = (resolved_repo / "dist").resolve()
        if resolved_output != allowed_root and allowed_root not in resolved_output.parents:
            raise SystemExit(
                "error: repository-local package output must be under dist/"
            )
    if resolved_output.exists():
        shutil.rmtree(resolved_output)
    resolved_output.mkdir(parents=True)

    github_dir = resolved_output / f"pl-pii-bench-{version}-github"
    huggingface_dir = resolved_output / f"pl-pii-bench-{version}-huggingface"
    prepare_github(github_dir, version, commit, results, public_summary)
    prepare_huggingface(
        huggingface_dir,
        version,
        commit,
        artifact_sha,
        results,
        runs,
        engine_rows,
        manifest["environment"],
    )
    scan_release_tree(github_dir)
    scan_release_tree(huggingface_dir)

    package_records = {}
    for name, package_dir in (
        ("github", github_dir),
        ("huggingface", huggingface_dir),
    ):
        archive = resolved_output / f"{package_dir.name}.tar.gz"
        archive_tree(package_dir, archive)
        package_records[name] = {
            "directory": package_dir.name,
            "archive": archive.name,
            "archive_sha256": sha256_file(archive),
            "archive_bytes": archive.stat().st_size,
            "tree_sha256": tree_sha256(package_dir),
            "file_count": len([path for path in package_dir.rglob("*") if path.is_file()]),
        }

    release_index = {
        "schema_version": 1,
        "benchmark_version": version,
        "source_commit": commit,
        "source_record": source_record.name,
        "source_record_sha256": sha256_file(source_record),
        "public_artifacts_sha256": artifact_sha,
        "packages": package_records,
    }
    json_dump(resolved_output / "release-index.json", release_index)
    return release_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="v1.0.0")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "results" / "v1.0.0" / "release-manifest.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "dist" / "release" / "v1.0.0",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    release_index = build(args.version, args.manifest, args.output_dir)
    print(json.dumps(release_index, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

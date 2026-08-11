from __future__ import annotations

import hashlib
import importlib.util
import json
from copy import deepcopy
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "package-release.py"
FIXTURE_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "release-manifest-v2.json"
SPLITS = {
    "core",
    "inflection",
    "identifiers",
    "address",
    "negative",
    "robustness",
    "pdf",
}
def _document_and_span_counts(corpus: Path) -> dict[str, tuple[int, int]]:
    """Count public source documents and annotation spans in every release lane."""

    counts = {}
    for split in SPLITS:
        documents = 0
        spans = 0
        for path in sorted((corpus / split).iterdir()):
            if not path.is_file() or "holdout" in path.name:
                continue
            if path.suffix == ".json":
                records = [json.loads(path.read_text(encoding="utf-8"))]
            elif path.suffix == ".jsonl":
                records = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line
                ]
            else:
                continue
            documents += len(records)
            for record in records:
                spans += len(record.get("entities", []))
                if "label" in record and "start" in record and "end" in record:
                    spans += 1
        counts[split] = (documents, spans)
    return counts


def _complete_public_manifest(destination: Path) -> Path:
    """Build a complete public matrix without depending on local run output."""

    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    seed = manifest["runs"][0]
    runs = []
    for engine in manifest["engine_registry"]["engines"]:
        for split in SPLITS:
            run = deepcopy(seed)
            run["scope"] = "public"
            run["engine"] = engine["id"]
            run["lane"] = split
            run["documents"] = 1
            runs.append(run)
    manifest["runs"] = runs
    manifest["matrix"]["run_count"] = len(runs)
    manifest["public_artifacts_sha256"] = "0" * 64
    destination.write_text(json.dumps(manifest), encoding="utf-8")
    return destination


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_package_release_module():
    spec = importlib.util.spec_from_file_location("package_release", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def packages(tmp_path_factory):
    root = tmp_path_factory.mktemp("release-package")
    manifest = _complete_public_manifest(root / "release-manifest.json")
    outputs = []
    for name in ("first", "second"):
        destination = root / name
        subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(destination),
            ],
            check=True,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        outputs.append(destination)
    return root, outputs[0], outputs[1]


def test_archives_are_deterministic_and_hashes_are_recorded(packages):
    _root, first, second = packages
    first_index = json.loads((first / "release-index.json").read_text())
    second_index = json.loads((second / "release-index.json").read_text())
    assert first_index == second_index

    for package in ("github", "huggingface"):
        record = first_index["packages"][package]
        first_archive = first / record["archive"]
        second_archive = second / record["archive"]
        assert first_archive.read_bytes() == second_archive.read_bytes()
        assert _sha256(first_archive) == record["archive_sha256"]
        assert record["file_count"] > 0
        assert len(record["tree_sha256"]) == 64
    assert len(first_index["source_commit"]) == 40


def test_github_package_has_code_data_and_license_boundaries(packages):
    _root, first, _second = packages
    package = first / "pl-pii-bench-v1.0.0-github"
    assert (package / "LICENSE").is_file()
    assert "Apache License" in (package / "LICENSE").read_text()
    assert (package / "DATA_LICENSE").is_file()
    assert "CC BY 4.0" in (package / "DATA_LICENSE").read_text()
    assert (package / "eval" / "score.py").is_file()
    assert (package / "adapters" / "presidio.py").is_file()
    assert (package / "corpus" / "pdf" / "boundary-001.pdf").is_file()
    assert not (package / "results").exists()
    assert "holdout" not in (package / "RESULTS.md").read_text().lower()
    local_home_prefix = "/" + "Users" + "/"
    assert local_home_prefix not in (package / "RESULTS.md").read_text()
    public_summary = json.loads((package / "release-summary.json").read_text())
    assert len(public_summary["runs"]) == 35
    assert {run["scope"] for run in public_summary["runs"]} == {"public"}
    assert {run["engine"] for run in public_summary["runs"]} == {
        "anonimator",
        "presidio",
        "spacy-pl",
        "gliner-pii-polish",
        "bardsai-eu-pii",
    }
    assert all("command" not in run for run in public_summary["runs"])
    assert all("artifacts" not in run for run in public_summary["runs"])


def test_huggingface_package_has_seven_splits_card_pdfs_and_citation(packages):
    _root, first, _second = packages
    package = first / "pl-pii-bench-v1.0.0-huggingface"
    assert {path.name for path in (package / "data").iterdir()} == SPLITS
    assert (package / "annotation-guidelines.md").is_file()
    assert (package / "DATA_LICENSE").is_file()
    assert (package / "CITATION.bib").is_file()
    card = (package / "README.md").read_text()
    assert card.startswith("---\n")
    assert "license: cc-by-4.0" in card
    assert "{{" not in card

    pdf_files = sorted((package / "data" / "pdf").glob("*.pdf"))
    scored_inputs = sorted((package / "data" / "pdf").glob("*.json"))
    assert pdf_files
    assert len(pdf_files) == len(scored_inputs)

    manifest = json.loads((package / "dataset-manifest.json").read_text())
    assert set(manifest["splits"]) == SPLITS
    assert manifest["pdf_contract"] == {
        "source_artifacts": "data/pdf/*.pdf",
        "scored_inputs": "data/pdf/*.json",
    }


def test_archives_extract_and_harness_tests_run_from_clean_directory(packages):
    root, first, _second = packages
    index = json.loads((first / "release-index.json").read_text())
    clean = root / "clean"
    clean.mkdir()
    with tarfile.open(first / index["packages"]["github"]["archive"], "r:gz") as archive:
        archive.extractall(clean, filter="data")
    checkout = clean / "pl-pii-bench-v1.0.0-github"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_no_product_import.py",
            "tests/test_metrics.py",
            "-q",
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    repacked = subprocess.run(
        [
            sys.executable,
            "scripts/package-release.py",
            "--output-dir",
            str(root / "repacked"),
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
    )
    assert repacked.returncode == 0, repacked.stdout + repacked.stderr


def test_render_card_rejects_an_unused_replacement_token(tmp_path, monkeypatch):
    module = _load_package_release_module()
    template_dir = tmp_path / "scripts"
    template_dir.mkdir()
    (template_dir / "dataset-card-template.md").write_text(
        "# {{VERSION}}\n{{SPLIT_ROWS}}\n{{RESULTS_TABLE}}\n"
        "{{SOURCE_COMMIT}}\n{{PUBLIC_ARTIFACTS_SHA256}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    runs = {
        (
            "engine",
            split,
        ): {
            "documents": 1,
            "summary": {"negative_lane_fp_count": 0, "relaxed_recall": 1.0},
        }
        for split in module.PUBLIC_SPLITS
    }
    engine_rows = ({"id": "engine", "display_name": "Test engine"},)

    with pytest.raises(SystemExit, match="missing token: \\{\\{DATA_FILES\\}\\}"):
        module.render_card("v1.0.0", "0" * 40, "0" * 64, runs, engine_rows)


def test_packages_match_the_current_corpus(packages):
    """Fixture-built packages must contain the same public corpus as the source."""

    _root, first, _second = packages
    expected = _document_and_span_counts(REPO_ROOT / "corpus")
    for name, corpus in {
        "github": first / "pl-pii-bench-v1.0.0-github" / "corpus",
        "huggingface": first / "pl-pii-bench-v1.0.0-huggingface" / "data",
    }.items():
        assert corpus.is_dir(), f"missing {name} fixture package: {corpus}"
        assert _document_and_span_counts(corpus) == expected


def test_no_package_path_contains_private_or_environment_material(packages):
    _root, first, _second = packages
    for package_name in ("github", "huggingface"):
        package = first / f"pl-pii-bench-v1.0.0-{package_name}"
        for path in package.rglob("*"):
            parts = {part.lower() for part in path.relative_to(package).parts}
            assert "holdout" not in parts
            assert not any(
                marker in part
                for part in parts
                for marker in ("codex", "prompt", "event", "session", "response")
            )
            assert not parts & {
                ".git",
                ".venv",
                "venv",
                "site-packages",
                "__pycache__",
            }


def test_packaging_rejects_non_public_engine_in_public_runs(tmp_path):
    manifest_path = _complete_public_manifest(tmp_path / "release-manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runs"].append(
        {
            **deepcopy(manifest["runs"][0]),
            "engine": "codex-subscription",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest_path),
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "non-public engine" in completed.stderr

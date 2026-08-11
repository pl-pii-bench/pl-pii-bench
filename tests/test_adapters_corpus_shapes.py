"""Regression coverage for corpus-shape and adapter-envelope contracts."""
from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters import anonimator, presidio
from eval.loader import load_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent
SPAN_LANES = {"core", "address", "identifiers", "robustness", "negative", "pdf"}


def _write_corpus(corpus: Path) -> None:
    for lane in sorted(SPAN_LANES):
        lane_dir = corpus / lane
        lane_dir.mkdir(parents=True)
        raw = {
            "doc_id": f"{lane}-doc",
            "lane": lane,
            "text": "Ala",
            "entities": [],
        }
        if lane == "negative":
            raw.pop("lane")
            raw["category"] = "plain"
        if lane == "pdf":
            raw["pdf"] = "source.pdf"
        (lane_dir / "doc.json").write_text(
            json.dumps(raw, ensure_ascii=False),
            encoding="utf-8",
        )

    inflection_dir = corpus / "inflection"
    inflection_dir.mkdir()
    rows = [
        {
            "text": "Widzę Annę.",
            "label": "PERSON",
            "start": 6,
            "end": 10,
            "entity_id": "person-a",
            "case": "accusative",
        },
        {
            "text": "Pomagam Piotrowi.",
            "label": "PERSON",
            "start": 8,
            "end": 16,
            "entity_id": "person-b",
            "case": "dative",
        },
    ]
    (inflection_dir / "inflection.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_predictions(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_loader_normalizes_all_seven_public_lanes(tmp_path):
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)

    documents = load_corpus(corpus)

    assert {document.lane for document in documents} == SPAN_LANES | {"inflection"}
    assert len(documents) == 8
    inflection = [document for document in documents if document.lane == "inflection"]
    assert [document.doc_id for document in inflection] == [
        "inflection-0000-person-a",
        "inflection-0001-person-b",
    ]
    assert [document.entities[0].entity_id for document in inflection] == [
        "person-a",
        "person-b",
    ]


def test_adapter_clis_cover_flat_inflection_and_emit_empty_envelopes(
    tmp_path,
    monkeypatch,
):
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    documents = load_corpus(corpus, lanes={"core", "inflection"})
    expected_ids = [document.doc_id for document in documents]

    anonimator_output = tmp_path / "anonimator.jsonl"
    monkeypatch.setattr(anonimator, "_require_paths", lambda *_args: None)
    monkeypatch.setattr(
        anonimator,
        "run_one_document_percall",
        lambda *_args: [],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "anonimator.py",
            "--corpus",
            str(corpus),
            "--lanes",
            "core",
            "inflection",
            "--out",
            str(anonimator_output),
            "--mode",
            "percall",
            "--product-repo",
            str(tmp_path / "product"),
        ],
    )
    anonimator.main()

    class EmptyAnalyzer:
        def analyze(self, *, text, language):
            assert text
            assert language == "pl"
            return []

    presidio_output = tmp_path / "presidio.jsonl"
    monkeypatch.setattr(presidio, "build_analyzer", EmptyAnalyzer)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "presidio.py",
            "--corpus",
            str(corpus),
            "--lanes",
            "core",
            "inflection",
            "--out",
            str(presidio_output),
        ],
    )
    presidio.main()

    for output in (anonimator_output, presidio_output):
        predictions = _read_predictions(output)
        assert [row["doc_id"] for row in predictions] == expected_ids
        assert all(row["spans"] == [] for row in predictions)
        assert any(row["doc_id"].startswith("inflection-") for row in predictions)


def test_release_plan_has_complete_public_and_holdout_matrix():
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run-release.py"),
            "--plan",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(result.stdout)

    assert plan["counts"] == {
        "public_lanes": 7,
        "holdout_lanes": 4,
        "engines": 5,
        "public_runs": 35,
        "holdout_runs": 20,
        "runs": 55,
    }
    assert {run["scope"] for run in plan["runs"]} == {"public", "holdout"}
    engine_ids = {engine["id"] for engine in plan["matrix"]["engines"]}
    assert engine_ids == {
        "anonimator",
        "presidio",
        "spacy-pl",
        "gliner-pii-polish",
        "bardsai-eu-pii",
    }
    assert plan["matrix"]["engine_registry_version"] == 1
    assert {run["engine"] for run in plan["runs"]} == engine_ids
    assert not engine_ids & {
        "anthropic-api",
        "claude-terminal-agent",
        "codex-subscription",
        "gemini-api",
        "openai-api",
    }
    assert len(
        {
            (run["scope"], run["engine"], run["lane"])
            for run in plan["runs"]
        }
    ) == 55


def test_release_plan_can_select_one_public_engine():
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run-release.py"),
            "--plan",
            "--engines",
            "spacy-pl",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(result.stdout)

    assert plan["counts"] == {
        "public_lanes": 7,
        "holdout_lanes": 4,
        "engines": 1,
        "public_runs": 7,
        "holdout_runs": 4,
        "runs": 11,
    }
    assert [engine["id"] for engine in plan["matrix"]["engines"]] == [
        "spacy-pl"
    ]


def test_release_summary_reports_negative_false_positives():
    release_module = runpy.run_path(str(REPO_ROOT / "scripts" / "run-release.py"))
    runs = []
    for scope, lanes in (
        ("public", release_module["PUBLIC_LANES"]),
        ("holdout", release_module["HOLDOUT_LANES"]),
    ):
        for engine in release_module["PUBLIC_ENGINE_REGISTRY"]:
            for lane in lanes:
                runs.append(
                    {
                        "scope": scope,
                        "engine": engine.engine_id,
                        "lane": lane,
                        "summary": {
                            "relaxed_recall": None if lane == "negative" else 1.0,
                            "negative_lane_fp_count": (
                                3 if engine.engine_id == "anonimator" else 6
                            ),
                        },
                    }
                )

    summary = release_module["_render_release_summary"](
        {
            "benchmark_version": "v1.0.0",
            "generated_at": "2026-07-27T00:00:00Z",
            "engine_registry": {
                "version": release_module["PUBLIC_ENGINE_REGISTRY_VERSION"],
                "engines": [
                    engine.manifest_row()
                    for engine in release_module["PUBLIC_ENGINE_REGISTRY"]
                ],
            },
            "runs": runs,
        }
    )

    assert (
        "| Scope | Lane | Anonimator recall | Presidio recall | spaCy PL recall | "
        "GLiNER PII Polish recall | BardsAI EU PII recall |"
    ) in summary
    assert (
        "| public | `negative` | n/a, 3 FP | n/a, 6 FP | n/a, 6 FP | "
        "n/a, 6 FP | n/a, 6 FP |"
    ) in summary


def test_release_publish_creates_missing_scope_directory(tmp_path):
    release_module = runpy.run_path(str(REPO_ROOT / "scripts" / "run-release.py"))
    staging = tmp_path / ".staging" / "public" / "anonimator-core"
    staging.mkdir(parents=True)
    (staging / "predictions.jsonl").write_text("{}\n", encoding="utf-8")
    final = tmp_path / "release" / "public" / "anonimator-core"

    release_module["_publish_run_directory"](staging, final)

    assert (final / "predictions.jsonl").read_text(encoding="utf-8") == "{}\n"
    assert not staging.exists()

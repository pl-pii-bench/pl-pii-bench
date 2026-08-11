"""Regression tests for the release runner's adapter command and resume gate.

Both defects these cover produced *plausible* release artifacts rather than an
error, which is why they need mechanical guards:

- repeating `--lanes` once per lane silently collapsed to the last lane, so a
  multi-lane group scored 0.0 recall on every lane but one;
- the resume gate verified artifact hashes but not the corpus, so a run record
  from an older, smaller corpus counted as a completed run for the new one.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_release", REPO_ROOT / "scripts" / "run-release.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_release"] = module
    spec.loader.exec_module(module)
    return module


def test_adapter_command_passes_every_lane_after_one_flag():
    runner = _load_runner()
    lanes = ["core", "identifiers", "address", "negative"]

    command = runner._adapter_command(
        runner.PUBLIC_ENGINE_BY_ID["spacy-pl"],
        REPO_ROOT / "corpus",
        lanes,
        Path("/tmp/predictions.jsonl"),
        REPO_ROOT.parent / "products" / "anonimator",
        Path(sys.executable),
    )

    assert command.count("--lanes") == 1
    assert command[command.index("--lanes") + 1 : command.index("--out")] == lanes


def test_resume_rejects_a_record_scored_against_a_different_corpus():
    runner = _load_runner()
    expected = {"scope": "public", "engine": "spacy-pl", "lane": "address"}
    record = {
        **expected,
        "documents": 3,
        "corpus_sha256": "a" * 64,
        "artifacts": {},
    }

    # Same lane, intact record, but the corpus has grown since it was written.
    assert not runner._run_record_is_verified(record, expected, (14, "b" * 64))
    # Same corpus content and same harness: the record is still a candidate for
    # resume (its artifacts are then hash-checked separately).
    assert runner._run_record_is_verified(
        {**record, "harness_sha256": runner._harness_sha256()}, expected, (3, "a" * 64)
    )


def test_lane_corpus_state_tracks_document_content():
    runner = _load_runner()
    sys.path.insert(0, str(REPO_ROOT))
    from eval.loader import load_corpus

    corpus = REPO_ROOT / "corpus"
    documents = [d for d in load_corpus(corpus, {"address"})]
    count, digest = runner._lane_corpus_state(documents, corpus)

    assert count == len(documents) >= 14
    assert len(digest) == 64
    # Dropping a document must change the recorded state.
    assert runner._lane_corpus_state(documents[:-1], corpus) != (count, digest)


def test_predictions_reject_ambiguous_duplicate_doc_ids(tmp_path):
    """Ten doc_ids live in both `core` and `pdf`. A combined predictions file
    without lane tags used to silently keep the last row, scoring core
    documents against the pdf lane's extraction."""
    import pytest
    sys.path.insert(0, str(REPO_ROOT))
    from eval.predictions import load_predictions

    path = tmp_path / "predictions.jsonl"
    path.write_text(
        '{"doc_id": "core-faktura-vat-high", "spans": []}\n'
        '{"doc_id": "core-faktura-vat-high", "spans": [{"label": "PERSON", "text": "X"}]}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate doc_id"):
        load_predictions(path)


def test_all_lanes_scoring_accepts_lane_tagged_predictions(tmp_path):
    sys.path.insert(0, str(REPO_ROOT))
    from eval.predictions import load_predictions

    path = tmp_path / "predictions.jsonl"
    path.write_text(
        '{"doc_id": "core-faktura-vat-high", "lane": "core",'
        ' "spans": [{"label": "PERSON", "text": "core-hit"}]}\n'
        '{"doc_id": "core-faktura-vat-high", "lane": "pdf",'
        ' "spans": [{"label": "PERSON", "text": "pdf-hit"}]}\n',
        encoding="utf-8",
    )

    predictions = load_predictions(path)
    assert [s.text for s in predictions[("core", "core-faktura-vat-high")]] == ["core-hit"]
    assert [s.text for s in predictions[("pdf", "core-faktura-vat-high")]] == ["pdf-hit"]

    core = load_predictions(path, lanes={"core"})
    assert [s.text for s in core[("core", "core-faktura-vat-high")]] == ["core-hit"]
    assert ("pdf", "core-faktura-vat-high") not in core


def test_predictions_reject_duplicate_doc_ids_in_the_same_lane(tmp_path):
    import pytest
    sys.path.insert(0, str(REPO_ROOT))
    from eval.predictions import load_predictions

    path = tmp_path / "predictions.jsonl"
    path.write_text(
        '{"doc_id": "core-faktura-vat-high", "lane": "core", "spans": []}\n'
        '{"doc_id": "core-faktura-vat-high", "lane": "core", "spans": []}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate doc_id.*lane 'core'"):
        load_predictions(path)


def test_resume_rejects_a_record_written_by_different_harness_code():
    """HARNESS_VERSION is hand-maintained, so a fix in eval/ or adapters/ does
    not change it. Resume must key on the code's content hash instead."""
    runner = _load_runner()
    expected = {"scope": "public", "engine": "spacy-pl", "lane": "address"}
    corpus_state = (14, "b" * 64)
    record = {**expected, "documents": 14, "corpus_sha256": "b" * 64, "artifacts": {}}

    # No harness hash at all (a record from before this gate existed).
    assert not runner._run_record_is_verified(record, expected, corpus_state)
    # A hash from other code.
    assert not runner._run_record_is_verified(
        {**record, "harness_sha256": "c" * 64}, expected, corpus_state
    )
    # The current code.
    assert runner._run_record_is_verified(
        {**record, "harness_sha256": runner._harness_sha256()}, expected, corpus_state
    )

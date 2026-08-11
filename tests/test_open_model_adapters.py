"""Contract tests for the public open-model adapter family.

Model downloads are intentionally not part of unit tests.  These tests use
small model-shaped doubles and verify only the reproducible adapter boundary.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters import bardsai_eu_pii, gliner_pii_polish, spacy_pl
from adapters.open_model import normalize_spans, write_predictions
from eval.loader import Document, load_corpus


def _write_all_shapes(corpus: Path) -> None:
    for lane in ("core", "address", "identifiers", "robustness", "negative", "pdf"):
        lane_path = corpus / lane
        lane_path.mkdir(parents=True)
        row = {"doc_id": f"{lane}-one", "text": "Jan Kowalski", "entities": []}
        if lane != "negative":
            row["lane"] = lane
        else:
            row["category"] = "plain"
        (lane_path / "one.json").write_text(json.dumps(row), encoding="utf-8")
    inflection = corpus / "inflection"
    inflection.mkdir()
    (inflection / "one.jsonl").write_text(
        json.dumps({"text": "Widzę Annę.", "label": "PERSON", "start": 6, "end": 10, "entity_id": "a"}) + "\n",
        encoding="utf-8",
    )


def test_normalizer_rejects_bad_offsets_and_deduplicates():
    spans = normalize_spans(
        "Jan Kowalski",
        [
            {"label": "B-PERSON_NAME", "start": 0, "end": 3, "text": "Jan"},
            {"label": "PERSON", "start": 0, "end": 3, "text": "Jan"},
            {"label": "PERSON", "start": -1, "end": 3},
            {"label": "PERSON", "start": 4, "end": 99},
            {"label": "PERSON", "start": 4, "end": 12, "text": "Wrong"},
            {"label": "not-a-public-label", "start": 0, "end": 3},
        ],
    )
    assert spans == [{"label": "PERSON", "start": 0, "end": 3, "text": "Jan"}]


def test_shared_writer_covers_every_corpus_shape_and_empty_predictions(tmp_path):
    corpus = tmp_path / "corpus"
    _write_all_shapes(corpus)
    docs = load_corpus(corpus)
    output = tmp_path / "predictions.jsonl"
    write_predictions(docs, output, lambda _text: [])
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["doc_id"] for row in rows] == [doc.doc_id for doc in docs]
    assert all(row["spans"] == [] for row in rows)
    assert any(row["doc_id"].startswith("inflection-") for row in rows)


def test_spacy_adapter_maps_native_labels():
    class Entity:
        label_ = "persName"
        start_char = 0
        end_char = 3
        text = "Jan"

    class Model:
        def __call__(self, text):
            assert text == "Jan"
            return type("Doc", (), {"ents": [Entity()]})()

    assert spacy_pl.predict_document(Model(), "Jan") == [
        {"label": "PERSON", "start": 0, "end": 3, "text": "Jan"}
    ]


def test_gliner_adapter_uses_pinned_revision_and_normalizes_output():
    class Model:
        def predict_entities(self, text, labels, threshold):
            assert text == "Anna"
            assert "name" in labels
            assert threshold == gliner_pii_polish.THRESHOLD
            return [{"label": "person", "start": 0, "end": 4, "text": "Anna"}]

    assert len(gliner_pii_polish.MODEL_REVISION) == 40
    assert gliner_pii_polish.predict_document(Model(), "Anna") == [
        {"label": "person", "start": 0, "end": 4, "text": "Anna"}
    ]


def test_bardsai_adapter_uses_grouped_token_predictions():
    def detector(text):
        assert text == "a@b.pl"
        return [{"entity_group": "EMAIL_ADDRESS", "start": 0, "end": 6, "word": "a@b.pl"}]

    assert bardsai_eu_pii.MODEL_ID == "bardsai/eu-pii-anonimization-multilang-v2-preview"
    assert bardsai_eu_pii.MODEL_REVISION == "8e0b19766bb0dd4916d096b4f540dd46c138c760"
    assert bardsai_eu_pii.predict_document(detector, "a@b.pl") == [
        {"label": "EMAIL_ADDRESS", "start": 0, "end": 6, "text": "a@b.pl"}
    ]

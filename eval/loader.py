"""Corpus loader for pl-pii-bench.

Normalizes the on-disk shapes the public corpus lanes use (doc-wrapped JSON
and the flat inflection JSONL) into one common `Document` / `Entity`
representation the scorer works against.

Lane shapes, as shipped under corpus/:

- core, address, identifiers, robustness, pdf: one pretty-printed JSON
  object per file, `{doc_id, lane, axes, text, entities:[{text,label,start,
  end,...}]}`. `text[start:end]` must equal the entity `text` exactly
  (loader asserts this). The `pdf` lane additionally carries a `pdf`
  sibling field (the source PDF's filename, relative to the lane
  directory) alongside its committed, canonical extracted `text` — the
  same extraction is scored for every system, so it is matched by the
  standard span matcher exactly like the other span lanes (see
  eval/matching.py). The `pdf` field is informational only (end-to-end
  appendix use); the scorer never reads the PDF file itself.
- negative: same doc shape, but no `lane`/`axes` keys and an always-empty
  `entities` list; ground truth is "nothing here is PII". Carries a
  `category` field used for per-category FP reporting and optional `decoys`
  that occur verbatim in the text but are never matched by the scorer.
- inflection: a single `inflection.jsonl`, one JSON object per line, where
  the object *is* both the carrier-sentence document and its one entity
  flattened together (`text` is the carrier sentence, `start`/`end` index
  into it). The loader splits each line into its own one-entity
  pseudo-document.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# The 17 frozen public labels (annotation-guidelines.md §2). Anything else
# in a document's entities or a system's predictions is out of scope and
# ignored by the scorer.
TRACKED_LABELS = {
    "PERSON", "ORG", "PESEL", "NIP", "REGON", "DOWOD", "IBAN", "LOC",
    "POSTAL", "DOB", "PHONE", "EMAIL", "PASSPORT", "DRIVING_LICENSE",
    "PAYMENT_CARD", "VIN", "VEHICLE_PLATE",
}

SPAN_LANES = {"core", "address", "identifiers", "robustness", "negative", "pdf"}
FLAT_LANES = {"inflection"}
KNOWN_LANES = SPAN_LANES | FLAT_LANES


class CorpusError(ValueError):
    """Raised when a corpus file violates a loader invariant."""


@dataclass
class Entity:
    text: str
    label: str
    start: int | None = None
    end: int | None = None
    identifier_class: str | None = None
    protection: str = "protect"
    entity_id: str | None = None
    case: str | None = None
    id_format: str | None = None
    date_format: str | None = None
    address_form: str | None = None
    notes: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "Entity":
        return cls(
            text=raw["text"],
            label=raw["label"],
            start=raw.get("start"),
            end=raw.get("end"),
            identifier_class=raw.get("identifier_class"),
            protection=raw.get("protection", "protect"),
            entity_id=raw.get("entity_id"),
            case=raw.get("case"),
            id_format=raw.get("id_format"),
            date_format=raw.get("date_format"),
            address_form=raw.get("address_form"),
            notes=raw.get("notes"),
        )


@dataclass
class Document:
    doc_id: str
    lane: str
    text: str | None
    entities: list[Entity] = field(default_factory=list)
    axes: dict = field(default_factory=dict)
    category: str | None = None       # negative lane
    decoys: list[str] = field(default_factory=list)  # negative lane
    clean_doc_id: str | None = None   # robustness lane
    pdf_file: str | None = None       # pdf lane
    source_file: Path | None = None


def _assert_span_invariant(doc_id: str, text: str, entity: Entity) -> None:
    if entity.start is None or entity.end is None:
        return
    actual = text[entity.start:entity.end]
    if actual != entity.text:
        raise CorpusError(
            f"{doc_id}: text[{entity.start}:{entity.end}] == {actual!r}, "
            f"expected entity text {entity.text!r}"
        )


def _load_span_doc(path: Path, lane: str) -> Document:
    raw = json.loads(path.read_text(encoding="utf-8"))
    doc_id = raw.get("doc_id", path.stem)
    text = raw["text"]
    entities = [Entity.from_dict(e) for e in raw.get("entities", [])]
    for entity in entities:
        _assert_span_invariant(doc_id, text, entity)
    return Document(
        doc_id=doc_id,
        lane=lane,
        text=text,
        entities=entities,
        axes=raw.get("axes", {}),
        category=raw.get("category"),
        decoys=raw.get("decoys", []),
        clean_doc_id=raw.get("clean_doc_id"),
        pdf_file=raw.get("pdf"),
        source_file=path,
    )


def _load_inflection_lines(path: Path) -> list[Document]:
    docs: list[Document] = []
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            carrier_text = raw["text"]
            entity = Entity(
                text=carrier_text[raw["start"]:raw["end"]],
                label=raw["label"],
                start=raw["start"],
                end=raw["end"],
                identifier_class=raw.get("identifier_class"),
                protection=raw.get("protection", "protect"),
                entity_id=raw.get("entity_id"),
                case=raw.get("case"),
            )
            doc_id = f"inflection-{i:04d}-{entity.entity_id}"
            _assert_span_invariant(doc_id, carrier_text, entity)
            docs.append(
                Document(
                    doc_id=doc_id,
                    lane="inflection",
                    text=carrier_text,
                    entities=[entity],
                    source_file=path,
                )
            )
    return docs


def load_corpus(corpus_dir: Path, lanes: set[str] | None = None) -> list[Document]:
    """Load every document across the requested lanes (default: all lanes
    present on disk) from `corpus_dir`, asserting the `text[start:end] ==
    entity.text` invariant on every span-based entity along the way.
    """
    corpus_dir = Path(corpus_dir)
    documents: list[Document] = []

    present_lanes = {p.name for p in corpus_dir.iterdir() if p.is_dir()}
    target_lanes = present_lanes if lanes is None else (present_lanes & lanes)

    for lane in sorted(target_lanes):
        lane_dir = corpus_dir / lane
        if lane in SPAN_LANES:
            for path in sorted(lane_dir.glob("*.json")):
                documents.append(_load_span_doc(path, lane))
        elif lane in FLAT_LANES:
            for path in sorted(lane_dir.glob("*.jsonl")):
                documents.extend(_load_inflection_lines(path))
        # Unknown lane directories (e.g. a future addition) are skipped
        # rather than erroring, so the loader degrades gracefully.

    return documents

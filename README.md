# pl-pii-bench

The first open benchmark for Polish PII detection and anonymization. Seventeen
entity labels, checksum-valid synthetic identifiers, a seven-case Polish
morphological-inflection lane, and document-level (including rendered-PDF)
realism — see `corpus/annotation-guidelines.md` for the full annotation
contract this corpus is built against.

This repository is a **clean-room scoring harness**: it contains no product
code. It re-implements the scoring logic described in the methodology from
scratch, against a public, redistributable corpus. See "Provenance" below.

## License

- **Code** (`eval/`, `adapters/`, `tests/`, `scripts/`): Apache-2.0, see `LICENSE`.
- **Data** (`corpus/`): CC BY 4.0, see `DATA_LICENSE`. The corpus is a
  standalone copy of the seven public splits; see
  `corpus/annotation-guidelines.md` for the complete annotation contract and
  provenance statement (100% synthetic, no real personal data).

## Layout

```
corpus/           synced copy of the public corpus (scripts/sync-corpus.sh)
eval/             the scorer: loader, matching, metrics, report, score.py CLI
adapters/         baseline adapters that emit the predictions contract
tests/            unit tests + the import-guard (no product-code imports)
results/          dated report files (Markdown + JSON)
.github/workflows CI: unit tests, import-guard, Presidio baseline run
```

## Release packages

From the desktop monorepo root, build both deterministic publication
packages with:

```bash
just pl-pii-bench-package
```

The command writes GitHub and Hugging Face release directories plus
normalized `.tar.gz` archives under `dist/release/v1.0.0/`. Repeating the
command against the same source tree produces identical archive bytes.
`release-index.json` records the source commit, tree hashes, archive hashes,
file counts, and the hash of the source release record.

The GitHub package contains the Apache-2.0 harness and CC BY 4.0 public
corpus. The Hugging Face package contains exactly seven named data splits,
the dataset card, annotation guidelines, source PDFs, canonical extracted
text, a sanitized public results summary, and citation metadata. Neither
package copies local predictions, detailed reports, private evaluation
material, environments, credentials, or maintainer-local paths.

## Quickstart

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Populate corpus/ from the upstream product repo (maintainer-only; the
# corpus is also distributed directly, so this step is not needed to use
# an already-populated checkout).
./scripts/sync-corpus.sh

# Run a baseline adapter, then score it.
uv pip install -e ".[presidio]"
python -m spacy download pl_core_news_lg
python adapters/presidio.py --corpus corpus/ --lanes core --out predictions-presidio.jsonl
python eval/score.py --corpus corpus/ --predictions predictions-presidio.jsonl \
    --system presidio-pl --corpus-version 0.1.0 \
    --out-md results/presidio-core.md --out-json results/presidio-core.json
```

## The predictions contract

A predictions file is JSONL, one line per document:

```json
{"doc_id": "core-umowa-001", "lane": "core", "spans": [
    {"label": "PERSON", "start": 12, "end": 24, "text": "Jan Kowalski"}
]}
```

Every input document must have one envelope, including a document for which
the detector returns no spans:

```json
{"doc_id": "negative-lookalike-001", "lane": "negative", "spans": []}
```

`lane` is optional for a single-lane predictions file and required when one
file covers several lanes. `doc_id` is unique per lane, not per corpus: the
`pdf` lane reuses ten `core` document ids for its own extraction of the same
source documents. The scorer keys every envelope by `(lane, doc_id)`, so it
honours lane tags even when all lanes are scored together. Without the lane
tag the scorer cannot tell duplicate rows apart, so it rejects the file
instead of silently keeping the last one. Untagged rows remain a legacy
fallback for a single-lane file.

Labels must already be normalized to the 17 public labels (`PERSON`, `ORG`,
`PESEL`, `NIP`, `REGON`, `DOWOD`, `IBAN`, `LOC`, `POSTAL`, `DOB`, `PHONE`,
`EMAIL`, `PASSPORT`, `DRIVING_LICENSE`, `PAYMENT_CARD`, `VIN`,
`VEHICLE_PLATE`) — anything else is ignored by the scorer, same as an
out-of-scope label in the ground truth. A predictions file is
system-agnostic: any detector integrates by producing one line like this
per corpus document; see `adapters/` for the five public-engine adapters.

The `pdf` lane carries committed, canonical extracted text (same input for
every system) with real character offsets, so it is matched by the same
span matcher as every other lane — predictions there need real `start`/
`end` offsets too, not just the surface text.

All five public adapters iterate the normalized document stream from `eval.loader`.
Wrapped JSON lanes and the flat inflection JSONL therefore use the same
document-ID and predictions contracts.

## Release matrix

Inspect the complete release matrix without loading either detector:

```bash
python scripts/run-release.py --plan
```

The plan contains seven public lanes and four private-holdout lanes for five
local engines: Anonimator, Presidio, spaCy PL, GLiNER PII Polish, and BardsAI
EU PII. That is 35 public and 20 holdout runs, for 55 runs in total.
Maintainers working from the desktop monorepo can use:

```bash
just pl-pii-bench-release-check
just pl-pii-bench-release-run
```

The check command runs the corpus-shape regression suite and prints the
matrix. The full command runs all 55 adapter and scorer combinations and
writes local, versioned files under `results/v1.0.0/`. It does not publish
or upload anything. The private holdout path is read in place and its source
documents are never copied into this repository. Presidio runs through the
maintainer's pinned native-arm64 baseline environment; override
`--presidio-python` when reproducing from a different installation.

`results/v1.0.0/release-manifest.json` is the authoritative source for release
claims. It records every command, timestamps, dependency versions, repository
commit, public and holdout corpus hashes, and hashes for every predictions and
report artifact. `release-summary.md` is generated from that manifest.

The shipped `results/v1.0.0/` was regenerated on 2026-07-31 against the corpus
described below: 55 runs, five engines, seven public and four holdout lanes.
Each run record carries the document count and content hash of the lane it was
scored against, plus a content hash of the harness that scored it, so a
release cannot resume a run produced by a different corpus or by different
scoring code.

The older two-engine reproduction in this checkout is a migration reference,
not a publishable v1.0.0 release record. A public score may be claimed only
after a manifest with all five registry engines is generated. The inflection
lane remains disclosed separately and is never blended into a corpus-wide
score.

## Corpus size

This table covers only the public corpus. Its document counts come from the
authoritative release manifest. The private-holdout lanes are read in place by
the release runner and their sizes are not published here.

| Lane | Documents |
|---|---:|
| `core` | 33 |
| `inflection` | 441 |
| `identifiers` | 12 |
| `address` | 14 |
| `negative` | 14 |
| `robustness` | 231 |
| `pdf` | 14 |

The public address lane has 164 annotated spans, so one missed span changes
its span recall by about 0.6 percentage point. The holdout address lane is
distinct material from the public address lane, with content hashes checked to
prevent a duplicated document from returning.

The `core` and `inflection` counts include material that was already present
in the source corpus but had not been synced into this repository before:
`core-wyrok-high` and `core-wyrok-low`, and the 168-line
`inflection-given-name-holdout.jsonl`. That file is public benchmark material
despite its name: "holdout" there means its 24 given names were deliberately
held out of the *detector's* curated name list, so that the lane measures
generalization to names a system has never been given. It is unrelated to the
private holdout corpus.

## The scorer

`eval/score.py` implements methodology §6:

- Strict (exact span+label) and relaxed (>=1 char overlap, same label)
  matching, both reported.
- Per-lane, per-label recall, precision, F2 (β=2), protected recall
  (`protection: protect`), direct-identifier recall (`identifier_class:
  direct`), quasi-identifier coverage (`identifier_class: quasi`, always
  reported separately, never blended).
- Case-stratified recall (`inflection` lane), format-stratified recall
  (`identifiers` lane, `id_format`/`date_format`), clean-vs-perturbed delta
  (`robustness` lane), and negative-lane false positives both as a raw count
  per category and as FP per 1000 tokens. Token counts use the deterministic
  definition `len(text.split())`.
- Residual-identifiability rate (§6.4): a deterministic, published rule set
  (`eval/reidentification_rules.py`): exactly `DOB+LOC` and `DOB+POSTAL`.
- Consistency/reversibility (§6.5): optional, `n/a` unless `--mapping` is
  supplied (a detection-only predictions file has nothing to score there).
- **No single aggregate.** The summary block is direct-identifier recall +
  protected recall (relaxed) + residual-identifiability rate + negative-lane
  FP count and FP per 1000 tokens, per methodology §6.2.

The harness version and corpus version are stamped into every report.
The standalone scorer in this repository is the authoritative scorer for
published rows. Product-side benchmark utilities are internal diagnostics
and do not define public results.

## Known biases

The maintainer of this benchmark also maintains one of the systems it
measures. These are the places where that shows up. They are disclosed here
and, where lane-specific, rendered into every generated report
(`eval/report.py:LANE_NOTES`) so a reader of a single report sees them
without consulting this file.

**The `address` lane's span convention came from Anonimator.** `LOC` and
`POSTAL` are annotated as separate spans and the street-type prefix (`ul.`,
`al.`, `pl.`) is kept inside the `LOC` span. `corpus/annotation-guidelines.md`
§5.4 states plainly that this ratifies the maintainer's internal 2026-07-18
measurement practice rather than being derived independently. Anonimator's
output follows it exactly, so **the strict columns on that lane favour
Anonimator by construction.** For `ul. Kwiatowej 15/2, 30-001 Kraków` its
spans are byte-identical to gold; Presidio and spaCy emit `ul.` and
`Kwiatowej` as two spans and drop the house number; GLiNER returns one span
for the whole address with the labels swapped. Strict `LOC` recall on that
lane reads 56.3% / 34.1% / 0.0% respectively, and the spread is boundary
convention as much as detection ability.

Two things bound this bias rather than excuse it:

- **Relaxed matching does not reward boundary agreement** (≥1 char overlap,
  same label), and the relaxed columns are the ones the methodology calls
  privacy-relevant. On relaxed `LOC` in that lane Anonimator is *behind*
  Presidio and spaCy on recall (80.2% vs 91.3%) and ahead on precision
  (98.1% vs 72.8%). The convention advantage does not reach the number the
  headline privacy claim rests on.
- **Segmentation disagreement is penalized in both directions and can never
  inflate a score.** Matching is one-to-one (`eval/matching.py`): splitting
  one gold span into two predictions costs precision, merging two into one
  costs recall.

**`POSTAL` is a head-to-head row that not every system contests.** Presidio
and spaCy score 0% on it with zero predictions, because they have no postal
label at all. That is a real coverage gap for a Polish PII task, but read it
as a taxonomy difference, not as a detector losing a fair fight.

## Adapters

Five public adapters, all emitting the predictions contract above:

- `adapters/anonimator.py` — shells out (subprocess, never imports) to the
  proprietary Anonimator CLI if it is on `PATH`, normalizes its
  `--predictions-output` schema to the public label set, and writes the
  predictions file. Exits with a clear message if the CLI is not installed;
  the harness itself has no dependency on it, and CI never runs this
  adapter.
- `adapters/presidio.py` — Microsoft Presidio (`presidio-analyzer`) with
  spaCy `pl_core_news_lg` plus custom Polish recognizers (PESEL, NIP, REGON,
  IBAN, DOWOD, EMAIL, PHONE) with checksum validation where the format
  allows it. This standalone adapter is the authoritative Presidio baseline
  configuration; the release manifest records its exact dependency
  versions. CI runs it on representative `core`, `negative`, and
  `inflection` lanes on every change.
- `adapters/spacy_pl.py` — spaCy `pl_core_news_lg` NER alone, normalized to
  the public labels.
- `adapters/gliner_pii_polish.py` — the pinned gliner-pii-polish model,
  normalized to the public labels.
- `adapters/bardsai_eu_pii.py` — the pinned
  `bardsai/eu-pii-anonimization-multilang-v2-preview` model, normalized to
  the public labels. Its rolling-preview revision is recorded in the manifest.

## Private Codex diagnostic

Maintainers may run a subscription-backed Codex comparison against public,
synthetic corpus text only. It is not a baseline, is not reproducible as a
public row, and must never be published. Plan a small run from the monorepo
root with:

```bash
just pl-pii-bench-codex-private --plan --lanes inflection --limit 3
```

The diagnostic rejects holdout paths and public output locations. A live run
uses an ephemeral, read-only Codex session in an empty temporary workspace;
it ignores user configuration and project rules, supplies document text over
stdin, rejects tool-using event streams, and requires a strict JSON response.
Prompts, event streams, responses, predictions, reports, and cache metadata
are written only below `.tmp/pl-pii-bench/codex/`, which is ignored by Git and
excluded from release packages. The cache key binds the Codex version, model
reported by its event stream, prompt hash, document hash, and adapter version.

## Provenance

Built against `products/anonimator/docs/plans/public-pii-benchmark-methodology.md`
and `products/anonimator/bench/corpus/annotation-guidelines.md` in the
private product repository. Nothing in this repository imports from that
repository's product code (`anonymize`, `products`, `toolkit`); see
`tests/test_no_product_import.py`, which asserts this on every commit.

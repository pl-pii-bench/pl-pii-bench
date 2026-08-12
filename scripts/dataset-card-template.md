---
pretty_name: pl-pii-bench
license: cc-by-4.0
language:
  - pl
task_categories:
  - token-classification
tags:
  - pii
  - privacy
  - polish
  - anonymization
version: "{{VERSION}}"
configs:
  - config_name: default
    data_files:
{{DATA_FILES}}
---

# pl-pii-bench

Maintained by [Anonimator.pl](https://anonimator.pl), a local-first Polish
document anonymization tool. [GitHub repository](https://github.com/pl-pii-bench/pl-pii-bench) ·
[Live benchmark results](https://anonimator.pl/benchmark.html)

`pl-pii-bench` is an open benchmark for Polish personally identifiable
information detection and text anonymization. It contains a fully synthetic,
exhaustively annotated, document-level corpus and a separate open scoring
harness.

The corpus is an evaluation set, not training data. Please do not train on
it. Every identifier is synthetic. Checksum-bearing Polish identifiers are
valid for their formats but do not belong to real people.

It is sized for depth rather than volume, and the item count should be read
in that light. The `core` documents are complete Polish administrative
documents, exhaustively annotated for all seventeen labels, not
sentence-length samples. Exhaustive annotation is what makes precision valid
on every split; a larger corpus annotated only for planted entities reports
recall honestly and precision meaninglessly.

## Why this benchmark is different

1. **Checksum-valid identifiers paired with invalid lookalikes.** PESEL, NIP,
   REGON, dowod osobisty, and IBAN/NRB values pass their real check-digit
   algorithms, and the negative split carries same-shape values that fail
   their check digit. Checksum-valid generation alone is not novel; the
   matched invalid set is what separates a detector that validates from one
   that only pattern-matches, and it is what makes identifier precision
   measurable rather than assumed.
2. **Polish morphology, reported per case.** The inflection split exercises
   person and organization names across all seven grammatical cases, and
   recall is stratified by case rather than averaged over them. Other
   benchmarks annotate inflected names; this one reports where inflection
   breaks a detector.
3. **Polish document types and an extraction-noise split.** The corpus covers
   complete synthetic Polish administrative documents. The PDF split includes
   both source PDFs and canonical extracted text with character-offset
   annotations, so ligature glitches, page-boundary-split identifiers, and
   table reordering are captured in the committed text. Detection is scored
   against that committed text, identical for every system, not against each
   system's own extraction.

## Splits and corpus size

{{SPLIT_ROWS}}

This table covers only the public corpus. Its document counts come from the
authoritative release manifest. The private holdout corpus is not described
here: its per-lane sizes are not part of the public release.

The public address lane has 164 annotated spans, so one missed span changes
its span recall by about 0.6 percentage point. Every identifier label carries
at least 30 annotated spans across at least four rendered formats.

The seven public splits are `core`, `inflection`, `identifiers`, `address`,
`negative`, `robustness`, and `pdf`. The `pdf` directory keeps each source
PDF next to the JSON document containing the canonical extracted text used
for scoring.

## Labels

The seventeen labels frozen for v1.0 are PERSON, ORG, PESEL, NIP, REGON,
DOWOD, IBAN, LOC, POSTAL, DOB, PHONE, EMAIL, PASSPORT, DRIVING_LICENSE,
PAYMENT_CARD, VIN, and VEHICLE_PLATE.

See `annotation-guidelines.md` for the complete span-boundary, protection,
identifier-class, address, and morphology contracts.

## Annotation and quality assurance

Annotations are emitted by the generator that produces each document, then
validated mechanically. The full corpus is re-validated at the start of every
release run, so no engine is scored against an unvalidated corpus.

Enforced as hard failures:

- **Offset contract.** `text[start:end]` must equal the entity `text` exactly
  for every entity in every document. The scoring harness re-asserts this
  independently when it loads the corpus, so a drifted offset fails the run
  instead of silently degrading a score.
- **Checksum validity of the ground truth.** Every PESEL, NIP, REGON, and
  DOWOD span in the positive splits is re-derived through its check-digit
  algorithm. Exemptions are explicit and narrow: `robustness` fixtures that
  corrupt check digits by design, each keyed to its clean twin.
- **Per-entity metadata.** `identifier_class` must be `direct` or `quasi` and
  `protection` must be `protect` or `keep`, so re-identification metrics
  cannot be computed over partially tagged documents.

Exhaustiveness, which precision depends on, is checked by reverse error
analysis: the open baseline detectors are run across the corpus and every
high-confidence prediction that is not in the ground truth is surfaced for
manual adjudication. This is the Presidio-research error-analysis loop
inverted into a completeness check. It is advisory rather than blocking,
because a baseline false positive and a real annotation gap look identical
until a human reads them.

There is no inter-annotator agreement figure, and that is deliberate rather
than an omission. This corpus is programmatically generated rather than
independently hand-annotated, so its failure mode is generator error, not
annotator disagreement; a pairwise F1 computed over one generator's own
output would be 1.0 by construction and would carry no information.
Human-annotated benchmarks do report agreement, and should. Here the
correctness guarantee is mechanical and total on the offset, checksum, and
metadata contracts, and human only in the adjudication loop above.

## Metrics

The open harness reports strict and relaxed span matching per split and
label, recall, precision, F2, protected recall, direct-identifier recall,
quasi-identifier coverage, residual-identifiability rate, morphology case
recall, format recall, robustness deltas, and negative-split false positives
as both a raw count and FP per 1000 tokens. Token counts use
`len(text.split())`.
There is no single blended aggregate.

**Read the per-label table, not an average across labels.** Of the seventeen
labels, only PERSON, ORG, and LOC genuinely require learned extraction. The
other fourteen have deterministic formats, and eight of those carry check
digits, so a rule set alone scores highly on them. Any average across all
labels is therefore dominated by rule coverage rather than model quality, and
two systems with very different extraction ability can land on the same
aggregate. This is also why the negative split matters: on deterministic
labels it is the only place where validating and pattern-matching separate.

## Reproduced v1.0.0 results

The table below is generated from the authoritative local release manifest.
Only public-split summaries are copied into this package. Commands, local
paths, private evaluation material, predictions, and detailed reports are
not included.

The public matrix contains exactly five local engines: Anonimator, Presidio,
spaCy PL, GLiNER PII Polish, and BardsAI EU PII. Remote API and terminal-agent
systems are not public rows. An optional private Codex subscription diagnostic
may inspect public synthetic documents, but it has no score in this card, the
public manifest, release packages, or the website.

{{RESULTS_TABLE}}

Every row above comes from one pinned tool version. Model-based engines are
pinned to an immutable revision, not a mutable branch, so the row is
reproducible.

{{ENVIRONMENT_TABLE}}

Source commit: `{{SOURCE_COMMIT}}`

Public artifact-set SHA-256: `{{PUBLIC_ARTIFACTS_SHA256}}`

## Licenses

The data in `data/` and the annotation guidelines are CC BY 4.0. See
`DATA_LICENSE`. The separate scoring harness and baseline adapters are
Apache-2.0.

## Citation

```bibtex
@misc{pl-pii-bench,
  title  = {pl-pii-bench: An Open Benchmark for Polish PII Detection and Anonymization},
  author = {Anonimator.pl},
  year   = {2026},
  note   = {Version {{VERSION}}},
  url    = {https://huggingface.co/datasets/pl-pii-bench/pl-pii-bench}
}
```

## Links

- Maintainer: [Anonimator.pl](https://anonimator.pl)
- Source code and open harness: [github.com/pl-pii-bench/pl-pii-bench](https://github.com/pl-pii-bench/pl-pii-bench)
- Live, human-readable results: [anonimator.pl/benchmark.html](https://anonimator.pl/benchmark.html)

## Related locale-specific PII benchmarks

This benchmark occupies the Polish slot in a small family of locale-specific
PII evaluation sets. The comparison is offered so the differences are legible
rather than implied.

| Benchmark | Locale | Items | Text length | Negative material | Per-case morphology | Private holdout |
|---|---|---|---|---|---|---|
| `pl-pii-bench` | Polish | 755 public | full documents | dedicated split, invalid checksums | yes | yes |
| [pii-bench-zh](https://huggingface.co/datasets/wan9yu/pii-bench-zh) | Chinese | 8,000 | sentences, chat messages | none | n/a | no |
| [tw-PII-bench](https://huggingface.co/datasets/lianghsun/tw-PII-bench) | Traditional Chinese | 910 | 15–5,000 chars | hard negatives, 40 items | n/a | no |
| [hivetrace/pii-bench](https://huggingface.co/datasets/hivetrace/pii-bench) | Russian | 1,810 | 6–520 chars | 42% of the scenario split | no | no |

Two properties here are not present in any of the others: recall stratified by
grammatical case, and a private holdout twin generated from the same axes with
a different seed, which turns "please do not train on this" from a request
into a measurement. Two properties of the others are worth knowing when
reading these numbers: `tw-PII-bench` covers longer texts than its item count
suggests, and `hivetrace/pii-bench` is human-annotated with a published
agreement figure, which this corpus does not have.

## Known limitations

- The private holdout address material is distinct from the public lane.
- The residual-identifiability rules are fixed deterministic combinations,
  not a probabilistic linkage adversary.
- End-to-end PDF extraction is not part of the comparison. The PDF split
  measures detection over one committed canonical extraction.

## Feedback

The maintainer of this benchmark also maintains one of the systems it
measures (see the harness repository's "Known biases" section for where that
shows up). Outside scrutiny is how a benchmark like that earns trust rather
than asserts it. Open an issue on
[github.com/pl-pii-bench/pl-pii-bench](https://github.com/pl-pii-bench/pl-pii-bench)
for anything that would make a split more thorough or more objective: a
labeling rule you think is wrong, an annotation gap, a matching or scoring
edge case, or a split you think is missing.

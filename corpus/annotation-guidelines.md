# Annotation Guidelines — `pl-pii-bench` (Public Corpus)

Status: v1.0 spec. This document is the public annotation contract for the
`pl-pii-bench` public benchmark corpus. It supersedes the internal-only
skip rules in `schema.md` for public documents only; see `schema.md` §
"Public Corpus (v1.0)" for how the two corpora relate.

Background: `products/anonimator/docs/plans/public-pii-benchmark-methodology.md`
§5 is the design rationale this spec implements. This file is the authoritative,
annotator-facing spec — if the methodology doc and this file ever disagree on a
concrete rule, this file wins for annotation work.

## 1. Label naming

The public corpus uses **`PERSON`**, not the internal corpus's flat
person-name label (see `schema.md` for that label's spelling). All
other label names are shared between the internal and public corpora.

## 2. The 17 frozen labels

Every public document is annotated against exactly these seventeen labels.
The list is frozen for v1.0 — do not add or remove labels without a schema
version bump.

1. `PERSON`
2. `ORG`
3. `PESEL`
4. `NIP`
5. `REGON`
6. `DOWOD`
7. `IBAN`
8. `LOC`
9. `POSTAL`
10. `DOB`
11. `PHONE`
12. `EMAIL`
13. `PASSPORT`
14. `DRIVING_LICENSE`
15. `PAYMENT_CARD`
16. `VIN`
17. `VEHICLE_PLATE`

**Explicitly out of scope for v1.0**: `IP_ADDRESS`, `NRP`, `DEM`. Do not
annotate these even if a detector under evaluation emits them; they are not
tracked labels and any prediction with these labels is ignored by the
scoring harness.

## 3. Per-entity metadata fields

Every entity in the public corpus carries these fields, in addition to the
base `text`/`label`/`start`/`end` fields already documented in `schema.md`:

| Field | Required on | Values | Default |
|---|---|---|---|
| `identifier_class` | every entity | `direct` \| `quasi` | none — always fixed, see §6 table |
| `protection` | every entity | `protect` \| `keep` | `protect` |
| `entity_id` | every entity | stable string per persona/identity within a document | none |
| `case` | `PERSON`, `ORG` when inflected | Polish grammatical case (`nom`, `gen`, `dat`, `acc`, `inst`, `loc`, `voc`) | n/a if not inflected |
| `id_format` | identifier-type labels (`PESEL`, `NIP`, `REGON`, `DOWOD`, `IBAN`, `PHONE`, `EMAIL`, `PASSPORT`, `DRIVING_LICENSE`, `PAYMENT_CARD`, `VIN`, `VEHICLE_PLATE`) | the per-label vocabulary below; every label has at least four rendered formats | n/a if only one format is possible |
| `date_format` | `DOB` | e.g. `numeric` (`12.03.1980`), `long-pl` (`12 marca 1980`), `iso` (`1980-03-12`) | n/a |
| `address_form` | `LOC`, `POSTAL` | `street-only`, `full` (LOC + POSTAL together), `bare-city`, `street-with-unit`, `po-box`, `admin-unit`, `letterhead-block` | n/a |

`address_form` records the rendered address pattern, not a new entity label. Use
the following values and examples:

| Value | Worked example |
|---|---|
| `street-only` | `ul. Długa 5` |
| `full` | `ul. Długa 5, 50-138 Wrocław` (the `LOC` and `POSTAL` remain separate spans) |
| `bare-city` | `we Wrocławiu` |
| `street-with-unit` | `os. Piastowskie 12/34` |
| `po-box` | `skr. poczt. 145, 00-950 Warszawa` |
| `admin-unit` | `gmina Michałowice, powiat pruszkowski` |
| `letterhead-block` | `Jan Kowalski\nul. Długa 5\n50-138 Wrocław` |

Every value in this table is in use in the corpus. Do not add a value here
before a document uses it: an unused `address_form` value is a contract an
annotator can silently mis-apply.

**Withdrawn value: `org-address`.** An earlier draft of this contract listed an
eighth value for an address carried by an organisation name. It is withdrawn and
must not be reintroduced without corpus coverage. Under §5.6 a place name inside
an organisation name stays inside the `ORG` span and no inner `LOC` or `POSTAL`
span is annotated, so no entity exists that could carry the value. A street
address printed next to an organisation name is annotated exactly like any other
address: `street-only`, `full`, or `letterhead-block` on its own `LOC` and
`POSTAL` spans.

`id_format` records the rendered identifier surface, including a permitted
label prefix when one is part of the span. Use this vocabulary. New values need
both a contract update and corpus coverage before they may be annotated.

| Label | `id_format` vocabulary |
|---|---|
| `PESEL` | `bare`, `labelled`, `spaced`, `dashed` |
| `NIP` | `bare`, `dashed`, `spaced`, `prefixed` |
| `REGON` | `bare`, `dashed`, `spaced`, `prefixed` |
| `DOWOD` | `bare`, `spaced`, `dashed`, `prefixed` |
| `IBAN` | `bare`, `spaced`, `dashed`, `nrb`, `prefixed` |
| `PHONE` | `bare`, `dashed`, `spaced`, `plus48` |
| `EMAIL` | `plain`, `plus-tag`, `subdomain`, `uppercase` |
| `PASSPORT` | `bare`, `spaced`, `dashed`, `prefixed` |
| `DRIVING_LICENSE` | `bare`, `dashed`, `spaced`, `prefixed` |
| `PAYMENT_CARD` | `bare`, `spaced`, `dashed`, `prefixed` |
| `VIN` | `bare`, `spaced`, `dashed`, `lowercase` |
| `VEHICLE_PLATE` | `bare`, `spaced`, `dashed`, `prefixed` |

A preposition is not part of a street address span. Annotate `ul. Floriańskiej
12` in `mieści się przy ul. Floriańskiej 12`, and `ulicy Kwiatowej` in `przy
ulicy Kwiatowej`. The bare-city case (§5.3) is the documented exception: there
the preposition stays inside the span (`we Wrocławiu`, `z Krakowa`), because it
carries the inflection that identifies the case.

`identifier_class` is a fixed field per label (§6) — annotators do not choose
it freely.

### Why `identifier_class` matters

`identifier_class` distinguishes entities that identify a person on their own
(`direct`) from entities that only identify in combination with others
(`quasi`). This field is what lets the benchmark measure re-identification
risk (direct-identifier recall, quasi-identifier coverage, residual
identifiability), not just raw span detection. It is cheap to add at
annotation time and effectively impossible to retrofit later — so it is
required on every entity, with no exceptions.

## 4. Span boundary policy (general)

Reused verbatim from `schema.md`'s PDF-lane guidance, and load-bearing for
the entire public corpus, not just the PDF lane:

> **Boundary policy: longest natural name** — keep locative qualifiers and
> role head nouns attached (`Wojewódzki Sąd Administracyjny w Kielcach`,
> `Dyrektor Izby Skarbowej`, `ZUS Oddział K.`), and count each mention at its
> longest rendered form. A mention that is bare in the text stays bare.

Also reused: annotate rendered surfaces, never lemmas. Polish inflects, so a
name mentioned in three grammatical cases is three separate entity entries
with their own `case` value, not one entry with a count.

In the `identifiers` lane, a documented label prefix is part of the span for
the self-labelling `labelled`, `prefixed`, and `nrb` `id_format` values. Every
other lane annotates the bare identifier. A detector that returns the bare
identifier for one of these identifier-lane surfaces still matches under the
published relaxed matcher.

## 5. Address boundary policy

This is a precise, load-bearing convention — annotators must follow it
exactly, it is the most annotator-error-prone part of the schema.

1. **`LOC` and `POSTAL` are separate spans.** A postal code is never folded
   into the `LOC` span, even when it sits immediately next to the street
   address in the text. `ul. Długa 5` is a `LOC` span; `50-138` next to it is
   its own `POSTAL` span.
2. **Street prefixes are included in the `LOC` span.** `ul.`, `al.`, `pl.`
   (and equivalents) are part of the `LOC` span, not stripped: annotate
   `ul. Długa 5`, not `Długa 5`.
3. **Bare, inflected city name — the annotator-error-prone case.** A city
   name mentioned on its own, inflected, with no other address context
   nearby (e.g. `we Wrocławiu` with no street or postal code in the
   surrounding text) is still annotated as `LOC`, with `address_form:
   "bare-city"`. Its `identifier_class` is `quasi`, same as the rest of
   `LOC` — a bare city name does not become `direct` just because it is the
   only address element present. Call this case out explicitly when training
   annotators: it is the single case most likely to be missed or
   miscategorized, because it looks like plain narrative text rather than an
   "address."
4. **This convention ratifies existing practice, not new practice.** It is
   the same LOC-span-plus-separate-POSTAL convention used in the 2026-07-18
   internal measurement (100% recall, 7/7 LOC and 2/2 POSTAL, across
   street-address and full-address-with-postal-code patterns). This document
   formalizes that convention for the public corpus rather than inventing a
   new one.

### 5.5 Person-named streets

For a person-named street, annotate one `LOC` span covering the complete
rendered street address, including its prefix. For example, `al. Jana Pawła
II 43` is one `LOC` span. Do not create a nested or separate `PERSON` span
for `Jana Pawła II`: it names the street in this context, not a person
mentioned by the document. A `PERSON` prediction for that embedded name is a
false positive.

### 5.6 Place names inside organisation names

When a place name occurs as part of an organisation's official rendered name,
annotate the whole name as one `ORG` span and never add an inner `LOC` span.
This applies to `Uniwersytet Warszawski`, `Sąd Rejonowy dla Warszawy-Woli`,
and `Urząd Miasta Krakowa`. A `LOC` prediction for the place-name portion of
one of these `ORG` spans is a false positive.

## 6. `identifier_class` assignment table

`identifier_class` is fixed per label, not left to annotator discretion. All
17 labels are covered below with no gaps.

| Label | `identifier_class` | Rationale |
|---|---|---|
| `PERSON` | `direct` | Identifies the person alone |
| `PESEL` | `direct` | Uniquely identifies one natural person |
| `DOWOD` | `direct` | Uniquely identifies one natural person's ID document |
| `PASSPORT` | `direct` | Uniquely identifies one natural person's travel document |
| `EMAIL` | `direct` | Typically identifies one person or one mailbox owner directly |
| `PHONE` | `direct` | Typically identifies one person or household directly |
| `NIP` | `direct` | Tied to one specific taxpayer (natural or legal person) |
| `REGON` | `direct` | Tied to one specific registered legal/natural-person entity |
| `IBAN` | `direct` | Tied to one specific account holder |
| `DRIVING_LICENSE` | `direct` | Tied to one specific natural person |
| `PAYMENT_CARD` | `direct` | Tied to one specific cardholder |
| `VIN` | `direct` | Tied to one specific vehicle, itself tied to an owner record |
| `VEHICLE_PLATE` | `direct` | Tied to one specific vehicle, itself tied to an owner record |
| `DOB` | `quasi` | Identifies only in combination with other attributes |
| `LOC` | `quasi` | Identifies only in combination (see §5 for the bare-city case) |
| `POSTAL` | `quasi` | Identifies only in combination with other attributes |
| `ORG` | `quasi` | Employer/affiliation identifies only in combination |

Reasoning for the seven labels not explicitly named in the methodology's
direct/quasi split (`NIP`, `REGON`, `IBAN`, `DRIVING_LICENSE`,
`PAYMENT_CARD`, `VIN`, `VEHICLE_PLATE`): each of these is an identifier tied
to one specific legal/natural person or one specific vehicle (which in turn
resolves to one owner record), which is the same GDPR-style reasoning that
makes `PESEL` and `DOWOD` direct identifiers. `direct` is therefore the
defensible default for all seven.

**Free-text context strings** (not one of the 17 labels): if annotators tag
profession or employer context strings as free text, they count as `quasi`
as well, consistent with `ORG`.

## 7. `pdf` lane extraction and annotation

The `pdf` lane (`bench/corpus/public/pdf/`) scores detection on committed,
canonical extracted text — a detection property, identical input for every
system under evaluation — not a system's own end-to-end PDF extraction (see
`public-pii-benchmark-methodology.md` §3, §4.3, §5). Each doc JSON carries
both a `text` field (the committed extraction) and a sibling `pdf` field
(the source PDF's filename, for the optional end-to-end appendix only; the
scorer never reads the PDF file itself).

- **Pinned extractor**: **pdfplumber 0.11.9**, run via the product venv
  (`toolkit/packages/py-anonymize/.venv`, arch -arm64) as corpus-generation
  tooling, not harness runtime. Per-page text is extracted with
  `page.extract_text()` and pages are joined with a blank line (`"\n\n"`),
  matching the convention already used in `bench/spikes/pdf_extraction_spike.py`.
  The exact version is a disclosed constant — every system is scored on the
  same extracted text, so the choice favors no system.
- **Offsets are exhaustive and rendered-surface, same as every other span
  lane** (§4): `text[start:end]` must equal the entity `text` field exactly,
  and every occurrence of a repeated entity gets its own entry (no `count`
  field — the pdf lane no longer uses the old count-based, offset-free
  shape).
- **Extraction noise is captured in the committed text, not smoothed over.**
  When a PDF's rendering artifacts (a page-boundary split, a table-cell
  layout, a ligature-bearing font) cause an entity's surface to differ from
  its "clean" source value, annotate the surface **as it actually renders**
  in the committed text and set the entity `text` field to that rendered
  form, so the offset invariant holds. This is deliberate: the pdf lane's
  stress fixtures (`boundary-001`, `ligature-001`, `tabular-001`) exist to
  put exactly this kind of extraction noise in front of detectors. Two
  examples from the current corpus:
  - `boundary-001`'s `UDA443 321` DOWOD is deliberately split mid-number at
    a page break; with pdfplumber's page-join convention it renders as
    `UDA443\n\n321` (two newlines, not the single space the identifier was
    authored with), and the entity is annotated with that literal rendered
    text.
  - `core-protokol-zebrania-high`'s `Zarząd i Administracja Nieruchomości
    Sp. z o.o.` ORG line-wraps inside pdfplumber's extraction to `Zarząd i
    Administracja\nNieruchomości Sp. z o.o.` (a single newline from the
    PDF's own line layout, not a page boundary); same rule applies.
  - `ligature-001` and `tabular-001` rendered cleanly under pdfplumber
    0.11.9 for the current fixture PDFs (no residual ligature codepoints,
    no cell reordering) — their entities are annotated verbatim. If a
    future re-render (different pdfplumber version, different font
    embedding) introduces noise here, apply the same rendered-surface rule.

## 8. Negative-lane decoys

Negative-lane documents have `entities: []` and may declare a `decoys` field.
`decoys` is a `list[str]`: every entry must occur verbatim in that document's
`text`. It records lookalikes and other deliberately non-PII strings so they
can be grouped and inspected without turning them into annotations. The
document's `category` remains the false-positive grouping key; decoys do not
replace it. The scorer never matches predictions against decoys, so a
prediction that overlaps a decoy is still scored as a false positive.

## 9. Checksum policy

Every ground-truth `PESEL`, `NIP`, `REGON`, and `IBAN` outside the `negative`
lane must pass its official checksum after normalizing the documented rendered
format. For IBAN, both a country-prefixed IBAN and a 26-digit Polish NRB are
valid representations of the same mod-97 check. This ensures that a detector
which validates identifiers is never penalized for rejecting an invalid
fixture.

The `robustness` lane is the only exception. Its entities are deliberately
damaged to measure resilience to extraction and transcription noise. A document
is exempt only when it declares `axes.noise`; being in a similarly named lane
without that axis is not enough.

## 10. Sample variety

Within expanded or generated lane material, no rendered entity surface may
account for more than five percent of that lane's annotated spans. This
ceiling applies to the `address`, `identifiers`, and `robustness` lanes once
they reach the benchmark's minimum statistical sample. In the `negative` lane,
apply the same ceiling to declared `decoys`, because those documents have no
annotated spans. Frozen legacy `core`, `pdf`, and `inflection` material is not
in scope for this rule unless a later corpus change regenerates it.

This guard ensures corpus growth adds varied evidence instead of repetitions of
one easy-to-detect string.

## 11. Sign-off

Informational record of the operator decisions this spec ratifies. Not
build-blocking.

- **Dataset working name**: `pl-pii-bench`.
- **Address boundary convention** (§5): `LOC` and `POSTAL` as separate
  spans, street prefixes included in `LOC`, bare-inflected-city treated as
  `LOC` / `quasi` — ratified as stated above.

# FAIR² → CDIF

Converts a [FAIR²](https://fair-squared.github.io/fair2-spec/) data package (a
`fair2.json` file) to a CDIF JSON-LD record. Written against FAIR² spec v1.3.0.

FAIR² is MLCommons Croissant plus an extension namespace, `fair2:`
(`https://fair2.ai/ns/`). A `fair2.json` file uses Croissant's own terms for the
data structure (`cr:RecordSet`, `cr:Field`, `cr:FileObject`, `recordSet`,
`field`, `source`, `dataType`, `citeAs`), declares
`conformsTo: http://mlcommons.org/croissant/1.0`, and adds `fair2:` terms for
data articles, archives, method sections and steps, field units and statistics,
contributor roles and changelogs. The `fair2:` ontology is not formally linked
to Croissant (its classes subclass schema.org and PROV-O); the link is only in
how the file is assembled.

- Converter: [`fair2_to_cdif.py`](fair2_to_cdif.py). Two passes:
  1. The Croissant core goes through
     [`../croissant/ConvertFromCroissant.py`](../croissant/ConvertFromCroissant.py),
     driven by `croissant-to-cdif.sssom.tsv`.
  2. What FAIR² adds goes through
     [`../mappings/fair2-to-cdif.sssom.tsv`](../mappings/fair2-to-cdif.sssom.tsv),
     read at runtime through the shared `sssom_engine.py`. A source key that
     table claims is withheld from pass 1, so for a claimed key the FAIR² row
     wins.

  Before either table is consulted, each key is expanded through the file's
  own `@context` and rewritten through
  [`../mappings/fair2-aliases.sssom.tsv`](../mappings/fair2-aliases.sssom.tsv),
  which maps the IRIs legacy exports use onto the ones the table names (see
  [Legacy exports](#legacy-exports)).

  Then the record `@id`, the catalog record and `dcterms:conformsTo` are
  settled once for the merged record. The table's rows are tool-suggested
  (`author_id` claude, empty `reviewer_id`) until a curator reviews them; rows
  marked OPEN QUESTION need a decision.
- Example: [`examples/borja2025.json`](examples/borja2025.json), the FAIR²
  specification's own example (from
  [`fair-squared/fair2-spec`](https://github.com/fair-squared/fair2-spec/tree/main/examples/example-1),
  CC BY 4.0), and its conversion
  [`examples/borja2025-cdif.jsonld`](examples/borja2025-cdif.jsonld), which
  validates against the CDIF Data Description schema and is detected as core +
  discovery + data_description + manifest.

## Usage

```bash
python FAIR2/fair2_to_cdif.py FAIR2/examples/borja2025.json -o out.jsonld

# straight from a FAIR² portal, then frame + validate
python FAIR2/fair2_to_cdif.py https://sen.science/doi/10.71728/r1rj-f947/fair2.json -o out/ --validate

# skip content-based conformance detection
python FAIR2/fair2_to_cdif.py fair2.json -o out.jsonld --static-conformance
```

## What maps where

| FAIR² | CDIF |
|---|---|
| Dataset `@id`, `identifier` | record `@id`; `schema:identifier` DOI PropertyValue |
| `dateUpdated` | `schema:dateModified` |
| `creator` | `schema:creator` `@list`, with ORCID/ROR identifiers and affiliations |
| `contributor` + `prov:hadRole` (CRediT / CRO) | one `schema:Role` per role, `roleName` a DefinedTerm with the role IRI |
| `funding` Grants | `schema:funding` MonetaryGrants with funders |
| `spatialCoverage` (bbox > hull > sites) | flat list of `schema:Place` |
| `accessRights` agreement level | `schema:conditionsOfAccess` |
| `dataArticle`, `dataArchive`, `citation` | `schema:relatedLink` LinkRoles |
| `dataPortal` | `schema:relatedLink` LinkRole; the target is typed `[schema:EntryPoint, schema:CreativeWork]` and describes the portal (name, description, version, authors) |
| `domain` | DefinedTerm in `schema:keywords` |
| `methodSection` Sections / Steps / Substeps | `prov:wasGeneratedBy` `[prov:Activity, schema:Action]` with a `schema:actionProcess` HowTo |
| Field `unit` | `schema:unitText` + `schema:unitCode` (QUDT) |
| Field `statistics` | `schema:minValue` / `schema:maxValue`, plus the full node as `fair2:statistics` |
| `_meta`, `subjectOf` spec reference | catalog record `dcterms:conformsTo`, `dateModified`, `dateCreated`, `version` |
| `changeLog` entries | `prov:wasGeneratedBy` activities (`additionalType` `schema:UpdateAction`), dated, with the revised version as `prov:used` |
| `citationKey`, `isExperimentRelated` | kept under `fair2:` (no CDIF term) |

Relative `@id`s resolve against the FAIR² `@base`, which is carried into the
output `@context`.

## Testing

Converted and validated (`--validate`, CDIF Data Description schema) against
the spec example and the `fair2.json` served for each Senscience DOI under
prefix 10.71728 (October 2026). All nine are valid; detected profiles:

| Source | Detected CDIF profiles |
|---|---|
| spec example `borja2025.json` (v1.3.0) | core, discovery, data_description, manifest |
| 10.71728/hw56-vj34, r1rj-f947, senscience.4f2j-8h1k, senscience.bt07-sboc, senscience.dbrh-5zc8, senscience.k2f7-p5v9 | core, discovery, data_description, manifest |
| 10.71728/senscience.f2na-3dct, senscience.g7x2-a9k4 | core, discovery, data_description |

Only the spec example is kept here; the live exports carry no stated metadata
licence. Fetch one with the URL form under [Usage](#usage).

## What CDIF cannot carry yet

These FAIR² properties are kept under their `fair2:` key, so nothing is lost,
but CDIF has no term a consumer would recognise:

- **Descriptive statistics beyond min/max** (count, mean, std, unique, missing
  values, top-N frequencies). CDIF variables have `minValue` / `maxValue` only;
  the rest could become `dqv:QualityMeasurement`s.
- **`isExperimentRelated`** and **`citationKey`**: no CDIF equivalent.
- **Certification** (`Certification`, `certifiedBy`, `certificationScope`,
  `certificationDocument`, `fair2ComplianceLevel`, `verificationEndpoint`): not
  mapped. FAIR² marks its certification model as a draft pending governance,
  and CDIF would first need its own definition of what a certification asserts.
  Not used in any file tested.
- **Conditional method steps** (`nextTrue`, `qualifiedUsage`): `schema:HowTo`
  is linear and cannot express a branch. Not used in any file tested.

**Provenance is not declared** for any file tested. The CDIF provenance shapes
require every activity to name its inputs (`prov:used`). Most FAIR² method
sections list none, a first release has no earlier version, and the per-field
statistics activities have none.

## Questions raised with the FAIR² maintainers

Points that could not be settled from the specification are reported in
[fair-squared/fair2-spec#7](https://github.com/fair-squared/fair2-spec/issues/7).
In summary:

- **Variables.** The FAIR² Data Dictionary says variables MUST use
  `schema:variableMeasured` with `skos:definition` and `qudt:unit`; every file
  tested uses Croissant `cr:Field`s with `description` and `fair2:unit`. The
  converter follows the files.
- **Context term definitions.** `variables` (`"@type": "schema:DefinedTermSet"`)
  and `ExperimentDataset` (`"@type": "schema:Dataset"`) put `@type` in the
  term definition, which in JSON-LD coerces value datatypes rather than
  declaring a range or superclass. `ExperimentDataset` is also missing from the
  ontology. Not mapped until clarified.
- **Under-defined terms.** `store`, `Submission`, `manuscript` and
  `attachment` have only one-line ontology comments and no shape or example.
  Not mapped. `DataPortal` has a shape and is mapped as a `relatedLink`, but no
  file tested contains one.
- **"FAIR²-Validated"** names no shape set, validator or report, so the claim
  cannot be checked the way a CDIF `conformsTo` URI can.
- **Certification credential.** `fair2-cert.json` is signed by a certifier
  DID; whether it is a W3C Verifiable Credential, and its schema, is not stated.
- **Spec example data.** One Organization `@id` (AZTI) carries two different
  ROR identifiers; `departament` is not a defined term.
- **Legacy exports.** The served `fair2.json` files predate v1.2 (see below);
  the converter's aliases table exists only to absorb them.

## Legacy exports

The `fair2.json` files served today for the eight Senscience DOIs (prefix
10.71728) predate FAIR² v1.2, and differ from the spec in ways the converter
absorbs:

| Legacy form | Handled by |
|---|---|
| `@vocab` `http://senscience.ai/`, so undeclared terms land in that namespace | aliases table (`senscience:X` → the term meant) |
| `fair2` prefix bound to `https://fair2.ai/spec/fair2_context[/]` | folded onto `fair2:` in the converter |
| `author`, `contribution`, `method`, `mnethodSection`, `title`, `mlc:domain` | aliases table |
| `contribution` as `schema:Contribution` {`prov:agent`, `prov:hadRole` CURIEs} | `contributorrole` shaper reads both shapes |
| method `steps` / `substeps` | `method` shaper reads both spellings |
| units as `qudt:Unit` {`@id`, `rdfs:label`, `qudt:symbol`} | unit shapers |
| flat statistics, or `varriableMeasured` | min/max shapers |
| no `@graph`; `_meta` inside the Dataset; authors inlined once and referenced by `@id` elsewhere | converter |
| Senscience-only Responsible-AI statements, `fundingStatement`, `socialMedia` | kept under their own IRI (passthrough rows) |

Every Dataset and Field key in the nine files seen (the spec example and the
eight live exports) is mapped, aliased or explicitly passed through. When a
file carries both an alias and the canonical key (`author` and `creator`), the
canonical key wins.

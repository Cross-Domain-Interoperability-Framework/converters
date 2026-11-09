# NIAID → CDIF

**In development.** The harvester converts NIAID records to CDIF conforming to the
draft `cdifNIAID` profile (`https://w3id.org/cdif/niaid/1.0`), which accounts for the
extended attributes NIAID records use. The mapping table has been reviewed, but the
profile, the table and this directory may still change.

[`cdifJSON-LD/`](cdifJSON-LD/README.md) holds the example records converted and
validated; regenerate it with `python NIAID/build_cdif_examples.py`. To convert records
from the live API:

```bash
python NIAID/harvest_niaid_to_cdif.py --source "Harvard Dataverse" -n 20 -o out/
```

## Source

- **Records:** the [NIAID Data Discovery Portal](https://data.niaid.nih.gov/), through
  its API (`https://api.data.niaid.nih.gov/v1`). The portal aggregates metadata from
  other repositories, named in each record's `includedInDataCatalog`.
- **Schemas:** [NIAID-Data-Ecosystem/nde-schemas](https://github.com/NIAID-Data-Ecosystem/nde-schemas),
  which defines the `nde:` namespace (`https://discovery.biothings.io/ns/nde/`). See
  [`schemas/SOURCE.md`](schemas/SOURCE.md) for the commit and license.

## Contents

| Path | What it is |
|---|---|
| [`harvest_niaid_to_cdif.py`](harvest_niaid_to_cdif.py) | Harvests Dataset records from the API and converts them to CDIF JSON-LD |
| [`../mappings/niaid-to-cdif.sssom.tsv`](../mappings/niaid-to-cdif.sssom.tsv) | The mapping table the harvester reads at run time (see [below](#mapping-table)) |
| [`check_niaid_mappings.py`](check_niaid_mappings.py) | Checks the table against the harvester and the examples; run in CI |
| [`build_cdif_examples.py`](build_cdif_examples.py) | Converts `niaidExamples/` into `cdifJSON-LD/` and validates each record against the profiles it claims |
| [`cdifJSON-LD/`](cdifJSON-LD/README.md) | The example records as CDIF, with `INDEX.json` giving each one's validation results |
| [`build_nde_jsonschemas.py`](build_nde_jsonschemas.py) | Generates `jsonschema/` from NIAID's schema and the example records |
| [`schemas/`](schemas/SOURCE.md) | NIAID's registered schema, `NDE_schema.jsonld`, unmodified |
| [`jsonschema/`](jsonschema/README.md) | JSON Schemas for unconverted NIAID records, as specified and as published |
| [`niaidExamples/`](niaidExamples/README.md) | Example records as the API returned them |
| [`archive/`](archive/README.md) | NIAID's per-class schema sources, which nothing here reads |

## What NIAID's schemas say

[`schemas/NDE_schema.jsonld`](schemas/NDE_schema.jsonld) is the schema as registered
on the Data Discovery Engine. NIAID builds it from per-class source files, which are in
[`archive/nde-schemas/`](archive/README.md). It has 52 nodes:

- **9 classes:** `Dataset`, `ComputationalTool`, `ResourceCatalog`, `DefinedTerm`,
  `Sample`, `SampleCollection`, `DataCollection`, `SemanticTriple` and `Inference`.
  Each holds a draft-07 JSON Schema in `$validation`, with bare property names.
- **43 properties, all `nde:`:** some are new terms, such as `species`, `topicCategory`,
  `doi` and `nctid`. Others redeclare names schema.org already has, such as `funding`
  and `distribution`.

Two things stand out when the schemas are compared with the records:

- **The stated rules aren't all enforceable.** `owl:cardinality`, `recommended`,
  `optional`, `vocabulary` and `strict` are annotations that JSON Schema validators
  ignore. In `Dataset` alone, `owl:cardinality` appears 64 times.
- **The records don't satisfy the schema.** None of the 140 example records pass the
  `Dataset` schema. For example, 61 give `includedInDataCatalog` as an array, though
  the schema annotates it as `owl:cardinality: one`, and 58 have a catalog with no `url`.

## How `jsonschema/` is generated

```bash
python NIAID/build_nde_jsonschemas.py
```

The output comes in two forms, because "what NIAID specifies" and "what the portal
publishes" differ:

1. **`asSpecified/`, all 9 classes.** Each class's `$validation`, with `owl:cardinality`
   made enforceable where the schema doesn't already say it: a "one" property is
   refused arrays, and a "many" property is given them. This changes 13 rules across
   the classes.
2. **`asPublished/Dataset.schema.json`, `Dataset` only.** The as-specified schema
   relaxed against the 140 examples until all of them pass, which takes 36 relaxations.
   Each failing record changes only the rule it broke, and top-level names the schema
   doesn't declare are added and marked as undeclared. The other classes have no
   example records to relax against.

[`jsonschema/relaxations.json`](jsonschema/relaxations.json) logs every relaxation with
the records that needed it, and lists the names the records use but the schema doesn't
declare. Relaxations are evidence about these records, not a specification: some may
be portal or source problems that should stay failures. See
[`jsonschema/README.md`](jsonschema/README.md) for results on records outside the
examples.

Nothing validates unconverted records routinely. The harvester does not check its input
against these schemas, and CI does not run `build_nde_jsonschemas.py`. The schemas are
for checking records by hand, for example:

```bash
python -m jsonschema -i NIAID/niaidExamples/9j82.json NIAID/jsonschema/asPublished/Dataset.schema.json
```

## Mapping table

The harvester reads [`niaid-to-cdif.sssom.tsv`](../mappings/niaid-to-cdif.sssom.tsv)
at run time, so a mapping change is a table edit. Each row maps one property on one
kind of node (`subject_class`), and rows run in file order: the first row to fill a
single-valued target wins, while array targets collect from every row. A row's
`transform` names the shaper for anything more than a rename, and its `profile` names
the building block that constrains the target. The
[sidecar](../mappings/niaid-to-cdif.sssom.yml) lists the shapers.

A name the table has no row for on that kind of node takes the target the table gives
it elsewhere. A name the table doesn't know at all is kept under `cdifniaid:` with a
note, and `check_niaid_mappings.py` fails if any committed example has one.

The record's `_id` isn't a URI, so the dataset's `@id` is the portal address built from
it, such as `https://data.niaid.nih.gov/resources?id=9j82`. The repository's own address
stays in `schema:url` and is added to `schema:sameAs`.

## The cdifNIAID profile

The profile lives in
[metadataBuildingBlocks](https://github.com/Cross-Domain-Interoperability-Framework/metadataBuildingBlocks):

| Block | What it holds |
|---|---|
| `profiles/cdifCompositeProfile/cdifNIAID` | The profile: `cdifCore` plus `niaidDataset` |
| `niaidProperties/niaidDataset` | NIAID's `nde:` terms on the dataset and its samples, and the `cdifniaid:` terms |
| `niaidProperties/niaidDefinedTerm` | The term annotations (`nde:isCurated`, `nde:originalName`, `nde:curatedBy`, `cdifniaid:commonName`), on a `schema:DefinedTerm` |
| `niaidProperties/niaidMonetaryGrant` | The parts of an NIH project number, on a `schema:MonetaryGrant` |
| `niaidProperties/niaidCreativeWork` | `nde:pmid`, `nde:doi` and the journal names, on a cited work |
| `niaidProperties/niaidPlace` | `nde:administrativeType`, on a `schema:Place` |
| `niaidProperties/niaidDataCatalog` | `nde:versionDate` and `schema:archivedAt`, on a `schema:DataCatalog` |
| `bioschemasProperties/bioschemasSample` | The Bioschemas sample terms NIAID uses |

The five typed blocks require their type, so the harvester types the nodes the portal
leaves untyped (grants, cited works, catalogs and terms).

Each NIAID name maps to one of four namespaces:

- **`bios:`** where Bioschemas defines the same term (NIAID's and Bioschemas'
  definitions were compared).
- **`nde:`** where NIAID's schema declares it.
- **`schema:`** for schema.org terms.
- **`cdifniaid:`** (`https://w3id.org/cdif/niaid/terms/`) for names the portal uses that
  neither vocabulary declares, such as the parts of an NIH project number.
  `niaidDataset`'s description defines them.

`cdifDiscovery` isn't part of the profile, because about one record in six has no
discovery content; records that have it claim and pass discovery separately.

Where a record lacks something CDIF requires, the harvester writes a sentinel value:

- a record with no date gets `dateModified` `1900-01-01`;
- a person with no name or identifier is named `missing`;
- a file or part with no address gets the OGC nil URL
  `http://www.opengis.net/def/nil/OGC/0/missing`;
- a url or contentUrl that is only a file name gets the same nil URL, and the file
  name joins the node's `schema:name` ("protocol: LDG-1218_Protocol.pdf").

Spaces in a url or contentUrl are written as `%20`, and an untyped `additionalType` object
is typed `schema:DefinedTerm`.

A date-time with no time zone is taken as UTC.

Results against the local `cdifNIAID` profile on 2026-11-09:

- 576 of 576 records sampled from 48 sources pass. The table and profile were not
  fitted to this sample.
- 210 of 210 development records pass.

The published core on w3id still rejects agents with several alternate names, until the
release that carries that change. See [`cdifJSON-LD/README.md`](cdifJSON-LD/README.md)
for the example records, checked with JSON Schema and SHACL.

The portal's own bookkeeping is dropped: `_score`, `_ignored`, `_meta` and the
`ibmGranite*` fields.

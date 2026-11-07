# NIAID → CDIF

**In development.** The harvester converts NIAID records to CDIF conforming to the
draft `cdifNIAID` profile (`https://w3id.org/cdif/niaid/1.0`), which accounts for the
extended attributes NIAID records use. The profile, the mapping table and this
directory may all still change, and no mapping row has been reviewed.

No converted CDIF records are committed here; the profile's building blocks carry
converted examples. To produce some (this queries the live API):

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
| [`build_nde_jsonschemas.py`](build_nde_jsonschemas.py) | Generates `jsonschema/` from the schemas and the example records |
| [`schemas/`](schemas/) | Unmodified copies of NIAID's schemas |
| [`jsonschema/`](jsonschema/README.md) | JSON Schemas for NIAID records, as specified and as published |
| [`niaidExamples/`](niaidExamples/README.md) | Example records as the API returned them |

## What NIAID's schemas say

[`schemas/NDE_schema.jsonld`](schemas/NDE_schema.jsonld) is the schema as registered
on the Data Discovery Engine, and the only file in `schemas/` the build reads. It has
52 nodes:

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
[metadataBuildingBlocks](https://github.com/Cross-Domain-Interoperability-Framework/metadataBuildingBlocks)
as three blocks:

| Block | What it holds |
|---|---|
| `profiles/cdifCompositeProfile/cdifNIAID` | The profile: `cdifCore` plus `niaidDataset` |
| `niaidProperties/niaidDataset` | NIAID's `nde:` terms, its term annotations, and the `cdifniaid:` terms |
| `bioschemasProperties/bioschemasSample` | The Bioschemas sample terms NIAID uses |

Each NIAID name maps to one of four namespaces:

- **`bios:`** where Bioschemas defines the same term (NIAID's and Bioschemas'
  definitions were compared).
- **`nde:`** where NIAID's schema declares it.
- **`schema:`** for schema.org terms.
- **`cdifniaid:`** (`https://w3id.org/cdif/niaid/terms/`) for names the portal uses that
  neither vocabulary declares, such as `fromGPT` and the parts of an NIH project
  number. `niaidDataset`'s description defines them.

`cdifDiscovery` isn't part of the profile, because about one record in six has no
discovery content; records that have it claim and pass discovery separately.

Results on 576 records sampled from 48 sources, which the table and profile were not
fitted to (2026-11-07): 522 (91%) pass every profile they claim, and 529 (92%) pass
core and `cdifNIAID`. Most failures are in the source: 47 records have no date of any
kind, which CDIF core requires for `schema:dateModified`.

The portal's own bookkeeping is dropped: `_score`, `_ignored`, `_meta` and the
`ibmGranite*` fields.

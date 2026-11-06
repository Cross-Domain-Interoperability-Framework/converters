# NIAID → CDIF

**In development.** The harvester is a prototype and its output, the mapping to
CDIF and the layout of this directory may all change. Some converted records still
fail CDIF validation, and there is no mapping table or CDIF profile yet for the
NIAID fields that have no CDIF equivalent.

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
| [`build_nde_jsonschemas.py`](build_nde_jsonschemas.py) | Generates `jsonschema/` from the schemas and the example records |
| [`schemas/`](schemas/) | Unmodified copies of NIAID's schemas |
| [`jsonschema/`](jsonschema/README.md) | JSON Schemas for NIAID records, as specified and as published |
| [`niaidExamples/`](niaidExamples/README.md) | Example records as the API returned them |

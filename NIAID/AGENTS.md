# AGENTS.md: NIAID to CDIF conversion

A guide for agents working in `NIAID/`. For the whole repository, see the root
[`CLAUDE.md`](../CLAUDE.md) and [`README.md`](../README.md). For an overview of this
directory, see [`README.md`](README.md).

## What lives here

| Path | Role |
|---|---|
| `harvest_niaid_to_cdif.py` | Harvests the portal API and converts records; `convert_record(hit, build)` does one record |
| `../mappings/niaid-to-cdif.sssom.tsv` | **The mapping.** The harvester reads it at runtime. Edit this, not the Python |
| `../mappings/niaid-to-cdif.sssom.yml` | Sidecar: prefixes, and the list of shapers named in the `transform` column |
| `check_niaid_mappings.py` | CI check: transforms, prefixes, and the table's coverage of `niaidExamples/` |
| `build_cdif_examples.py` | Rebuilds `cdifJSON-LD/` and validates it |
| `cdifJSON-LD/` | Generated. The examples as CDIF, plus `INDEX.json` with each record's results |
| `niaidExamples/` | 140 raw API records, the regression input |
| `build_nde_jsonschemas.py`, `schemas/`, `jsonschema/` | JSON Schemas for the portal's unconverted records. Not part of conversion |
| `archive/` | NIAID's per-class schema sources, kept for reference; nothing reads them |

`aiStudio/` (if present) is a local-only reference copy of another tool's converter. It is
never committed and nothing reads it.

## The table drives the converter

- **Precedence:** rows are grouped by `subject_class` and applied in file order. For a
  scalar target, the first row to fill it wins; array targets collect from every row.
  "Prefer X, else Y" is written as two rows in that order. For example, the
  `dateModified` fallbacks run `date`, then `datePublished`, then `dateCreated`.
- **Python is for structure only:** a mapping change is a table edit. Change the Python
  only to add or fix a shaper. A shaper is named in the `transform` column, registered in
  `SHAPERS`, and listed in the sidecar. `check_niaid_mappings.py` fails on a name the
  converter doesn't define.
- **Rows that run with no value:** a row is skipped when its source property is absent,
  unless its transform is in `RUNS_WITHOUT_VALUE`. That is how the sentinels work:
  `license_or_nil`, `content_url_or_nil`, `date_or_sentinel` (`1900-01-01`),
  `name_or_missing` (a person named `missing`) and `name_with_file` (a url that is only
  a file name moves into the name; `_address` makes the url the OGC nil value). Put such a row after the rows it falls
  back from, and have the shaper return `None` when an earlier row has already filled
  the target.
- **Deferred paths:** a `$.` path on a nested class writes to the dataset root, and a
  `$.schema:distribution[0].` path writes to the record's only distribution.
- **Unmapped names:** a name with no row on its class takes the target the table gives
  it elsewhere. A name the table doesn't know at all is kept under `cdifniaid:` with a
  note, and the CI check fails if a committed example has one.
- **Spreadsheet edits:** keep every row at the header's 13 columns. Check with
  `awk -F'\t' '{print NF}' mappings/niaid-to-cdif.sssom.tsv | sort | uniq -c`.

## Where the profile lives

`cdifNIAID` (`https://w3id.org/cdif/niaid/1.0`) and its blocks are in metadataBuildingBlocks:
`_sources/profiles/cdifCompositeProfile/cdifNIAID` and `_sources/niaidProperties/*`.

- **New terms:** a property the converter newly writes in the `nde:` or `cdifniaid:`
  namespace needs a definition in a `niaidProperties` block. A property the converter
  stops writing should be removed there.
- **Typed blocks:** the blocks for DefinedTerm, MonetaryGrant, CreativeWork, Place and
  DataCatalog require `@type`. The harvester types untyped nodes through
  `TYPE_WHEN_UNTYPED`.
- **Shape targets:** a SHACL shape in those blocks targets property subjects
  (`sh:targetSubjectsOf`), not a schema.org class. A class target would claim every
  node of that type in any graph using the block.
- **Block examples:** they are regenerated from this converter. After a converter change,
  check that they still match what it writes.

## Validating

- **The CI check:**
  ```bash
  python NIAID/check_niaid_mappings.py
  ```
- **The example records:**
  ```bash
  python NIAID/build_cdif_examples.py
  ```
  This needs `../metadataBuildingBlocks` beside this repo. It validates every claimed
  profile against the local `resolvedSchema.json`, and against the SHACL bundle that
  metadataBuildingBlocks' `tools/validate_shacl.py --emit-shapes` makes for it. It
  uses whatever metadataBuildingBlocks branch is checked out.
- **Output is deterministic:** the build date is fixed, and SHACL results are sorted.
  Commit a rebuild separately, with a `[generated]` subject.
- **Not the w3id schemas:** cdifNIAID isn't published on w3id yet, and the published
  core lags metadataBuildingBlocks main until the release repos' `updates` branches are
  released. A record can pass locally and fail against w3id.
- **Bad source metadata:** some failures can't be fixed in conversion and are listed in
  `cdifJSON-LD/README.md`. Examples are URLs with spaces and bare file names given as
  URLs.

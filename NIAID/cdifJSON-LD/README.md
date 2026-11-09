# NIAID examples as CDIF JSON-LD

The 140 records in [`../niaidExamples/`](../niaidExamples/) converted to CDIF by
[`../harvest_niaid_to_cdif.py`](../harvest_niaid_to_cdif.py), one `.jsonld` per
source file, with the same name.

The converter writes each record's conformance claims. They sit in the catalog record's
`dcterms:conformsTo`:

- **Every record:** CDIF core (`https://w3id.org/cdif/core/1.1`) and the cdifNIAID
  profile (`https://w3id.org/cdif/niaid/1.0`).
- **Some records:** `discovery/1.1` and `manifest/1.1`, where their content supports
  those profiles.

[`INDEX.json`](INDEX.json) lists each record's claims and whether it validates
against each one.

## Validation

[`../build_cdif_examples.py`](../build_cdif_examples.py) checks every claimed profile:

1. It frames each record with `validation/CDIF-frame-2026.jsonld`.
2. It finds the profile in the metadataBuildingBlocks checkout beside this repository,
   by matching the URI to the `conformsTo` in a profile's `conformance.json` under
   `_sources/profiles/`.
3. It validates against that profile's `resolvedSchema.json`.
4. It validates with the profile's SHACL bundle. The bundle merges the rules of every
   block the schema references, as made by metadataBuildingBlocks'
   `tools/validate_shacl.py --emit-shapes`. That is the same bundle a release repo
   publishes as its `*Rules.shacl`. Only `sh:Violation` results fail a record;
   warnings and info don't.

For cdifNIAID the schema is
`_sources/profiles/cdifCompositeProfile/cdifNIAID/resolvedSchema.json`, which combines
cdifCore and niaidDataset. cdifNIAID has no SHACL of its own; its bundle is the rules
of cdifCore and the NIAID blocks. The w3id copies are not used: cdifNIAID is not
published there yet, and the published core lags metadataBuildingBlocks main.

Where the source leaves out something CDIF requires, the converter writes a sentinel
value instead:

- **A record with no date** (`microbiome_gems1_case_control`, `reframedb_a00230`):
  `dateModified` is set to `1900-01-01`.
- **A person with no name or identifier:** the person is named `missing`.
- **A date-time with no time zone:** the date-time is taken as UTC (`Z`).

133 of the 140 records pass every profile they claim. All 7 failures are in the
source records, not the NIAID rules:

| Records | Check | Problem |
|---|---|---|
| `pxd077284`, `vdj_prjna272713` | core SHACL | A `contentUrl` with spaces in it, or a bare file name such as `SRR1767427_R1.fastq` |
| `accessclinicaldata_tb-portals-…`, `lds-1224`, `lds-1229`, `malariagen_…` | core SHACL | A `url` with spaces in it, or a bare file name such as `LDG-1218_Protocol.pdf`. In `lds-1224` and `lds-1229` that file name is the url of the `prov:wasDerivedFrom` CreativeWork, so that shape fails too |
| `nichd_dash_dataset_1132` | manifest SHACL | An `additionalType` that looks like a CURIE but is a plain string |

Fractional-second date-times (the two `ndex_` records) and `ftp://` EntryPoints (the two
`hca_` records) passed only from metadataBuildingBlocks#73 onward; before it, 129 passed.

[`INDEX.json`](INDEX.json) gives each failing record's first errors.

## Regenerating

```bash
python NIAID/build_cdif_examples.py
```

Regenerate after any change to the converter or its table
(`mappings/niaid-to-cdif.sssom.tsv`). The output depends on the
metadataBuildingBlocks checkout and the `validation` submodule. It is otherwise
deterministic, because the build date is fixed to the examples' index build,
2026-10-28.

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
of cdifCore and the NIAID blocks. The w3id copies are not used: cdifNIAID is published
at `https://w3id.org/cdif/niaid/1.0`, but the published core lags metadataBuildingBlocks
main.

Where the source leaves out something CDIF requires, the converter writes a sentinel
value instead:

- **A record with no date** (`microbiome_gems1_case_control`, `reframedb_a00230`):
  `dateModified` is set to `1900-01-01`.
- **A person with no name or identifier:** the person is named `missing`.
- **A date-time with no time zone:** the date-time is taken as UTC (`Z`).

All 140 records pass every profile they claim. Where a source value would fail, the
converter repairs it:

- **A url or contentUrl that is only a file name** (`lds-1224`, `lds-1229`,
  `vdj_prjna272713`): the file name joins `schema:name` ("protocol:
  LDG-1218_Protocol.pdf"), or is the name when there is none, and the address is the
  OGC nil value `http://www.opengis.net/def/nil/OGC/0/missing`.
- **Spaces in a url or contentUrl** (`pxd077284`, `accessclinicaldata_tb-portals-…`):
  written as `%20`.
- **An untyped additionalType object** (`nichd_dash_dataset_1132`, `{"name": "Codebook/Variable
  Dictionary"}`): typed `schema:DefinedTerm`, which the manifest's additionalType rule
  accepts.

Fractional-second date-times (the two `ndex_` records) and `ftp://` EntryPoints (the two
`hca_` records) pass from metadataBuildingBlocks#73 onward, and the `ftp://` citation in
`malariagen_…` from metadataBuildingBlocks#74.

From converters#26, the converter also links values to shared identifiers and
vocabularies, as NIAID's Blueprint for Digital Objects asks. It matches exactly, or
leaves the value as it was:

| Value | Written as | Records |
|---|---|---|
| Access level Open, Restricted or Closed | A `schema:CreativeWork` link to the COAR access right (open access, restricted access, metadata only access), named with the portal's word | 114 |
| An annotated term (species, infectious agent, health condition, technique, keyword) | The term keeps its own node, with `schema:sameAs {"@id": ...}` the ontology IRI read from its url; a UniProt taxonomy page becomes the NCBITaxon IRI | 132 |
| An NCIT term | Both NCIT IRIs as `sameAs`: OBO's (`…/obo/NCIT_C…`) and NCI's own (`…/Thesaurus.owl#C…`), which the Blueprint's CEDAR template uses (converters#28) | 79 |
| A Creative Commons or SPDX license, or a license text naming one | A link named by the SPDX identifier (`CC-BY-4.0`), `sameAs` the SPDX IRI | 52 licenses |
| A funder, or (from converters#28) an organization creator, with no identifier | The ROR id of an organization of the same name (`../org_ror.json`) | 507 of 566 funders have a ROR id; 8 organization creators gained one |
| A place named as a country (`Kenya`, `Czechia`, `Korea, Republic of`) | The ISO 3166 alpha-3 code on its name term (`../iso3166.json`) | 127 of 151 places have a code |

The cdifNIAID SHACL warns, without failing a record, for each of the Blueprint's 17
elements a record lacks (metadataBuildingBlocks#78). In two records (`vivli_40b1791e…`,
`vivli_ca191d8b…`) the portal gives India the code `IOT` (British Indian Ocean
Territory); the converter corrects a code that contradicts an exact country name, so
they carry `IND`.

[`../blueprintCEDAR/`](../blueprintCEDAR/README.md) holds these records written as
instances of GO FAIR US's CEDAR template for the
[NIAID Blueprint](https://doi.org/10.5281/zenodo.17161561). All 140 are valid against
the template's JSON Schema, and its INDEX reports how complete each is against the
Blueprint.

[`INDEX.json`](INDEX.json) gives each record's results, and the first errors of any that fail.

## Regenerating

```bash
python NIAID/build_cdif_examples.py
```

Regenerate after any change to the converter or its table
(`mappings/niaid-to-cdif.sssom.tsv`). The output depends on the
metadataBuildingBlocks checkout and the `validation` submodule. It is otherwise
deterministic, because the build date is fixed to the examples' index build,
2026-10-28.

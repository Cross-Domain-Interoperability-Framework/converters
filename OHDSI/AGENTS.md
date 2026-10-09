# AGENTS.md — guide for AI agents / contributors

## What this directory is

A **metadata harvesting** project: it converts schema.org dataset metadata from
the **OHDSI gaiaCatalog** datastore
([OHDSI/gaiaCatalog `/datastore/data`](https://github.com/OHDSI/gaiaCatalog/tree/main/datastore/data),
published by the University of Miami **Geospatial Digital Special Collections /
GDSC**) into **CDIF** (Cross-Domain Interoperability Framework) JSON-LD.

Target profiles: **cdifCore + cdifDiscovery + cdifProvenance**.

## Layout

| Path | Role |
|------|------|
| `harvest_ohdsi_to_cdif.py` | The converter (single, self-contained script) |
| `OHDSIMetadata/` | Source `meta_json-ld_{table_id}.json` (16 datasets, as harvested) |
| `cdifMetadata/` | Output `cdif_{table_id}.json` (generated — do not hand-edit) |
| `check_ohdsi_mappings.py` | Drift check: the converter against `../mappings/ohdsi-to-cdif.sssom.tsv` over the corpus (run in CI) |
| `../mappings/ohdsi-to-cdif.sssom.tsv` (+ `.yml`) | The property mapping, in SSSOM. It documents the converter (which does not read it) |
| `README.md` | Human-facing description, mapping summary, validation status |
| `AGENTS.md` | This file |

`cdifMetadata/` is **generated output**. Never edit those files by hand — change
the converter and regenerate, or the next run will silently overwrite your edits.

## How to run

From the root of the `converters` repository:

```bash
python OHDSI/harvest_ohdsi_to_cdif.py                 # OHDSIMetadata/ -> cdifMetadata/ (provenance on)
python OHDSI/harvest_ohdsi_to_cdif.py --no-provenance # discovery + core only
python OHDSI/harvest_ohdsi_to_cdif.py -i IN -o OUT    # custom dirs
```

Dependencies (for the converter): none beyond the Python 3.9+ standard library.
Validation needs the CDIF tooling in the `validation` and `profile-provenance` repos (see below):
`pip install PyLD jsonschema rdflib pyshacl`.

## Converter architecture

`convert_document()` builds each CDIF doc. The core is a generic recursive
`convert()` that prefixes bare schema.org keys/types (source authors with
`@vocab: https://schema.org/`, so tokens are unprefixed) to explicit `schema:`
(`http://schema.org/`) and normalizes `@type` to arrays. Specialized helpers
handle the parts that need shaping to pass CDIF schema + SHACL:

- `make_identifier` — synthesizes the required `schema:identifier` from the `@id`.
- `build_catalog_record` — the `schema:subjectOf` CatalogRecord carrying
  `dcterms:conformsTo` (core + discovery, plus provenance when emitted), the
  harvest note, and `schema:about`.
- `convert_spatial_coverage` — turns source bbox `GeoShape.polygon` rings into
  `schema:box` (`minLat minLon maxLat maxLon`); CDIF SHACL rejects `polygon`.
- `convert_additional_property` — derives a `schema:name` (required by SHACL).
- `convert_variables` — cleans stray whitespace in names, drops empty entries.
- `convert_references` — reduces `isBasedOn` to pure `{@id}` links.
- `find_etl_action` + `build_provenance_activity` — map the real ETL `about`
  action to `prov:wasGeneratedBy` (see below).

### Provenance mapping (cdifProv)

Each source doc has two `about` Events: a `"Pseudo Code"`/`"TODO"` placeholder
(dropped) and a real ETL action (`curl`/`wget`/`local`/`srtm_merge_and_crop`/…)
with `object`+`instrument`+`result`. The real action becomes a dual-typed
`["prov:Activity","schema:Action"]` activity: `object` → `schema:object` (`{@id}`
ref to the source URL), `instrument` → `prov:used[].schema:instrument` (a
`schema:name` is derived because SHACL requires one), `result` → `schema:result`
(`{@id}` ref to the generated dataset).

**Critical invariant:** `schema:object`/`schema:result` are `{@id}` **references**,
never inline `schema:Dataset` nodes. Extra `schema:Dataset` nodes break the CDIF
**discovery** frame's root selection (it roots on `schema:Dataset`). If you add
provenance entities, keep them as references or type them as something other than
`schema:Dataset` (the cdifProv examples use `schema:Thing`/`schema:MediaObject`).

`schema:actionProcess` → `schema:HowTo`/`schema:step` is intentionally **not**
emitted: the source's placeholder event has only `"TODO"`, so there is no step
methodology to map. Add it only if the source gains real pseudo-code.

## Validation — always run before committing regenerated output

The Discovery checks use the `validation` submodule and run from the root of
this repository; the provenance checks use a `profile-provenance` checkout beside
it (run from inside that repo). Both profiles must show **0 SHACL violations** and
**16/16 JSON Schema pass**:

```bash
# Discovery (from the converters repo root, using the validation submodule)
python validation/tools/FrameAndValidate.py <file> -v --schema validation/CDIFDiscoverySchema.json --frame validation/CDIF-frame-2026.jsonld
python validation/ShaclValidation/ShaclJSONLDContext.py <file> validation/ShaclValidation/CDIF-Discovery-Shapes.ttl

# Provenance (from the profile-provenance repo)
python FrameAndValidate.py <file> -v
python ../validation/ShaclValidation/ShaclJSONLDContext.py <file> provenanceRules.shacl
```

Warnings/Info are acceptable (advisory source-data-completeness recommendations:
non-URI `propertyID`s, missing variable descriptions, no instrument category
vocabulary). **Violations are not** — a violation means the output is
non-conformant and must be fixed in the converter.

## Conventions

- Match the existing code style; keep the converter a single self-contained
  script with stdlib-only dependencies.
- When you change the mapping, **update `../mappings/ohdsi-to-cdif.sssom.tsv`
  to match and run `python OHDSI/check_ohdsi_mappings.py`** (CI fails on drift),
  then **regenerate `cdifMetadata/` and re-validate both profiles** before
  committing; commit the regenerated output together with the
  converter change.
- `tz_2022_nbs` in the source is a **container** directory whose real dataset is
  the nested `tz_2022_nbs_districts` — that nested record is the one harvested.

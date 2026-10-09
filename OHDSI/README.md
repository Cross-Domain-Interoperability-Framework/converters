# OHDSI gaiaCatalog → CDIF harvest

Harvests schema.org dataset metadata from the OHDSI **gaiaCatalog** datastore
([OHDSI/gaiaCatalog `/datastore/data`](https://github.com/OHDSI/gaiaCatalog/tree/main/datastore/data),
published by the University of Miami **Geospatial Digital Special Collections /
GDSC**) and converts it to **CDIF Core + Discovery** JSON-LD.

## Layout

| Folder | Contents |
|--------|----------|
| `OHDSIMetadata/` | Source `meta_json-ld_{table_id}.json` files as fetched from gaiaCatalog (16 datasets) |
| `cdifMetadata/`  | Converted `cdif_{table_id}.json` — CDIF Discovery + Core conformant |
| `harvest_ohdsi_to_cdif.py` | The converter |

Run, from the root of this repository:

```bash
python OHDSI/harvest_ohdsi_to_cdif.py     # OHDSIMetadata/ -> cdifMetadata/
```

This directory was the separate `OHDSI` repository until 2026-10-09.

## What the converter does

The source documents are schema.org `Dataset` records authored with the default
vocabulary set to `https://schema.org/` (unprefixed tokens). CDIF needs explicit
`schema:` prefixes on `http://schema.org/` terms, `@type` as arrays, an explicit
`schema:identifier`, and a `schema:subjectOf` CatalogRecord carrying
`dcterms:conformsTo`. Key mappings:

- **Namespaces** — `https://schema.org/` default vocab → explicit `schema:`
  (`http://`) prefixes; `@type` normalized to arrays.
- **Identifier** — synthesized `schema:identifier` (PropertyValue) from the
  source `@id` (the stable GDSC detail URL) + `table_id`.
- **CatalogRecord** — a `schema:subjectOf` node (`schema:Dataset` +
  `schema:additionalType: dcat:CatalogRecord`) with `schema:about` (→ the
  dataset), a harvest/transformation **note** (`schema:description`),
  `schema:sdDatePublished`, and **`dcterms:conformsTo`** →
  `https://w3id.org/cdif/core/1.1` + `https://w3id.org/cdif/discovery/1.1`
  (+ `https://w3id.org/cdif/provenance/1.1` when provenance is emitted).
- **License** — carried through; when the source states none, the OGC nil value
  `http://www.opengis.net/def/nil/OGC/0/missing` is emitted so the CDIF
  license-or-conditionsOfAccess requirement is met.
- **spatialCoverage** — GeoNames `Place` refs kept; source bbox `GeoShape.polygon`
  rings converted to `schema:box` (`minLat minLon maxLat maxLon`), which the CDIF
  discovery SHACL accepts (polygon is not accepted).
- **measurementTechnique** — the verbose enumerated `DefinedTermSet` collapsed to
  the selected `DefinedTerm` (name + termCode).
- **additionalProperty** — a `schema:name` is derived from the `propertyID` URI
  (required by SHACL).
- **variableMeasured** — kept as `schema:PropertyValue` (discovery level, not
  `cdi:InstanceVariable`); stray whitespace in names/descriptions stripped and
  empty entries dropped.
- **isBasedOn** — reduced to pure `{@id}` references (upstream lineage links).
- **`about` (ETL events) → provenance** — each source doc has two `about`
  Events: a `"Pseudo Code"`/`"TODO"` placeholder (dropped) and a **real ETL
  action** naming the operation (`curl`/`wget`/`local`/`srtm_merge_and_crop`/…)
  with `object` (upstream source), `instrument` (tool), and `result` (output).
  The real action is mapped to a **cdifProv** `prov:wasGeneratedBy` activity
  (default on; `--no-provenance` to skip):

  | source `potentialAction` | cdifProv activity |
  |---|---|
  | `@type: Action` | `@type: ["prov:Activity","schema:Action"]` |
  | `name` | `schema:name` (`"ETL: <op>"`) |
  | `description` | `schema:description` |
  | `object` (source) | `schema:object` → `{@id}` ref to the source URL |
  | `instrument` (SoftwareApplication) | `prov:used[].schema:instrument` (a `schema:name` is derived from `featureList`, required by SHACL) |
  | `result` (output) | `schema:result` → `{@id}` ref to the generated dataset |

  `object`/`result` are emitted as `{@id}` references (not inline
  `schema:Dataset` nodes) so they don't collide with the root Dataset during
  discovery framing — matching the cdifProv example convention.

The source's `"Pseudo Code"` event is the natural home for
`schema:actionProcess` → `schema:HowTo`/`schema:step`, but it contains only
`"TODO"`, so no step methodology is emitted. Variables remain plain
`schema:PropertyValue`, so the document is **not** claimed as data_description.

## Validation status (16/16)

Validated with the tools in the `validation` (discovery) and `profile-provenance`
repositories, checked out side by side:

- **Discovery** — JSON Schema (`CDIFDiscoverySchema.json` via
  `tools/FrameAndValidate.py`): **16/16 pass**; SHACL
  (`ShaclValidation/CDIF-Discovery-Shapes.ttl`): **0 violations**.
- **Provenance** — JSON Schema (`cdifProvenanceStructuredSchema.json` via
  `profile-provenance/FrameAndValidate.py`): **16/16 pass**; SHACL
  (`profile-provenance/provenanceRules.shacl`): **0 violations**.

Remaining Warnings/Info in both profiles are advisory source-data-completeness
recommendations (non-URI `propertyID`s, missing variable descriptions, no
instrument category vocabulary) — the same profile as the CDIF repos' own
passing corpora.

```bash
# discovery — from the validation repo
python tools/FrameAndValidate.py <file> -v --schema CDIFDiscoverySchema.json --frame CDIF-frame-2026.jsonld
python ShaclValidation/ShaclJSONLDContext.py <file> ShaclValidation/CDIF-Discovery-Shapes.ttl
# provenance — from the profile-provenance repo
python FrameAndValidate.py <file> -v
python ../validation/ShaclValidation/ShaclJSONLDContext.py <file> provenanceRules.shacl
```

## Note on nested datasets

`tz_2022_nbs` in the source is a **container** directory whose actual dataset is
the nested `tz_2022_nbs_districts`; that nested record is the one harvested.

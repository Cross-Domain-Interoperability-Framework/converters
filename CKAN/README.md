# CKAN/ — CKAN package → CDIF

[`ckan_to_cdif.py`](ckan_to_cdif.py) converts a [CKAN](https://ckan.org/) dataset — the "package" the Action API returns from `package_show` — to CDIF JSON-LD. Each package becomes one record.

```bash
python CKAN/ckan_to_cdif.py "https://data.gov.ie/api/3/action/package_show?id=barony-boundaries" -o out/
python CKAN/ckan_to_cdif.py package.json --host data.gov.ie -o out/
```

- **`--host`** is the portal's domain. A package does not name its portal, so a file needs it; a URL supplies it.
- **`--catalog-name`** names the portal in the catalog record. The default is the domain.

A `package_show` response envelope (`{"success": true, "result": {…}}`) is unwrapped automatically, so the raw API URL works directly.

## Why the native package, and when not to use this

The native package dict is **universal** across CKAN installations. A portal also serves DCAT (DCAT-AP / DCAT-US / schema.org) **only when it runs [`ckanext-dcat`](https://github.com/ckan/ckanext-dcat)** — for those portals, fetching that DCAT and running [`../DCAT/dcat_to_cdif.py`](../DCAT/dcat_to_cdif.py) (which handles a DCAT-US `data.json` directly) is the richer path. This converter is the fallback that works everywhere.

## Three places a package carries metadata — all handled

1. **Base CKAN fields:** `title`, `notes`, `tags`, `resources`, `organization`, `license_*`, `author` / `maintainer`, `metadata_created` / `metadata_modified`, `url`.
2. **Promoted DCAT fields** a [scheming](https://github.com/ckan/ckanext-scheming) portal adds at the top level: `spatial` (a GeoJSON geometry), `temporal_start` / `time_period_coverage_start`, `issued` / `date_published`, `theme`, `subject`, `topic_category`, and the multilingual `title_translated` / `notes_translated` / `keywords` maps (`{lang: value}` — the preferred language, English first, is taken for a scalar; keyword maps are flattened).
3. **Extras**, a list of `{key, value}`, reached by the path `_extras.<key>`: the [`ckanext-spatial`](https://github.com/ckan/ckanext-spatial) keys (`bbox-west-long`/`-east-long`/`-south-lat`/`-north-lat`, `spatial`, `dataset-reference-date`, `responsible-party`) and whatever else a portal defines. Every extra no row maps becomes a `schema:additionalProperty`, so nothing is dropped; harvester bookkeeping (`harvest*`) is dropped.

## The record

- The `@id` is the DOI (`https://doi.org/…`, from `digital_object_identifier`) when present, otherwise the landing page `https://{host}/dataset/{name}`. `schema:identifier` carries the DOI (when present) and the CKAN id.
- **Spatial coverage** is a `schema:Place` with a `schema:GeoShape` box computed from the GeoJSON `spatial` geometry, or from the `bbox-*` extras.
- **Resources** with an `http(s)` URL become `schema:DataDownload`s (`format`/`mimetype` → `encodingFormat`, `size` bytes → `contentSize`).
- **Agents:** `author` → `schema:creator`; `maintainer` and `contact_name`/`contact_email` → `schema:contributor` Roles (`pointOfContact`); `organization` → `schema:publisher`. A name containing a space is written as a `schema:Person`, otherwise a `schema:Organization`.
- **License:** `license_title` / `license_id` / `license_url` → a `schema:CreativeWork`; `isopen` adds a `schema:conditionsOfAccess` note only when no licence is named.

## What maps to what

[`mappings/ckan-to-cdif.sssom.tsv`](../mappings/ckan-to-cdif.sssom.tsv) drives the converter; it is read at runtime.

- `subject_json_path` is a path from the package root: dot-separated member names, `[*]` for every item of an array. `_extras.<key>` reaches an extra.
- Each `transform` is a shaper `tf_<transform>` in the converter.
- Rows run in order: for a scalar target the first row to fill it wins; array targets accumulate.

| CKAN | CDIF |
|---|---|
| `title`, `notes` (+ `*_translated`) | `schema:name`, `schema:description` |
| `tags` | `schema:keywords` (DefinedTerm when a tag has a `vocabulary_id`) |
| `keywords`, `theme`, `subject`, `topic_category`, `groups` | `schema:keywords` |
| `license_title` / `license_id` / `license_url` | `schema:license` CreativeWork |
| `isopen` | `schema:conditionsOfAccess` (only if no licence) |
| `author`, `maintainer`, `contact_name`/`contact_email` | `schema:creator`, `schema:contributor` |
| `organization` | `schema:publisher` |
| `metadata_created`, `date_published`/`issued`, `metadata_modified` | `schema:dateCreated`, `schema:datePublished`, `schema:dateModified` |
| `time_period_coverage_start`/`_end`, `temporal_*` | `schema:temporalCoverage` |
| `spatial` (GeoJSON), `bbox-*` extras, `geographic_region` | `schema:spatialCoverage` |
| `resources` | `schema:distribution` |
| `url` | `schema:relatedLink` (homepage) |
| every other extra | `schema:additionalProperty` |
| `id` / `name` / `digital_object_identifier` | `@id` and `schema:identifier` |
| `state`, `private`, counts, revisions, tracking, relationships | Not converted |

## Examples

- [`examples/`](examples/) holds packages fetched by [`../harvesters/harvest_ckan_examples.py`](../harvesters/harvest_ckan_examples.py) from a few portals (a plain CKAN demo, bilingual Open Government Canada, INSPIRE-style data.gov.ie, the Humanitarian Data Exchange). `examples/INDEX.json` records each one's URL and retrieval date.
- `python CKAN/build_cdif_examples.py` converts them to [`cdifJSON-LD/`](cdifJSON-LD/) and validates each record against the JSON Schema and SHACL of every profile it claims, read from `../metadataBuildingBlocks`.

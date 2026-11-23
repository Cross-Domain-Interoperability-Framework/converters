# STAC/ — STAC Collection → CDIF

[`stac_to_cdif.py`](stac_to_cdif.py) converts a [STAC](https://stacspec.org/) 1.x Collection to CDIF JSON-LD. Each Collection becomes one record, which describes it as a `schema:Dataset`.

The converter does not read Items. A Collection already describes the dataset: its extent, licence, providers and keywords. An Item describes one scene or file, and usually has no licence or provider of its own.

```bash
python STAC/stac_to_cdif.py collection.json -o out/ --source-url "https://data.geo.admin.ch/api/stac/v1/collections/ch.bafu.bundesinventare-bln"
python STAC/stac_to_cdif.py "https://data.geo.admin.ch/api/stac/v1/collections/ch.bafu.bundesinventare-bln" -o out/
```

- **`--source-url`** is the URL the file was fetched from. Relative links are resolved against it. It is also the record's `@id` when the Collection has no DOI and no self link.
- **`--catalog-name`** and **`--catalog-url`** name the STAC catalog the Collection is in.

## The record

- The `@id` and `schema:identifier`:
  - With a `sci:doi` (scientific extension), the identifier is the DOI as a PropertyValue, and the `@id` is its `https://doi.org/` URL.
  - Without a DOI, the identifier is the Collection `id`, and the `@id` is the Collection's `self` link.
  - A `sci:doi` that is also listed under `sci:publications` belongs to a paper, so it is not used as the record's identifier.
- `schema:url` is an HTML `alternate` link, such as a STAC Browser page. Without one, it is the `self` link.
- **Providers** are mapped by their role:
  - `producer`: an Organization in the `schema:creator` `@list`, in the order the providers are listed;
  - `host`: `schema:provider`;
  - `licensor` and `processor`: a `schema:Role` contributor with that `roleName`.
- **Assets:**
  - A data asset becomes a `schema:DataDownload` under `schema:distribution`. An asset is a data asset when it has no roles, or one of the roles `data`, `archive`, `default`, `visual`, `overview`, `stac-items` or `collection-mirror`.
  - Every other asset (thumbnail, metadata, documentation, style) becomes a `schema:relatedLink`, named by its first role.

## What maps to what

[`mappings/stac-to-cdif.sssom.tsv`](../mappings/stac-to-cdif.sssom.tsv) drives the converter; it is read at runtime.

- `subject_json_path` is a path from the Collection root, made of member names separated by dots:
  - `*` takes every member of an object;
  - `[*]` takes every item of an array;
  - `[?key=a|b]` takes the array items whose `key` is `a` or `b`, or, for a list such as `roles`, contains one of them.
- Each `transform` is a shaper `tf_<transform>` in the converter.
- Rows run in order: for a scalar target the first row to fill it wins; array targets accumulate.

| STAC | CDIF |
|---|---|
| `title`, `description` | `schema:name`, `schema:description` (CommonMark, kept as written) |
| `sci:doi` | `schema:identifier` and the `@id` |
| `keywords` | `schema:keywords` |
| `themes` concepts | `schema:keywords`: DefinedTerms in their scheme |
| `summaries` `platform`, `constellation`, `instruments` | `schema:keywords`: DefinedTerms in the set `STAC platform` (and so on) |
| `license`: an SPDX id | `schema:license` `{"@id": "https://spdx.org/licenses/<id>"}`. The id is matched ignoring case and punctuation, so `cc-by-4.0`, `CC BY-SA 4.0` and `nlod_2_0` are all recognised. |
| `license`: another value | `schema:conditionsOfAccess` `License: <value>`. `proprietary`, `other` and `various` name no licence, so they are not written. |
| `rel=license` links | `schema:license`, a CreativeWork |
| `gee:terms_of_use` | `schema:conditionsOfAccess` |
| `providers` | `schema:creator`, `schema:provider` and `schema:contributor`, by role (above) |
| `contacts` (contacts extension) | `schema:contributor`: Role `pointOfContact`, with a Person or an Organization |
| `updated`, `created`, `version` | `schema:dateModified`, `schema:dateCreated`, `schema:version` |
| `extent.spatial.bbox` | `schema:spatialCoverage`: one Place per box, with a GeoShape box (S W N E). A repeated box is dropped, and a 3D box loses its heights. |
| `extent.temporal.interval` | `schema:temporalCoverage`: ISO 8601 `start/end`, with `..` for an open end. An interval whose start and end are equal becomes a single instant, and `[null, null]` is dropped. |
| `assets` | `schema:distribution` or `schema:relatedLink` (above) |
| Links `describedby`, `about`, `related`, `cite-as`, `via`, `alternate`, `items`, `derived_from`, `source`, version links, `preview`, `thumbnail` | `schema:relatedLink`: a LinkRole whose `linkRelationship` is the link's relation |
| `sci:publications`, `sci:citation` | `schema:citation` (CreativeWorks), `schema:creditText` |
| `item_assets`, the other summaries (bands, gsd, projection, …), `crs`, `stac_extensions`, `table:columns`; navigation links (`root`, `parent`, `child`, `item`, queryables) | Not converted |

## Sentinels

| Missing | Written |
|---|---|
| `updated` | `schema:dateModified` `1900-01-01` |
| `title` | The Collection `id` |
| A licence: `license` is `proprietary`, `other` or `various`, and there is no `license` link | `schema:license` is the OGC nil URI `http://www.opengis.net/def/nil/OGC/0/missing` |

## Examples and the build

[`../harvesters/harvest_stac_examples.py`](../harvesters/harvest_stac_examples.py) collected `examples/`: 50 Collections from 28 of the public catalogs that [STAC Index](https://stacindex.org/) lists, both STAC APIs and static catalogs, with at most 2 from each. 26 are STAC 1.0.0 and 23 are 1.1.0. `examples/INDEX.json` gives each Collection's request URL, its catalog and the date it was retrieved.

```bash
python harvesters/harvest_stac_examples.py --target 50
python STAC/build_cdif_examples.py
```

The build converts each Collection to `cdifJSON-LD/`:
- the request URL is the base for relative links;
- the catalog it came from is the record's `includedInDataCatalog`;
- the retrieval date dates the catalog record.

It then validates each record against every profile it claims, using the profile's `resolvedSchema.json` and SHACL bundle from the metadataBuildingBlocks checkout beside this repository. This is the same check as the other table-driven builds. The results are in `cdifJSON-LD/INDEX.json`, and the output is deterministic.

All 50 records pass, and all claim core and discovery.
- **Placeholders:** 35 have no `updated` date, so they get the `1900-01-01` placeholder. 12 name no licence, so they get the nil licence.
- **Distributions:** 13 have data assets, and so distributions.

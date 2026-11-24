# Socrata/ — Socrata view → CDIF

[`socrata_to_cdif.py`](socrata_to_cdif.py) converts a [Socrata](https://dev.socrata.com/) asset's native metadata to CDIF JSON-LD. The input is the "view" that every Socrata portal serves at `https://{host}/api/views/{id}.json`. Each view becomes one record.

```bash
python Socrata/socrata_to_cdif.py "https://data.wa.gov/api/views/f6w7-q2d2.json" -o out/
python Socrata/socrata_to_cdif.py view.json --host data.wa.gov -o out/
```

- **`--host`** is the portal's domain. A view does not name its portal, so a file needs it; a URL supplies it.
- **`--catalog-name`** names the portal in the catalog record. The default is the domain.

## Why the native view

A Socrata portal publishes the same assets in three forms:

| Source | Schema | Columns |
|---|---|---|
| `/api/views/{id}.json` | Socrata's own | Yes: name, type, description, cached statistics |
| `/data.json` | DCAT-US 1.1 (Project Open Data) | No |
| The asset's web page | schema.org `Dataset` JSON-LD | No |

Only the view describes the columns, and only the view carries the portal's own metadata fields. The other two forms go through the [DCAT](../DCAT/) and [SOSO](../soso/) converters.

## The record

- The `@id` is the portal's permalink, `https://{host}/d/{id}`. `schema:identifier` is the four-by-four id.
- **A tabular view** (`viewType` `tabular`) gets:
  - one `cdi:InstanceVariable` per column under `schema:variableMeasured`;
  - its full CSV export, `/api/views/{id}/rows.csv`, as a `cdi:TabularTextDataSet` with one physical mapping per column;
  - its query endpoint, `/resource/{id}.{format}`, as a `schema:WebAPI` with one `schema:SearchAction`. The endpoint names columns by API field name and returns 1,000 rows unless `$limit` is given.
- **Columns** are those of the CSV export, in its order. Hidden columns and the `:@computed_region_…` columns the portal adds are left out.
  - `schema:name` is the display name, which heads the column in the export. `schema:alternateName` is the API field name.
  - `cdif:physicalDataType` is the XSD type of the value as the export writes it. Geometry columns (`point`, `location`, `polygon`, …) are written as WKT text, so they are `xsd:string`.
- **Statistics** come from each column's `cachedContents`, which the portal computes at a time the view does not state:
  - `count`, `non_null` and `null` are one `cdi:Statistics` `count`, with `cdi:computationBase` `Total`, `ValidOnly` and `MissingOnly`;
  - `top`, the most frequent values, are its `cdi:CategoryStatistics`;
  - `cardinality` is a `cdi:Statistics` `distinct count`;
  - `smallest` and `largest` of a numeric column are `schema:minValue` and `schema:maxValue`. Those of a text or date column go in the collection's `schema:description`, labelled, because a `cdi:Statistic` value is a number;
  - when every column gives the same `count`, it is the dataset's `row count` under `cdif:statistics`.
- **The row identifier** (`rowIdentifierColumnId`) is the `cdif:hasPrimaryKey`, and its variable has `cdif:role` `UnitIdentifier`.
- **Agents:**
  - `schema:creator` lists the `attribution` organisation, then the `tableAuthor` account unless it has the same name;
  - `owner`, the account that owns the asset on the portal and can edit it, is `schema:maintainer` of the catalog record;
  - an account is a `schema:Person`, or a `schema:Organization` when its type is `team`, with the account id as `schema:identifier`. Many accounts are shared or service accounts, so the type is not reliable.
- **A file asset** (`blobId`) gets the file as a `schema:DataDownload`.
- **An external-link asset** gets one `schema:DataDownload` per URL of its access points.

## Custom fields

Each portal defines its own metadata fields, in groups, under `metadata.custom_fields`. For example, CDC has `Common Core` / `Contact Name`, and NYC has `Update` / `Update Frequency`.

- A table row with the path `metadata.custom_fields.*.<field>` maps that field in whatever group a portal puts it. Mapping another portal's field is one new row.
- Every field that no row maps becomes a `schema:additionalProperty` named `<group>: <field>`, so nothing is dropped.
- A mapped field whose value the shaper cannot use (a `Temporal Applicability` that is not an ISO 8601 interval, a `Homepage` that is not a URL) is kept as an additional property.

## What maps to what

[`mappings/socrata-to-cdif.sssom.tsv`](../mappings/socrata-to-cdif.sssom.tsv) drives the converter; it is read at runtime.

- `subject_json_path` is a path from the view root, made of member names separated by dots. A name may contain spaces. `*` takes every member of an object, and `[*]` every item of an array.
- Each `transform` is a shaper `tf_<transform>` in the converter.
- Rows run in order: for a scalar target the first row to fill it wins; array targets accumulate.

| Socrata view | CDIF |
|---|---|
| `name`, `description` | `schema:name`, `schema:description` (HTML markup removed) |
| `id` | `schema:identifier` |
| `tags` | `schema:keywords` |
| `category` | `schema:keywords`: a DefinedTerm in the set `{host} categories` |
| `license` with a `termsLink` | `schema:license`: a CreativeWork |
| `license` without one, or `licenseId` alone | `schema:conditionsOfAccess` `License: <name>` |
| `attribution`, `attributionLink` | `schema:creator`: an Organization |
| `tableAuthor` | `schema:creator`, after the attribution |
| `owner` | `schema:maintainer` of the catalog record |
| `createdAt`, `publicationDate` | `schema:dateCreated`, `schema:datePublished` |
| `rowsUpdatedAt`, else `viewLastModified` | `schema:dateModified` |
| `viewLastModified` | `schema:dateModified` of the catalog record |
| `metadata.attachments` | `schema:relatedLink`: LinkRole `attachment` |
| `columns` | `schema:variableMeasured`, with statistics; the CSV and SODA `schema:distribution`s; `cdif:statistics` (above) |
| `rowIdentifierColumnId` | `cdif:hasPrimaryKey` |
| `metadata.rowLabel` | `schema:additionalProperty` `Row label` |
| `blobId`, `metadata.accessPoints` | `schema:distribution` (above) |
| Custom fields `Publisher`, `Nombre de la Entidad` | `schema:publisher` |
| Custom fields `Contact Name`, `Contact Email` | `schema:contributor`: Role `pointOfContact` |
| Custom field `Issued` | `schema:datePublished`, ahead of `publicationDate` |
| Custom field `Temporal Applicability` | `schema:temporalCoverage` |
| Custom fields `Geographic Coverage`, `Geographic Name`, `Cobertura Geográfica` | `schema:spatialCoverage`: a named Place |
| Custom fields `Suggested Citation`, `Homepage` | `schema:creditText`, `schema:relatedLink` |
| Every other custom field | `schema:additionalProperty` |
| `provenance`, view and download counts, ratings, approvals, grants, query and display settings | Not converted |

## Examples

- [`examples/`](examples/) holds views fetched by [`harvesters/harvest_socrata_examples.py`](../harvesters/harvest_socrata_examples.py): the most viewed datasets, files and external links of the portals it lists. `examples/INDEX.json` records each one's URL and retrieval date.
- `python Socrata/build_cdif_examples.py` converts them to [`cdifJSON-LD/`](cdifJSON-LD/) and validates each record against the JSON Schema and SHACL of every profile it claims, read from `../metadataBuildingBlocks`.

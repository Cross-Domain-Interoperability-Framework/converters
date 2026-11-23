# DublinCore/ — Dublin Core → CDIF

[`dc_to_cdif.py`](dc_to_cdif.py) converts Dublin Core records to CDIF JSON-LD. It reads the two encodings that metadata services publish:

| Encoding | Where it comes from | What it adds to plain `dc:` |
|---|---|---|
| `csw:Record` | OGC CSW 2.0.2 catalogues (GeoNetwork and others), `outputSchema=csw:Record` | `dct:abstract`, `dct:modified`, `dct:alternative`, an `ows:BoundingBox`, and `dc:URI` links with a protocol |
| `oai_dc:dc` | Any OAI-PMH repository, `metadataPrefix=oai_dc` | Nothing: the fifteen simple Dublin Core elements |

A record can be on its own or inside a CSW `GetRecords` / `GetRecordById` response or an OAI-PMH `ListRecords` / `GetRecord` response. Each record becomes one CDIF record.

```bash
python DublinCore/dc_to_cdif.py record.xml -o out/
python DublinCore/dc_to_cdif.py getrecords-response.xml -o out/ --catalog-name "Swiss geocat" --catalog-url https://www.geocat.ch/
python DublinCore/dc_to_cdif.py oai_dc.xml -o out/ --identifier oai:example.org:123
```

- **`--identifier`** is the record's identifier in its service, used when the record has no usable `dc:identifier`.
- **`--landing-url`** is `schema:url` when no identifier is a web page.
- **`--record-url`** is the catalog record's `@id`, such as the CSW `GetRecordById` request.
- **`--catalog-name`** and **`--catalog-url`** become `schema:includedInDataCatalog`.

## What maps to what

[`mappings/dc-to-cdif.sssom.tsv`](../mappings/dc-to-cdif.sssom.tsv) drives the converter; it is read at runtime.

- `subject_xpath` is an XPath from the record element, with the `dc:`, `dct:` and `ows:` prefixes. One row serves both encodings.
- Each `transform` is a shaper `tf_<transform>` in the converter.
- Rows run in order: for a scalar target the first row to fill it wins; array targets accumulate.

Dublin Core names no identifier scheme, vocabulary or role, so the shapers read those from the values.

| Dublin Core | CDIF |
|---|---|
| `identifier` | `@id`, from the best identifier. In order of preference: a DOI (`https://doi.org/…`), an http(s) URL, a Handle or ARK (resolver URL), a UUID (`urn:uuid:`), a URI as written (`urn:`, `oai:`), anything else (`urn:dc:…`). It is also `schema:identifier`, a PropertyValue naming the scheme. A value with spaces (a citation) is not an identifier. |
| Other `identifier`s | `schema:sameAs` |
| A non-DOI web identifier, else the DOI | `schema:url` |
| `title`; further titles and `dct:alternative` | `schema:name`; `schema:alternateName` |
| `creator` | `schema:creator`, an ordered `@list` (agents below) |
| `contributor` | `schema:contributor`; Dublin Core gives no role |
| `publisher` | `schema:publisher`, an Organization |
| `dct:modified`; else `Updated:` dates; else the latest `date` | `schema:dateModified` |
| `Issued:` dates; else the earliest `date` | `schema:datePublished` |
| `Created:` and `Collected:` dates | `schema:dateCreated`, `schema:temporalCoverage` |
| `dct:abstract`, `description` | `schema:description`, distinct texts joined |
| `subject` | `schema:keywords`, as strings |
| `type` | `schema:additionalType`: a URI (`info:eu-repo/semantics/…`) as an IRI reference, a label (`dataset`) as a string |
| `format`, `language` | `schema:encodingFormat`; `schema:inLanguage` (the first language, since CDIF's is a single string) |
| `rights` that is a URL, or a licence URL in the text | `schema:license` |
| Other `rights` text | `schema:conditionsOfAccess`. This covers licence names, OpenAIRE access rights and csw:Record's ISO restriction codes. |
| `coverage` that is a date or period | `schema:temporalCoverage` (`(Time Period) From 1922 - 1974` becomes `1922/1974`) |
| Other `coverage` | `schema:spatialCoverage`, named Places. Eras (`18th dynasty`) and bare numbers are dropped. |
| `ows:BoundingBox` | `schema:spatialCoverage`: a box, or a point. The CRS decides the axis order: an EPSG URN (`urn:ogc:def:crs:EPSG::4326`, as GeoNetwork writes) has latitude first; `ows:WGS84BoundingBox` and CRS84 have longitude first. |
| `relation` that is a URL or DOI | `schema:relatedLink` LinkRoles (`relation`) |
| `source` | `prov:wasDerivedFrom` |
| `dc:URI` with a download protocol | `schema:distribution` DataDownloads, with the link's name and description |
| Other `dc:URI` | `schema:relatedLink` LinkRoles whose linkRelationship is the protocol (`WWW:LINK`, `OGC:WMS`) |

**Agents.** A creator is only a name, and the FGDC converter's rule applies:
- "Family, Given" or "Family, Initials" is a Person.
- So are two to four capitalised words with no organisation word.
- Anything else is an Organization.
- An ORCID written after the name (`Visbeck, M. <https://orcid.org/…>`) makes a Person with that identifier.

**Labelled dates.** Some OAI-PMH servers write DataCite date types into `dc:date` (`Updated: 2024-01-05T…`). A labelled date goes to its own property; only unlabelled dates count as Dublin Core's plain date.

## Sentinels

| Missing | Written |
|---|---|
| A date with year and month | A year alone is padded to `YYYY-01-01` for `dateModified`. CDIF needs at least a year and month. |
| Any date | `schema:dateModified` `1900-01-01`. Most GeoNetwork `csw:Record`s carry no date. |
| A description | The name |
| Rights | `schema:license` is the OGC nil URI `http://www.opengis.net/def/nil/OGC/0/missing` |
| A web page among the identifiers | `--landing-url`, else the same nil URI |

## Examples and the build

`examples/csw_record/` holds 50 `csw:Record`s from 11 CSW catalogues, all GeoNetwork; `examples/oai_dc/` holds 50 `oai_dc` records from 49 OAI-PMH repositories. [`../harvesters/harvest_format_examples.py`](../harvesters/harvest_format_examples.py) harvested them; each folder's `INDEX.json` gives every record's source.

```bash
python DublinCore/build_cdif_examples.py
```

The build converts each record to `cdifJSON-LD/csw_record/` or `cdifJSON-LD/oai_dc/`:
- The repository or catalogue is the catalog.
- For a `csw:Record`, the catalog record's `@id` is the CSW `GetRecordById` request for it.
- A GeoNetwork record page (`…/catalog.search#/metadata/<uuid>`) is the landing page.

It then validates each record against every profile it claims, using the profile's `resolvedSchema.json` and SHACL bundle from the metadataBuildingBlocks checkout beside this repository. This is the same check as the other table-driven builds. The results are in `cdifJSON-LD/INDEX.json`, and the output is deterministic.

All 100 records pass. Of the 50 `csw:Record`s, 33 claim core and discovery (the ones with a bounding box) and 17 claim core. Of the 50 `oai_dc` records, 7 claim core and discovery and 43 claim core.

Simple Dublin Core carries little that CDIF's discovery profile looks for: spatial and temporal coverage are rare, and there are no variables or distributions.

# EML/ — Ecological Metadata Language → CDIF

[`eml_to_cdif.py`](eml_to_cdif.py) converts EML documents (`<eml:eml>`, EML 2.0.1 to 2.2.0) to CDIF JSON-LD, one record per document. GBIF, DataONE member nodes and LTER sites publish EML.

A `dataTable` with an `attributeList` becomes a tabular distribution with one variable per attribute, so a DataONE package can reach CDIF data_description.

```bash
python EML/eml_to_cdif.py eml.xml -o out/
python EML/eml_to_cdif.py eml.xml -o out/ --record-url https://api.gbif.org/v1/dataset/<key>/document --catalog-name GBIF --catalog-url https://www.gbif.org/
python EML/eml_to_cdif.py eml.xml -o out/ --landing-url https://search.dataone.org/view/<id>
```

- **`--record-url`** is the URL of the EML document itself. It is the catalog record's `@id`; without it, the record is `<@id>#metadata`.
- **`--landing-url`** is the dataset's page in its repository. It is `schema:url` when the EML names no information URL.
- **`--catalog-name`** and **`--catalog-url`** become `schema:includedInDataCatalog`.

## What maps to what

[`mappings/eml-to-cdif.sssom.tsv`](../mappings/eml-to-cdif.sssom.tsv) drives the converter; it is read at runtime.

- `subject_xpath` is an XPath from the `<eml:eml>` element. EML's child elements have no namespace, so one table serves every EML version.
- Each `transform` is a shaper `tf_<transform>` in the converter.
- Rows run in order: for a scalar target the first row to fill it wins; array targets accumulate.

| EML | CDIF |
|---|---|
| A DOI `alternateIdentifier`, else the `packageId` | `@id` (`https://doi.org/…`; a GBIF key as `https://www.gbif.org/dataset/…`; otherwise `urn:eml:…`), and `schema:identifier` |
| Other `alternateIdentifier`s | `schema:sameAs` |
| The `information` URL, else the DOI, else the GBIF page | `schema:url` |
| `title`; further titles and `shortName` | `schema:name`; `schema:alternateName` |
| `creator` | `schema:creator`, an ordered `@list` |
| `publisher` | `schema:publisher` |
| `contact`, `associatedParty`, project `personnel` | `schema:contributor` Roles: `contact`, or the party's EML role |
| `metadataProvider` | The catalog record's `schema:maintainer` |
| Project `funding` | `schema:funding` MonetaryGrants |
| `pubDate` | `schema:datePublished` and `schema:dateModified` |
| GBIF `dateStamp` | The catalog record's `schema:dateModified` |
| `language` | `schema:inLanguage` |
| `abstract`, `purpose`, `additionalInfo`, project title, abstract, study area and design | `schema:description`: the abstract first, the rest labelled |
| `keywordSet` | `schema:keywords`: DefinedTerms in the set's thesaurus, or strings when it has none |
| `taxonomicCoverage` | `schema:keywords`: a DefinedTerm per taxon, with its rank and common names |
| `licensed` (EML 2.2), a licence linked in `intellectualRights` | `schema:license` CreativeWorks |
| `intellectualRights` text | `schema:conditionsOfAccess` |
| `geographicCoverage` | `schema:spatialCoverage` Places: a box, or a point when the box has no extent |
| `temporalCoverage` | `schema:temporalCoverage`: `begin/end` intervals and single dates |
| `methodStep`, `sampling`, `qualityControl` | `prov:wasGeneratedBy` cdifProv Actions |
| GBIF `citation` | `schema:creditText` |
| GBIF `bibliography` | `schema:relatedLink` LinkRoles to the publications that have a DOI or URL |
| `distribution` download URLs | `schema:distribution` DataDownloads |
| `dataTable` | A distribution typed `schema:DataDownload` and `cdi:TabularTextDataSet` (below) |
| `dataTable` attributes | `schema:variableMeasured` `cdi:InstanceVariable`s (below) |
| `maintenance`, attribute code lists and missing-value codes, `access` | Not converted |

**Agents.** An `individualName` makes a Person: the name is "surName, givenName", the `organizationName` is the affiliation and the `positionName` is the jobTitle. Otherwise the agent is an Organization. A party with only a `positionName` is a Person named by the position, as in the ISO converter.
- `electronicMailAddress`, `phone` and `onlineUrl` become `schema:email`, `schema:telephone` and `schema:url`.
- A `userId` is the identifier. An ORCID becomes an identifiers.org orcid PropertyValue.
- A `<references>` element is followed to the party it names; NEON uses them.

**Data tables.**
- The distribution's `contentUrl` is the table's `physical/distribution` URL, its `name` the `entityName`, and its `description` the `entityDescription`.
- A `simpleDelimited` text format gives `csvw:delimiter`, `csvw:quoteChar` and `csvw:headerRowCount` (from `numHeaderLines`), plus `text/csv` for a comma delimiter or `text/tab-separated-values` for a tab.
- Each attribute has a `cdif:PhysicalMapping` in column order, indexed from 0.

**Variables.**
- Each attribute's `attributeName` is the variable's name, `attributeLabel` its alternateName, `attributeDefinition` its description, and the standard or custom unit its `unitText`.
- The type comes from the measurement scale and storage type:
  - A `dateTime` scale is `xsd:date` or `xsd:dateTime` only when its `formatString` is ISO 8601 (`YYYY-MM-DD`). Otherwise (`mm/dd/yyyy`) it is `xsd:string`, whatever `storageType` says.
  - Otherwise an XSD-named `storageType` is used as is.
  - Otherwise the `numberType` of an interval or ratio scale gives the type (real → `xsd:decimal`, integer → `xsd:integer`).
  - Anything else is `xsd:string`.
- Variables are identified `#<attributeName>`, prefixed `#t<n>/` when the document has several tables.

## Sentinels

| Missing | Written |
|---|---|
| A full `pubDate` | A year alone is padded to `YYYY-01-01` for `dateModified`. CDIF needs at least a year and month. |
| Any `pubDate` | `schema:dateModified` `1900-01-01`. GBIF's `dateStamp` is when GBIF wrote the document, so it dates the catalog record, not the dataset. |
| A description | The name |
| A licence and a rights statement | `schema:license` is the OGC nil URI `http://www.opengis.net/def/nil/OGC/0/missing` |
| A landing page | `schema:url` is the same nil URI |
| A data table's URL | Its `contentUrl` is the same nil URI |

Empty and placeholder values (`N/A`, `NA`, `none`) are skipped as funding and as a keyword thesaurus. GBIF's IPT writes them.

## Examples and the build

`examples/` holds 50 EML documents. [`../harvesters/harvest_format_examples.py`](../harvesters/harvest_format_examples.py) harvested them: 46 from the GBIF registry (checklists, occurrence and sampling-event datasets) and 4 from DataONE member nodes (LTER, NEON, PISCO, TERN). `examples/INDEX.json` gives each document's source.

```bash
python EML/build_cdif_examples.py
```

The build converts each document to `cdifJSON-LD/`:
- The request URL from `INDEX.json` is the catalog record's `@id`.
- GBIF, or the DataONE member node, is the catalog.
- A DataONE document's `search.dataone.org` page is its landing page.

It then validates each record against every profile it claims, using the profile's `resolvedSchema.json` and SHACL bundle from the metadataBuildingBlocks checkout beside this repository. This is the same check as the NIAID, ISO, SDMX and DataCite builds. The results are in `cdifJSON-LD/INDEX.json`, and the output is deterministic.

All 50 records pass. Three DataONE documents (LTER, NEON, PISCO) describe data tables and claim data_description, with 45 variables between them. Every record claims core; 35 also claim discovery and 19 provenance.

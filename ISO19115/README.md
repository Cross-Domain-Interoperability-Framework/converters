# ISO19115/ — ISO 19115 XML ↔ CDIF

[`iso_to_cdif.py`](iso_to_cdif.py) converts ISO 19115 metadata XML to CDIF JSON-LD. It reads both XML encodings of ISO 19115:

| Encoding | Root element | Mapping table |
|---|---|---|
| ISO 19115-3 | `mdb:MD_Metadata` | [`mappings/iso19115-3-to-cdif.sssom.tsv`](../mappings/iso19115-3-to-cdif.sssom.tsv) |
| ISO 19139 | `gmd:MD_Metadata` | [`mappings/iso19139-to-cdif.sssom.tsv`](../mappings/iso19139-to-cdif.sssom.tsv) |
| ISO 19115-2 in the 19139 encoding | `gmi:MI_Metadata` | the 19139 table |

The converter picks the encoding from the root element. The input can be a single record or a CSW `GetRecords` / `GetRecordById` response. A response yields one output file per record.

```bash
python ISO19115/iso_to_cdif.py record.xml -o out/
python ISO19115/iso_to_cdif.py csw-response.xml -o out/ --catalog-name "GA eCat" --catalog-url https://ecat.ga.gov.au/
python ISO19115/iso_to_cdif.py record.xml -o out/ --validate
```

## How the tables drive it

The tables are read at runtime: a mapping change is a table edit. Each row has these columns:

- **`subject_xpath`**: an XPath 1.0 expression from the metadata root. It selects the ISO elements the row reads, and may use role-code and date-type predicates and `|` unions.
- **`object_json_path`**: where the value lands, either `$.<property>` on the dataset or `$.schema:subjectOf.<property>` on the catalog record.
- **`transform`**: the shaper in `iso_to_cdif.py` (`tf_<transform>`) that builds the value. The tables' `.sssom.yml` sidecars list them all.

Rows run in file order. For a scalar target the first row to fill it wins; array targets accumulate.

The two tables have the same rows in the same order. They differ only in their XPaths, and in `subject_id` / `subject_class`, which name the element in that encoding's namespace. Keep them aligned.

The shapers read elements by local name, which the two encodings share for nearly every class. The one exception is the responsible party: `CI_ResponsibleParty` in 19139 versus `CI_Responsibility` with `party` in 19115-3. `_parties()` handles both.

## Where ISO has no CDIF property

Where ISO has no CDIF property, the converter follows two sources of conventions:
- the **GeoNetwork DDE conventions**, from core-geonetwork's `iso19115-3.2018` `formatter/cdif` and `convert/fromJsonCdif.xsl`;
- the **DDE building blocks** ([usgin/ddeBuildingBlocks](https://usgin.github.io/ddeBuildingBlocks/)).

| ISO | CDIF |
|---|---|
| `supplementalInformation` sections `FUNDING:`, `MEASUREMENT TECHNIQUE:`, `PUBLISHING PRINCIPLES:` | `schema:funding`, `schema:measurementTechnique`, `schema:publishingPrinciples`. The rest of the text is appended to the description. |
| An identifier whose `codeSpace` is `sameAs` | `schema:sameAs`. The primary identifier is the first identifier with any other codeSpace. |
| An online resource whose linkage is the OGC nil URL `…/nil/OGC/0/inapplicable` | An archive member (`schema:hasPart`) of the distribution before it |
| A metadata contact, or a distributor | `subjectOf/schema:maintainer`, or `schema:provider` |
| Spatial representation type, resolution, reference system | `dde:spatialRepresentationType`, `dde:spatialResolution`, `dde:referenceSystemIdentifier`, `dde:referenceSystemType` PropertyValues (ddeGeographicDataset) |
| `EX_VerticalExtent` | A `dde:verticalExtent` PropertyValue: QuantitativeValue min/max, with the vertical CRS as `valueReference` ([usgin/ddeBuildingBlocks#12](https://github.com/usgin/ddeBuildingBlocks/pull/12)) |
| Topic category; resource scope | DefinedTerms in `dde:codelist/TopicCategoryCode` and `dde:codelist/ResourceTypeCode` (ddeCore). Mappings: `series` → `collection`; a dataset with a bounding box → `geographicDataset`. |
| Acquisition instruments and platforms (19115-2 `gmi:`, 19115-3 `mac:`) | One acquisition Action whose instruments are typed `dde:sensorType` / `dde:platform`, plus the Wikidata measuring-instrument term (ddeImagery, ddeActivity) |
| Service identification | `schema:WebAPI` (added to `@type`) with `serviceType`, `potentialAction`, `schema:dataset` (operatesOn) and `termsOfService` (DDEWebAPI) |

**Lineage.** The lineage statement and each process step become `prov:wasGeneratedBy` cdifProv Actions, with the text as `schema:actionProcess`. A step's sources become its `schema:object`. Lineage sources become `prov:wasDerivedFrom`: a labelled link when the source has an address, text otherwise.

## Sentinels

Where the source lacks something CDIF requires, the converter writes a sentinel:

| Missing | Written |
|---|---|
| A revision date | The publication date, else the creation date, else the metadata date, else `1900-01-01` |
| A year-only `dateModified` (`1886`) | A fuller date from the record if there is one, else the year padded to `YYYY-01-01`. CDIF needs at least a year and month. |
| A licence and any access constraint | `schema:license` is the OGC nil URI `http://www.opengis.net/def/nil/OGC/0/missing` |
| A service's operations | One `schema:Action` named `missing`, whose `urlTemplate` is the OGC nil URL |
| An operation's connect point, or its name | The OGC nil URL, or `missing` |
| A service's access properties | `termsOfService` `"not specified"`, the CDIF webAPI block's own default |

Four fixes keep values valid:
- A date-time with no time zone is taken as UTC.
- A keyword code shorter than three characters (SeaDataNet's `13`) is also the term's `termCode`.
- A codelist element's text (an EDMO code's organisation name) is preferred to its `codeListValue`.
- A polygon's bounding box goes in `schema:geo`, with the polygon itself kept as geosparql WKT.
- A process step's sources are `schema:Thing`s, not CreativeWorks. CDIF's rule for a CreativeWork source requires a url, which lineage sources rarely have.

## Examples and the build

`examples/iso19115-3/` and `examples/iso19139/` hold the source records. [`../harvesters/harvest_format_examples.py`](../harvesters/harvest_format_examples.py) harvested them; each folder's `INDEX.json` gives every file's endpoint and request. The sources are:
- **ISO 19115-3:** GeoNetwork CSWs, including Geoscience Australia, AODN/IMOS and BGS.
- **ISO 19139 / 19115-2:** NOAA NCEI web-accessible folders, PANGAEA's OAI-PMH, DataONE, and the CSWs.

```bash
python ISO19115/build_cdif_examples.py
```

The build converts every example to `cdifJSON-LD/<encoding>/`, then validates each record against every profile it claims. Each check uses the profile's `resolvedSchema.json` and its SHACL bundle, both from the metadataBuildingBlocks checkout beside this repository; it is the same check as the NIAID build. The results are in `cdifJSON-LD/INDEX.json`.

## CDIF → ISO 19115-3

[`cdif_to_iso.py`](cdif_to_iso.py) goes the other way: it converts a CDIF JSON-LD record to one ISO 19115-3 `mdb:MD_Metadata`. Its mapping is a Python port of the DDE GeoNetwork's `convert/fromJsonCdif.xsl` (the `iso19115-3.2018` schema plugin), written as the table [`mappings/cdif-to-iso19115-3.sssom.tsv`](../mappings/cdif-to-iso19115-3.sssom.tsv), which is read at runtime.

```bash
python ISO19115/cdif_to_iso.py record.jsonld -o out/
python ISO19115/cdif_to_iso.py DataCite/cdifJSON-LD/ -o out/ --xsd path/to/iso19115-3.2018/schema.xsd
```

- **`subject_json_path`** names the CDIF values, as a path from the record root.
  - Steps are property names, as CDIF writes them, separated by dots: `schema:subjectOf.dcterms:conformsTo`.
  - `[*]` takes every item, and `@id` / `@type` the node's own keywords.
  - Arrays and `@list`s are always expanded.
  - An `@id` reference into the document's `@graph` is replaced by the node it names.
- **`object_xpath`** is where the shaper's elements go, as a path from `mdb:MD_Metadata`.
- **`transform`** is the shaper `tf_<transform>`. Arguments go in parentheses: `party(author,first)` calls `tf_party` with `author, first`.

**Order:** the record is assembled in the element order the ISO 19115-3 XML Schema requires (`SLOTS` in the converter), not in table order.
- Within one location, rows run in table order.
- A location that holds one element keeps the first row's.
- The `FUNDING`, `MEASUREMENT TECHNIQUE`, `PUBLISHING PRINCIPLES` and `VARIABLES MEASURED` lines are joined into one `supplementalInformation`.

**Validation:** with `--xsd`, or a GeoNetwork checkout at `../../geonetwork/core-geonetwork`, each output is checked against the XML Schema. That checkout's schema plugin holds the ISO XSDs.

### Where it differs from fromJsonCdif.xsl

The stylesheet runs inside GeoNetwork on a JSON-LD-framed record that has been converted to XML. The port reads the CDIF JSON-LD directly.

**Fixes that keep the output schema-valid:**
- `mrd:distributor` comes before `mrd:transferOptions`, as the schema requires.
- Each `gml:id` is unique.
- `mdb:dateInfo`, which is mandatory, falls back to the resource's `dateModified`, else a `cit:date` with `gco:nilReason="missing"`.
- A unit name with a space or colon (`1 Year`) is not a valid `gco:UomIdentifier`, so it is written as `unitText: …` in the attribute's `constrainedBy` instead.

**Values the stylesheet dropped:**
- Every bounding box and point of `spatialCoverage` is written, not just the first.
- `conditionsOfAccess` is written alongside a licence, not only when there is none.
- Each licence gets its own `MD_LegalConstraints`, its IRI as the online resource.
- The resource's `dateModified` and `dateCreated` go on its citation. The `1900-01-01` placeholder is not written.
- A place named by a DefinedTerm, and a `time:ProperInterval` of XSD dates, are converted.

**Values that now round-trip:**
- Keywords are grouped by vocabulary, with `inDefinedTermSet` as the `thesaurusName`.
- An open interval end (`..`) is `indeterminatePosition="unknown"`. An instant is written as a period whose begin and end are the same.
- A related link's title and URL go in the associated resource's `mri:name` citation, which names the linked resource. The stylesheet used `mri:metadataReference`.
- A Person with no affiliation is a `CI_Individual`. The stylesheet named an organisation after the person.
- An agent with no type is a `CI_Organisation` alone.

**Choices that differ:**
- The metadata identifier is the catalog record's identifier, else a UUID made from the dataset `@id`, so a record always gets the same one. The stylesheet used the UUID GeoNetwork assigns.
- The feature catalogue's `versionDate` is `datePublished`, else `dateModified`. The stylesheet used the conversion date.
- Code lists point at the ISO TC 211 codelists, rather than GeoNetwork's `codeListLocation` placeholder.

### The build

```bash
python ISO19115/build_iso_examples.py
```

The build takes 6 records from each CDIF corpus in this repository: DataCite, Dublin Core, EML, FGDC, ISO 19115, NIAID, OGC services, SDMX, STAC and DCAT. It writes their ISO records to `isoFromCDIF/<corpus>/`, validates each against the XML Schema, and converts each back to CDIF with `iso_to_cdif.py`. `isoFromCDIF/INDEX.json` records, for each record, the schema result and any property that did not survive the round trip. The output is deterministic.

All 60 records are valid against the ISO 19115-3 XML Schema. Every property compared survives the round trip in every record that has it:
- name, description, identifier;
- the dates and version;
- creators, contributors and publisher;
- keywords, licence and conditions of access;
- spatial and temporal coverage;
- distributions, variables and related links;
- funding, and `wasGeneratedBy` / `wasDerivedFrom`.

The round trip adds a little:
- the landing page comes back as a distribution, because it is written as a transfer option when there is no download URL;
- `additionalType` values come back as keywords.

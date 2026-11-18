# ISO19115/ — ISO 19115 XML → CDIF

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

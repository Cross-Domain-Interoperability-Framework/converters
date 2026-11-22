# FGDC/ — FGDC CSDGM → CDIF

[`fgdc_to_cdif.py`](fgdc_to_cdif.py) converts FGDC Content Standard for Digital Geospatial Metadata (CSDGM, FGDC-STD-001-1998, including its Biological Data Profile) to CDIF JSON-LD, one record per `<metadata>` document. USGS, state geological surveys, ORNL DAAC and other DataONE member nodes publish CSDGM.

```bash
python FGDC/fgdc_to_cdif.py metadata.xml -o out/
python FGDC/fgdc_to_cdif.py metadata.xml -o out/ --identifier scimeta_383.xml --identifier-type "DataONE identifier" --record-url https://cn.dataone.org/cn/v2/object/scimeta_383.xml
```

CSDGM has no element for the dataset's identifier, so the record's `@id` is chosen in this order:
1. A DOI among the citation's online linkages, as `https://doi.org/<doi>`.
2. The `--identifier` you give: the repository's identifier for the document, such as a DataONE identifier or a WAF file name. A URL is kept; anything else becomes `urn:fgdc:<identifier>`.
3. The first online linkage.

The options:
- **`--identifier-type`** names that identifier's kind; it becomes the `schema:identifier` PropertyValue's propertyID.
- **`--record-url`** is the URL of the CSDGM document itself. It is the catalog record's `@id`; without it, the record is `<@id>#metadata`.
- **`--catalog-name`** and **`--catalog-url`** become `schema:includedInDataCatalog`.

## What maps to what

[`mappings/fgdc-to-cdif.sssom.tsv`](../mappings/fgdc-to-cdif.sssom.tsv) drives the converter; it is read at runtime.

- `subject_xpath` is an XPath from the `<metadata>` element; CSDGM elements have no namespace.
- Each `transform` is a shaper `tf_<transform>` in the converter.
- Rows run in order: for a scalar target the first row to fill it wins; array targets accumulate.

| CSDGM | CDIF |
|---|---|
| A DOI `onlink` | `@id` and `schema:identifier` |
| The first `onlink`; the others | `schema:url`; `schema:relatedLink` LinkRoles (`online linkage`), except those that are also download URLs |
| `title`, `edition`, `geoform` | `schema:name`, `schema:version`, `schema:additionalType` |
| `origin` | `schema:creator`, an ordered `@list` |
| `pubinfo/publish` | `schema:publisher` |
| `pubdate` | `schema:datePublished`, and `schema:dateModified` |
| `metainfo/metd` | `schema:dateModified` when there is no full `pubdate`; the catalog record's `schema:dateModified` |
| `abstract`, `purpose`, `supplinf`, `eaover` | `schema:description`: the abstract first, the rest labelled |
| `timeperd` | `schema:temporalCoverage`: `begdate/enddate` intervals (with `begtime`/`endtime`); `sngdate` and `mdattim` dates. `Present` ends an open interval. |
| `status/progress` | `schema:creativeWorkStatus` |
| `spdom/bounding`, `descgeog` | `schema:spatialCoverage`: a Place with a box, or a point when the box has no extent |
| `keywords` (theme, place, stratum, temporal) | `schema:keywords`: DefinedTerms in the set's thesaurus, described by keyword type, or strings when the thesaurus is `None` |
| `taxonomy` | `schema:keywords`: a DefinedTerm per taxon, with its rank |
| `accconst`, `useconst` | `schema:conditionsOfAccess`, labelled. `None` is kept, since it says there are none. |
| A Creative Commons, Open Data Commons or SPDX URL in `useconst` | `schema:license` |
| `ptcontac` | `schema:contributor` Role `pointOfContact` |
| `distinfo/distrib` | `schema:provider` |
| `metainfo/metc` | The catalog record's `schema:maintainer` |
| `datacred` | `schema:creditText` |
| `procstep` | `prov:wasGeneratedBy` cdifProv Actions: `procdesc` is the actionProcess and `procdate` the endTime |
| `srcinfo` | `prov:wasDerivedFrom`: each source's title and link |
| `crossref`, `lworkcit` | `schema:relatedLink` LinkRoles (`crossReference`, `largerWork`) when they have a link |
| `digform` with a `networkr` | `schema:distribution` DataDownloads: `formname` is the encodingFormat and `transize` the contentSize (MB) |
| `eainfo/detailed/attr` | `schema:variableMeasured` `cdi:InstanceVariable`s: `attrlabl` is the name and `attrdef` the description; a range domain gives minValue, maxValue and unitText |
| Enumerated domains, `spref`, `spdoinfo`, `update`, and the Esri, ORNL Mercury and USGS ERP blocks | Not converted |

**Agents.** An originator or publisher is only a name.
- "Family, Initials" (`Blodgett, R.B.`) is a Person.
- Two to four capitalised words with no organisation word (`B.E. Dieter`, `Karen A. Westphal`) are a Person.
- Anything else (`Amador County`, `NRCS`, `Water and Climate Center, National Resources Conservation Service`) is an Organization.

A contact (`cntinfo`) is more explicit:
- A `cntperp` is a Person (`cntper`), affiliated to its `cntorg`.
- A `cntorgp` is an Organization, with the named person in its description.
- `cntpos`, `cntemail` and `cntvoice` become the jobTitle, email and telephone.

**Variables.** CSDGM attributes have no physical format or column order, so the variables carry no physical mapping.

A record with entity attributes still claims data_description, because detection finds `cdi:InstanceVariable`s.

**Keywords.** A term name shorter than three characters (`GC`) is also the term's `termCode`, because CDIF requires a name of at least three characters or a code. The ISO converter does the same.

## Sentinels

| Missing | Written |
|---|---|
| A date with year and month | A year alone is padded to `YYYY-01-01` for `dateModified`. CDIF needs at least a year and month. |
| Any date | `schema:dateModified` `1900-01-01` |
| A description | The name |
| Access and use constraints | `schema:license` is the OGC nil URI `http://www.opengis.net/def/nil/OGC/0/missing` |
| An online linkage | `schema:url` is the same nil URI |

## Examples and the build

`examples/` holds 28 CSDGM documents. [`../harvesters/harvest_format_examples.py`](../harvesters/harvest_format_examples.py) harvested them:
- 22 from DataONE member nodes: CDL, NMEPSCOR, ORNL DAAC, USGS Science Data Catalog, EDAC GSTORE and RW.
- 6 from the Alaska Division of Geological & Geophysical Surveys web-accessible folder.

`examples/INDEX.json` gives each document's source.

```bash
python FGDC/build_cdif_examples.py
```

The build converts each document to `cdifJSON-LD/`:
- The request URL from `INDEX.json` is the catalog record's `@id`.
- Its identifier (the DataONE identifier, or the WAF file name) is the dataset identifier when there is no DOI.
- The member node or the WAF is the catalog.

It then validates each record against every profile it claims, using the profile's `resolvedSchema.json` and SHACL bundle from the metadataBuildingBlocks checkout beside this repository. This is the same check as the other table-driven builds. The results are in `cdifJSON-LD/INDEX.json`, and the output is deterministic.

All 28 records pass.
- Every record claims core and discovery, and 17 also claim provenance (from process steps).
- 11 also claim data_description; they describe 263 attributes between them.
- 5 records have DOIs, and the other 23 are identified by their repository identifier.

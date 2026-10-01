# UMM-C → CDIF

Converts NASA Common Metadata Repository (CMR) collection metadata in
[UMM-C](https://github.com/nasa/Common-Metadata-Repository/tree/master/umm-spec-lib/resources/json-schemas/collection/umm/v1.18.6)
JSON to CDIF core + discovery JSON-LD. Tested on UMM-C 1.18.2 and 1.18.6.

- Converter: [`umm_to_cdif.py`](umm_to_cdif.py). It follows the
  `DCAT/dcat_to_cdif.py` pattern: it reads its mapping table at runtime, the
  `transform` column names the shaper (`tf_<name>`) that builds each value, and
  it adds a catalog record and runs `detect_conformance`.
- Mapping table: [`../mappings/ummc-to-cdif.sssom.tsv`](../mappings/ummc-to-cdif.sssom.tsv)
  and its sidecar [`../mappings/ummc-to-cdif.sssom.yml`](../mappings/ummc-to-cdif.sssom.yml).
  The sidecar documents the transform vocabulary and precedence rules. Rows are
  still marked tool-suggested (`author_id` claude, empty `reviewer_id`) until a
  curator reviews them.
- Schemas: [`schemas/`](schemas/) holds the UMM-C v1.18.6 and UMM-Var v1.9.0
  JSON Schemas the table is written against, in versioned folders, with
  their upstream source.

## Usage

```bash
# a CMR search result from a file (one or many collections)
python UMM/umm_to_cdif.py UMM/examples/umm-c-aq/lcs_aq_airgradient.json -o out/

# straight from CMR -- e.g. the "Copy CMR metadata URL" link on an Earthdata
# dataset page; any collections.umm_json[_vX_Y_Z] search URL works
python UMM/umm_to_cdif.py "https://cmr.earthdata.nasa.gov/search/collections.umm_json_v1_18_2?concept-id[]=C4178560190-LARC_CLOUD" -o out/

# also frame + validate each record against the CDIF Discovery schema
python UMM/umm_to_cdif.py UMM/examples/umm-c-aq/*.json -o out/ --validate

# add GCMD KMS concept URIs to keywords and variables (network lookups)
python UMM/umm_to_cdif.py in.json -o out/ --kms

# offline: skip fetching associated UMM-Var records
python UMM/umm_to_cdif.py in.json -o out/ --no-umm-var
```

The mapping table can be edited in Excel. The converter reads it as quoted
TSV, so the quote marks Excel adds around cells containing commas (including
`subject_filter` values) are handled.

Input can be a CMR search result (`{"items": [{"meta", "umm"}]}`), a single
item, or a bare UMM-C document. With a bare document there is no CMR
concept-id, so the CMR-derived identifier, `sameAs` and catalog-record `@id`
fall back as described below.

## Examples

- [`examples/umm-c-aq/`](examples/umm-c-aq/): the 14 collections of the NASA
  [Low-Cost Sensor Air Quality](https://www.earthdata.nasa.gov/data/projects/low-cost-sensor-aq/data-access-tools)
  harmonization database (ASDC, `LARC_CLOUD`), fetched with the
  `collections.umm_json_v1_18_2` URLs the Earthdata dataset pages link to.
- [`examples/umm-c/`](examples/umm-c/): nine collections from other DAACs
  (PO.DAAC, LP DAAC, GES DISC, NSIDC, ORNL, ASF, GHRC, OB.DAAC, LAADS) in UMM-C
  1.18.6. `podaac_mur_sst.json` describes the same dataset as the hand-made
  `doc-corediscovery/examples/ncei-ghrsst-mur-sst.jsonld`.
- [`examples/cdif/`](examples/cdif/): the converted records. All 23 frame and
  validate against `CDIFDiscoverySchema.json`, and conformance detection finds
  core + discovery for each.

## Mapping decisions

- **@id**: `https://doi.org/<DOI>`, else the CMR concept URL.
  `schema:identifier` is a DOI `PropertyValue`, else the CMR concept id.
- **Creators**: `CollectionCitations.Creator` is split into an ordered list of
  people (e.g. HLS: 10 names). A string that reads as an organization ("VCST
  Team", "JPL MUR MEaSUREs Project") stays one `schema:Organization`. If there
  is no Creator, the converter uses Investigator contacts, then the ORIGINATOR
  data center, and otherwise leaves out `schema:creator`. Authors that appear
  only inside `OtherCitationDetails` (e.g. Daymet) are not parsed out; that
  string goes to `dcterms:bibliographicCitation` verbatim.
- **Agents**: `schema:publisher` is the first DISTRIBUTOR data center, else the
  first ARCHIVER. Every data center role and every contact (including contacts
  nested in data centers) becomes a `schema:contributor` `schema:Role`.
- **variableMeasured**: taken from the collection's UMM-Var records when CMR
  has them (`meta.has-variables`). The converter fetches them by the concept
  ids in `meta.association-details`, because CMR cannot search variables by
  collection. Each becomes a `schema:PropertyValue` with name, long name,
  definition, units, the CF standard-name URI as `propertyID`, and the CMR
  variable concept as `url`. The valid range is unpacked with Scale/Offset,
  so MUR SST's `analysed_sst` reads 265.38–330.92 K rather than
  ±32767. If a collection has no UMM-Var records, or you pass `--no-umm-var`
  (offline), the converter falls back to GCMD science keywords that reach
  VariableLevel1 or deeper and uses the deepest level (with `--kms`, the KMS
  concept URI goes in `propertyID`). Of the examples, MERRA-2, HLS, MUR,
  Daymet and OB.DAAC use UMM-Var. The 14 air-quality collections have none and
  use keywords. GHRC is flagged `has-variables` in CMR but has no associated
  variables. ASF Sentinel-1 still gets 77 keyword "variables", because its
  keywords are deep.
- **Distributions**: GET DATA links become `schema:DataDownload` +
  `schema:Collection` with `schema:url`, because they are portals and
  directories rather than files. USE SERVICE API links (OPeNDAP, …) become
  `schema:WebAPI`. `DirectDistributionInformation` becomes an "AWS S3 direct
  access" distribution whose description lists the bucket prefixes.
  Documentation, visualization and tool links become `schema:relatedLink`
  LinkRoles typed by the UMM Type/Subtype.
- **License**: `UseConstraints.LicenseURL` becomes `schema:license`.
  Constraint text goes to `schema:conditionsOfAccess`. If a record has neither,
  `schema:license` is the OGC nil URI (the DCAT converter's convention).
- **Extents**: bounding rectangles become GeoShape boxes (`S W N E`, kept as
  given even across the antimeridian), polygons become WKT, points become
  GeoCoordinates, and GCMD location keywords become named Places. Time ranges
  become ISO 8601 intervals, with an open end (`begin/..`) when there is no end
  date.
- **Keyword URIs**: KMS lookups match by label within the scheme (science
  keywords, platforms, instruments, projects). A label used at more than one
  level of the hierarchy could resolve to the wrong concept.
- **Passthrough**: UMM-C content with no CDIF slot is kept as `ummc:<Key>` JSON
  literals (`@type: @json`). For partly mapped keys (SpatialExtent,
  TemporalExtents, DOI, DataDates, …) only the unmapped remainder is kept.
  `RelatedUrls` entries that no table row selects (e.g. a DistributionURL of
  Type GET SERVICE) are kept as `ummc:RelatedUrls`. Which entries count as
  matched comes from the rows' `subject_filter` values, so adding a row for a
  new type takes those entries out of the passthrough.

# SDMX/ — SDMX structure → CDIF

[`sdmx_to_cdif.py`](sdmx_to_cdif.py) converts SDMX-ML 2.1 structure messages to CDIF JSON-LD, one record per dataflow. The input is the answer to an SDMX-REST structure query for a dataflow and everything it references:

```
{endpoint}dataflow/{agency}/{id}/{version}?references=all&detail=referencepartial
```

The answer is the dataflow plus its data structure definition (DSD), concept schemes and codelists.

```bash
python SDMX/sdmx_to_cdif.py structure.xml -o out/ --endpoint https://stats.bis.org/api/v1/ --partial
python SDMX/sdmx_to_cdif.py "https://stats.bis.org/api/v1/dataflow/BIS/WS_TC/2.0?references=all" -o out/ --endpoint https://stats.bis.org/api/v1/
python SDMX/sdmx_to_cdif.py structure.xml -o out/ --endpoint https://registry.sdmx.org/ws/public/sdmxapi/rest/ --no-data
```

- **`--endpoint`** is the SDMX-REST root, as the [SDMX Global Discovery Service](https://gds.sdmx.io/) lists it. It gives the record its addresses.
  - With it, the `@id` is the dataflow's REST URL, and the data query is the distribution.
  - Without it, the `@id` is the dataflow URN and the record has no distribution.
- **`--no-data`** is for a structure-only registry (the SDMX Global Registry, IMF SDMX Central): no data distribution.
- **`--partial`** says the codelists hold only the codes the dataflow uses, as `detail=referencepartial` returns them.

## What maps to what

[`mappings/sdmx-to-cdif.sssom.tsv`](../mappings/sdmx-to-cdif.sssom.tsv) drives the converter; it is read at runtime.

- `subject_xpath` is an XPath from the `str:Dataflow` element. `$df` is the dataflow, `$dsd` its DSD, and absolute paths reach the rest of the message.
- Each `transform` is a shaper `tf_<transform>` in the converter.
- Rows run in order: for a scalar target the first row to fill it wins; array targets accumulate.

| SDMX | CDIF |
|---|---|
| Dataflow name, description (English, else the first language); the DSD's description when the dataflow has none | `schema:name`, `schema:alternateName` (the other languages), `schema:description` |
| Dataflow URN, version | `schema:identifier` (a PropertyValue; its url is the REST URL), `schema:version` |
| `LAST_UPDATE` annotation, else the dataflow's `validFrom`, else an Actual content constraint's `validFrom` | `schema:dateModified` |
| Maintenance agency, with its contacts from the agency scheme | `schema:publisher` |
| The data providers of its provision agreements | `schema:provider` |
| The categories it is categorised under | `schema:keywords`: DefinedTerms in the category scheme |
| `TIME_PERIOD` range of a content constraint | `schema:temporalCoverage` (ISO 8601 interval) |
| `EXT_RESOURCE` annotations (.Stat Suite) | `schema:relatedLink` |
| Each dimension, time dimension, primary measure and attribute | A `cdi:InstanceVariable` in `schema:variableMeasured` (below) |
| The DSD | A `cdi:DimensionalDataStructure` on the data distribution (below) |

**Variables.** Each DSD component becomes one variable:
- `schema:name` is the component id, which is the column name in SDMX-CSV.
- The concept's name and description become `schema:alternateName` and `schema:description`. The concept URN is the `schema:propertyID`.
- `cdif:role` is `Dimension`, `Measure` or `Attribute`.
- A coded component has `cdif:physicalDataType` `xsd:string`, and a `cdi:takesSubstantiveValuesFrom` value domain. That domain's `cdif:EnumerationDomain` names the codelist, gives its URN and how many codes it has, and references the codelist's REST URL. The codes themselves are not copied.
- An uncoded component maps its SDMX text type to an XSD type. SDMX time periods such as `2020-Q1` have no XSD type and keep the SDMX name, `ObservationalTimePeriod`.
- An attribute `cdif:qualifies` the first dimension it is attached to, else the primary measure.

**Structure.** The distribution is the data query `{endpoint}data/{agency},{id},{version}/all`, a `schema:DataDownload` in SDMX-ML. Its `cdi:isStructuredBy` is the DSD as a `cdi:DimensionalDataStructure`:
- Each component is defined by its variable (`cdif:isDefinedBy_Variable`).
- Each attribute component qualifies the components it is attached to.
- `cdif:has_PrimaryKey` is the dimensions in key order, which is the SDMX series key plus the time period.

SDMX's dimensional model is the one DDI-CDI's DimensionalDataStructure describes.

## Sentinels

| Missing | Written |
|---|---|
| A date | `schema:dateModified` `1900-01-01` |
| A description | The name |
| A licence (SDMX has no licence element) | `schema:license` is the OGC nil URI `http://www.opengis.net/def/nil/OGC/0/missing` |
| An endpoint | `schema:url` is the same nil URI |

The catalog record (`schema:subjectOf`) is the structure query itself. Its `dateModified` is when the service prepared the message.

## Examples and the build

`examples/` holds 45 structure messages from 9 SDMX-REST services. [`../harvesters/harvest_format_examples.py`](../harvesters/harvest_format_examples.py) found the services through the SDMX Global Discovery Service and harvested the messages; `examples/INDEX.json` gives each message's endpoint and request.

```bash
python SDMX/build_cdif_examples.py
```

The build converts each message to `cdifJSON-LD/`, taking the endpoint from `INDEX.json`. It treats `registry.sdmx.org` and `sdmxcentral.imf.org` as structure-only registries. It then validates each record against every profile it claims, using the profile's `resolvedSchema.json` and SHACL bundle from the metadataBuildingBlocks checkout beside this repository; this is the same check as the NIAID and ISO builds. The results are in `cdifJSON-LD/INDEX.json`.

All 45 records pass. The 35 from data-bearing services claim core, discovery, data_description and data_structure. The 10 from the two registries have no data distribution and claim the first three.

Only SDMX-ML 2.1 is read. SDMX 3.0 structure messages use other namespaces and are not converted yet.

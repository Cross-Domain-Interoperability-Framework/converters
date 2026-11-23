# OGCServices/ — WMS / WFS capabilities → CDIF

[`ogc_services_to_cdif.py`](ogc_services_to_cdif.py) converts OGC GetCapabilities responses to CDIF JSON-LD, one record per service. It reads:
- WMS 1.3.0 (`WMS_Capabilities`);
- WFS 2.0 and 1.1 (`WFS_Capabilities`, on OWS Common 1.1 and 1.0).

```bash
python OGCServices/ogc_services_to_cdif.py capabilities.xml -o out/ --capabilities-url "https://example.org/geoserver/wms?service=WMS&request=GetCapabilities"
python OGCServices/ogc_services_to_cdif.py "https://ogc.bgs.ac.uk/cgi-bin/BGS_1GE_Geology/ows?service=WFS&request=GetCapabilities" -o out/
```

- **`--capabilities-url`** is the GetCapabilities URL the file was fetched from. It is the record's `@id` and its `schema:documentation`. Without it, the converter builds the URL from the GetCapabilities operation's own address.
- **`--catalog-name`** and **`--catalog-url`** name the catalogue that listed the service.

## The record

A capabilities document describes a service, so the record describes the service, as the ISO converter describes an ISO service record:
- `@type` is `schema:Dataset` and `schema:WebAPI`.
- The CDIF webAPI block's properties are filled:
  - `schema:serviceType`, a DefinedTerm such as "OGC Web Map Service 1.3.0" with `termCode` `OGC:WMS`;
  - `schema:termsOfService`;
  - `schema:documentation`, the capabilities URL;
  - one `schema:potentialAction` per operation.
- Each named WMS layer or WFS feature type is listed under `schema:dataset`.

**Layers.** A layer is a `schema:CreativeWork` whose `additionalType` is `WMS layer` or `WFS feature type`, identified as `<capabilities URL>#layer/<Name>`.
- It is not typed `schema:Dataset`, because CDIF would then require each layer to carry a full record of its own: a modification date, a licence and a catalog record. A capabilities document has none of these for a layer.
- A layer keeps its title (name), Name (identifier), abstract, keywords, WGS 84 box and MetadataURL (a `describedby` link).
- A WMS layer with no box inherits its nearest ancestor's.

## What maps to what

[`mappings/ogc-services-to-cdif.sssom.tsv`](../mappings/ogc-services-to-cdif.sssom.tsv) drives the converter; it is read at runtime.

- `subject_xpath` is an XPath from the document root. `wms:` is the WMS 1.3 namespace. `wfs:` and `ows:` are bound at run time to the document's own WFS and OWS Common versions, so one row serves every version.
- Each `transform` is a shaper `tf_<transform>` in the converter.
- Rows run in order: for a scalar target the first row to fill it wins; array targets accumulate.

| Capabilities | CDIF |
|---|---|
| The root element and its `version` | `schema:serviceType`, `schema:version` |
| Service `Title`, `Abstract` | `schema:name`, `schema:description` |
| Service keywords | `schema:keywords`: DefinedTerms in their vocabulary (WMS `vocabulary`, OWS `Type`), or strings |
| WMS `OnlineResource`, OWS `ProviderSite` | `schema:url`; else the service endpoint |
| `ContactOrganization`, `ProviderName` | `schema:provider`, an Organization with the contact email and telephone |
| `ContactPerson`, `IndividualName` | `schema:contributor` Role `pointOfContact`, with position, email and telephone |
| `Fees`, `AccessConstraints` | `schema:termsOfService`, labelled; else `not specified`, the webAPI block's default |
| `AccessConstraints` | `schema:conditionsOfAccess`. `none` is kept, since it says there are none. |
| A licence URL in `Fees` or `AccessConstraints` (Creative Commons, Open Data Commons, SPDX, Etalab, UK OGL) | `schema:license` |
| WMS `Request` operations, OWS `Operation`s | `schema:potentialAction`: an Action per operation, whose EntryPoint is the HTTP Get (else Post) address, with `httpMethod` and the output formats as `contentType` |
| Named WMS `Layer`s, WFS `FeatureType`s | `schema:dataset` (above) |
| The union of the WMS layers' boxes (the root layer's, when it has one), or of the feature types' boxes | `schema:spatialCoverage` |
| INSPIRE `MetadataUrl` | `schema:relatedLink` (`describedby`) |
| INSPIRE `ResponseLanguage` | `schema:inLanguage` |
| A date-shaped `updateSequence` | `schema:dateModified` |
| Dimensions, styles, legends, filter capabilities | Not converted |

## Sentinels

| Missing | Written |
|---|---|
| A date | `schema:dateModified` `1900-01-01`. Capabilities documents carry no date. The catalog record's `dateModified` is when the document was fetched. |
| An abstract | The title |
| Constraints and a licence | `schema:license` is the OGC nil URI `http://www.opengis.net/def/nil/OGC/0/missing` |
| Fees and constraints | `schema:termsOfService` `not specified` |
| Any operation address | One Action named `missing` whose `urlTemplate` is the nil URI, as the ISO converter writes it |

## Examples and the build

`examples/wms/` holds 25 WMS 1.3.0 documents and `examples/wfs/` holds 19 WFS documents (18 at 2.0.0, 1 at 1.1.0). [`../harvesters/harvest_format_examples.py`](../harvesters/harvest_format_examples.py) found them through the service records of CSW catalogues (PDOK, BGS, Ifremer Sextant, Swiss geocat, GeoNetwork demo) and fetched each GetCapabilities. Each folder's `INDEX.json` gives a document's request URL, its retrieval date and the catalogue that listed it.

```bash
python OGCServices/build_cdif_examples.py
```

The build converts each document to `cdifJSON-LD/wms/` or `cdifJSON-LD/wfs/`, with the request URL as `@id` and the listing catalogue as the catalog. It then validates each record against every profile it claims, using the profile's `resolvedSchema.json` and SHACL bundle from the metadataBuildingBlocks checkout beside this repository. This is the same check as the other table-driven builds. The results are in `cdifJSON-LD/INDEX.json`, and the output is deterministic.

All 44 records pass. 43 claim core and discovery. The other one claims core only: an access-restricted WFS that lists no feature types and so has no extent.

The records describe 2,641 layers and feature types between them. The largest record is 751 KB: data.geopf.fr's WMS, with 462 layers.

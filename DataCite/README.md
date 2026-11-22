# DataCite/ — DataCite XML → CDIF

[`datacite_to_cdif.py`](datacite_to_cdif.py) converts DataCite Metadata Schema XML to CDIF JSON-LD. It reads kernel 3 and kernel 4 `<resource>` records. A record can be on its own or inside an OAI-PMH response (`oai_datacite`, or a repository's own DataCite prefix). Each resource becomes one CDIF record.

```bash
python DataCite/datacite_to_cdif.py record.xml -o out/
python DataCite/datacite_to_cdif.py oai-response.xml -o out/ --catalog-name PANGAEA --catalog-url https://www.pangaea.de/
```

`--catalog-name` and `--catalog-url` name the repository the record came from; they become the catalog record's `schema:includedInDataCatalog`.

## What maps to what

[`mappings/datacite-to-cdif.sssom.tsv`](../mappings/datacite-to-cdif.sssom.tsv) drives the converter; it is read at runtime.

- `subject_xpath` is an XPath from the `<resource>` element. The `datacite:` prefix is bound to the record's own namespace, so one row serves kernel 3 and kernel 4. The shapers handle the places where the kernels differ.
- Each `transform` is a shaper `tf_<transform>` in the converter.
- Rows run in order: for a scalar target the first row to fill it wins; array targets accumulate.

| DataCite | CDIF |
|---|---|
| `identifier` | `@id` and `schema:url`: the resolver URL (`https://doi.org/…`, `https://hdl.handle.net/…`). Also `schema:identifier`: a PropertyValue whose propertyID is the identifiers.org registry entry. |
| `alternateIdentifier` | `schema:sameAs` PropertyValues |
| `title` (no `titleType`); other titles | `schema:name`; `schema:alternateName` |
| `creator` | `schema:creator`, an ordered `@list` of Persons and Organizations |
| `publisher` | `schema:publisher` |
| `contributor` | `schema:contributor`: a Role with the `contributorType` as roleName. A `HostingInstitution` is also `schema:provider`. |
| `fundingReference`; kernel 3's `Funder` contributor | `schema:funding` MonetaryGrants: funder, award number (with `awardURI`), award title |
| `date` `Updated`, else `Issued`, `Available`, `Created`, else `publicationYear` | `schema:dateModified` |
| `date` `Issued`, else `publicationYear` | `schema:datePublished` |
| `date` `Created` | `schema:dateCreated` |
| `date` `Collected` or `Coverage` | `schema:temporalCoverage` |
| `subject` | `schema:keywords`: a DefinedTerm when it has a scheme, valueURI or classification code, else a string |
| `language`, `version` | `schema:inLanguage`, `schema:version` |
| `resourceType` | `schema:additionalType`: `resourceTypeGeneral` as a DefinedTerm, plus the free text. `@type` stays `schema:Dataset`. |
| `rights` | `schema:license`: a CreativeWork at the `rightsURI`. An OpenAIRE `info:eu-repo/semantics/…` access right is `schema:conditionsOfAccess` instead. |
| `description` | `schema:description`: the Abstract, then the other descriptions, each labelled with its type |
| `geoLocation` | `schema:spatialCoverage` Places: the place name, and a box, polygon or point |
| `relatedIdentifier` | `schema:relatedLink` LinkRoles, with the `relationType` as linkRelationship. `IsDerivedFrom` is also `prov:wasDerivedFrom`. |
| `format` | `schema:encodingFormat` |
| `size` | Not converted. It is free text, often one per file with nothing tying it to the file. |

**Agents.** `nameType` decides between Person and Organization. Without it, the agent is a Person if it has any of these: a given name, a family name, an ORCID, or a comma in its name.
- The first name identifier becomes `schema:identifier` and any others go in `schema:sameAs`. An `e-mail` "identifier" becomes the contact email.
- A Person keeps its first affiliation only, because CDIF's Person has one. The affiliation's ROR is its identifier.

**Subjects.** A record that gives one concept in several languages (the same `valueURI`) gets one DefinedTerm, with the other languages as its `schema:alternateName`.

**Related identifiers.**
- A `URL` that is a path (`/EHES_93.pdf`) is resolved against the row's `schemeURI`.
- An identifier with no resolver URL is dropped, because a LinkRole's EntryPoint needs one. `-v` lists what was dropped.

## Sentinels

| Missing | Written |
|---|---|
| Any date other than `publicationYear` | `schema:dateModified` is the year padded to `YYYY-01-01`. CDIF needs at least a year and month. |
| Any date at all | `schema:dateModified` `1900-01-01` |
| A description | The name |
| A licence and an access right | `schema:license` is the OGC nil URI `http://www.opengis.net/def/nil/OGC/0/missing` |
| An identifier with a resolver | `schema:url` is the same nil URI |

DataCite records no download URLs, so the records have no `schema:distribution`. The landing page in `schema:url` is how CDIF reaches the data.

The catalog record (`schema:subjectOf`) is `<@id>#metadata`.

## Examples and the build

`examples/` holds 50 DataCite records. [`../harvesters/harvest_format_examples.py`](../harvesters/harvest_format_examples.py) harvested them from the OAI-PMH endpoints of 37 repositories, sampled from re3data. Of the 50, 46 are kernel 4 and 4 are kernel 3. `examples/INDEX.json` gives each record's repository, endpoint and request.

```bash
python DataCite/build_cdif_examples.py
```

The build converts each record to `cdifJSON-LD/`, with the repository as the catalog. It then validates each record against every profile it claims, using the profile's `resolvedSchema.json` and SHACL bundle from the metadataBuildingBlocks checkout beside this repository. This is the same check as the NIAID, ISO and SDMX builds. The results are in `cdifJSON-LD/INDEX.json`.

All 50 records pass. 12 claim core and discovery; the other 38 have no spatial or temporal coverage and claim core only.

Only DataCite XML is read. The DataCite REST API's JSON is not converted.

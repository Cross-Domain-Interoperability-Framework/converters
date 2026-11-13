---
title: DCAT Use Cases and Requirements — CDIF Coverage Matrix
subtitle: Requirement-by-requirement assessment of W3C DXWG requirements against DCAT 3 and the CDIF building blocks
date: 2026-11-12
status: analysis, not normative
---

# DCAT Use Cases and Requirements — CDIF Coverage Matrix

Assesses every requirement in the W3C *Dataset Exchange Use Cases and Requirements* (UCR) against two implementations: **DCAT 3** (W3C Recommendation, 22 August 2024) and the **CDIF metadata building blocks**. The point is to establish where CDIF closes gaps DCAT left open, and where CDIF is behind DCAT.

## Summary

The UCR source contains **73 requirement sections** across 13 groups. Of these, **44** are in scope for DCAT itself; the remaining 29 were deliverables of the Profiles and Content Negotiation work, not of DCAT.

| Verdict | DCAT 3 | CDIF |
|---|---:|---:|
| Met | 15 | 41 |
| Partial | 14 | 21 |
| Not met | 15 | 11 |
| Out of scope (PROF / conneg) | 29 | — |
| **Total** | **73** | **73** |

Counting only the 44 requirements both can be judged on, DCAT 3 scores Met 15 / Partial 14 / Not met 15, and CDIF scores Met 24 / Partial 14 / Not met 6. CDIF leads on data description, provenance and access conditions; DCAT 3 leads on versioning and catalog-record semantics. On the 29 profile and negotiation requirements — out of scope for DCAT — CDIF scores Met 17 / Partial 7 / Not met 5.

## Key findings

### 1. CDIF's additions concentrate in the one requirement group DCAT declined to implement

UCR group *Qualitative information* holds seven requirements — usage notes, summary statistics, provenance, funding source, project context, data quality model, quality-related information. **DCAT 3 natively satisfies none of them.** Quality was deferred to DQV, a separate Note that DCAT does not profile; funding and project context have no DCAT property at all; provenance gets a bare `prov:wasGeneratedBy` link with no activity model.

CDIF meets four of the seven outright (RSS, RPIF, RFS, RDQIF) and partially meets the other three (RUN, RPCX, RDQM). That is not an arbitrary extension of DCAT — it is a documented, scoped hole the DXWG collected requirements for and then left open.

### 2. The same pattern holds for structural description

**RDSAT** (dataset aspects: instrument, observable property, quantity kind, dimensions) and **RDIS** (distribution schema) are unmet and partly met respectively in DCAT 3, which offers only `dct:conformsTo` pointing at a Standard. CDIF answers both through `schema:variableMeasured`, cdifDataDescription, cdifDataStructure and `cdif:hasPhysicalMapping`. **RDIP** (contents of a packaged distribution) is likewise unmet in DCAT and met by cdifManifest.

### 3. RES is met by construction

**RES** asks for schema.org equivalents of DCAT properties to support entailment of schema.org-compliant profiles. DCAT 3 never delivered a normative mapping; the JRC crosswalk is external and informal. CDIF satisfies the requirement by being expressed in schema.org terms from the outset.

### 4. Versioning is where CDIF is clearly behind

Five versioning requirements. DCAT 3 meets four and partly meets the fifth, using `dcat:version`, `hasVersion`, `previousVersion`, `hasCurrentVersion`, `adms:versionNotes` and `dcat:DatasetSeries`. cdifCore answers with a single `schema:version` string, meeting none and partly meeting one. For a framework aimed at research data, where *which version did I analyse* is a reproducibility question, this is the least defensible gap in the set.

### 5. Two smaller gaps worth closing

**RPC — rights over the metadata record.** DCAT can assert `dct:rights` on a `dcat:CatalogRecord`. CDIF has `schema:license` and `schema:conditionsOfAccess` for the data, and nothing for the metadata. Relevant to any aggregation scenario.

**RPFCARDR — cardinality including *recommended*.** CDIF's JSON Schema expresses required versus optional but has no way to mark a property recommended. DCAT-AP encodes this as SHACL `sh:Warning` severity; DCAT-US carries a `requirementLevel` annotation. CDIF has neither, so a validator cannot distinguish "should" from "may".

### 6. CDIF re-solved the profile requirements with a different mechanism

Of the 29 profile and negotiation requirements, the OGC building-blocks pattern meets 17 and partly meets 7 — inheritance, profile-of-profiles, validation, value lists, dependency rules, documentation, machine-readable profile metadata. These were PROF and Content Negotiation deliverables that DCAT itself never addressed, and PROF remains a Note with little uptake.

The unmet half is uniformly the HTTP layer: **RPFNEG**, **RPFLREL**, **RPFSUP**, **RPFSREQ**, **RPFMDUSE**. CDIF has no content-negotiation-by-profile story, so a client cannot ask a CDIF endpoint for a particular profile and receive a conformance-declaring response. Whether that matters depends on whether CDIF endpoints are ever expected to serve more than one profile from one URL.

### 7. RPFN1.1 is the requirement CDIF implements most directly

Filed as unclassified and never resolved in DCAT, RPFN1.1 asks for machine-actionable links that declare protocol and interchange format rather than leaving the client to guess through content negotiation. cdifOpenApi, `schema:potentialAction`, `schema:urlTemplate` and the linkRole block are precisely this. Worth citing if CDIF's API-description work ever needs external justification.

## Matrix

Verdicts: **Met** — a defined property or block satisfies the requirement. **Partial** — reachable but incomplete, indirect, or expressed as prose rather than structure. **Not met** — no mechanism. **n/a** — out of scope for that implementation.

### Identification

*3 requirements. DCAT 3: Partial 1, Not met 2. CDIF: Met 3.*

#### RDID — Dereferenceable identifiers

> Encode identifiers as dereferenceable HTTP URIs

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | IRIs are the identity mechanism, but dereferenceability is convention; `dct:identifier` is a literal. |
| CDIF | Met | `@id` plus `schema:identifier` (schemaorgProperties/identifier: `propertyID`, `value`, `url`). |

*Use cases: ID11*

#### RIDT — Identifier type

> Indicate type of identifier (e.g. prism:doi, bibo:doi, ISBN etc.).

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | `dct:identifier` is an untyped literal. `adms:identifier` typing is a DCAT-AP addition, not DCAT 3 core. |
| CDIF | Met | `schema:identifier` as PropertyValue carries `propertyID` to type the identifier scheme. |

*Use cases: ID11*

#### RIDALT — Primary and alternative identifier

> Provide means to distinguish the primary and alternative (legacy) identifiers.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | No primary/alternative distinction in core. |
| CDIF | Met | cdifCore documents `schema:identifier` as primary and `schema:sameAs` as other identifiers. |

*Use cases: ID11*

### Versioning

*5 requirements. DCAT 3: Met 4, Partial 1. CDIF: Partial 1, Not met 4.*

#### RVSS — Version subject

> Identify DCAT resources that are subject to versioning, i.e. Catalog, Dataset, Distribution.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | Version properties sit on `dcat:Resource`, so Catalog/Dataset/DataService inherit them; `dcat:DatasetSeries` added in DCAT 3. |
| CDIF | Not met | `schema:version` on the dataset only; no statement of which CDIF resources are versioned. |

*Use cases: ID4, ID44*

#### RVer4 — Version status

> Indicate the status of a version in terms stability, fidelity etc. (e.g. major, minor, stable). The version identifier might refer to the version status (semantic version).

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | `dcat:version` is a literal; `adms:status` exists on Distribution/CatalogRecord but is not a version-status axis. |
| CDIF | Not met | No version status property. |

*Use cases: ID4*

#### RVSDT — Version release date

> It must be possible to assign a date to a version. The version identifier might refer to the release date.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `dct:issued` on each version resource. |
| CDIF | Partial | `schema:datePublished` applies to the dataset; no per-version resource to date. |

*Use cases: ID4*

#### RVSDA — Version delta

> Provide a way to indicate the change delta or other change information from the previous version.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `adms:versionNotes`. |
| CDIF | Not met | No delta or change-note property. |

*Use cases: ID4, ID50*

#### RVer7 — Version discovery

> Provide a means to search and discover existing versions of a DCAT resource. This should include both metadata elements referencing previous and next versions, lists of versions, and by implication the ability to search catalogs for related versions.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `dcat:previousVersion`, `dcat:hasVersion`, `dcat:hasCurrentVersion`; `dcat:first`/`last`/`prev` on DatasetSeries. |
| CDIF | Not met | No version lineage properties; no series concept. |

*Use cases: ID4*

### Qualitative information

*7 requirements. DCAT 3: Partial 2, Not met 5. CDIF: Met 4, Partial 3.*

#### RUN — Usage notes

> Ability to provide information on how to use the data. Should this also apply to specific distributions?

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | `dct:rights` and `foaf:page` carry free text; no dedicated usage-note property. |
| CDIF | Partial | `schema:publishingPrinciples` (maintenance, mutability, expected time-to-live) and `schema:conditionsOfAccess`; no per-distribution usage note. |

*Use cases: ID9*

#### RSS — Summary statistics

> Express summary statistics and descriptive metrics to characterize a Dataset.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | No summary-statistics vocabulary. |
| CDIF | Met | `cdif:statistics` (cdifDataDescription) backed by cdifStatistics: `cdif:typeOfStatistic`, `cdi:computationBase`, `cdi:isWeighted`, `cdif:has_CategoryStatistics`. |

*Use cases: ID33*

#### RPIF — Provenance information

> Provide a way to link to structured information about the provenance of a dataset including: the input data used to create a dataset to the dataset. the software used to produce the dataset to the dataset. an extensible model different types of agent roles funders dct:creator, dct:publisher etc are special cases, which require guidance, further roles may be defined in provenance or other richer models. The requirement is to establish an extensible mechanism, and for profiles to specify canonical equivalents for the special case properties of dcat:Dataset

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | `prov:wasGeneratedBy` is a bare link; `prov:qualifiedAttribution` covers agent roles; `dct:provenance` is free text. No activity model is profiled. |
| CDIF | Met | `prov:wasGeneratedBy` resolves into cdifProvActivity (`schema:agent`, `schema:participant`, `prov:used` with `schema:instrument`, `schema:actionProcess` as HowTo, start/end, `object`/`result` chaining); `prov:wasDerivedFrom` for input data; `agentInRole` gives the extensible role mechanism the requirement asks for. |

*Use cases: ID12, ID13, ID31, ID9*

#### RFS — Funding source

> Provide means to describe the funding (amount and source) of a Dataset (or entire Catalog).

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | DCAT 3 has no funding or grant property. Neither does DCAT-AP. |
| CDIF | Met | `schema:funding` in cdifCore, with schemaorgProperties/monetaryGrant (`schema:funder`, `identifier`, `name`, `description`). Grant *amount* is not modelled. |

*Use cases: ID31*

#### RPCX — Project context

> Provide a means to define a "project" as a research, funding or work organzation context of a dataset.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | No project construct. |
| CDIF | Partial | Reachable through `schema:funding` to a MonetaryGrant; no first-class Project. |

*Use cases: ID31, ID49*

#### RDQM — Data quality model

> Identify common modeling patterns for different aspects of data quality based on frequently referenced data quality attributes found in existing standards and practices. This includes potential use and revision of DQV Aspects include: the degree of a dataset's precision (i.e. measure of resolution or variability). the degree of a dataset's accuracy (i.e. measure of correctness). the degree a dataset conforms to a stated quality standard. details of data quality conformance test results.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | Deferred to DQV, which is a separate W3C Note and not profiled by DCAT 3. |
| CDIF | Partial | `dqv:hasQualityMeasurement` plus qualityProperties/qualityMeasure (`dqv:isMeasurementOf`, `dqv:value`) and qualityAnnotation (`oa:hasBody`/`hasTarget`/`motivatedBy`). No catalogue of precision/accuracy/conformance patterns. |

*Use cases: ID14, ID15, ID16, ID21, ID22, ID23*

#### RDQIF — Quality-related information

> Define a way to associate quality-related information with Datasets.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | No quality association in core. |
| CDIF | Met | `dqv:hasQualityMeasurement` is a first-class cdifDiscovery property. |

*Use cases: ID45*

### Formal description

*5 requirements. DCAT 3: Met 2, Partial 1, Not met 2. CDIF: Met 3, Partial 2.*

#### RDST — Dataset type

> Provide a mechanism to indicate the type of data being described and recommend vocabularies to use given the dataset type indicated. Providing examples of scope will provide guidance, without being unnecessarily restrictive. The key requirement is interoperability, achieved by using standardised vocabulary terms. It it unclear whether a canonical registry is required or whether communities should constrain choice via DCAT profiles.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | `dct:type` exists with no mandated vocabulary and no registry. |
| CDIF | Partial | `schema:additionalType` documented for external-vocabulary datatypes; also no mandated registry. |

*Use cases: ID20, ID8*

#### RDSAT — Dataset aspects

> Provide recommendations and mechanisms for data providers to describe datasets with a formal description of aspects (e.g. instrument/sensor used, spatial feature, observable property, quantity kind). Finer grained semantics will also allow dataset dimensions to be described, and distributions described using these semantics - for example how a dataset is composed of multiple subsets, such as a set of image bands or tiles, or parameterised filtering/subsetting services This requirement applies to catalogues of DCAT records, and is thus related to the concept of profiles, which are expected to define classification dimensions (use of controlled vocabularies in mandatory properties)

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | Nothing describes instruments, observable properties, quantity kinds or dataset dimensions. |
| CDIF | Met | `schema:variableMeasured` (propertyID, unitCode/unitText, measurementTechnique, min/max), `schema:measurementTechnique` at dataset level, cdifInstanceVariable / cdifRepresentedVariable (`cdi:measures`, `cdif:unitOfMeasureKind`, `cdif:uses_Concept`), cdifDataStructure dimension components, and the instrument building block. |

*Use cases: ID33, ID37, ID46, ID7*

#### RRS — Reference system

> Provide means to specify the reference system(s) used in a dataset.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | No reference-system property. GeoDCAT-AP adds one; DCAT 3 does not. |
| CDIF | Partial | `time:hasTRS` in temporalExtent gives the temporal reference system; spatial CRS is implicit in `geosparql:hasGeometry` rather than stated. |

*Use cases: ID28*

#### RSC — Spatial coverage

> Provide means to specify spatial coverage with geometries.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `dct:spatial` with `dcat:bbox` / `dcat:centroid`. |
| CDIF | Met | `schema:spatialCoverage` to spatialExtent (`geosparql:hasGeometry`, `schema:geo`). |

*Use cases: ID29*

#### RTC — Temporal coverage

> Allow for specification of the start and/or end date of temporal coverage.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `dct:temporal` with `dcat:startDate` / `dcat:endDate`. |
| CDIF | Met | `schema:temporalCoverage` to temporalExtent (`time:hasBeginning`, `time:hasEnd`, interval relations). |

*Use cases: ID27*

### Distribution

*5 requirements. DCAT 3: Met 2, Partial 2, Not met 1. CDIF: Met 3, Partial 2.*

#### RDIDF — Distribution definition

> Revise definition of Distribution. Make clearer what a Distribution is and what it is not. Provide better guidance for data publishers.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | DCAT 2 and 3 rewrote the Distribution definition and added `dcat:DataService` plus usage guidance. |
| CDIF | Partial | `schema:distribution` to the dataDownload block is defined but CDIF publishes no equivalent narrative guidance on what is and is not a distribution. |

*Use cases: ID34*

#### RDIS — Distribution schema

> Define a way to include identification of the schema the described data conforms to This may include rich information via extensions points, URI templates and parameters, dimensions and subsetting operations, dereferenceable identifiers of service behaviour profiles and canonical identifiers of well-known web service interfaces (e.g. OGC - WFS, WMS, OpenDAP, REST apis). Such a description may be provided through identifier of a suitable profile that defines interoperability conditions the distribution conforms to.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | `dct:conformsTo` on Distribution, `dcat:endpointDescription` on DataService. No parameter, template or subsetting model. |
| CDIF | Met | cdifDataStructure (`cdi:has_DataStructureComponent`), `cdif:hasPhysicalMapping`, cdifOpenApi (`oas:parameters`, `schema:urlTemplate`, `schema:potentialAction`) and `dcterms:conformsTo` on dataDownload. |

*Use cases: ID17, ID22, ID33, ID6*

#### RDISV — Distribution service

> Ability 1) to describe the type of distribution and 2) provide information about the type of service Such a description may be provided through a suitable profile identifier that defines a profile of the relevant service type.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `dcat:DataService` with `endpointURL`, `endpointDescription`, `dct:conformsTo`. |
| CDIF | Met | cdifOpenApi (`schema:serviceType`, `schema:httpMethod`, `oas:*`) and the webAPI block. |

*Use cases: ID18, ID21, ID6*

#### RDIC — Distribution container

> Provide a means to specify the container structure of a distribution for access methods that return lists, independently of the specification of the profile the list items conform to. Related to the distinction between dct:accessURL and dct:downloadURL. May be covered by service type, but specifically supports identification of lists vs items. (items have no container). lists may be wrapped in a structural element or not - so this also needs to be described.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | No container/list-vs-item construct. |
| CDIF | Partial | cdifStructuredDataSet (`cdif:encoding`), cdifLocatorMapping (`cdi:locator` for XPath/JSONPath) and cdifTabularData container attributes describe structure, but list-vs-item is not an explicit axis. |

*Use cases: ID18, ID51, ID6*

#### RDIP — Distribution package

> Define way to specify content of packaged files in a Distribution. For example, a set of files may be organised in an archive format and then compressed, but dct:hasFormat property only indicates the encoding type of the outer layer of packaging.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | `dcat:packageFormat` and `dcat:compressFormat` name the packaging but say nothing about contents. |
| CDIF | Met | cdifManifest enumerates package members with `schema:hasPart`, `schema:name`, `schema:size`, `schema:encodingFormat`, `spdx:checksum`. |

*Use cases: ID1*

### Relationships

*3 requirements. DCAT 3: Met 2, Not met 1. CDIF: Partial 3.*

#### RRDS — Related datasets

> Ability to represent the different relationships between datasets, including: versions of a dataset, collection of datasets, to describe their inclusion criteria and to define the 'hasPart'/'partOf' relationship, derivation, e.g. processed data that is derived from raw data this requriement to be rolled in here: Update method of Dataset: Indicate the update method of a Dataset description, e.g. whether each new dataset entirely supercedes previous ones (is stand-alone), or whether there is a base dataset with files that effect updates to that base.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `dct:hasPart`, `dcat:inSeries`, the version properties, `dct:source`, `dcat:qualifiedRelation`. |
| CDIF | Partial | `prov:wasDerivedFrom` and `schema:relatedLink` with `schema:linkRelationship` give typed relations; `schema:hasPart` appears in cdifManifest and cdifDataStructure. No collection or series model. |

*Use cases: ID32, ID47*

#### RPR — Project relation

> Provide a means to indicate the relation of Datasets to a project.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | No project relation. |
| CDIF | Partial | Only via `schema:funding`. |

*Use cases: ID49*

#### RDSCR — Datasets vs. Catalog relation

> Clarify the relationships between Datasets and zero, one or multiple Catalogs, e.g. in scenarios of copying, harvesting and aggregation of Dataset descriptions among Catalogs.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `dcat:CatalogRecord`, `foaf:primaryTopic`, `dct:source`, `dcat:catalog` cover copying, harvesting and aggregation. |
| CDIF | Partial | cdifCatalogRecord exists (`schema:includedInDataCatalog`, `schema:about`, `schema:sdDatePublished`, `schema:maintainer`) but is a datatype block, not part of Core or Discovery. |

*Use cases: ID35*

### Federation and citation

*5 requirements. DCAT 3: Met 3, Partial 2. CDIF: Met 3, Partial 1, Not met 1.*

#### RDSA — Dataset access

> Provide a way to specify access restrictions for both a dataset and a distribution. Provide a way to define the meaning of the access restrictions for a dataset or distribution and to specify what is required to access a dataset and distribution.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | `dct:accessRights` on Resource and Distribution, with no way to state what is required to obtain access. |
| CDIF | Met | `schema:conditionsOfAccess` in cdifCore plus the cdifAccess profile carrying ODRL (odrlOffer, odrlPolicy, odrlRule, odrlConstraint: `action`, `assignee`, `constraint`, `duty`, `permission`, `prohibition`), which states what is required, not just that access is restricted. |

*Use cases: ID17*

#### RDSC — Dataset citation

> Provide a way to specify information required for data citation (e.g., dataset authors, title, publication year, publisher, persistent identifier)

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `dct:creator`, `dct:title`, `dct:issued`, `dct:publisher`, `dct:identifier`. |
| CDIF | Met | `schema:creator` (with `@list` to preserve author order, which DCAT cannot express), `schema:name`, `schema:datePublished`, `schema:publisher`, `schema:identifier`. |

*Use cases: ID10*

#### RDSP — Dataset publications

> Provide a way to link publications about a dataset to the dataset.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `dct:isReferencedBy`. |
| CDIF | Met | `schema:subjectOf` and `schema:relatedLink` with `schema:linkRelationship`. |

*Use cases: ID9*

#### RPS — Publication source

> Provide a way to cite the original metadata with a dereferenceable identifier.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `dcat:CatalogRecord` with `dct:source` cites the upstream metadata record. |
| CDIF | Partial | cdifCatalogRecord has `schema:about` and `schema:sdDatePublished` but no pointer to a source record, and is not in Core. |

*Use cases: ID25*

#### RPC — Publication control

> Provide means to express rights relating to reuse of DCAT metadata

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | `dct:rights` can be asserted on a CatalogRecord to govern the metadata itself. |
| CDIF | Not met | `schema:license` and `schema:conditionsOfAccess` describe the data. Nothing in cdifCore or cdifCatalogRecord expresses rights over the metadata record. |

*Use cases: ID25*

### Alignment

*3 requirements. DCAT 3: Met 1, Partial 1, Not met 1. CDIF: Met 2, Partial 1.*

#### RVC — Related vocabularies comparision

> Analyse and compare similar concepts defined by vocabularies related to DCAT (e.g. VOID, Data Cube dataset).

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | DCAT 2/3 include an analysis of relationships to VoID, Data Cube, schema.org and others. |
| CDIF | Met | The building-block design is explicitly cross-vocabulary (DDI-CDI, PROV, SKOS, ODRL, OGC, Bioschemas) and each block records its source vocabulary. |

*Use cases: ID36*

#### RVM — Related vocabularies mapping

> Define guidelines how to create a DCAT description of a VOID or Data Cube dataset

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | Narrative relationship notes, no normative mapping guidance. |
| CDIF | Partial | cdifDataCube and `cdi:DimensionalDataStructure` cover the Data Cube case; no VoID treatment. |

*Use cases: ID36*

#### RES — Entailment of schema.org

> Define schema.org equivalents for DCAT properties to support entailment of schema.org compliant profiles of DCAT records.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | DCAT 3 publishes no normative schema.org mapping. The JRC dcat-ap-to-schema-org crosswalk is external and informal. |
| CDIF | Met | Satisfied by construction: CDIF is expressed in schema.org terms, with non-schema.org vocabularies reached through declared extension points. |

*Use cases: ID40*

### Meta-level and methodology

*4 requirements. DCAT 3: Met 1, Partial 1, Not met 2. CDIF: Met 3, Not met 1.*

#### RMDG — Metadata guidance rules

> Ability to express "guidance rules" or "creation rules" in DCAT

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | No mechanism to express creation or guidance rules. |
| CDIF | Met | Each block ships `description.md`, per-property `description` text, and `rules.shacl`; the profile is itself the expression of the guidance rules. |

*Use cases: ID42*

#### RQF — Qualified forms

> Define qualified forms to specify additional attributes of appropriate binary relations (e.g. temporal context). This requirement is still under review

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Met | `dcat:qualifiedRelation` with `dcat:Relationship` and `dcat:hadRole`; `prov:qualifiedAttribution`. |
| CDIF | Met | agentInRole (`schema:contributor` + `schema:roleName`) and labeledLink (`dcat:hadRole`, `dcterms:relation`). |

*Use cases: ID19*

#### RQFM — Mapping of qualified and non-qualified forms

> Specify mapping of qualified to non-qualified forms (lowering mapping). The reverse requires information (qualification) that might not be present/evident. This requirement is still under review

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | Marked under review in the UCR and never resolved. |
| CDIF | Not met | No lowering mapping between qualified and plain forms. |

*Use cases: ID19*

#### RPFDIST — Distribution profile

> There is a need to distinguish between distributions that package the entire dataset and those that support access to specific items, queries, and packaged downloads of data.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | `dcat:accessService` versus `dcat:downloadURL` implies the distinction without stating it. |
| CDIF | Met | Packaged downloads are described by cdifManifest; item/query access by cdifOpenApi with `schema:urlTemplate` and `oas:parameters`. Distinct shapes. |

*Use cases: ID51*

### Profiles — abstract requirements applying to the general definition of profiles

*15 requirements. DCAT 3: n/a 15. CDIF: Met 12, Partial 3.*

#### RPFNCTERMS — Named collections of terms

> Profiles are "named collections of properties" or metadata terms (if not RDF).

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable, not DCAT. |
| CDIF | Met | A building block is a named collection of terms defined by its `schema.yaml`. |

*Use cases: ID41*

#### RPFMBSPEC — Multiple base specifications

> A profile can have multiple base specifications.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | `allOf` with multiple `$ref` targets. |

*Use cases: ID37*

#### RPFPP — Profile of profiles

> One can create a profile of profiles, with elements potentially inherited on several levels.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | cdifCompositeProfile: CoreDiscovery, DiscoveryDataDescription, DiscoveryDataDescriptionStructure, cdifComplete. |

*Use cases: ID37*

#### RPFINHER — Profile inheritance

> Profiles may add to or specialise clauses from one or more base specifications. Such profiles inherit all the constraints from base specifications.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | `allOf`/`$ref` composition inherits every constraint of the referenced blocks. |

*Use cases: ID37, ID39*

#### RPFDAPUB-1 — Data publication according to different profiles-1

> Some data may conform to one or more profiles at once

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | A record can validate against several profiles simultaneously; the composite profiles institutionalise this. |

*Use cases: ID3*

#### RPFDAPUB-2 — Data publication according to different profiles-2

> Data publishers may publish data according to different profiles, either simultaneously (e.g. in one same data "distribution") or in parallel (e.g. via content negotiation)

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Content-negotiation deliverable. |
| CDIF | Partial | Simultaneous conformance works. Parallel publication by negotiation does not — CDIF has no conneg story. |

*Use cases: ID37*

#### RPFHRDEF — Human-readable definitions

> Profiles can have human-readable definitions of terms and input instructions.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | `description.md` per block plus a `description` on every property. |

*Use cases: ID46*

#### RPGRDC — Global rules for descriptive content

> There needs to be a property in the profile where the rules for the descriptive content can be provided. This would apply to the entire profile.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | Schema-level `title`/`description` carry profile-wide descriptive rules. |

*Use cases: ID42*

#### RPFVALID — Data validation

> A profile may be (partially) "implemented" by "schemas" (in OWL, SHACL, XML Schema...) that allow different levels of data validation

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | JSON Schema 2020-12 plus `rules.shacl` per block, with a built `resolvedSchema.json`; two validation levels. |

*Use cases: ID37*

#### RPFESIP — External specifications for individual properties

> Profiles should be able to indicate which external specifications are expected to be applied/have been applied to values of individual properties.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Partial | `dcterms:conformsTo`, `schema:inDefinedTermSet` and `cdif:takesValuesFrom` can point at external specs per property, but there is no systematic per-property declaration. |

*Use cases: ID43*

#### RPFVALIDR — Validity rules

> Profiles may provide rules governing value validity.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | JSON Schema `pattern`/`format`/`enum`, plus cdifValueDomain (`cdif:regularExpression`, `cdif:formatPattern`, min/max inclusive and exclusive). |

*Use cases: ID41*

#### RPFVLIST — Value lists for data elements

> Profiles may provide lists of values to pick from in order to populate data elements.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | cdifEnumerationDomain, cdifCodelist, skosConceptScheme, and JSON Schema `enum`. |

*Use cases: ID46*

#### RPFCARDR — Cardinality rules

> Profiles may provide rules on cardinality of terms (including "recommended").

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Partial | JSON Schema `required`/`minItems` express mandatory versus optional, but there is no way to mark a property *recommended*. DCAT-AP encodes this as SHACL `sh:Warning` severity and DCAT-US as a `requirementLevel` annotation; CDIF has no equivalent. |

*Use cases: ID41*

#### RPFDEPR — Dependency rules

> Profiles may express dependencies between elements of the vocabulary (if A then not B, etc.).

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | JSON Schema `if`/`then`, `dependentRequired` and `oneOf`; cdifDataDescription uses dataset-type branching this way. |

*Use cases: ID41*

#### RPFUI — User interface support

> Profiles can have what is needed to drive forms for data input or for user display.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | A `jsonforms` source tree exists for driving form input. |

*Use cases: ID46*

### Profiles — Profile distributions

*2 requirements. DCAT 3: n/a 2. CDIF: Met 2.*

#### RPFDOCU — Profile documentation

> A profile should have human-readable documentation that expresses for humans the main components of a profile, which can also be available as machine-readable resources (ontology or schema files, SHACL files, etc). This includes listing of elements in the profile, instructions and recommendations on how to use them, constraints that determine what data is valid according to the profile, etc.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | `description.md` for humans alongside `schema.yaml`, `rules.shacl` and `context.jsonld` for machines, per block. |

#### RPFSCHEMAS — Schema implementations

> Profiles may be written in or may link to a document or schema in a validation language (ShEx, SHACL, XMLschema).

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | JSON Schema and SHACL are both shipped. |

*Use cases: ID41*

### Profiles — Profile metadata

*12 requirements. DCAT 3: n/a 12. CDIF: Met 3, Partial 4, Not met 5.*

#### RPFMD — Profile metadata

> Profiles must be discoverable through a machine-readable metadata that describes what is offered and how to invoke the offered profiles.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | PROF deliverable. |
| CDIF | Met | `bblock.json` per block and `bblocks-config.yaml` for the register. |

*Use cases: ID5*

#### RPFNEG — Profile negotiation

> Enable the ability to negotiate the data profile via http, similar to the negotiation of metadata formats today.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Content-negotiation deliverable. |
| CDIF | Not met | No HTTP profile negotiation. |

*Use cases: ID30*

#### RPFLREL — Profile link relations

> There needs to be a way to encode the necessary relations using an http link header.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Content-negotiation deliverable. |
| CDIF | Not met | No Link-header relations. |

*Use cases: ID30*

#### RPFDISCO — Profile discoverability support

> Profiles should support discoverability via search engines.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Content-negotiation deliverable. |
| CDIF | Partial | Block documentation is published as HTML, but the profiles themselves carry no schema.org markup. |

*Use cases: ID40*

#### RPFSUP — Server-side profile support

> A client should be able to determine which profiles are supported by a server, and with which content types or other properties, in order to receive the one most appropriate for their use.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Content-negotiation deliverable. |
| CDIF | Not met | No server capability declaration. |

*Use cases: ID2*

#### RPFLUP — Lookup of profile details

> There should be a way to look up additional information about a profile - this may be machine readable for run-time mediation or used to develop or configure a client.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Content-negotiation deliverable. |
| CDIF | Met | The block register and `resolvedSchema.json` provide run-time lookup of profile detail. |

*Use cases: ID2*

#### RPFMDUSE — Profile metadata use

> According to the notes of group discussions, this requirement has not yet been approved. Metadata about server profile support can be used for discovery and mediated traversal via content negotiation.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Content-negotiation deliverable; UCR records it as not yet approved. |
| CDIF | Not met | No mediated traversal. |

*Use cases: ID5*

#### RPFSREQ — Profile selection on request

> When requesting a representation, a client must be able to specify which profile it prefers the representation to adhere to. This information about the requested profile is not a replacement for content type (e. g. application/xml), language (e. g. zh-Hant) nor any other negotiated dimension of the representation.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Content-negotiation deliverable. |
| CDIF | Not met | A client cannot request a profile. |

*Use cases: ID5*

#### RPFRCONF — Response conformance to multiple profiles

> Some data may conform to one or more profiles at once. A server can indicate that a response conforms to multiple profiles.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Content-negotiation deliverable. |
| CDIF | Partial | `dcterms:conformsTo` on cdifCatalogRecord and dataDownload can assert multi-profile conformance in-band, but not over HTTP. |

*Use cases: ID3*

#### RPFDPUB — Profile-aware data publication

> Data publishers may publish data according to different profiles, either simultaneously (e.g. in one same data "distribution") or in parallel (e.g. via content negotiation).

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Duplicate of RPFDAPUB-2. |
| CDIF | Partial | As RPFDAPUB-2: simultaneous yes, negotiated no. |

*Use cases: ID37*

#### RPFID — Profile identifier

> A profile must have an identifier that can be served with a response to an API or HTTP request.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Content-negotiation deliverable. |
| CDIF | Met | Each block has an `$id` and register identifier, assertable via `dcterms:conformsTo`. |

*Use cases: ID2*

#### RPFALIAS — Profile alias

> A short token to specify a profile may be used as long as there is a discoverable mapping from it to the profile's identifying URI.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | n/a | Content-negotiation deliverable. |
| CDIF | Partial | Block names act as short tokens, but there is no registered alias-to-URI mapping. |

*Use cases: ID5*

### unclassified - to be reviewed and moved

*4 requirements. DCAT 3: Partial 3, Not met 1. CDIF: Met 3, Partial 1.*

#### RPFN6.2 — Vocabulary extension points

> Define extension points (e.g. properties) for integration of external, specialized vocabularies.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | DCAT's open-world stance permits extension; `dct:conformsTo` is the only declared hook. |
| CDIF | Met | `schema:additionalProperty` and `schema:additionalType` as generic hooks, plus whole domain property sets (bioschemas, niaid, odrl, xas) composed through `$ref`. |

*Use cases: ID26*

#### RPFRP4.1 — Serialized Dataset identifier

> Define a means to identify a serialized DCAT Data(sub)set (i.e. a particular Distribution of a Dataset or its subset).

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | A Distribution need carry no identifier of its own. |
| CDIF | Met | dataDownload carries `schema:contentUrl` and `spdx:checksum`; cdifDataFingerprint adds `cdi:typeOfFingerprint`, `cdi:algorithmSpecification`, `cdi:value`. |

*Use cases: ID44*

#### RPFRP7.1 — Update method of Dataset

> Indicate the update method of a Dataset description, e.g. whether each new dataset entirely supercedes previous ones (is stand-alone), or whether there is a base dataset with files that effect updates to that base.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Not met | No update-method property. |
| CDIF | Partial | `schema:publishingPrinciples` is documented as covering mutability and update policy, but as prose rather than a typed value. |

*Use cases: ID47*

#### RPFN1.1 — TODO:Machine actionable link

> Metadata 'distribution' elements need a content model (see white paper referenced above) associated with links to communicate the protocol expected and the interchange formats (information model and encoding/serialization) available via the link. Content negotiation does not solve the problem very well because it requires the client to play a guessing game; it's better to explicitly identify the 'profile' for a link's behavior in the metadata so the client can pick the 'affordance' it needs.

| | Verdict | Basis |
|---|---|---|
| DCAT 3 | Partial | `dcat:accessService` and `dcat:endpointDescription` approach it; no per-link affordance model. |
| CDIF | Met | cdifOpenApi with `schema:potentialAction`, `schema:urlTemplate`, `schema:serviceType` and `oas:*`; labeledLink (`dcat:hadRole`) and linkRole (`schema:linkRelationship`) type the link itself. This is the requirement CDIF most directly implements. |

*Use cases: ID21*

## Method and provenance

**Requirement set.** Taken from the UCR source in the `w3c/dxwg` repository at commit `5c9e27bb` (2019-01-09), the last commit to `ucr/index.html` preceding publication of the Working Group Note on 17 January 2019. Every `<section class="requirement">` was extracted with its identifier, title and statement text.

**Count discrepancy — read this before citing a number.** The source yields **73** requirement sections. Secondary readings of the published Note's table of contents give 68, and three codes that appear in such readings — `RVSDF`, `RVSID`, `RPFDECOS` — **do not exist in the source**. Positionally they correspond to `RVer4`, `RVer7` and `RPFDPUB`. Four further requirements sit in a section headed *unclassified - to be reviewed and moved* (`RPFN6.2`, `RPFRP4.1`, `RPFRP7.1`, `RPFN1.1`) and are omitted from some counts. The published HTML could not be retrieved directly from this environment to byte-verify, so the source is treated as authoritative and all 73 are included here.

Note also that the source collapses the profile requirements into three `h2` groups, where the published Note renders them as subsections 6.10 through 6.14. Group headings here follow the source.

**DCAT 3 assessment** is against the Recommendation of 22 August 2024 and its core vocabulary only. Where a capability exists solely in DCAT-AP, GeoDCAT-AP or DCAT-US, that is stated rather than credited to DCAT 3.

**CDIF assessment** is against the `_sources` tree of the metadata building blocks as of this date: the `cdifCore` (24 properties), `cdifDiscovery` (6), `cdifAccess` (3), `cdifDataDescription` (5), `cdifDataStructure` (12), `cdifProvenance` (2) and `cdifManifest` (13) profiles, together with the cdifDataType, schemaorgProperties, provProperties, qualityProperties, odrlProperties and skosProperties blocks they compose. Property lists were read from each `schema.yaml`; semantics were read from the `description` fields and `description.md`. Where a `description` is absent — `schema:funding` and `schema:subjectOf` in cdifCore both lack one — the assessment infers intent from the property name and the referenced block, and should be checked.

**Known limitation.** Assessments are at the level of *is there a mechanism*, not *is the mechanism adequate* or *is it used*. No instance data was validated.

## Sources

- *Dataset Exchange Use Cases and Requirements*, W3C Working Group Note, 17 January 2019 — <https://www.w3.org/TR/dcat-ucr/>; source at <https://github.com/w3c/dxwg>
- *Data Catalog Vocabulary (DCAT) — Version 3*, W3C Recommendation, 22 August 2024 — <https://www.w3.org/TR/vocab-dcat-3/>
- Albertoni, Browning, Cox, Gonzalez-Beltran, Perego & Winstanley, *The W3C Data Catalog Vocabulary, Version 2: Rationale, Design Principles, and Uptake*, Data Intelligence 6(2):457 — <https://doi.org/10.1162/dint_a_00241>; preprint <https://arxiv.org/abs/2303.08883>
- CDIF metadata building blocks, `_sources` tree (local working copy)


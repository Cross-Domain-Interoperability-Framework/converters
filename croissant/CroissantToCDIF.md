# Croissant to CDIF DataDescription Mapping

## Overview

This is the **inverse** of [`CDIFtoCroissant.md`](CDIFtoCroissant.md): it maps from
[Croissant](https://docs.mlcommons.org/croissant/docs/croissant-spec-1.1.html)
JSON-LD (version **1.1 or 1.0** — the converter accepts either on input) to the
current **CDIF** `cdif:`-namespaced schema (a profile of schema.org extended with
DDI-CDI, CSVW, PROV, and DQV).

`ConvertFromCroissant.py` emits the current CDIF shape:
`cdif:hasPhysicalMapping` (with `cdif:index` / `cdif:formats_InstanceVariable`),
single-valued `cdif:physicalDataType`, `cdi:hasIntendedDataType`, and
`cdif:hasPrimaryKey` / `cdif:has_PrimaryKey`. The legacy `cdi:hasValueDomain` / `cdi:locator` /
`cdi:hasIndex` / `cdi:ValueMapping` forms are no longer produced.

The conversion is **lossy in the Croissant→CDIF direction** because Croissant
does not carry information that CDIF requires or commonly expects:

- CDIF normally wraps a DOI as a structured `schema:identifier` `PropertyValue` —
  Croissant carries it only as `citeAs` (string) + `url`. The inverse recovers
  the DOI by regex from those.
- CDIF distinguishes `cdi:TabularTextDataSet` / `cdi:StructuredDataSet` /
  `schema:DataDownload` and may carry CSVW parsing properties. Croissant carries
  CSV semantics *implicitly* inside `cr:Transform`, so the inverse cannot
  reconstruct the CSVW block — only typed-column membership in a RecordSet.
- CDIF discovery features (`schema:spatialCoverage`, `temporalCoverage`,
  `measurementTechnique`, PROV `wasGeneratedBy`) have no Croissant equivalents.
  The inverse leaves these absent; downstream curation is required.
- Croissant has no data-structure layer. The inverse builds a
  `cdi:isStructuredBy` structure only where keys call for one (see
  [Data structure and foreign keys](#data-structure-and-foreign-keys)).

The inverse handles the **lossless subset**: discovery-level dataset metadata,
distribution / file inventory (flat and archive), tabular RecordSet →
`cdi:InstanceVariable` + `cdif:hasPhysicalMapping`, `cr:RecordSet.key` →
a primary key, and `cr:Field.references` → `cdif:ForeignKey`.

> **Validation target depends on content.** A Croissant document **with** a
> `cr:RecordSet` produces a Data-Description-level CDIF document (validate against
> the DataDescription schema, which requires `schema:variableMeasured`). A
> Croissant document **without** any RecordSet produces a Discovery-level CDIF
> document (validate against the Discovery schema).

## Property Mapping: Dataset Level

### Direct mappings (schema.org shared surface)

| Croissant property | CDIF property | Notes |
|--------------------|---------------|-------|
| `name` | `schema:name` | Both required |
| `description` | `schema:description` | Required in Croissant, optional in CDIF — passed through |
| `url` | `schema:url` | Direct |
| `license` | `schema:license` | If absent or OGC `nil:missing`, output is empty |
| `datePublished` | `schema:datePublished` | Direct |
| `dateModified` | `schema:dateModified` | Direct; **fallback** to `datePublished` then `dateCreated` when absent (CDIF discovery requires it). Never fabricated — if the source carries no date at all, it stays absent (enrich upstream; see note below). |
| `creator` | `schema:creator` (wrapped in `@list`) | CDIF uses `@list` so authorship order is RDF-stable; each agent's `@type` is an array, as for every agent (publisher, funder, affiliation) |
| `keywords` | `schema:keywords` | Direct (kept as array) |
| `publisher` | `schema:publisher` | Direct |
| `version` | `schema:version` | Omitted if Croissant's placeholder `"not assigned"` |
| `inLanguage` | `schema:inLanguage` | Direct |
| `sameAs` | `schema:sameAs` | Direct |
| `includedInDataCatalog` | `schema:includedInDataCatalog` | Direct (shared schema.org property) |
| `funding` | `schema:funding` | Always an array of `MonetaryGrant`; description / funder mapped through. A grant with only a description also takes it as `schema:name`, since CDIF requires a name, funder or identifier. A grant with several funders is written once per funder. A funder given only as a web or `urn:` link (a string, or an unnamed node's `@id` or `url`) becomes an `{"@id": …}` reference; one given as other text is dropped. An identifier given as text or as a `PropertyValue` (`value`, `url`, `propertyID`) becomes a `schema:PropertyValue`; other properties of the source node are not carried |

### Croissant `citeAs` and `url` → CDIF `schema:identifier`

Croissant has no structured `PropertyValue` identifier. The inverse reconstructs
one when a DOI can be detected:

1. Inspect `citeAs`, `url`, and `@id` for `10.NNNN/...` strings.
2. If found, emit a `schema:PropertyValue` with `schema:propertyID`
   (`https://registry.identifiers.org/registry/doi`), `schema:value`, and
   `schema:url` (`https://doi.org/...`).
3. **Fallback** (no DOI): CDIF discovery requires `schema:identifier`, but most
   HF/Kaggle/OpenML records carry no DOI. The inverse then uses the dataset's
   landing-page `url` (or `@id`) as a string `schema:identifier` so the record is
   usable; `citeAs` is still preserved verbatim as `schema:citeAs`. A curator can
   replace the URL identifier with a DOI later.

### Conformance declaration

Croissant declares conformance as a single top-level `dct:conformsTo` URI
(`http://mlcommons.org/croissant/1.1` or `/1.0`). CDIF requires a catalog record
on `schema:subjectOf`. The inverse emits the current CDIF catalog-record shape
(conformance URIs at **1.1**). `dcterms:conformsTo` is **derived from the record's
actual content** by [`detect_conformance.py`](../validation/detect_conformance.py) — a presence
SPARQL ASK per profile, gated by that profile's content SHACL — rather than a
hardcoded list. In practice this means:

- `core` + `discovery` — any named, identified dataset (the discovery baseline).
- `data_description/2.0` — only when the record carries `cdi:InstanceVariable`-typed
  variables. A source Croissant with no fields (e.g. an OpenML record set with zero
  `field`s) produces a discovery-level record that neither inserts an empty
  `schema:variableMeasured` nor claims `data_description`.
- `manifest/2.0` — when an archive distribution carries member files (a
  `schema:DataDownload` with `schema:hasPart`), as Kaggle zip datasets do. Detected
  automatically; no source-specific special-casing.
- `data_structure/2.0` — when keys gave a file a `cdi:isStructuredBy` structure.

```json
"schema:subjectOf": {
  "@type": ["schema:Dataset"],
  "schema:additionalType": ["dcat:CatalogRecord"],
  "schema:about": {"@id": "<dataset @id>"},
  "dcterms:conformsTo": [
    {"@id": "https://w3id.org/cdif/core/2.0"},
    {"@id": "https://w3id.org/cdif/discovery/2.0"}
    // + data_description/2.0 when InstanceVariable variables are present
    // + manifest/2.0 when an archive distribution has hasPart member files
    // + data_structure/2.0 when keys produced a data structure
  ]
}
```

(If `rdflib`/`detect_conformance` is unavailable, the converter falls back to the
prior heuristic: `core` + `discovery`, plus `data_description` iff variables exist.)

The Croissant `conformsTo` is dropped (it is not part of CDIF's conformance
space) but the source is recorded in a `prov:wasDerivedFrom` block for
traceability.

## Property Mapping: Distribution / Files

### Concept correspondence

| Croissant type | CDIF type | Notes |
|----------------|-----------|-------|
| `cr:FileObject` (top-level, with `contentUrl`) | `schema:DataDownload` | Direct |
| `cr:FileObject` with `containedIn: {"@id": …}` | `schema:hasPart` item of the containing archive `DataDownload` | Grouped by `containedIn`; archive carries the components |
| `cr:FileObject` referenced by a `cr:RecordSet` field source | `schema:DataDownload` of `@type [schema:DataDownload, cdi:TabularTextDataSet]` | The attached RecordSet promotes the file to tabular type |
| `cr:FileSet` (glob-based, e.g. Hugging Face parquet shards) | `schema:DataDownload` | Inherits `contentUrl` from the `containedIn` parent FileObject (the FileSet itself has none); the `includes` glob is recorded in `schema:description`. The host parent FileObject is not emitted separately. RecordSet fields whose `source` is a `fileSet` anchor their `cdif:hasPhysicalMapping` here. |

### Archive reconstruction

Croissant's archive idiom is "flat `FileObject` list with `containedIn`
back-references". CDIF's is "one parent `DataDownload` (`application/zip`) whose
`schema:hasPart` contains the components". The inverse pivots the structure:
groups components by `containedIn.@id`, finds the parent FileObject (the one with
the real `contentUrl`), and emits one archive `DataDownload` whose `schema:hasPart`
collects the components as `schema:MediaObject`s.

### File metadata mapping

| Croissant | CDIF | Notes |
|-----------|------|-------|
| `contentUrl` | `schema:contentUrl` | Direct; omitted if OGC `nil:inapplicable` |
| `encodingFormat` (string) | `schema:encodingFormat` (array of one) | CDIF prefers array |
| `contentSize` (string `"NNN B"`) | `schema:size` (`QuantitativeValue`) | Parsed to number + unit |
| `sha256` | `spdx:checksum` (`spdx:algorithm` `SHA256`) | The nil placeholder (64 zeros) the forward converter emits is recognized and dropped |
| `md5` | `spdx:checksum` (`spdx:algorithm` `MD5`) | Used when `sha256` is absent |
| `name` | `schema:name` | Direct |
| `description` | `schema:description` | Direct |

## Property Mapping: Data Description (RecordSet/Field → variables)

### Concept correspondence

| Croissant concept | CDIF concept | Notes |
|-------------------|--------------|-------|
| `cr:RecordSet` | attaches `cdif:hasPhysicalMapping` to the source file | One RecordSet maps to one source file (or file set); fields are assumed to draw from one source |
| `cr:Field` | `cdi:InstanceVariable` (added to top-level `schema:variableMeasured`) + a `cdif:hasPhysicalMapping` entry | Field name → variable name + column |
| `cr:Field.source.fileObject` **or** `cr:Field.source.fileSet` | the anchor file/file-set the mapping attaches to | Both are resolved (CSV via `fileObject`; Hugging Face parquet shards via `fileSet`) |
| `cr:Field.source.extract.column` | the field's column identity | Reflected in the variable name and mapping order |
| `cr:Field.dataType` | `cdif:physicalDataType` (single value) + `cdi:hasIntendedDataType` (IRI) on the variable, and `cdif:physicalDataType` on the mapping | Reverse-mapped via the table below |
| `cr:Field.description` | `schema:description` on the InstanceVariable | Direct |
| `cr:Field.equivalentProperty` (URL) | `schema:propertyID` on the InstanceVariable | Direct |
| `cr:RecordSet.key` | `cdif:Key` / `cdif:isComposedOf`, as `cdif:hasPrimaryKey` or `cdif:has_PrimaryKey` | See [Primary key](#primary-key) |
| `cr:Field.references` | `cdif:ForeignKey` on a `cdi:WideDataStructure` | See [Data structure and foreign keys](#data-structure-and-foreign-keys) |

### Data type mapping (inverse)

| Croissant type | CDIF / XSD type |
|----------------|-----------------|
| `sc:Float` | `xsd:decimal` |
| `sc:Integer` | `xsd:integer` |
| `sc:Date` | `xsd:dateTime` |
| `sc:Boolean` | `xsd:boolean` |
| `sc:Text` (or any unmapped) | `xsd:string` |
| `sc:URL` | `xsd:anyURI` |

`sc:Float`'s precise width (float/double) is not recoverable; the inverse uses
`xsd:decimal` as the safe default.

### InstanceVariable + physical mapping emission

For each `cr:Field` the inverse emits a `schema:variableMeasured` entry:

```json
{
  "@id": "#<sluggified field name>",   // numeric suffix when a name repeats
  "@type": ["schema:PropertyValue", "cdi:InstanceVariable"],
  "schema:name": "<field name>",
  "schema:description": "<field description>",
  "schema:propertyID": "<equivalentProperty URL, if present>",
  "cdif:physicalDataType": "<xsd:type>",
  "cdi:hasIntendedDataType": {"@id": "<XMLSchema datatype IRI>"}
}
```

…and a `cdif:hasPhysicalMapping` entry on the source file's node (the `hasPart`
item, or the `DataDownload` directly for a non-archive single file):

```json
{
  "cdif:index": <ordinal, 1..N in field order>,
  "cdif:physicalDataType": "<xsd:type>",
  "cdif:formats_InstanceVariable": {"@id": "#<sluggified field name>"}
}
```

There is no per-mapping `cdi:locator` in the current schema; the column identity
is the variable name and the `cdif:index` ordinal.

### Primary key

`cr:RecordSet.key` (a single `{"@id": …}` or a list) is resolved to the key
fields' InstanceVariable `@id`s and written as a `cdif:Key`: one
`cdi:ComponentPosition` per field, in key order. Where it goes depends on the
number of tables (RecordSets with file-backed fields):

- **One table:** dataset-level `cdif:hasPrimaryKey`. If the table also has a
  data structure (a self-referencing foreign key), the dataset-level property is
  an `@id` reference to the structure's key.
- **Several tables:** no dataset-level key, since no one key identifies every
  data instance. Each key is the `cdif:has_PrimaryKey` of its own file's data
  structure (below).

```json
"cdif:hasPrimaryKey": {
  "@type": ["cdif:Key"],
  "cdif:isComposedOf": [
    {"@type": ["cdi:ComponentPosition"], "cdi:indexes": {"@id": "#patientid"}, "cdi:value": 1},
    {"@type": ["cdi:ComponentPosition"], "cdi:indexes": {"@id": "#measurename"}, "cdi:value": 2}
  ]
}
```

A key is skipped, with a warning under `-v`, unless all of it resolves to fields
of its RecordSet in one file.

## Data structure and foreign keys

CDIF states a foreign key on a data structure, so a resolvable
`cr:Field.references` gives a `cdi:isStructuredBy` structure to both the
referencing file and the referenced file. In a dataset of several tables, every
file with a declared key gets one too. Other files get none.

The structure is a `cdi:WideDataStructure` (a Croissant table is wide).
`cdi:DataStructure` is abstract and is never emitted.

| Part of the structure | Source |
|-----------------------|--------|
| `cdi:has_DataStructureComponent` | One component per variable of the file: `cdi:IdentifierComponent` for a key field, `cdi:MeasureComponent` for every other field, foreign key columns included. Each names its variable with `cdif:isDefinedBy_Variable` |
| `cdif:has_PrimaryKey` | The file's key as a `cdif:Key` with an `@id`; `cdif:isComposedOf` holds one `cdi:ComponentPosition` (`cdi:indexes`, `cdi:value`) per key field |
| `cdif:has_ForeignKey` | One `cdif:ForeignKey` per referencing field: `cdif:isComposedOf` (that variable, position 1) and `cdif:references_PrimaryKey` (the target key's `@id`) |

```json
"cdi:isStructuredBy": {
  "@type": ["cdi:WideDataStructure"],
  "cdi:has_DataStructureComponent": [
    {"@type": ["cdi:IdentifierComponent"], "cdif:isDefinedBy_Variable": {"@id": "#userid"}},
    {"@type": ["cdi:MeasureComponent"], "cdif:isDefinedBy_Variable": {"@id": "#movieid"}}
  ],
  "cdif:has_PrimaryKey": {
    "@type": ["cdif:Key"],
    "@id": "#userid-primarykey",
    "cdif:isComposedOf": [
      {"@type": ["cdi:ComponentPosition"], "cdi:indexes": {"@id": "#userid"}, "cdi:value": 1}
    ]
  },
  "cdif:has_ForeignKey": [{
    "@type": ["cdif:ForeignKey"],
    "cdif:isComposedOf": [
      {"@type": ["cdi:ComponentPosition"], "cdi:indexes": {"@id": "#movieid"}, "cdi:value": 1}
    ],
    "cdif:references_PrimaryKey": {"@id": "#movieid_2-primarykey"}
  }]
}
```

The referenced key is the target RecordSet's declared `key`. A target RecordSet
with no `key` gets one made of the referenced field, provided only one of its
fields is referenced anywhere. A reference is skipped, with a warning under
`-v`, when:

- either end has no CDIF variable (for example a RecordSet with inline `cr:data`);
- the referenced field is only part of a composite key, or is not the declared key;
- two different fields of a keyless RecordSet are referenced;
- its file already has a different primary key (a structure holds one; the same
  rule drops a second declared key in one file).

Croissant carries no information for `cdi:RepresentedVariable` nodes, attribute
components, or the Long and Dimensional layouts; those are left to curation.

## Croissant Properties with No CDIF Equivalent (Dropped)

A warning is printed when any of these is encountered.

| Croissant | What it carried | Why dropped |
|-----------|-----------------|-------------|
| `cr:Field.subField` / `cr:parentField` | Nested fields | CDIF variables are flat |
| `cr:Field.isArray` / `cr:arrayShape` | Array-valued field (1.1) | No equivalent in `cdi:InstanceVariable` |
| `cr:Transform` (regex, separator, delimiter, jsonPath) | Post-extraction transforms | CDIF describes data as-is |
| `cr:Split` | Train/validation/test partition | ML-only concept |
| `cr:Label`, `cr:BoundingBox`, `cr:SegmentationMask` | ML annotation types | Not part of science-data metadata |
| `cr:isLiveDataset` | Continuously-updated flag | No equivalent |
| `cr:data` / `cr:examples` | Inline records / examples | No equivalent |

## CDIF Features Not Produced (Required Curation)

| CDIF property | Reason |
|---------------|--------|
| `schema:measurementTechnique` | Croissant has no equivalent. Add a `DefinedTerm` after conversion. |
| `schema:spatialCoverage` / `schema:temporalCoverage` | No Croissant spatial/temporal block. |
| `prov:wasGeneratedBy` | No Croissant PROV activity block. |
| `prov:wasDerivedFrom` | Populated only with the source Croissant reference (traceability), not science-data lineage. |
| `dqv:hasQualityMeasurement` | No Croissant DQV equivalent. |
| CSVW table block (`csvw:delimiter`, `csvw:header`, …) | Croissant carries CSV semantics implicitly via Transform. |
| `cdi:isStructuredBy` for files that need no key structure; Attribute and Dimension component roles | Croissant fields have no role/structure concept. |
| `cdif:uses` (concept reference) | Croissant has only `equivalentProperty` URL → emitted as `schema:propertyID`; a SKOS concept scheme is unknown. |

## Pass-through Properties

If the input Croissant document carries CDIF pass-through properties (the forward
converter preserves these verbatim: `prov:wasGeneratedBy`, `prov:wasDerivedFrom`,
`dqv:hasQualityMeasurement`, `schema:spatialCoverage`, `schema:temporalCoverage`,
`schema:measurementTechnique`, `schema:contributor`, `schema:subjectOf`), they are
copied directly into the CDIF output — this is how the inverse round-trips the
forward converter's pass-throughs.

## Round-tripping

A CDIF document converted to Croissant 1.1 and back is **not** byte-identical to
the original. Properties outside Croissant's vocabulary (unless preserved as
pass-through) are lost. The forward converter reads only the primary key at
data-structure level, so foreign keys do not survive the trip back to Croissant.
Round-tripping is intended for CDIF→Croissant→CDIF integrity testing of the
**discovery and tabular subset** (which validates against the current Discovery /
DataDescription schemas), not for general lossless transport.

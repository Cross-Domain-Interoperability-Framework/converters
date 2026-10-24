# DDI-CDI XML → CDIF

[`ddicdi_to_cdif.py`](ddicdi_to_cdif.py) converts **DDI Cross-Domain Integration
(DDI-CDI) 1.0 XML** instances to CDIF JSON-LD.

- **Root / namespace:** `<cdi:DDICDIModels>`, `http://ddialliance.org/Specification/DDI-CDI/1.0/XMLSchema/`
- **Schema:** `ddi-cdi.xsd` (DDI-CDI 1.0 XML encoding)

```bash
python ddicdi_to_cdif.py Examples/XML/SPSS_Example.xml -o out.json
python ddicdi_to_cdif.py input.xml --id https://catalog.example/dataset/123
```

`../DDI/ddi2cdif.py` also routes here: it sniffs a `<cdi:DDICDIModels>` root as
`ddi-cdi-xml` and calls this converter, producing byte-identical output. Use the
front door when you do not know the flavor up front, this script when you do.
The **JSON-LD** serialisation has its own reader,
[`ddicdi_jsonld_to_cdif.py`](ddicdi_jsonld_to_cdif.py) (below); the dispatcher
sniffs a JSON document with a top-level `DDICDIModels` list as `ddi-cdi-jsonld`
and routes it there. Other RDF / JSON-LD forms are still refused.

## DDI-CDI JSON-LD → CDIF

[`ddicdi_jsonld_to_cdif.py`](ddicdi_jsonld_to_cdif.py) reads the compact JSON-LD
form (DDI-CDI 1.1beta context) written by the CODATA export
(`https://ai.codata.org/export`): a Dublin Core / PROV header for one data file,
plus a flat `DDICDIModels` list of objects linked by local `#…` ids. It reads
the objects as plain JSON, indexed by `@id`, and reuses the XML converter's
role and structure-type tables.

```bash
python ddicdi_jsonld_to_cdif.py Examples/107910_DVN_CWOZTP.jsonLD -o Examples/cdif/cdif_107910_DVN_CWOZTP.json
```

| DDI-CDI JSON-LD | CDIF |
|---------|------|
| header `dct:title` / `description` / `license` / `language` / `identifier` | `schema:name` / `description` / `license` / `inLanguage` / `identifier` |
| header `dct:isPartOf` | `schema:isPartOf` (the Dataverse dataset DOI) |
| header `prov:wasDerivedFrom` | `prov:wasDerivedFrom`, and the distribution's `schema:contentUrl` |
| header `schema:contentSize` / `schema:checksum` | distribution `schema:contentSize` / `spdx:checksum` |
| `InstanceVariable` name, `definition` (when it is not just the name) | `schema:variableMeasured` `schema:name` / `schema:description` |
| `SubstantiveValueDomain/recommendedDataType` | variable `cdif:physicalDataType` (the `xsd:` IRI) |
| `ValueAndConceptDescription/classificationLevel` | `cdi:classificationLevel` |
| `MeasureComponent` etc. `isDefinedBy_RepresentedVariable` | `cdif:role` |
| `TabularTextDataSet` `isDelimited` / `delimiter` / `hasHeader` / `headerRowCount` / `recordCount` | distribution `cdi:TabularTextDataSet` properties; `countRows` / `countColumns` |
| `TextMapping` + `PhysicalMappingPosition` | `cdif:hasPhysicalMapping` (`cdif:TextMapping`: index, format, `physicalDataType`, `maximumLength`) |
| `WideDataStructure` + components + `ComponentPosition` | `cdi:isStructuredBy` |

Not carried over: the data sample (`DataPoint` / `InstanceValue`); the
`nullSequence` list, which the export writes identically on every column and
CDIF's `cdi:nullSequence` holds only one string; the character encoding. The
source has no date, so `schema:dateModified` is the `1900-01-01` placeholder.

### RepresentedVariables for the CODATA variableindex

[`extract_represented_variables.py`](extract_represented_variables.py) builds one
`cdi:RepresentedVariable` document per variable that a page of the
[variableindex](https://ai.codata.org/variableindex) API lists, from the DDI-CDI
JSON-LD export of that variable's file. The output validates against the
metadataBuildingBlocks `cdifRepresentedVariable` schema.

```bash
curl -s "https://ai.codata.org/api/variableindex/data?limit=100&offset=0" -o page.json
python extract_represented_variables.py Examples/107910_DVN_CWOZTP.jsonLD page.json -o Examples/representedVariables
```

| DDI-CDI JSON-LD | `cdi:RepresentedVariable` |
|---|---|
| `InstanceVariable` name / displayLabel / definition | `cdif:name` / `cdif:displayLabel` / `cdif:definition` |
| `SubstantiveValueDomain/recommendedDataType` | `cdi:takesSubstantiveValuesFrom` → `cdif:SubstantiveValueDomain` `cdif:recommendedDataType` (`xsd:*`) |
| `ValueAndConceptDescription/classificationLevel` | … `cdi:isDescribedBy` → `cdi:classificationLevel` |

`Examples/representedVariables/` holds the 100 the index's first page lists, all
from `V-Dem-CY-Full+Others-v13.csv`. Each `@id` is
`<export URL>#representedVariable-<name>`, following the export's
`#instanceVariable-<name>` pattern; the export does not contain these nodes, so
the `@id` does not yet dereference to them. The export gives no concept links,
so none carries the `cdif:uses_Concept` the building block's SHACL recommends.

Profile scope is decided **per content** via
[`detect_conformance`](../validation/detect_conformance.py).

## DDI-CDI XML in one paragraph

Unlike DDI Codebook (a nested document), a DDI-CDI XML instance is a **flat list
of typed objects** under `DDICDIModels`, linked by **`ddiReference`**. Every
object and reference is identified by a `dataIdentifier` +
`registrationAuthorityIdentifier` + `versionIdentifier` triple. The converter
indexes every object by its `dataIdentifier` and resolves references to
reassemble the graph. Reified associations appear as their own objects/elements
(`DataStructure_has_DataStructureComponent`,
`DataStructureComponent_isDefinedBy_RepresentedVariable`, `CodeList_has_Code`, …).

DDI-CDI is also far more verbose than what CDIF embeds: a `cdi:InstanceVariable`
nests `identifier → ddiIdentifier`, `name → name`, `displayLabel →
languageSpecificString → content`, `hasIntendedDataType`, and value-domain
references — which CDIF collapses into a simplified `schema:variableMeasured` /
`cdi:InstanceVariable`. **The converter is a semantic down-shift** from the full
DDI-CDI graph to the CDIF profile form, using the class/attribute crosswalk
encoded in the sibling **`ucmism2m`** project
(`configuration/ddi-cdi2cdif*_mapping.json`) as the mapping authority.

## Status — phased build toward broadest end-to-end

**Phase 1 (done — walking skeleton):**

| DDI-CDI | CDIF |
|---------|------|
| `DataStore` / `WideDataSet` / `PhysicalDataSet` | `schema:Dataset` |
| `InstanceVariable` | `schema:variableMeasured` / `cdi:InstanceVariable` |
| `…/name/name` | `schema:name` |
| `…/displayLabel/…/content` | `schema:description` |
| `…/hasIntendedDataType/name` (SPSS/Stata format) | `cdi:hasIntendedDataType` (`xsd:*`) |
| `Measure`/`Identifier`/`Dimension`/`AttributeComponent` `_isDefinedBy_` the variable | `cdif:role` |
| `PhysicalDataSet` (no download URL) | `schema:distribution` (`DataDownload`, `nil:missing`) |

**Phase 3 (done — data structure):**

| DDI-CDI | CDIF |
|---------|------|
| `WideDataStructure` / `LongDataStructure` / `DimensionalDataStructure` | `cdi:isStructuredBy` → `cdi:<X>DataStructure` on the distribution (no extra `@type`: `cdi:StructuredDataSet` marks a hierarchical XML/JSON file in CDIF) |
| `DataStructure_has_DataStructureComponent` → `Identifier`/`Measure`/`Dimension`/`AttributeComponent` | `cdi:has_DataStructureComponent` → `cdi:<Component>` |
| `DataStructureComponent_isDefinedBy_RepresentedVariable` | `cdif:isDefinedBy_Variable` → `{@id}` of the variable |
| `DataStructure_has_PrimaryKey` → `PrimaryKeyComponent` → component → variable | `cdif:has_PrimaryKey` → inline `cdif:Key` (`cdif:isComposedOf` → `cdi:ComponentPosition` with `cdi:indexes` the variable, `cdi:value` its 1-based position) |

`detect_conformance` now adds **`data_structure/1.1`** to the declared profiles.

**Phase 2 (done — value domains + code lists):**

| DDI-CDI | CDIF |
|---------|------|
| `InstanceVariable` -takesSubstantiveValuesFrom-> `SubstantiveValueDomain` | `cdi:takesSubstantiveValuesFrom` → `cdif:SubstantiveValueDomain` (on the variable) |
| `SubstantiveValueDomain` -takesValuesFrom-> `EnumerationDomain`/`CodeList` | `cdif:takesValuesFrom` → `cdif:EnumerationDomain` → `cdif:references` → `skos:ConceptScheme` |
| `CodeList_has_Code` → `Code` -denotes-> `Category`, -uses-> `Notation` | `skos:hasTopConcept` → `skos:Concept` (`skos:notation` = notation, `skos:prefLabel` = category label) |
| `CodeList` identifier triple / `name` | `schema:identifier` (DDI URN `urn:ddi:<agency>:<id>:<version>`) / `skos:prefLabel` (the name, leading `#` dropped) |
| _(no date or licence in DDI-CDI)_ | `schema:dateModified` `1900-01-01` and `schema:license` OGC `nil/missing`, the dataset's unknown values — CDIF requires both on a code list |
| `CodeList` with no codes | not emitted (a CDIF code list needs at least one concept); the variable keeps its `cdi:classificationLevel` |
| `ValueAndConceptDescription/classificationLevel` (Nominal/Continuous/…) | `cdi:classificationLevel` |

Coded (Nominal) variables get their full concept list (e.g. `maritalb`:
`1.0`→"Legally married", …); Continuous/identifier variables carry the
classification level but no code list. Sentinel (missing-value) domains are
deferred to a later pass.

**Phase 5 (done — provenance):**

| DDI-CDI | CDIF |
|---------|------|
| `Activity` (invoked by `Sequence`) + sub-activities | `prov:wasGeneratedBy` → `["schema:Action","prov:Activity"]` |
| `name` / `description` | `schema:name` / `schema:description` |
| `entityUsed/uri` (over the activity tree) | `prov:used` (`{@id}`) |
| `entityProduced/uri` | `schema:result` (`{@id}`) |
| `Activity_has_Step` → `Step` (name, description, script) | `schema:actionProcess` → `schema:HowTo` / `schema:step` (`schema:HowToStep`) |
| `Organization/organizationName` | `schema:agent` |
| `ProductionEnvironment/name` | `schema:location` |

Lights up `Process_Example_CDI.xml` (58 steps, 13 inputs, agent, environment) →
`detect_conformance` adds **`provenance/1.1`**.

**Phase 4 (done — physical mappings):**

| DDI-CDI | CDIF |
|---------|------|
| `PhysicalSegmentLayout` `isDelimited` / `isFixedWidth` | `cdi:isDelimited` / `cdi:isFixedWidth` on the distribution |
| `InstanceVariable_has_ValueMapping` → `ValueMapping`, positioned by `ValueMappingPosition/value` | `cdif:hasPhysicalMapping` entry: `cdif:index` (column), `schema:name`, `cdif:physicalDataType`, `cdif:formats_InstanceVariable` → the variable |

**Phase 6 (done — discovery enrichment + sentinel value domains):**

| DDI-CDI | CDIF |
|---------|------|
| `catalogDetails/title` (dataset-level, when present) | `schema:name` (else the DDI-CDI name / activity name / file stem) |
| `PhysicalDataSet/physicalFileName` | `schema:name` of the distribution |
| `DataStore/recordCount` | `schema:additionalProperty` ("record count") on the distribution |
| `InstanceVariable` -takesSentinelValuesFrom-> `SentinelValueDomain` → CodeList | `cdi:takesSentinelValuesFrom` → `cdif:SentinelValueDomain` → `cdif:takesValuesFrom` → `cdif:EnumerationDomain` |

The two domains hang off different properties —
`cdi:takesSubstantiveValuesFrom` and `cdi:takesSentinelValuesFrom` — so each
variable can list *both*, and which is which is in the property name — e.g.
Continuous `nwspol` has no substantive code list (DDI-CDI gives it an empty one,
which is not carried over) but a 3-code sentinel list
(Refusal / Don't know / No answer). This follows the CDIF value-domain guidance in
[profile-datadescription#1](https://github.com/Cross-Domain-Interoperability-Framework/profile-datadescription/issues/1#issuecomment-5255033824):
substantive always, sentinel when the source distinguishes it, so a processor need
not merge the two.

**Out of scope:** `DataPoint` / `InstanceValue` (the actual data values) — CDIF is
a *metadata* framework, so the data itself is not carried over.

## Test fixtures

`Examples/XML/` holds two of the **official DDI-CDI 1.0 example instances**
(from `ddi-toolkit/specifications/ddi-cdi-1.0/source/example/xml/`):

- **`SPSS_Example.xml`** — data description/structure: 10 `InstanceVariable`s,
  value domains, code lists, `WideDataStructure`, physical layout, and data
  points (an ESS-style survey extract).
- **`Process_Example_CDI.xml`** — pure **provenance**: `Activity` / `Step` /
  `Parameter` / `ProcessingAgent` (0 variables — exercises phase 5).

A larger `Stata_Example.xml` (2 MB) is available upstream.

## Validation status

- `cdif_SPSS_Example.json` — conformsTo core + discovery + data_description +
  **data_structure**, matching detection. Against metadataBuildingBlocks as of
  2026-10-23: JSON Schema and SHACL pass for all four, **0 violations**
  (`validation/ConformanceValidate.py`).
- `cdif_Process_Example.json` — **Discovery + Provenance JSON Schema pass**;
  Discovery and Provenance SHACL **0 violations**. conformsTo:
  core + discovery + **provenance** (a pure process/workflow, 0 variables).

# CDIF converters

Scripts that convert metadata **to or from** CDIF JSON-LD. Each format lives in
its own sub-directory with its converter(s), a README, and (where useful) mapping
docs and examples. All of these are ordinary command-line tools — most also
expose their conversion as an importable function.

| Path | Direction | Format |
|------|-----------|--------|
| [`soso2cdif.py`](soso2cdif.py) | SOSO (file **or URL**) → CDIF | Science-on-Schema.org |
| [`soso/ConvertToSOSO.py`](soso/ConvertToSOSO.py) | CDIF core+discovery → SOSO | Science-on-Schema.org |
| [`soso/ConvertFromSOSO.py`](soso/ConvertFromSOSO.py) | SOSO → CDIF core+discovery | Science-on-Schema.org |
| [`croissant/ConvertToCroissant.py`](croissant/ConvertToCroissant.py) | CDIF → Croissant 1.1 | MLCommons Croissant |
| [`croissant/ConvertFromCroissant.py`](croissant/ConvertFromCroissant.py) | Croissant → CDIF | MLCommons Croissant |
| [`DCAT/dcat_to_cdif.py`](DCAT/dcat_to_cdif.py) | DCAT → CDIF | W3C DCAT |
| [`UMM/umm_to_cdif.py`](UMM/umm_to_cdif.py) | NASA CMR UMM-C (file **or URL**) → CDIF core+discovery | NASA UMM-C JSON |
| [`FAIR2/fair2_to_cdif.py`](FAIR2/fair2_to_cdif.py) | FAIR² `fair2.json` (file **or URL**) → CDIF (reuses the Croissant converter for the Croissant core) | FAIR² |
| [`DDI/ddi_to_cdif.py`](DDI/ddi_to_cdif.py) | DDI Codebook 2.5 (Harvard Dataverse) → CDIF | DDI Codebook XML |
| [`DDI/ddi122_to_cdif.py`](DDI/ddi122_to_cdif.py) | DDI 1.2.2 (ICPSR, source-agnostic) → CDIF | DDI XML |
| [`DDICodebook/ddi25_to_cdif.py`](DDICodebook/ddi25_to_cdif.py) | DDI Codebook 2.5 (source-agnostic) → CDIF | DDI Codebook XML |
| [`DDI-CDI/ddicdi_to_cdif.py`](DDI-CDI/ddicdi_to_cdif.py) | DDI-CDI 1.0 → CDIF *(phased; all six phases)* | DDI-CDI XML |
| [`ROCrate/ConvertToROCrate.py`](ROCrate/ConvertToROCrate.py) | CDIF → RO-Crate 1.2 | RO-Crate |
| [`ROCrate/ROCrateToCDIF.py`](ROCrate/ROCrateToCDIF.py) | RO-Crate 1.2 → CDIF | RO-Crate |
| [`ROCrate/ValidateROCrate.py`](ROCrate/ValidateROCrate.py) | RO-Crate structural + SHACL validator | RO-Crate |

### Harvesters (`harvesters/`)

Harvesters fetch records from a catalogue and convert them to CDIF. They differ
from the converters above in where the input comes from, not in what they
produce. For converting a SOSO file or URL you already have, use `soso2cdif.py`.

| Path | Source | Notes |
|------|--------|-------|
| [`harvesters/geocodes_harvester.py`](harvesters/geocodes_harvester.py) | EarthCube GeoCodes SPARQL catalog → source landing-page JSON-LD → CDIF core/discovery | Moved from the `validation` repo |
| [`harvesters/OHDSI/harvest_ohdsi_to_cdif.py`](harvesters/OHDSI/harvest_ohdsi_to_cdif.py) | OHDSI gaiaCatalog schema.org records → CDIF core/discovery (+ provenance) | With its corpus: `harvesters/OHDSI/OHDSIMetadata/` (16 source records) → `cdifMetadata/`. Formerly the separate `OHDSI` repo; see [`harvesters/OHDSI/README.md`](harvesters/OHDSI/README.md) |

`geocodes_harvester.py` and `soso/ConvertFromSOSO.py` each carry their own copy
of the schema.org prefixing step. The harvester's resolves unprefixed names
through the source record's `@context`; the SOSO converter's still uses a fixed
list of names and sends the rest to `unk:`.

## Setup

```bash
git clone https://github.com/Cross-Domain-Interoperability-Framework/converters.git
cd converters
git submodule update --init      # pulls the `validation` repo (detect_conformance + schemas)
pip install -r requirements.txt
```

The [`validation`](https://github.com/Cross-Domain-Interoperability-Framework/validation)
repo is bundled as a git submodule at [`validation/`](validation/). The
`format → CDIF` converters import `detect_conformance` from it to set
`conformsTo` from content, and the `--validate` / `build_corpus.py` schema checks
read the CDIF schemas and frame from it. **The submodule is optional at runtime:**
every converter guards the import, so conversion still works without it — the
`conformsTo` declaration just falls back to the built-in default (and `--validate`
falls back to fetching the schema from GitHub). Initialize the submodule to get
content-derived conformance detection.

## The "detect conformance" convention (for `format → CDIF` converters)

A CDIF record declares which profiles it conforms to in a `schema:subjectOf`
catalog record via `dcterms:conformsTo`. Rather than hard-coding a profile list,
a converter should set that from the record's **actual content**: the
[`detect_conformance.py`](validation/detect_conformance.py) module (in the
bundled `validation/` submodule) tests, per CDIF class, a presence SPARQL `ASK` (the elements the class introduces
beyond its base) gated by a content-SHACL validity check, and
`apply_conformance()` writes the detected `cdif:` URIs into
`subjectOf/dcterms:conformsTo` (preserving any non-`cdif:` domain claims).
`detect_conformance` has a remote-SHACL fallback, so it works without a local
building-blocks checkout.

All six `format → CDIF` converters — **`ConvertFromSOSO.py`**,
**`ConvertFromCroissant.py`**, **`dcat_to_cdif.py`**, **`ddi_to_cdif.py`**,
**`DDI/ddi122_to_cdif.py`**, and **`DDICodebook/ddi25_to_cdif.py`** —
run `detect_conformance` by default and write the detected `conformsTo` into the
catalog record. Each takes a **`--static-conformance`** flag that skips detection
and keeps the converter's built-in default instead (and detection degrades to
the built-in default automatically if `detect_conformance` or its deps are
unavailable).

**Detection decides, including when it finds nothing.** A record whose content
meets no CDIF profile declares **no** `conformsTo` rather than falling back to a
built-in claim it has not earned — the fallback list exists only for
`--static-conformance` and for when `detect_conformance` cannot be imported.
Over-claiming is not a cosmetic error: the declared URI is what selects the
schema and shapes to validate against, so a false claim also makes the record
*look* checkable. `ConformanceValidate` and `FrameAndValidate -v` fail a record
that over-claims; under-declaring is advisory. For example, the DDI converter's output carries `cdi:InstanceVariable`
variables, so detection adds `data_description` to the declared profiles.

## Why framing / structure matters

CDIF is JSON-LD built on schema.org (plus DDI-CDI, PROV, DQV, …). The JSON
Schemas validate a **framed tree** rooted at `schema:Dataset` with `schema:`-
prefixed property names and a required `subjectOf` catalog record. The other
formats differ in shape — SOSO uses bare schema.org terms via `@vocab` and has no
catalog record; Croissant nests `RecordSet`/`Field`; DCAT and DDI use their own
vocabularies. Each converter reconciles those structural differences (see each
format's README / mapping doc for the property-by-property detail).

---

## soso2cdif.py — SOSO file/URL → CDIF

The front-end for the SOSO→CDIF engine. Reads a SOSO `schema:Dataset` from a
**local file path or an http(s) URL** (extracting embedded `application/ld+json`
from an HTML landing page when the response isn't JSON), converts it via
`soso/ConvertFromSOSO.py`, derives `conformsTo` from content, and writes
`<input-stem>-cdif.json` (or the `-o` path — a file or a directory).

```bash
python soso2cdif.py path/to/soso.json                  # -> soso-cdif.json
python soso2cdif.py https://example.org/dataset -o out.json
python soso2cdif.py soso.json --cdif core --static-conformance
```

## soso/ — CDIF ↔ Science-on-Schema.org

Both are schema.org profiles, so conversion is structural alignment, not
vocabulary translation. `ConvertToSOSO.py` reshapes the `@context` to SOSO style,
strips `schema:` prefixes, drops the CDIF catalog record (SOSO has no equivalent),
passes CDIF-only properties through open-world, and warns on SOSO-required gaps
(`--https` emits `https://schema.org/`). `ConvertFromSOSO.py` prefixes names,
rewrites the `@context`, ensures CDIF-required fields where derivable, wraps
creators in a JSON-LD `@list`, and **adds** the required catalog record — with
`conformsTo` from `detect_conformance`. Mapping detail:
[`soso/README.md`](soso/README.md) and the property-by-property comparison in
`../../doc-corediscovery/documents/CDIF-Discovery-vs-SOSO-comparison.md`
(ESIP issue #283).

## croissant/ — CDIF ↔ MLCommons Croissant

`ConvertToCroissant.py` converts CDIF to Croissant 1.1 for ML dataset discovery
(`DataDownload` → `cr:FileObject`; `variableMeasured` + physical mapping →
`cr:RecordSet`/`cr:Field`; CDIF-only properties passed through). The lossy inverse
`ConvertFromCroissant.py` converts Croissant (1.0 or 1.1) back to CDIF
DataDescription/Discovery and sets `conformsTo` via `detect_conformance`. Mapping
docs: [`croissant/CDIFtoCroissant.md`](croissant/CDIFtoCroissant.md) (forward) and
[`croissant/CroissantToCDIF.md`](croissant/CroissantToCDIF.md) (inverse).

## DCAT/ — DCAT → CDIF

`dcat_to_cdif.py` converts a DCAT JSON-LD catalog or dataset to CDIF schema.org
form, mapping DCAT / Dublin Core properties to their schema.org equivalents per
the CDIF DCAT implementation guide. It can list the datasets in a catalog,
convert a selection, and optionally validate the output against the CDIF core
schema. See [`DCAT/README.md`](DCAT/README.md).

## DDI/ — DDI Codebook 2.5 → CDIF

`ddi_to_cdif.py` converts DDI Codebook 2.5 XML (e.g., a Harvard Dataverse DDI
export) to CDIF DataDescription JSON-LD: study-level metadata → the dataset;
`<var>` → `schema:variableMeasured` (`cdi:InstanceVariable`); `<fileDscr>` →
`schema:DataDownload` (`cdi:TabularTextDataSet`) with CSVW properties; tab-file
headers → physical mappings. `--doi` is required; `--fetch-headers` /
`--fetch-file-meta` pull column headers and size/checksum from the Dataverse API.

---

## Validating converter output

```bash
# CDIF output -> a CDIF profile schema (frame first; schemas live in the submodule)
python validation/tools/FrameAndValidate.py out.json -v \
    --schema validation/CDIFDiscoverySchema.json --frame validation/CDIF-frame-2026.jsonld

# SOSO output -> SOSO v1.3 SHACL (use ConvertToSOSO --https so the shapes target it)
#   soso_common_v1.3.0.ttl from the ESIP science-on-schema.org repo

# Croissant output -> mlcroissant
mlcroissant validate --jsonld out-croissant.json
```

## Repository layout

```
converters/                  (repository root)
├── soso2cdif.py             SOSO file/URL -> CDIF (front-end for soso/)
├── sssom_engine.py          shared table-driven mapping engine
├── soso/  croissant/  DCAT/  DDI/  DDICodebook/  DDI-CDI/  ROCrate/   the converters
├── mappings/                SSSOM crosswalk tables for every converter
└── validation/              git submodule: the CDIF `validation` repo
```

### Root files

| File | Description |
|------|-------------|
| `soso2cdif.py` | Front-end for the SOSO→CDIF engine: reads a SOSO record from a path or http(s) URL and writes CDIF (see below) |
| `sssom_engine.py` | The shared table-driven mapping engine (`MappingSet`) that the DCAT/DDI/Croissant converters read their SSSOM tables through |
| `requirements.txt` | Python dependencies (see [Setup](#setup)) |
| `LICENSE`, `LICENSE-CC-BY-4.0` | Apache-2.0 (software) and CC BY 4.0 (mappings, docs, example metadata); see [License](#license) |
| `README.md`, `CLAUDE.md` | This file, and the project guide for Claude Code |
| `.gitignore`, `.gitmodules` | Git configuration; `.gitmodules` declares the `validation` submodule |

### Converter subdirectories

Each holds its converter(s), a `README.md`, and (where useful) mapping docs and example corpora.

| Directory | Contents |
|-----------|----------|
| [`soso/`](soso/) | `ConvertToSOSO.py` / `ConvertFromSOSO.py` (CDIF ↔ ESIP Science-on-Schema.org), the `check_soso_mappings.py` drift-checker, `examples/`, and `README.md` |
| [`croissant/`](croissant/) | `ConvertToCroissant.py` / `ConvertFromCroissant.py` (CDIF ↔ MLCommons Croissant 1.1), `check_croissant_mappings.py`, the mapping docs `CDIFtoCroissant.md` / `CroissantToCDIF.md` (+ `AGENTS.md` and background `.docx` notes), and the `croissantExamples/` and `MLCroissantExamples/` corpora |
| [`DCAT/`](DCAT/) | `dcat_to_cdif.py` (table-driven DCAT → CDIF), `build_corpus.py` (regression harness), `make_coverage_xlsx.py` + `dcat_profile_coverage.xlsx`, the `dcat-ap-vs-dcat-us.md` comparison, `README.md`, and the corpora: `dcat-examples/` (783 upstream files), `dcatExamplesOK/` (the 240 that describe a `dcat:Dataset`), and `cdifOK/` (the converted CDIF records) |
| [`DDI/`](DDI/) | The DDI entry point `ddi2cdif.py` (flavor sniff + dispatch), the data-driven engine `ddi_sssom_to_cdif.py`, `ddi122_to_cdif.py` (1.2.2), the Harvard-Dataverse-specific `ddi_to_cdif.py`, `build_ddi_corpus.py`, the DDI Codebook 1.2.2 XSD, `Examples/`, and `README.md` |
| [`DDICodebook/`](DDICodebook/) | `ddi25_to_cdif.py` (a thin DDI Codebook 2.5 shim over the engine), the 2.5 XSD, the `ddi25-additions-cdif-mapping.md` notes, `Examples/`, and `README.md` |
| [`DDI-CDI/`](DDI-CDI/) | `ddicdi_to_cdif.py` (DDI-CDI 1.0 → CDIF), `Examples/`, and `README.md` |
| [`FAIR2/`](FAIR2/) | `fair2_to_cdif.py` (FAIR² → CDIF, a FAIR² extension pass over the Croissant converter), the FAIR² specification example and its conversion, and `README.md` |
| [`ROCrate/`](ROCrate/) | `ConvertToROCrate.py` (CDIF → RO-Crate 1.2), `ROCrateToCDIF.py`, `ValidateROCrate.py` (structural + optional SHACL), example RO-Crate/CDIF records, `requirements.txt`, and `README.md` |
| [`mappings/`](mappings/) | The SSSOM crosswalk tables (`*.sssom.tsv` + `.yml` sidecars) for every converter path, the alias tables, the compiled `ddi_mappings.json`, the sync scripts (`sync_sssom.py`, `sync_ddi_mappings.py`), `ddiwalk_lib.py`, and `README.md`. See the next section |
| [`validation/`](validation/) | **git submodule** — the CDIF [`validation`](https://github.com/Cross-Domain-Interoperability-Framework/validation) repo, providing `detect_conformance.py`, the CDIF schemas, the frame, and `tools/`. Run `git submodule update --init` to populate it (see [Setup](#setup)) |
| `.github/` | CI: `workflows/check-mappings.yml` runs the SOSO and Croissant mapping drift-checkers |

## Mappings (SSSOM)

[`mappings/`](mappings/) holds an [SSSOM](https://mapping-commons.github.io/sssom/)
mapping set for each converter path (`cdif→soso`, `soso→cdif`, `cdif→croissant`,
`croissant→cdif`, `dcat→cdif`, `ddi25→cdif`, `ddi122→cdif`) — the property-level
correspondences each converter applies, hand-authored from the mapping docs and
code. See [`mappings/README.md`](mappings/README.md).

## License

This repository is dual-licensed by content type:

| Content | License |
|---------|---------|
| Software: the Python converters, checkers and scripts (`*.py`) and the CI workflow (`.github/`) | [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) — see [`LICENSE`](LICENSE) |
| Mapping tables (`mappings/*.sssom.tsv` and their `.yml` sidecars, `mappings/ddi_mappings.json`), documentation (`*.md`), and the example metadata produced by these converters (e.g. `DCAT/cdifOK/`, the `cdif/` folders under each format's examples, the CDIF→Croissant output examples) | [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/) — see [`LICENSE-CC-BY-4.0`](LICENSE-CC-BY-4.0) |

Third-party material bundled here for testing and reference keeps its own
license: the upstream source examples (e.g. `DCAT/dcat-examples/`,
`DCAT/dcatExamplesOK/`, `croissant/MLCroissantExamples/`,
the harvested Croissant exports in `croissant/croissantExamples/`, the `XML/` source folders, the UMM-C source
records, the RO-Crate inputs), the DDI Codebook XSDs, the NASA UMM schemas under
`UMM/schemas/`, the FAIR² specification example in `FAIR2/examples/`, and the background `.docx` papers in `croissant/`. The
[`validation/`](validation/) submodule is a separate repository with its own
license.

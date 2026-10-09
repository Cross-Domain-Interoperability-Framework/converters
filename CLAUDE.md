# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See [README.md](README.md) for the converter inventory and setup. Notes below are
the non-obvious things that bite when editing this repo.

## How a converter relates to its mapping table

Each converter path has an SSSOM table in `mappings/`, but tables play
different roles — check which before editing either side:

- **Table drives the converter (read at runtime):** DCAT (`dcat_to_cdif.py`
  reads the TSVs directly), UMM (`umm_to_cdif.py`, own `load_table`; `tf_*`
  functions are the shapers named in the `transform` column), Croissant (via
  the shared `sssom_engine.py`), FAIR² (`FAIR2/fair2_to_cdif.py`: runs the
  Croissant converter on the keys `fair2-to-cdif.sssom.tsv` does not claim,
  then applies that table), DDI (worksheets compiled to
  `mappings/ddi_mappings.json`, applied by `DDI/ddi_sssom_to_cdif.py`). A
  mapping change for these is a table edit; code is only for structural
  shapers.
- **Table only documents the converter:** SOSO, OHDSI (and much of
  Croissant's structure). Change the code and the table together; CI's drift-checkers
  fail if they disagree.
- Row order is precedence: for a scalar target the first row to fill it wins;
  array targets accumulate.
- After editing a DDI `*.sssom.tsv` (in a text editor, not a spreadsheet), run
  `python mappings/sync_ddi_mappings.py` — it repairs the TSV, regenerates the
  sidecar and rebuilds `ddi_mappings.json`, which the converters actually read.
  It rewrites every DDI table and sidecar with LF line endings even when their
  content is unchanged; if `git diff --ignore-cr-at-eol` shows nothing for a
  file, `git checkout --` it rather than committing line-ending churn.
- In the DDI engine, a distribution-level target path ending `[*]` is written
  as an array of distinct values, and a `schema:additionalProperty` target
  becomes PropertyValues named by the row's `object_label` — so a new
  per-file field needs no code, only a row with the right path.
- The `validation/` submodule is optional at runtime: keep every
  `detect_conformance` import guarded so conversion works without it.
- `detect_conformance` fetches its SHACL shapes from metadataBuildingBlocks
  **main on GitHub**, so a converter's detected `conformsTo` can change with no
  change in this repo — e.g. when a term moves namespace there
  (`cdi:qualifies` became `cdif:qualifies`, and every DDI record silently lost
  `data_description` until the engine followed). When a regenerated corpus
  drops a profile, run detection verbosely on the committed record too before
  blaming your change.
- `schemaorg_names.py` is the one schema.org naming step for
  `soso/ConvertFromSOSO.py` and `harvesters/geocodes_harvester.py`; change it
  there, not in a caller.

## Checks

There is no unit-test suite. What exists:

- `python soso/check_soso_mappings.py`,
  `python croissant/check_croissant_mappings.py` and
  `python OHDSI/check_ohdsi_mappings.py` — table/converter drift
  checks (the CI job in `.github/workflows/check-mappings.yml`); stdlib only.
- Regression corpora: `python DCAT/build_corpus.py` (see below) and
  `python DDI/build_ddi_corpus.py`.
- Validate one output: `python UMM/umm_to_cdif.py <in> -o out/ --validate`
  (DCAT and FAIR2 have the same flag), or
  `python validation/tools/FrameAndValidate.py out.json -v --schema validation/CDIFDiscoverySchema.json --frame validation/CDIF-frame-2026.jsonld`.

## Mapping tables (`mappings/*.sssom.tsv`)

- Every row must have exactly the header's columns. The tables get edited in a
  spreadsheet, which has padded rows with trailing empty columns, turned commas
  inside `comment` into tabs, and wrapped comments in `"..."`. Check after any
  spreadsheet round-trip:
  `awk -F'\t' '{print NF}' mappings/<name>.sssom.tsv | sort | uniq -c`
- `dcat_to_cdif.py` reads `dcat-to-cdif.sssom.tsv` with a plain tab split (no
  CSV quoting) and ignores `comment`; SSSOM tooling reads it with pandas, which
  does honour quotes.
- `python -m sssom.cli parse <name>.sssom.tsv -m <name>.sssom.yml -o out.tsv`:
  for the DCAT table the expected "not well-formed" rows are the passthrough rows
  (no `predicate_id`) and the transform-consumed `*-part` / `catalog-walk` rows
  (predicate but no `object_id`).
- After changing which prefixes a table uses, run `python mappings/sync_sssom.py`
  (`--check` to preview) to regenerate the sidecars' `curie_map`.

## DCAT corpus (`DCAT/cdifOK/`)

- Regenerate with `python DCAT/build_corpus.py` after any change to the DCAT
  table or converter; commit it separately with a `[generated]` subject.
- Output is not deterministic (conversion-time `dateModified`, random blank-node
  labels, sibling records swapping filenames); see
  [DCAT/cdifOK/README.md](DCAT/cdifOK/README.md#regenerating) for how to compare
  two builds. Matching records by `@id` after dropping `schema:dateModified` /
  `sdDatePublished` and normalising `_:` labels isolates a real change; commit
  only the records that actually changed rather than ~200 files of date churn.
- `cdifOK/psdi/` (50 records) was **not** made by `build_corpus.py`: it was made
  with `python DCAT/dcat_to_cdif.py DCAT/dcatExamplesOK/psdi/dataset-7e871747-1bd3-4abf-8fe3-dc4ec55b3771.jsonld --output DCAT/cdifOK/psdi --catalog-name "PSDI Resource Catalogue" --catalog-url "https://metadata.psdi.ac.uk/"`.
  A `build_corpus.py` run re-parses that source and writes the 50 again as
  `dataset-…__dcat-<slug>.jsonld` with a different catalog record and lost
  labels; delete those, then regenerate `psdi/` with the command above or
  `git checkout -- DCAT/cdifOK/psdi`.
- The schema and SHACL checks read `metadataBuildingBlocks` from a checkout
  beside this repo (`../metadataBuildingBlocks`), not from the `validation`
  submodule; without it they report "skipped". `DDI/build_ddi_corpus.py` does
  the same.

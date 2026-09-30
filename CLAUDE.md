# CLAUDE.md — CDIF converters

See [README.md](README.md) for the converter inventory and setup. Notes below are
the non-obvious things that bite when editing this repo.

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
  two builds.
- The schema and SHACL checks currently report "skipped": `build_corpus.py`
  still resolves `metadataBuildingBlocks` as `../../..` from `DCAT/`, a path from
  before the converters moved out of `validation/`.

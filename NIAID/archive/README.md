# Archive

Files kept for reference that nothing in `NIAID/` reads.

## `nde-schemas/`

NIAID's per-class schema sources, moved here from `NIAID/schemas/` on 2026-11-09.
[`build_nde_jsonschemas.py`](../build_nde_jsonschemas.py) reads only
`schemas/NDE_schema.jsonld`, so these files were unused.

They are unmodified copies from
[NIAID-Data-Ecosystem/nde-schemas](https://github.com/NIAID-Data-Ecosystem/nde-schemas), commit
[`b9abacc9a4f5`](https://github.com/NIAID-Data-Ecosystem/nde-schemas/tree/b9abacc9a4f54aea43af56b0a9b4d1a9d4d7045b)
(2026-11-02), downloaded on 2026-11-04. They are NIAID's work under the Apache License 2.0;
see [`nde-schemas/LICENSE`](nde-schemas/LICENSE).
[`nde-schemas/README.md`](nde-schemas/README.md) is NIAID's own README for these files.

The files are schema.org-style JSON-LD class and property definitions (`@context` and
`@graph`), not JSON Schema documents:

- **One class per file:** `Dataset.json`, `ComputationalTool.json`, `DataDownload.json`,
  `MonetaryGrant.json`, `Organization.json`, `AdministrativeArea.json`,
  `ScholarlyArticle.json` and `TemporalInterval.json`.
- **Shared properties:** `commonProperties.json` has the properties those classes share.
- **Combined:** `combined_schema_DO_NOT_EDIT/NIAID_schema.json` is all of the above in one
  file, built by NIAID's `compile_nde_schema.py`. That script was not copied.

The schema NIAID registers on the Data Discovery Engine, `NDE_schema.jsonld`, is built from
these files. It stays in [`../schemas/`](../schemas/).

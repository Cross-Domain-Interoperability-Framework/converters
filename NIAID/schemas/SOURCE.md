# Source of these files

Unmodified copies from [NIAID-Data-Ecosystem/nde-schemas](https://github.com/NIAID-Data-Ecosystem/nde-schemas),
commit [`b9abacc9a4f5`](https://github.com/NIAID-Data-Ecosystem/nde-schemas/tree/b9abacc9a4f54aea43af56b0a9b4d1a9d4d7045b)
(2026-11-02), downloaded 2026-11-04. They are NIAID's work under the Apache
License 2.0; see [`LICENSE`](LICENSE). `README.md` is NIAID's own, and its build
script `compile_nde_schema.py` is not copied.

The files are schema.org-style JSON-LD class and property definitions
(`@context` + `@graph`), not JSON Schema documents:

- `Dataset.json`, `ComputationalTool.json`, `DataDownload.json`,
  `MonetaryGrant.json`, `Organization.json`, `AdministrativeArea.json`,
  `ScholarlyArticle.json`, `TemporalInterval.json`: one class each.
- `commonProperties.json`: properties shared between those classes.
- `combined_schema_DO_NOT_EDIT/NIAID_schema.json`: the above combined.
- `NDE_schema.jsonld`: the schema as registered on the
  [Data Discovery Engine](https://discovery.biothings.io/view/nde), namespace
  `https://discovery.biothings.io/ns/nde/`.

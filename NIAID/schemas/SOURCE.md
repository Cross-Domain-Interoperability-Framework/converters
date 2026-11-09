# Source of this file

`NDE_schema.jsonld` is an unmodified copy from
[NIAID-Data-Ecosystem/nde-schemas](https://github.com/NIAID-Data-Ecosystem/nde-schemas), commit
[`b9abacc9a4f5`](https://github.com/NIAID-Data-Ecosystem/nde-schemas/tree/b9abacc9a4f54aea43af56b0a9b4d1a9d4d7045b)
(2026-11-02), downloaded on 2026-11-04. It is NIAID's work under the Apache License 2.0; see
[`LICENSE`](LICENSE).

It is the schema as registered on the
[Data Discovery Engine](https://discovery.biothings.io/view/nde), in the namespace
`https://discovery.biothings.io/ns/nde/`. It uses schema.org-style JSON-LD class and property
definitions, with a draft-07 JSON Schema in each class node's `$validation`.
[`../build_nde_jsonschemas.py`](../build_nde_jsonschemas.py) reads it to generate
[`../jsonschema/`](../jsonschema/).

NIAID's per-class source files, which were copied from the same commit, are in
[`../archive/nde-schemas/`](../archive/nde-schemas/), because nothing here reads them.

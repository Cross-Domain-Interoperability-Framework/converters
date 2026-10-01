# UMM schemas

The NASA UMM JSON Schemas that `umm_to_cdif.py` and its mapping table
(`../../mappings/ummc-to-cdif.sssom.tsv`) are written against. The files carry
no version of their own (no `$id`, no version in the filename; `umm-cmn` has
none at all), so the version is recorded by the folder they sit in.

| Folder | Schema | Role in the converter |
|---|---|---|
| `umm-c/v1.18.6/` | UMM-C collection (`umm-c-json-schema.json`), UMM-Common (`umm-cmn-json-schema.json`), CMR search results (`umm-search-results-json-schema.json`) | The converter's input. The mapping table's `subject_id` paths (`ummc:`, `ummcmn:`) follow these definitions; `cmrmeta:` paths follow the search-result `meta`. |
| `umm-var/v1.9.0/` | UMM-Var variable (`umm-var-json-schema.json`), its search results (`umm-var-search-results-json-schema.json`) | The associated variable records fetched for `schema:variableMeasured` (`ummvar:` rows). |

These are the versions CMR served when the converter was written (checked
2026-10-01 from the `Content-Type` of `collections.umm_json` and
`variables.umm_json` responses). The converter has also been run on UMM-C
1.18.2 records, as linked from Earthdata dataset pages.

## Source

Unmodified copies from
[nasa/Common-Metadata-Repository](https://github.com/nasa/Common-Metadata-Repository),
`umm-spec-lib/resources/json-schemas/`, at commit
`c3d7f4087445f3f37c04ee15310f6fd6cfc588e4` (downloaded 2026-10-01):

- `collection/umm/v1.18.6/` → `umm-c/v1.18.6/`
- `variable/umm/v1.9.0/` → `umm-var/v1.9.0/`

To check them against upstream or add a version (paths are relative to this
folder):

```bash
curl -sSO https://raw.githubusercontent.com/nasa/Common-Metadata-Repository/master/umm-spec-lib/resources/json-schemas/collection/umm/v1.18.6/umm-c-json-schema.json
```

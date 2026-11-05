# NIAID example records

Dataset records from the [NIAID Data Discovery Portal](https://data.niaid.nih.gov/)
API (`https://api.data.niaid.nih.gov/v1/query`, index build 2026-10-28), saved
as the API returned them, one file per record, named from the record's
`_id`. They are third-party metadata aggregated by the portal from the
repositories named in each record's `includedInDataCatalog`.

One exception: the `description` of `phs000239`, `phs000257`, `phs001821` and
`phs001846` (dbGaP) held stray `]]>` markers with no opening `<![CDATA[`, which
are removed here because the sequence breaks tools that handle the JSON as XML.
The harvester removes them too.

[`INDEX.json`](INDEX.json) lists every file with its group, sources, metadata
score, and whether it has `variableMeasured`, `conditionsOfAccess` and `usageInfo`.

| `group` | Records | How chosen |
|---|---|---|
| `selected-score-over-20` | 25 | `_meta.completeness.total_score` over 20, spread across the five sources that have such records: Protein Data Bank, Project Tycho, RADx, dbGaP and the Data Discovery Engine |
| `selected-any-of-fields` | 25 | Has `variableMeasured`, `conditionsOfAccess` or `usageInfo`, from sources with no record scoring over 20; highest-scoring first, largest sources first, one record per catalog until the sources ran out, then a second from the largest |
| `initial-sample` | 90 | The first 8 Dataset records the API returned for each of 12 sources, 96 rows of which 6 repeat. Used to develop the harvester |

The score is the portal's metadata completeness score, out of 30
(`_meta.completeness.total_max_score`). The API's `metadata_score` field is in
its field list but empty on every record, so it cannot be filtered on. Requesting
`show_meta=true` returns `_meta`; the `initial-sample` files were fetched without
it, so their score is only in `INDEX.json`.

The over-20 requirement alone selects 78,303 Datasets, 99% of them from the
Protein Data Bank, and every one has at least one of the three fields.

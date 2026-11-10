# NIAID examples as NIAID Blueprint metadata

The 140 records in [`../cdifJSON-LD/`](../cdifJSON-LD/README.md), written as instances of
GO FAIR US's CEDAR template for the
[NIAID Blueprint for Digital Objects](https://doi.org/10.5281/zenodo.17161561), by
[`../cdif_to_blueprint.py`](../cdif_to_blueprint.py). The template, "NIAID Blueprint
Compliant Schema 0.7.0", is in
[go-fair-us/blueprint-cedar-specifications](https://github.com/go-fair-us/blueprint-cedar-specifications);
its README notes it has not been reviewed by NIAID. That repository has no license, so
the template is read from GitHub when the script runs and is not copied here.

All 140 instances are valid against the template's JSON Schema. That schema checks shape
only (its own examples leave fields empty), so [`INDEX.json`](INDEX.json) also says, for
each Blueprint element, whether the record fills it (`filled`), fills it with the
Blueprint's default identifier or vocabulary (`default`), or leaves it out (`missing`):

| Blueprint element | Default form | With default | Other | Missing |
|---|---|---|---|---|
| identifier | DOI | 94 | 12 | 34 |
| name | | | 140 | 0 |
| description | | | 140 | 0 |
| dateCreated | | | 128 | 12 |
| author | ORCID / ROR | 20 | | 120 |
| funder | ROR | 111 | | 29 |
| funding (grant number) | | | 74 | 66 |
| measurementTechnique | NCIT | 54 | 47 | 39 |
| distribution | | | 140 | 0 |
| citation | | | 55 | 85 |
| infectiousAgent | NCBITaxon | 80 | | 60 |
| host | NCBITaxon | 104 | | 36 |
| healthCondition | MONDO | 79 | 21 | 40 |
| conditionsOfAccess | | | 114 | 26 |
| license | SPDX | 52 | 49 | 39 |
| spatialCoverage | ISO 3166 / NCIT country | | | 140 |
| temporalCoverage | ISO 8601 | | 66 | 74 |

Two of the gaps come from the template, not the records:

- **author:** the template takes an author only as an ORCID (person) or ROR
  (organization) IRI. Most creators the portal gives have a name and no ORCID, so they
  have no slot; the CDIF record keeps them.
- **spatialCoverage:** the template's country field takes an NCIT Country concept, with
  GAZ ids, coordinates and WKT for other places. It has no slot for a place name or an
  ISO 3166 code, which is what the CDIF records carry (127 of 151 places have a code), so
  only places with coordinates fill it, and none of these records have any.

Other choices:

- **host** is `nde:species` in CDIF, written to the template's `host` field (which the
  template maps to `schema:taxonomicRange`).
- **Terms** use the IRI the CDIF record links with `schema:sameAs`, preferring NCI's own
  NCIT form, which the template uses.
- **License:** an SPDX license goes in the template's SPDX selection field as its
  `spdx.org` IRI (the template's own pick list is the FAIR Data Collective's GDMT license
  identifiers); another license URL is a generic license IRI, and text is the license
  terms.
- **dateCreated** is the record's `dateCreated`, else `datePublished`; never the
  `1900-01-01` sentinel `dateModified`.
- **conditionsOfAccess** is the COAR access-right IRI where the CDIF record links one,
  else the statement's text.

Regenerate with `python NIAID/cdif_to_blueprint.py` (needs network access, or
`--template` for a local copy). Its output is deterministic.

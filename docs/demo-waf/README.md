# Demonstration web folder

Seventeen example metadata files in the formats the CDIF Catalog can harvest, published with
GitHub Pages so that a harvester can read them as a web folder:

https://cross-domain-interoperability-framework.github.io/converters/demo-waf/

`index.html` is the listing a harvester reads: a page of links, one per file. GitHub Pages does
not list a folder by itself, so **a file added here must also be added to `index.html`**.

## Where the files came from

Copies, made on 23 November 2026, renamed by format. They are not kept in step with their
originals.

| File | Copied from |
|---|---|
| `iso19139.xml` | `ISO19115/examples/iso19139/AODN_IMOS-806340f5-608e-41ef-9579-0b1afc9db8a0.xml` |
| `iso19115-2.xml` | `ISO19115/examples/iso19139/dataone-IEDA_MGDL-http_get.iedadata.org_metadata_iso_321390.xml` |
| `iso19115-3.xml` | `ISO19115/examples/iso19115-3/AODN_IMOS-69404adf-97cd-4970-962b-c28132fd0b7f.xml` |
| `ddicdi-xml.xml` | `DDI-CDI/Examples/XML/Process_Example_CDI.xml` |
| `ddicdi-jsonld.json` | `DDI-CDI/Examples/representedVariables/COWcode.json` |
| `schemaorg-jsonld.json` | `soso/examples/soso-dataset-example.json` |
| `croissant.jsonld` | `croissant/croissantExamples/2025-11-10_C2G6XB_Test_CDIF_in_Croissant_with_python_1.croissant.jsonld` |
| `umm-c.json` | `UMM/examples/umm-c/asf_sentinel1.json` |
| `rocrate-xas.json` | `ROCrate/se_na2so4_rt-rocrate.json` |
| `datacite-1.xml` to `datacite-6.xml` | The first six files, by name, in `DataCite/examples/` |
| `cdif-exampleCdifAccess.json` | The `cdifAccess` building block example in metadataBuildingBlocks |
| `cdif-exampleDDEDataset.json` | The `DDEDataset` profile example in ddeBuildingBlocks |

## What a harvest of it gives

With the CDIF Catalog and the converters at `8c02b14`: 13 records published and 4 stored but not
published (Croissant, DDI-CDI JSON-LD, UMM-C, and the access fragment, which is not a whole
record). The four show what a failed check looks like.

`docs/.nojekyll` tells GitHub Pages to serve the files as they are.

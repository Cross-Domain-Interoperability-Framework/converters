#!/usr/bin/env python3
"""
extract_represented_variables.py - Build cdi:RepresentedVariable documents for
the variables a CODATA variableindex page lists, from the DDI-CDI JSON-LD
export of their file.

The variableindex (https://ai.codata.org/variableindex) lists InstanceVariables
from tabular files; its API returns them a page at a time
(``/api/variableindex/data?limit=100&offset=0``), each row naming the variable
and the ``cdif_export_url`` of the DDI-CDI export of its file. For every row
whose export is the given file, this writes one JSON-LD document, valid against
the metadataBuildingBlocks ``cdifRepresentedVariable`` schema:

  InstanceVariable name / displayLabel / definition
                                  -> cdif:name / cdif:displayLabel / cdif:definition
  SubstantiveValueDomain recommendedDataType (xmlschema-2 spec anchor)
                                  -> cdi:takesSubstantiveValuesFrom
                                     cdif:SubstantiveValueDomain cdif:recommendedDataType (xsd:*)
  ValueAndConceptDescription classificationLevel
                                  -> ... cdi:isDescribedBy cdi:classificationLevel

The variable's physicalDataType describes the file, not the concept, so it is
left on the InstanceVariable. Each RepresentedVariable's @id is
``<export URL>#representedVariable-<name>``, the pattern the export uses for its
``#instanceVariable-<name>`` nodes; the export itself does not contain these
nodes, so the @id does not yet dereference to them.

Rows from another file are skipped and reported, as are rows that disagree with
the export (index description vs definition, index data_type vs
physicalDataType).

Usage:
    curl -s "https://ai.codata.org/api/variableindex/data?limit=100&offset=0" -o page.json
    python extract_represented_variables.py Examples/107910_DVN_CWOZTP.jsonLD page.json \\
        -o Examples/representedVariables
"""
import argparse
import json
from pathlib import Path

XSD_DOC = "https://www.w3.org/TR/xmlschema-2/#"
CTX = {"cdi": "http://ddialliance.org/Specification/DDI-CDI/1.0/RDF/",
       "cdif": "https://w3id.org/cdif/",
       "xsd": "http://www.w3.org/2001/XMLSchema#"}


def value(node, *path):
    """Follow keys through nested dicts; return the leaf or None."""
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def represented_variable(iv, index, export_url):
    name = value(iv, "name", "name")
    rv = {"@context": CTX, "@type": ["cdi:RepresentedVariable"],
          "@id": f"{export_url}#representedVariable-{name}",
          "cdif:name": [name]}
    label = value(iv, "displayLabel", "locationVariant", "entryValue")
    if label:
        rv["cdif:displayLabel"] = [label]
    definition = value(iv, "definition", "content")
    if definition:
        rv["cdif:definition"] = definition

    svd = index.get(iv.get("takesSubstantiveValuesFrom_SubstantiveValueDomain"), {})
    domain = {"@type": ["cdif:SubstantiveValueDomain"]}
    dt = value(svd, "recommendedDataType", "entryValue") or ""
    if dt.startswith(XSD_DOC):
        domain["cdif:recommendedDataType"] = ["xsd:" + dt[len(XSD_DOC):]]
    level = value(index.get(svd.get("isDescribedBy"), {}), "classificationLevel")
    if level:
        domain["cdi:isDescribedBy"] = {"@type": ["cdi:ValueAndConceptDescription"],
                                       "cdi:classificationLevel": level}
    if len(domain) > 1:
        rv["cdi:takesSubstantiveValuesFrom"] = domain
    return rv


def extract(export_path, page_path, out_dir):
    """Write one RepresentedVariable file per matching row; return (written,
    notes) where notes lists (variable, reason) for skipped or differing rows."""
    doc = json.loads(Path(export_path).read_text(encoding="utf-8"))
    index = {o["@id"]: o for o in doc.get("DDICDIModels", []) if "@id" in o}
    export_url = doc["@id"]
    rows = json.loads(Path(page_path).read_text(encoding="utf-8"))["variables"]

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written, notes = 0, []
    for row in rows:
        name = row["variable_name"]
        if row.get("cdif_export_url") != export_url:
            notes.append((name, "listed under a different export; skipped"))
            continue
        iv = index.get(f"#instanceVariable-{name}")
        if iv is None:
            notes.append((name, "no InstanceVariable in the export; skipped"))
            continue
        rv = represented_variable(iv, index, export_url)
        definition = rv.get("cdif:definition", "")
        if row.get("description") and row["description"] != definition:
            notes.append((name, f"index description {row['description']!r} "
                                f"!= definition {definition!r}"))
        if row.get("data_type") != value(iv, "physicalDataType", "entryValue"):
            notes.append((name, "index data_type differs from physicalDataType"))
        (out / f"{name}.json").write_text(
            json.dumps(rv, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written += 1
    return written, notes


def main():
    ap = argparse.ArgumentParser(
        description="Build cdi:RepresentedVariable documents for the variables a "
                    "variableindex API page lists, from their DDI-CDI JSON-LD export")
    ap.add_argument("export", help="DDI-CDI JSON-LD export of the file (with DDICDIModels)")
    ap.add_argument("page", help="A saved variableindex API page "
                                 "(/api/variableindex/data?limit=...&offset=...)")
    ap.add_argument("-o", "--output", default="representedVariables",
                    help="Output directory (default: representedVariables)")
    args = ap.parse_args()

    written, notes = extract(args.export, args.page, args.output)
    print(f"Written: {written} RepresentedVariable file(s) to {args.output}")
    for name, why in notes:
        print(f"  {name}: {why}")


if __name__ == "__main__":
    main()

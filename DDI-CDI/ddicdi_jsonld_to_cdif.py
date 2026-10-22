#!/usr/bin/env python3
"""
ddicdi_jsonld_to_cdif.py - Convert DDI-CDI JSON-LD instances to CDIF JSON-LD.

Reads the compact JSON-LD serialisation of DDI-CDI (1.1beta context,
``https://ddi-cdi.github.io/m2t-ng/DDI-CDI_1-1beta/encoding/json-ld/ddi-cdi.jsonld``)
as written by the CODATA DDI-CDI export (https://ai.codata.org/export): a
dataset-level header in Dublin Core / schema.org / PROV terms, plus a flat
``DDICDIModels`` list of typed objects linked by local ``#...`` ids.

The objects are read as plain JSON (no JSON-LD expansion): every object is
indexed by its ``@id`` and references are followed by id, the same way
``ddicdi_to_cdif.py`` reassembles the XML serialisation.

  header dct:title / description / license / language -> schema:name / ...
  header dct:identifier                       -> schema:identifier
  header dct:isPartOf                         -> schema:isPartOf
  header prov:wasDerivedFrom                  -> prov:wasDerivedFrom, and the
                                                 distribution's contentUrl
  header schema:contentSize / checksum        -> distribution contentSize /
                                                 spdx:checksum
  InstanceVariable                            -> schema:variableMeasured
    name, definition (when not just the name), classificationLevel,
    cdif:role (from the MeasureComponent etc. that it defines),
    SubstantiveValueDomain/recommendedDataType -> cdif:physicalDataType (xsd IRI)
  TabularTextDataSet                          -> distribution typed
                                                 cdi:TabularTextDataSet:
    isDelimited, isFixedWidth, delimiter, hasHeader, headerRowCount,
    recordCount -> countRows, variable count -> countColumns
  TextMapping + PhysicalMappingPosition       -> cdif:hasPhysicalMapping
                                                 (cdif:TextMapping)
  WideDataStructure + components + positions  -> cdi:isStructuredBy

Out of scope: DataPoint / DataPointPosition / InstanceValue (a sample of the
data values) - CDIF is a metadata framework.

Usage:
    python ddicdi_jsonld_to_cdif.py Examples/107910_DVN_CWOZTP.jsonLD -o out.json
"""
import argparse
import json
import sys
from pathlib import Path

from ddicdi_to_cdif import (CDI_CTX, COMPONENT_ROLE, DATASET_TYPE,
                            STRUCTURE_TYPES, _HAVE_DETECT)
if _HAVE_DETECT:
    from detect_conformance import detect_conformance, apply_conformance

NIL_MISSING = "http://www.opengis.net/def/nil/OGC/0/missing"
XSD = "http://www.w3.org/2001/XMLSchema#"
# The export names XML Schema datatypes by their spec anchor
# (https://www.w3.org/TR/xmlschema-2/#decimal); CDIF wants the datatype IRI.
XSD_DOC = "https://www.w3.org/TR/xmlschema-2/#"
DDI_DISCOVERY = "http://rdf-vocabulary.ddialliance.org/discovery#"


def value(node, *path):
    """Follow keys through nested dicts; return the leaf or None."""
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def by_type(objects, *types):
    return [o for o in objects if o.get("@type") in types]


def position_map(objects, pos_type):
    """{indexed object id: value} for a DDI-CDI *Position class."""
    return {o["indexes"]: o["value"] for o in by_type(objects, pos_type)
            if "indexes" in o and "value" in o}


def as_list(x):
    return x if isinstance(x, list) else [] if x is None else [x]


def xsd_iri(dt):
    if dt and dt.startswith(XSD_DOC):
        return XSD + dt[len(XSD_DOC):]
    return dt


def build_variable(iv, roles, index):
    name = value(iv, "name", "name") or iv["@id"].lstrip("#")
    vm = {"@type": ["schema:PropertyValue", "cdi:InstanceVariable"],
          "@id": iv["@id"], "schema:name": name}
    definition = value(iv, "definition", "content")
    if definition and definition != name:
        vm["schema:description"] = definition
    if iv["@id"] in roles:
        vm["cdif:role"] = roles[iv["@id"]]
    svd = index.get(iv.get("takesSubstantiveValuesFrom_SubstantiveValueDomain"))
    if svd:
        dt = xsd_iri(value(svd, "recommendedDataType", "entryValue"))
        if dt:
            vm["cdif:physicalDataType"] = {"@id": dt}
        level = value(index.get(svd.get("isDescribedBy")), "classificationLevel")
        if level:
            vm["cdi:classificationLevel"] = level
    return vm


def build_role_map(objects):
    """{InstanceVariable id: cdif:role} from the components that it defines."""
    roles = {}
    for comp in by_type(objects, *COMPONENT_ROLE):
        var = comp.get("isDefinedBy_RepresentedVariable")
        if var:
            roles[var] = COMPONENT_ROLE[comp["@type"]]
    return roles


def build_data_structure(objects, index):
    struct = next(iter(by_type(objects, *STRUCTURE_TYPES)), None)
    if struct is None:
        return None, None
    order = position_map(objects, "ComponentPosition")
    comp_ids = sorted(as_list(struct.get("has_DataStructureComponent")),
                      key=lambda c: order.get(c, 1_000_000))
    components = []
    for cid in comp_ids:
        comp = index.get(cid)
        if comp is None:
            continue
        node = {"@type": ["cdi:" + comp["@type"]], "@id": cid}
        var = comp.get("isDefinedBy_RepresentedVariable")
        if var:
            node["cdif:isDefinedBy_RepresentedVariable"] = {"@id": var}
        components.append(node)
    node = {"@type": ["cdi:" + struct["@type"]], "@id": struct["@id"]}
    if components:
        node["cdi:has_DataStructureComponent"] = components
    pk = struct.get("has_PrimaryKey")
    if pk:
        node["cdif:has_PrimaryKey"] = {"@id": pk}
    return node, DATASET_TYPE.get(struct["@type"], "cdi:StructuredDataSet")


def build_physical_mappings(objects, index):
    """TextMapping -> cdif:TextMapping, ordered by PhysicalMappingPosition.

    The export gives every column the same nullSequence list (its own default
    set of null tokens); cdi:nullSequence takes a single string, so it is not
    carried over.
    """
    order = position_map(objects, "PhysicalMappingPosition")
    mappings = []
    for tm in by_type(objects, "TextMapping"):
        entry = {"@type": ["cdif:TextMapping"]}
        if tm["@id"] in order:
            entry["cdif:index"] = order[tm["@id"]]
        fmt = value(tm, "format", "entryValue")
        if fmt:
            entry["cdif:format"] = fmt
        var = index.get(tm.get("formats"))
        pdt = value(var, "physicalDataType", "entryValue")
        if pdt:
            entry["cdif:physicalDataType"] = pdt
        for key in ("minimumLength", "maximumLength"):
            if key in tm:
                entry["cdi:" + key] = tm[key]
        if tm.get("formats"):
            entry["cdif:formats_InstanceVariable"] = {"@id": tm["formats"]}
        mappings.append(entry)
    mappings.sort(key=lambda m: m.get("cdif:index", 1_000_000))
    return mappings


def count_property(name, concept, n):
    return {"@type": ["schema:PropertyValue"],
            "schema:propertyID": [DDI_DISCOVERY + concept],
            "schema:name": name, "schema:value": n}


def build_distribution(header, objects, index, nvars):
    ttd = next(iter(by_type(objects, "TabularTextDataSet")), {})
    segment = next(iter(by_type(objects, "Segment")), {})
    dist = {"@type": ["schema:DataDownload"],
            "schema:name": (segment.get("physicalFileName")
                            or value(ttd, "name", "name")
                            or header.get("dct:title"))}
    # The export records the file it read as prov:wasDerivedFrom; for a
    # Dataverse file that is its download (api/access/datafile) URL.
    source = value(header, "prov:wasDerivedFrom", "@id")
    dist["schema:contentUrl"] = source or NIL_MISSING
    if ttd.get("isDelimited") and ttd.get("delimiter") == ",":
        dist["schema:encodingFormat"] = ["text/csv"]
    if "schema:contentSize" in header:
        dist["schema:contentSize"] = str(header["schema:contentSize"])
    checksum = header.get("schema:checksum")
    if checksum and checksum.get("schema:checksumValue"):
        dist["spdx:checksum"] = {
            "@type": ["spdx:Checksum"],
            "spdx:algorithm": checksum.get("schema:algorithm", ""),
            "spdx:checksumValue": checksum["schema:checksumValue"]}

    if ttd:
        dist["@type"].append("cdi:TabularTextDataSet")
        for key in ("isDelimited", "isFixedWidth", "delimiter", "hasHeader",
                    "headerRowCount"):
            if key in ttd:
                dist["cdi:" + key] = ttd[key]
        counts = []
        if "recordCount" in ttd:
            counts.append(count_property("countRows", "caseQuantity",
                                         ttd["recordCount"]))
        if nvars:
            counts.append(count_property("countColumns", "variableQuantity", nvars))
        if counts:
            dist["schema:additionalProperty"] = counts

    structure, dataset_type = build_data_structure(objects, index)
    if structure is not None:
        dist["@type"].append(dataset_type)
        dist["cdi:isStructuredBy"] = structure
    mappings = build_physical_mappings(objects, index)
    if mappings:
        dist["cdif:hasPhysicalMapping"] = mappings
    return dist


def build_catalog_record(dataset_iri, name, source_conforms):
    note = ("Metadata converted to CDIF by ddicdi_jsonld_to_cdif.py from a "
            "DDI-CDI JSON-LD description")
    if source_conforms:
        note += f" (dct:conformsTo {source_conforms})"
    note += (". The data sample in the source (DataPoint / InstanceValue) is "
             "not carried over.")
    return {
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "@id": dataset_iri + "#cdif-catalog-record",
        "schema:name": f"Metadata record for: {name[:120]}",
        "schema:about": {"@id": dataset_iri},
        "schema:description": note,
        "dcterms:conformsTo": [
            {"@id": "https://w3id.org/cdif/core/1.1"},
            {"@id": "https://w3id.org/cdif/discovery/1.1"},
            {"@id": "https://w3id.org/cdif/data_description/1.1"}],
    }


def convert(path, explicit_id=None, detect=True):
    header = json.loads(Path(path).read_text(encoding="utf-8"))
    objects = header.pop("DDICDIModels", [])
    index = {o["@id"]: o for o in objects if "@id" in o}

    roles = build_role_map(objects)
    variables = [build_variable(o, roles, index)
                 for o in by_type(objects, "InstanceVariable")]

    iri = explicit_id or header.get("@id") or f"urn:ddi-cdi:{Path(path).stem}"
    name = header.get("dct:title") or value(
        next(iter(by_type(objects, "WideDataSet")), {}), "name", "name") or Path(path).stem

    doc = {"@context": dict(CDI_CTX, spdx="http://spdx.org/rdf/terms#"),
           "@id": iri, "@type": ["schema:Dataset"], "schema:name": name}
    if header.get("dct:description"):
        doc["schema:description"] = header["dct:description"]
    doc["schema:identifier"] = {"@type": ["schema:PropertyValue"],
                                "schema:value": header.get("dct:identifier") or iri}
    # No modification date in the source; the converters' placeholder.
    doc["schema:dateModified"] = "1900-01-01"
    doc["schema:license"] = [header.get("dct:license") or NIL_MISSING]
    if header.get("dct:language"):
        doc["schema:inLanguage"] = header["dct:language"]
    parts = [p for p in as_list(header.get("dct:isPartOf")) if value(p, "@id")]
    if parts:
        doc["schema:isPartOf"] = [{"@id": p["@id"]} for p in parts]
    if value(header, "prov:wasDerivedFrom", "@id"):
        doc["prov:wasDerivedFrom"] = [{"@id": header["prov:wasDerivedFrom"]["@id"]}]
    if variables:
        doc["schema:variableMeasured"] = variables
    doc["schema:distribution"] = [
        build_distribution(header, objects, index, len(variables))]
    doc["schema:subjectOf"] = build_catalog_record(
        iri, name, header.get("dct:conformsTo"))

    if detect and _HAVE_DETECT:
        try:
            uris = detect_conformance(doc)
            if uris:
                apply_conformance(doc, uris)
        except Exception as e:
            print(f"WARN: conformance detection failed ({e}); keeping the "
                  f"built-in conformsTo", file=sys.stderr)
    return doc


def main():
    ap = argparse.ArgumentParser(
        description="Convert DDI-CDI JSON-LD instances to CDIF JSON-LD")
    ap.add_argument("input", help="Input DDI-CDI JSON-LD file (with DDICDIModels)")
    ap.add_argument("-o", "--output", help="Output JSON file (default: stdout)")
    ap.add_argument("--id", dest="explicit_id",
                    help="Explicit dataset IRI for @id (default: the source @id)")
    ap.add_argument("--static-conformance", action="store_true",
                    help="Keep the built-in conformsTo instead of detecting it")
    args = ap.parse_args()

    doc = convert(args.input, explicit_id=args.explicit_id,
                  detect=not args.static_conformance)
    out = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"Written: {args.output} "
              f"({len(doc.get('schema:variableMeasured', []))} vars)")
    else:
        print(out)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Harvest OHDSI gaiaCatalog / GDSC dataset metadata to CDIF Discovery + Core JSON-LD.

Reads the schema.org `meta_json-ld_{table_id}.json` files harvested from
https://github.com/OHDSI/gaiaCatalog/tree/main/datastore/data (stored in
OHDSIMetadata/ beside this script) and writes CDIF-conformant documents to
cdifMetadata/.

The source documents are schema.org Dataset records authored with the default
vocabulary set to https://schema.org/ (so property/type tokens are unprefixed).
CDIF requires explicit `schema:` prefixes on http://schema.org/ terms, `@type`
as arrays, an explicit `schema:identifier`, and a `schema:subjectOf`
CatalogRecord carrying `dcterms:conformsTo`. This script performs that
transformation.

Target profiles: cdifCore (https://w3id.org/cdif/core/1.1) and
cdifDiscovery (https://w3id.org/cdif/discovery/1.1). The per-variable
`variableMeasured` entries are plain schema:PropertyValue (discovery level, not
cdi:InstanceVariable), so the document is not claimed as data_description.

By default the source ETL `about` action (its object/instrument/result) is also
mapped to a cdifProv `prov:wasGeneratedBy` activity and the document additionally
declares cdifProvenance (https://w3id.org/cdif/provenance/1.1). Disable with
`--no-provenance`.

Usage:
    python harvesters/OHDSI/harvest_ohdsi_to_cdif.py      # OHDSIMetadata/ -> cdifMetadata/
    python harvesters/OHDSI/harvest_ohdsi_to_cdif.py -i IN -o OUT
    python harvesters/OHDSI/harvest_ohdsi_to_cdif.py --no-provenance   # discovery + core only
"""
import argparse
import datetime
import json
import pathlib
import sys
from urllib.parse import parse_qs, urlsplit

SCHEMA = "http://schema.org/"

# Source repo location for the CatalogRecord provenance note / isBasedOn link.
SRC_REPO = "https://github.com/OHDSI/gaiaCatalog/tree/main/datastore/data"
SRC_RAW = "https://raw.githubusercontent.com/OHDSI/gaiaCatalog/main/datastore/data"

CDIF_CORE = "https://w3id.org/cdif/core/1.1"
CDIF_DISCOVERY = "https://w3id.org/cdif/discovery/1.1"
CDIF_PROVENANCE = "https://w3id.org/cdif/provenance/1.1"

# CDIF-standard placeholder for "license not stated" (OGC nil value).
LICENSE_MISSING = "http://www.opengis.net/def/nil/OGC/0/missing"

OUT_CONTEXT = {
    "schema": SCHEMA,
    "dcterms": "http://purl.org/dc/terms/",
    "prov": "http://www.w3.org/ns/prov#",
    "dcat": "http://www.w3.org/ns/dcat#",
    "qudt": "http://qudt.org/schema/qudt/",
    "geosparql": "http://www.opengis.net/ont/geosparql#",
}

# Extension formats to MIME types for DataDownload encodingFormat.
FORMAT_MIME = {
    "sql": "application/sql",
    "shp": "application/octet-stream; format=shapefile",
    "csv": "text/csv",
    "json": "application/json",
    "geojson": "application/geo+json",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "gpkg": "application/geopackage+sqlite3",
}

# JSON-LD keywords passed through untouched.
KEYWORDS = {"@id", "@value", "@list", "@type", "@context"}


def prefix_type(value):
    """Normalize a @type value to a list of `schema:`-prefixed tokens."""
    tokens = value if isinstance(value, list) else [value]
    out = []
    for tok in tokens:
        if isinstance(tok, str) and ":" not in tok and not tok.startswith("@"):
            out.append("schema:" + tok)
        else:
            out.append(tok)
    return out


def convert(node):
    """Recursively convert a source node: prefix bare schema.org keys/types."""
    if isinstance(node, list):
        return [convert(v) for v in node]
    if not isinstance(node, dict):
        return node

    out = {}
    for key, val in node.items():
        if key == "@type":
            out["@type"] = prefix_type(val)
        elif key in KEYWORDS:
            out[key] = val
        elif ":" in key or key.startswith("@"):
            # Already-prefixed (e.g. qudt:dataType) or keyword-ish: keep as-is.
            out[key] = convert(val)
        else:
            out["schema:" + key] = convert(val)
    return out


def make_identifier(source_id, table_id):
    return {
        "@type": ["schema:PropertyValue"],
        "schema:value": table_id,
        "schema:url": source_id,
    }


def convert_creators(items, default_type):
    out = []
    for it in items:
        if isinstance(it, str):
            out.append({"@type": [default_type], "schema:name": it})
            continue
        node = convert(it)
        if "@type" not in node:
            node = {"@type": [default_type], **node}
        out.append(node)
    return out


def _name_from_property_id(pid):
    """Derive a human label from a propertyID URI (last path/fragment segment)."""
    if not isinstance(pid, str):
        return None
    tail = pid.rstrip("/").replace("#", "/").split("/")[-1]
    tail = tail.replace("_", " ").replace("-", " ").strip()
    return tail[:1].upper() + tail[1:] if tail else None


def convert_additional_property(items):
    """Convert additionalProperty PropertyValues, ensuring each has a name.

    The CDIF discovery SHACL requires schema:name (>=3 chars) on every
    additionalProperty node. Source SRS entries carry only propertyID + value,
    so derive a label from the propertyID.
    """
    out = []
    for it in items:
        node = convert(it)
        if not node.get("schema:name"):
            label = _name_from_property_id(it.get("propertyID"))
            node["schema:name"] = label if label and len(label) >= 3 else "property"
        out.append(node)
    return out


def convert_references(items):
    """Convert isBasedOn-style items to pure {@id} references.

    These are cross-references to source works, not resources being cataloged.
    A bare @id reference carries the link without pulling the node into the
    dataset-completeness SHACL shapes. Items without an @id (no resolvable
    identifier to reference) are dropped.
    """
    out = []
    for it in items:
        if isinstance(it, str):
            out.append({"@id": it})
        elif isinstance(it, dict) and it.get("@id"):
            out.append({"@id": it["@id"]})
    return out


def convert_spatial_coverage(items):
    """Convert source Place/GeoShape, turning polygon rings into a schema:box.

    The CDIF discovery SHACL geoShape shape accepts schema:line or schema:box
    (lat/long pair strings), not schema:polygon. Source bbox polygons are
    "lon lat" rings; emit their bounding box as "minLat minLon maxLat maxLon".
    """
    out = []
    for p in items:
        node = convert(p)
        geo = p.get("geo") if isinstance(p, dict) else None
        if isinstance(geo, dict) and geo.get("polygon"):
            nums = [float(x) for x in geo["polygon"].split()]
            lons, lats = nums[0::2], nums[1::2]
            box = f"{min(lats)} {min(lons)} {max(lats)} {max(lons)}"
            node["schema:geo"] = {"@type": ["schema:GeoShape"], "schema:box": box}
        out.append(node)
    return out


def convert_variables(items):
    """Convert variableMeasured PropertyValues, cleaning source whitespace.

    Source variable names often carry stray leading newlines; strip them.
    Entries with no usable name (e.g. an empty {"@type":"PropertyValue"}) are
    dropped, since schema:name is required on variableMeasured.
    """
    out = []
    for v in items:
        node = convert(v)
        name = node.get("schema:name")
        if isinstance(name, str):
            name = name.strip()
            node["schema:name"] = name
        if not name:
            continue
        desc = node.get("schema:description")
        if isinstance(desc, str):
            node["schema:description"] = desc.strip()
        out.append(node)
    return out


def convert_measurement_technique(items):
    """Simplify the verbose source DefinedTerm (drop enumerated term sets)."""
    out = []
    for t in items:
        if isinstance(t, str):
            out.append({"@type": ["schema:DefinedTerm"], "schema:name": t})
            continue
        dt = {"@type": ["schema:DefinedTerm"]}
        if t.get("description"):
            dt["schema:name"] = t["description"]
        if t.get("termCode"):
            dt["schema:termCode"] = t["termCode"]
        out.append(dt)
    return out


def _encoding_format(url):
    fmt = parse_qs(urlsplit(url).query).get("format", [None])[0]
    if fmt:
        return FORMAT_MIME.get(fmt.lower(), fmt)
    return None


def convert_distribution(dist):
    """Turn source distribution URL string(s) into schema:DataDownload nodes.

    Non-URL placeholders ("TBD", relative paths) yield no distribution; the
    root schema:url still satisfies the CDIF url-or-distribution requirement.
    """
    urls = dist if isinstance(dist, list) else [dist]
    out = []
    for u in urls:
        if not isinstance(u, str) or not u.startswith("http"):
            continue
        dd = {"@type": ["schema:DataDownload"], "schema:contentUrl": u}
        fmt = _encoding_format(u)
        if fmt:
            dd["schema:encodingFormat"] = [fmt]
        out.append(dd)
    return out


def find_etl_action(src):
    """Return the source ETL potentialAction that carries real provenance.

    Each source doc has two `about` Events: a "Pseudo Code"/"TODO" placeholder
    and a real ETL action naming the operation (curl/wget/...) with object
    (upstream source), instrument (tool), and result (output). Return the latter.
    """
    for event in src.get("about", []):
        if not isinstance(event, dict):
            continue
        pa = event.get("potentialAction")
        if isinstance(pa, dict) and pa.get("object") and pa.get("instrument") \
                and pa.get("result"):
            return pa
    return None


def _instrument_name(instr):
    """Derive a name for the ETL tool (source instrument has no schema:name)."""
    if instr.get("name"):
        return instr["name"]
    command = instr.get("featureList")
    return f"GDSC ETL tools ({command})" if command else "GDSC ETL tools"


def build_provenance_activity(pa, dataset_id):
    """Map a source ETL potentialAction to a cdifProv wasGeneratedBy Activity.

        Action           -> ["prov:Activity", "schema:Action"]
        name             -> schema:name ("ETL: <operation>")
        description      -> schema:description
        object (source)  -> schema:object   (reference to the upstream source URL)
        instrument       -> prov:used[].schema:instrument (name ensured)
        result (output)  -> schema:result   (reference to the generated dataset)

    object/result are emitted as {@id} references, not inline schema:Dataset
    nodes: the cdifProv convention keeps activity entities as references so they
    don't collide with the root schema:Dataset during discovery framing.
    """
    instrument = convert(pa["instrument"])
    if not instrument.get("schema:name"):
        instrument["schema:name"] = _instrument_name(pa["instrument"])

    activity = {
        "@type": ["prov:Activity", "schema:Action"],
        "schema:name": "ETL: " + pa.get("name", "process"),
    }
    if pa.get("description"):
        activity["schema:description"] = pa["description"]

    source = pa.get("object") or {}
    source_ref = source.get("@id") or source.get("url")
    if source_ref:
        activity["schema:object"] = {"@id": source_ref}

    activity["prov:used"] = [{"schema:instrument": instrument}]
    activity["schema:result"] = {"@id": dataset_id}
    return activity


def build_catalog_record(source_id, table_id, name, sd_date, source_filename,
                         with_provenance):
    if with_provenance:
        prov_note = (
            "The source ETL activity is carried as prov:wasGeneratedBy "
            "(schema:Action); the placeholder pseudo-code event was omitted."
        )
        conforms = [CDIF_CORE, CDIF_DISCOVERY, CDIF_PROVENANCE]
    else:
        prov_note = (
            "ETL pseudo-code events (schema:about) from the source were not "
            "carried over."
        )
        conforms = [CDIF_CORE, CDIF_DISCOVERY]

    note = (
        f"CDIF metadata record for '{name}', harvested from the OHDSI "
        f"gaiaCatalog datastore ({source_filename}) published by the University "
        f"of Miami Geospatial Digital Special Collections (GDSC), and transformed "
        f"to the CDIF Core and Discovery profiles. {prov_note} Upstream lineage "
        f"is retained via schema:isBasedOn where the source provided it."
    )
    return {
        "@id": source_id + "#cdif-catalog-record",
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:about": {"@id": source_id},
        "schema:description": note,
        "schema:sdDatePublished": sd_date,
        "schema:isBasedOn": {"@id": f"{SRC_RAW}/{table_id}/{source_filename}"},
        "dcterms:conformsTo": [{"@id": uri} for uri in conforms],
    }


def convert_document(src, source_filename, harvest_date, provenance=True):
    source_id = src["@id"]
    table_id = source_id.rstrip("/").split("/")[-1]
    name = src.get("name", table_id)

    doc = {
        "@context": dict(OUT_CONTEXT),
        "@id": source_id,
        "@type": ["schema:Dataset"],
        "schema:name": name,
        "schema:identifier": make_identifier(source_id, table_id),
    }

    if src.get("description"):
        doc["schema:description"] = src["description"]

    # dateModified is required; fall back to datePublished / version.
    doc["schema:dateModified"] = (
        src.get("dateModified") or src.get("datePublished") or src.get("version")
    )
    if src.get("datePublished"):
        doc["schema:datePublished"] = src["datePublished"]
    if src.get("version"):
        doc["schema:version"] = src["version"]

    if src.get("url"):
        doc["schema:url"] = src["url"]

    # license OR conditionsOfAccess is required; supply OGC nil when absent.
    license_val = src.get("license")
    doc["schema:license"] = [license_val] if license_val else [LICENSE_MISSING]

    if src.get("@language"):
        doc["schema:inLanguage"] = src["@language"]
    if src.get("keywords"):
        doc["schema:keywords"] = src["keywords"]
    if src.get("type"):
        doc["schema:additionalType"] = [src["type"]]

    if src.get("creator"):
        doc["schema:creator"] = convert_creators(src["creator"], "schema:Organization")
    if src.get("provider"):
        doc["schema:provider"] = convert_creators(src["provider"], "schema:Organization")

    if src.get("spatialCoverage"):
        doc["schema:spatialCoverage"] = convert_spatial_coverage(src["spatialCoverage"])
    if src.get("temporalCoverage"):
        doc["schema:temporalCoverage"] = src["temporalCoverage"]

    if src.get("measurementTechnique"):
        doc["schema:measurementTechnique"] = convert_measurement_technique(
            src["measurementTechnique"]
        )
    if src.get("additionalProperty"):
        doc["schema:additionalProperty"] = convert_additional_property(
            src["additionalProperty"]
        )
    if src.get("variableMeasured"):
        variables = convert_variables(src["variableMeasured"])
        if variables:
            doc["schema:variableMeasured"] = variables
    if src.get("includedInDataCatalog"):
        doc["schema:includedInDataCatalog"] = convert(src["includedInDataCatalog"])
    if src.get("isBasedOn"):
        doc["schema:isBasedOn"] = convert_references(src["isBasedOn"])

    dist = convert_distribution(src.get("distribution", []))
    if dist:
        doc["schema:distribution"] = dist

    etl_action = find_etl_action(src) if provenance else None
    if etl_action:
        doc["prov:wasGeneratedBy"] = [build_provenance_activity(etl_action, source_id)]

    doc["schema:subjectOf"] = build_catalog_record(
        source_id, table_id, name, harvest_date, source_filename,
        with_provenance=bool(etl_action),
    )
    return doc


def main():
    here = pathlib.Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-i", "--indir", default=str(here / "OHDSIMetadata"))
    ap.add_argument("-o", "--outdir", default=str(here / "cdifMetadata"))
    ap.add_argument("--provenance", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="map the source ETL action to prov:wasGeneratedBy and "
                         "declare the CDIF provenance profile (default: on)")
    args = ap.parse_args()

    indir = pathlib.Path(args.indir)
    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    harvest_date = datetime.date.today().isoformat()

    files = sorted(indir.glob("meta_json-ld_*.json"))
    if not files:
        print(f"No meta_json-ld_*.json files in {indir}", file=sys.stderr)
        return 1

    for f in files:
        src = json.loads(f.read_text(encoding="utf-8"))
        table_id = f.stem.replace("meta_json-ld_", "")
        doc = convert_document(src, f.name, harvest_date, provenance=args.provenance)
        out = outdir / f"cdif_{table_id}.json"
        out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  {f.name} -> {out.name}")

    print(f"Converted {len(files)} document(s) to {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

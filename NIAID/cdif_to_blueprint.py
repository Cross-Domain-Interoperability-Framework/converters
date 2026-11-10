"""Write CDIF NIAID records as NIAID Blueprint metadata, and report how complete they are.

The NIAID Blueprint for Digital Objects (https://doi.org/10.5281/zenodo.17161561) lists
17 schema.org-based elements with default identifiers and vocabularies. GO FAIR US's
CEDAR template "NIAID Blueprint Compliant Schema 0.7.0"
(https://github.com/go-fair-us/blueprint-cedar-specifications, not reviewed by NIAID)
encodes them as a CEDAR template whose JSON Schema an instance can be validated against.

For each CDIF record this writes a CEDAR-shaped instance and checks it against the
template's JSON Schema. That schema checks shape only (an empty field passes), so the
INDEX also says, for each Blueprint element, whether the record fills it and whether it
uses the Blueprint's default identifier or vocabulary:

    python NIAID/cdif_to_blueprint.py                  # cdifJSON-LD/ -> blueprintCEDAR/
    python NIAID/cdif_to_blueprint.py rec.jsonld -o out/ --template template.json

The template is read from GitHub at run time (its repository has no license, so it is
not copied here) unless --template names a local copy.
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

import jsonschema

HERE = Path(__file__).resolve().parent
TEMPLATE_URL = ("https://raw.githubusercontent.com/go-fair-us/blueprint-cedar-specifications/"
                "main/niaid-blueprint-compliant-md-template.json")
TEMPLATE_IRI = "https://repo.metadatacenter.org/templates/bf5c4fc0-8a01-488d-9151-31aae2635f7c"
CONVERTER = "https://github.com/Cross-Domain-Interoperability-Framework/converters/tree/main/NIAID"
OGC_NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
NCIT_NCI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"

# The instance's names for the Blueprint elements, and the IRIs the CEDAR template gives them.
CONTEXT = {
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#", "xsd": "http://www.w3.org/2001/XMLSchema#",
    "pav": "http://purl.org/pav/", "schema": "http://schema.org/", "oslc": "http://open-services.net/ns/core#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "rdfs:label": {"@type": "xsd:string"}, "schema:isBasedOn": {"@type": "@id"},
    "schema:name": {"@type": "xsd:string"}, "schema:description": {"@type": "xsd:string"},
    "pav:createdOn": {"@type": "xsd:dateTime"}, "pav:createdBy": {"@type": "@id"},
    "pav:lastUpdatedOn": {"@type": "xsd:dateTime"}, "oslc:modifiedBy": {"@type": "@id"},
    "type": "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
    "identifier": "https://schema.org/identifier", "name": "https://schema.org/name",
    "description": "https://schema.org/description", "dateCreated": "https://schema.org/dateCreated",
    "Author(s)": "https://schema.org/author", "funder": "https://schema.org/funder",
    "funding": "https://schema.org/funding", "measurementTechnique": "https://schema.org/measurementTechnique",
    "distribution": "https://schema.org/distribution", "citation": "https://schema.org/citation",
    "infectiousAgent": "https://schema.org/infectiousAgent", "host": "https://schema.org/taxonomicRange",
    "healthCondition": "https://schema.org/healthCondition",
    "conditionsOfAccess": "https://schema.org/conditionsOfAccess", "License(s)": "https://schema.org/license",
    "Spatial Coverage": "https://schema.org/spatialCoverage",
    "Temporal Coverage": "https://schema.org/temporalCoverage",
}


def as_list(v):
    return v if isinstance(v, list) else [] if v is None else [v]


def types(node):
    return as_list(node.get("@type")) if isinstance(node, dict) else []


def is_url(v):
    return isinstance(v, str) and v.startswith(("http://", "https://")) and v != OGC_NIL


def dataset_of(record):
    nodes = record.get("@graph", [record])
    return next(n for n in nodes if "schema:Dataset" in types(n))


def identifier_url(value):
    """A resolvable identifier: a URL, a PropertyValue's url, or a DOI made resolvable."""
    for v in as_list(value):
        if is_url(v):
            return v
        if isinstance(v, dict):
            if is_url(v.get("schema:url")):
                return v["schema:url"]
            if is_url(v.get("@id")):
                return v["@id"]
            val = v.get("schema:value")
            if isinstance(val, str) and val.startswith("10."):
                return "https://doi.org/" + val
    return None


def term_iri(term):
    """A term's ontology IRI: NCI's own form for NCIT (the template's), else its first
    sameAs, else its url when that is an IRI."""
    if not isinstance(term, dict):
        return None
    same = [s["@id"] for s in as_list(term.get("schema:sameAs")) if isinstance(s, dict) and is_url(s.get("@id"))]
    nci = [s for s in same if s.startswith(NCIT_NCI)]
    if nci or same:
        return (nci or same)[0]
    return term["schema:url"] if is_url(term.get("schema:url")) else None


def label(node):
    name = node.get("schema:name") if isinstance(node, dict) else None
    return name if isinstance(name, str) else None


def iri_field(iri, text=None):
    return {"@id": iri, **({"rdfs:label": text} if text else {})}


def terms(value):
    out = [iri_field(term_iri(t), label(t)) for t in as_list(value) if term_iri(t)]
    return out or [{}]


def agents(dataset):
    creators = dataset.get("schema:creator")
    items = creators.get("@list", []) if isinstance(creators, dict) else as_list(creators)
    people, orgs = [], []
    for agent in items:
        ident = identifier_url(agent.get("schema:identifier")) if isinstance(agent, dict) else None
        if "schema:Person" in types(agent) and ident and "orcid.org/" in ident:
            people.append(iri_field(ident, label(agent)))
        elif "schema:Organization" in types(agent) and ident and "ror.org/" in ident:
            orgs.append(iri_field(ident, label(agent)))
    return {"accountablePerson": people or [{}], "sourceOrganization": orgs or [{}],
            "@context": {"accountablePerson": "https://schema.org/accountablePerson",
                         "sourceOrganization": "http://schema.org/sourceOrganization"}}


def grants(dataset):
    funders, numbers = [], []
    for grant in as_list(dataset.get("schema:funding")):
        if not isinstance(grant, dict):
            continue
        funder = grant.get("schema:funder")
        ident = identifier_url(funder.get("schema:identifier")) if isinstance(funder, dict) else None
        if ident and "ror.org/" in ident and ident not in [f["@id"] for f in funders]:
            funders.append(iri_field(ident, label(funder)))
        for g in as_list(grant.get("schema:identifier")):
            value = g.get("schema:value") if isinstance(g, dict) else g
            if isinstance(value, str) and value not in [n["@value"] for n in numbers]:
                numbers.append({"@value": value})
    return funders or [{}], numbers or [{"@value": None}]


def links(values, keys):
    out = []
    for v in as_list(values):
        url = v if is_url(v) else next((v[k] for k in keys if isinstance(v, dict) and is_url(v.get(k))), None)
        if not url and isinstance(v, dict):
            url = identifier_url(v.get("schema:identifier"))
        if url and {"@id": url} not in out:
            out.append({"@id": url})
    return out


def access(dataset):
    out = []
    for item in as_list(dataset.get("schema:conditionsOfAccess")):
        text = item.get("schema:url") or label(item) if isinstance(item, dict) else item
        if isinstance(text, str) and text.strip():
            out.append({"@value": text.strip()})
    return out or [{"@value": None}]


def licenses(dataset):
    out = []
    for item in as_list(dataset.get("schema:license")):
        entry = {"licenseIdentifierSelection": {}, "licenseIdentifierIri": {}, "licenseTerms": {"@value": None},
                 "@context": {"licenseIdentifierSelection": "https://schema.org/termCode",
                              "licenseIdentifierIri": "https://schema.org/identifier",
                              "licenseTerms": "https://schema.org/termsOfService"}}
        spdx = [s["@id"] for s in as_list(item.get("schema:sameAs")) if isinstance(s, dict)
                and str(s.get("@id", "")).startswith("http://spdx.org/licenses/")] if isinstance(item, dict) else []
        if spdx:
            entry["licenseIdentifierSelection"] = iri_field(spdx[0], label(item))
        elif is_url(item):
            entry["licenseIdentifierIri"] = {"@id": item}
        elif isinstance(item, str) and item.strip() and item != OGC_NIL:
            entry["licenseTerms"] = {"@value": item.strip()}
        else:
            continue
        out.append(entry)
    return out


def places(dataset):
    out = []
    for place in as_list(dataset.get("schema:spatialCoverage")):
        if not isinstance(place, dict):
            continue
        geo = place.get("schema:geo")
        wkt = None
        if isinstance(geo, dict) and "schema:latitude" in geo and "schema:longitude" in geo:
            wkt = f"POINT ({geo['schema:longitude']} {geo['schema:latitude']})"
        elif isinstance(geo, dict) and isinstance(geo.get("schema:box"), str):
            s, w, n, e = geo["schema:box"].replace(",", " ").split()[:4]
            wkt = f"POLYGON (({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"
        # The template's country field takes an NCIT Country concept; CDIF records carry
        # ISO 3166 codes, which have no slot in the template, so the field stays empty.
        out.append({"addressCountry": [{}], "containedInPlace": [{}], "geo": [{"@value": None}],
                    "applicableLocation": [{"@value": wkt}],
                    "@context": {"addressCountry": "https://schema.org/addressCountry",
                                 "containedInPlace": "https://schema.org/containedInPlace",
                                 "geo": "https://schema.org/geo",
                                 "applicableLocation": "https://schema.org/applicableLocation"}})
    return out


def intervals(dataset):
    out = []
    for t in as_list(dataset.get("schema:temporalCoverage")):
        start = end = None
        if isinstance(t, str) and "/" in t:
            start, end = (p[:10] or None for p in t.split("/", 1))
        elif isinstance(t, dict):
            start = (t.get("time:hasBeginning") or {}).get("time:inXSDDate")
            end = (t.get("time:hasEnd") or {}).get("time:inXSDDate")
        if start or end:
            out.append({"startDate": {"@value": start, "@type": "xsd:date"},
                        "startTime": {"@value": None, "@type": "xsd:time"},
                        "endDate": {"@value": end, "@type": "xsd:date"},
                        "endTime": {"@value": None, "@type": "xsd:time"},
                        "@context": {"startDate": "https://schema.org/startDate",
                                     "startTime": "https://schema.org/startTime",
                                     "endDate": "https://schema.org/endDate",
                                     "endTime": "https://schema.org/endTime"}})
    return out


def text(value):
    return {"@value": value if isinstance(value, str) and value.strip() else None}


# The template's element fields, which need their own @id and @context, even when empty.
ELEMENT_CONTEXTS = {
    "Author(s)": {"accountablePerson": "https://schema.org/accountablePerson",
                  "sourceOrganization": "http://schema.org/sourceOrganization"},
    "License(s)": {"licenseIdentifierSelection": "https://schema.org/termCode",
                   "licenseIdentifierIri": "https://schema.org/identifier",
                   "licenseTerms": "https://schema.org/termsOfService"},
    "Spatial Coverage": {"addressCountry": "https://schema.org/addressCountry",
                         "containedInPlace": "https://schema.org/containedInPlace",
                         "geo": "https://schema.org/geo",
                         "applicableLocation": "https://schema.org/applicableLocation"},
    "Temporal Coverage": {"startDate": "https://schema.org/startDate", "startTime": "https://schema.org/startTime",
                          "endDate": "https://schema.org/endDate", "endTime": "https://schema.org/endTime"},
}


def identify_elements(instance, base):
    for key, context in ELEMENT_CONTEXTS.items():
        slug = key.split("(")[0].strip().lower().replace(" ", "-")
        items = instance[key] if isinstance(instance[key], list) else [instance[key]]
        for n, item in enumerate(items, 1):
            item["@id"] = f"{base}-{slug}-{n}"
            item["@context"] = context
    return instance


def to_blueprint(record):
    d = dataset_of(record)
    catalog = d.get("schema:subjectOf") if isinstance(d.get("schema:subjectOf"), dict) else {}
    stamp = str(catalog.get("schema:dateModified") or "1900-01-01")[:10] + "T00:00:00Z"
    created = next((str(d[k])[:10] for k in ("schema:dateCreated", "schema:datePublished") if d.get(k)), None)
    ident = identifier_url(d.get("schema:identifier"))
    funders, numbers = grants(d)
    downloads = links(d.get("schema:distribution"), ("schema:contentUrl", "schema:url"))
    return identify_elements({
        "@context": CONTEXT,
        "@id": d["@id"] + "#niaid-blueprint",
        "schema:isBasedOn": TEMPLATE_IRI,
        "schema:name": f"NIAID Blueprint metadata for {d['@id']}",
        "schema:description": "Written from the CDIF NIAID record by " + CONVERTER,
        "pav:createdOn": stamp, "pav:createdBy": CONVERTER,
        "pav:lastUpdatedOn": stamp, "oslc:modifiedBy": CONVERTER,
        "type": {"@id": "https://schema.org/Dataset", "rdfs:label": "Dataset"},
        "identifier": {"@id": ident} if ident else {},
        "name": text(d.get("schema:name")),
        "description": text(d.get("schema:description")),
        "dateCreated": {"@value": created, "@type": "xsd:date"},
        "Author(s)": agents(d),
        "funder": funders,
        "funding": numbers,
        "measurementTechnique": terms(d.get("schema:measurementTechnique")),
        "distribution": downloads or ([{"@id": d["schema:url"]}] if is_url(d.get("schema:url")) else [{}]),
        "citation": links(d.get("schema:citation"), ("schema:url",)) or [{}],
        "infectiousAgent": terms(d.get("schema:infectiousAgent")),
        "host": terms(d.get("nde:species")),
        "healthCondition": terms(d.get("schema:healthCondition")),
        "conditionsOfAccess": access(d),
        "License(s)": licenses(d) or [{"licenseIdentifierSelection": {}, "licenseIdentifierIri": {},
                                       "licenseTerms": {"@value": None}}],
        "Spatial Coverage": places(d) or [{"addressCountry": [{}], "containedInPlace": [{}],
                                           "geo": [{"@value": None}], "applicableLocation": [{"@value": None}]}],
        "Temporal Coverage": intervals(d) or [{"startDate": {"@value": None, "@type": "xsd:date"},
                                               "startTime": {"@value": None, "@type": "xsd:time"},
                                               "endDate": {"@value": None, "@type": "xsd:date"},
                                               "endTime": {"@value": None, "@type": "xsd:time"}}],
    }, d["@id"] + "#niaid-blueprint")


def filled(v):
    if isinstance(v, dict):
        return any(filled(x) for k, x in v.items() if k not in ("@context", "@type"))
    if isinstance(v, list):
        return any(filled(x) for x in v)
    return v not in (None, "", {})


def ids(v):
    if isinstance(v, dict):
        return ([v["@id"]] if isinstance(v.get("@id"), str) else []) + \
            [i for k, x in v.items() if k != "@context" for i in ids(x)]
    if isinstance(v, list):
        return [i for x in v for i in ids(x)]
    return []


# Each Blueprint element: the instance key, and a test for the Blueprint's default form.
ELEMENTS = {
    "identifier (DOI)": ("identifier", lambda v: any("doi.org/" in i for i in ids(v))),
    "name": ("name", None), "description": ("description", None), "dateCreated": ("dateCreated", None),
    "author (ORCID/ROR)": ("Author(s)", lambda v: bool(ids(v))),
    "funder (ROR)": ("funder", lambda v: any("ror.org/" in i for i in ids(v))),
    "funding": ("funding", None),
    "measurementTechnique (NCIT)": ("measurementTechnique", lambda v: any("Thesaurus.owl#" in i or "/NCIT_" in i for i in ids(v))),
    "distribution": ("distribution", None), "citation": ("citation", None),
    "infectiousAgent (NCBITaxon)": ("infectiousAgent", lambda v: any("NCBITaxon" in i or "NCBITAXON" in i for i in ids(v))),
    "host (NCBITaxon)": ("host", lambda v: any("NCBITaxon" in i or "NCBITAXON" in i for i in ids(v))),
    "healthCondition (MONDO)": ("healthCondition", lambda v: any("MONDO_" in i for i in ids(v))),
    "conditionsOfAccess": ("conditionsOfAccess", None),
    "license (SPDX)": ("License(s)", lambda v: any("spdx.org/licenses/" in i for i in ids(v))),
    "spatialCoverage": ("Spatial Coverage", None), "temporalCoverage": ("Temporal Coverage", None),
}


def assess(instance, validator):
    errors = [f"{'/'.join(map(str, e.path))}: {e.message[:200]}" for e in validator.iter_errors(instance)]
    elements = {}
    for name, (key, default) in ELEMENTS.items():
        value = instance.get(key)
        if key in ELEMENT_CONTEXTS:   # an element's own @id is bookkeeping, not content
            value = [{k: v for k, v in item.items() if k not in ("@id", "@context")}
                     for item in as_list(value)]
        has = filled(value)
        elements[name] = "default" if has and default and default(value) else "filled" if has else "missing"
    return {"schema_valid": not errors, "schema_errors": errors[:5], "elements": elements}


def load_template(path):
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    request = urllib.request.Request(TEMPLATE_URL, headers={"User-Agent": "cdif-converters"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="*", default=[str(HERE / "cdifJSON-LD")],
                    help="CDIF JSON-LD files or directories (default: cdifJSON-LD/)")
    ap.add_argument("-o", "--output", default=str(HERE / "blueprintCEDAR"))
    ap.add_argument("--template", help="Local copy of the CEDAR template JSON (default: fetch from GitHub)")
    args = ap.parse_args()

    validator = jsonschema.Draft4Validator(load_template(args.template))
    files = [f for p in map(Path, args.inputs)
             for f in (sorted(p.glob("*.jsonld")) if p.is_dir() else [p])]
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for f in files:
        instance = to_blueprint(json.loads(f.read_text(encoding="utf-8")))
        (out / (f.stem + ".json")).write_text(json.dumps(instance, indent=2, ensure_ascii=False) + "\n",
                                              encoding="utf-8", newline="\n")
        index.append({"file": f.stem + ".json", "source": f.name, **assess(instance, validator)})
    (out / "INDEX.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="\n")
    valid = sum(r["schema_valid"] for r in index)
    print(f"{len(index)} instances written to {out}; {valid} valid against the CEDAR template schema")
    for name in ELEMENTS:
        counts = {s: sum(r["elements"][name] == s for r in index) for s in ("default", "filled", "missing")}
        print(f"  {name:30s} default {counts['default']:4d}  other {counts['filled']:4d}  missing {counts['missing']:4d}")
    return 0 if valid == len(index) else 1


if __name__ == "__main__":
    sys.exit(main())

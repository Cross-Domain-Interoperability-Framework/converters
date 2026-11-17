#!/usr/bin/env python3
"""
cdif_to_dcat.py - Convert CDIF JSON-LD records to W3C DCAT 3 JSON-LD.

The output is one JSON-LD document with an @graph:

- a dcat:Dataset made from the CDIF record's root (a schema:Dataset);
- a dcat:CatalogRecord made from its schema:subjectOf catalog record, whose
  foaf:primaryTopic is the dataset;
- where the record names them, the dcat:Catalog that the metadata came from
  (dcat:record -> the record, dcat:dataset -> the dataset) and the catalogs the
  dataset is held in (dcat:dataset -> the dataset).

Distributions, agents, places, periods and the like are nested nodes. The
mapping is mappings/cdif-to-dcat.sssom.tsv, which this script reads at run time:
each row names a CDIF property, the kind of CDIF node it sits on, the DCAT
property it becomes and the shaper (transform) that builds the value. A CDIF
property with no row, or whose value a shaper has no DCAT form for, is kept on
the DCAT node unchanged (open world), as dcat_to_cdif.py does in the other
direction.

Usage:
    python DCAT/cdif_to_dcat.py record.jsonld -o out/
    python DCAT/cdif_to_dcat.py NIAID/cdifJSON-LD/ -o out/ --validate
"""
import argparse
import csv
import itertools
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "mappings" / "cdif-to-dcat.sssom.tsv"
OGC_NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
DATE_SENTINEL = "1900-01-01"
IANA = "http://www.iana.org/assignments/media-types/"

CONTEXT = {
    "dcat": "http://www.w3.org/ns/dcat#",
    "dcterms": "http://purl.org/dc/terms/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "vcard": "http://www.w3.org/2006/vcard/ns#",
    "prov": "http://www.w3.org/ns/prov#",
    "adms": "http://www.w3.org/ns/adms#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "org": "http://www.w3.org/ns/org#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "locn": "http://www.w3.org/ns/locn#",
    "gsp": "http://www.opengis.net/ont/geosparql#",
    "spdx": "http://spdx.org/rdf/terms#",
    "schema": "http://schema.org/",
}
# Keys that are never copied as open-world leftovers.
STRUCTURAL = {"@context", "@id", "@type"}


def as_list(v):
    return v if isinstance(v, list) else [] if v is None else [v]


def types(node):
    return as_list(node.get("@type")) if isinstance(node, dict) else []


def is_iri(v):
    return isinstance(v, str) and bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:\S+$", v)) \
        and not v.startswith("_:") and v != OGC_NIL


def load_rules(path=TABLE):
    rules = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            rules.setdefault(row["subject_class"], []).append(row)
    return rules


RULES = load_rules()


class Converter:
    """Converts one CDIF record. Nodes that must stand at the top level of the
    output graph (record, catalogs) are collected in self.graph."""

    def __init__(self, doc):
        self.doc = doc
        self.graph = []
        self.catalogs = {}
        self.blank = itertools.count(1)
        self.root = self._root(doc)
        rid = self.root.get("@id")
        self.dataset = {"@id": rid if is_iri(rid) else "_:dataset", "@type": self._dataset_types()}
        self.record = None

    # --- structure -------------------------------------------------------
    @staticmethod
    def _is_catalog_record(node):
        marks = [x.get("@id") if isinstance(x, dict) else x
                 for k in ("@type", "schema:additionalType") for x in as_list(node.get(k))]
        return "dcat:CatalogRecord" in marks

    def _root(self, doc):
        nodes = doc.get("@graph") or [doc]
        for n in nodes:
            if isinstance(n.get("schema:subjectOf"), dict) and self._is_catalog_record(n["schema:subjectOf"]):
                return n
        for n in nodes:
            if "schema:Dataset" in types(n) and not self._is_catalog_record(n):
                return n
        return nodes[0]

    def _dataset_types(self):
        t = types(self.root)
        out = ["dcat:Dataset"] if "schema:Dataset" in t or not t else ["dcat:Resource"]
        return out + [x for x in t if x != "schema:Dataset"]

    def new_blank(self, stem):
        return f"_:{stem}{next(self.blank)}"

    def apply(self, src, cls, out):
        """Write src's properties onto out by the rows for cls; keep the rest."""
        handled = set()
        for rule in RULES.get(cls, []):
            key = rule["subject_id"]
            if key not in src or key in handled:
                continue
            result = SHAPERS[rule["transform"]](self, src[key], rule, src)
            if result is None:
                continue                                  # no DCAT form: kept below
            handled.add(key)
            if isinstance(result, dict):
                for prop, vals in result.items():
                    add(out, prop, vals)
            else:
                add(out, rule["object_id"], result)
        for key, val in src.items():
            if key not in handled and key not in STRUCTURAL:
                add(out, key, val)
        return out

    def convert(self):
        self.apply(self.root, "schema:Dataset", self.dataset)
        self.graph.insert(0, self.dataset)
        if self.record:
            self.graph.insert(1, self.record)
        self.graph.extend(self.catalogs.values())
        ctx = dict(CONTEXT)
        src_ctx = self.doc.get("@context")
        for c in as_list(src_ctx):
            if isinstance(c, dict):
                for k, v in c.items():
                    if isinstance(v, str) and k not in ctx:
                        ctx[k] = v
        return {"@context": ctx, "@graph": [prune(n) for n in self.graph]}

    # --- shared node builders -------------------------------------------
    def agent(self, a):
        """schema:Person / Organization -> foaf:Agent; returns (node, role)."""
        if isinstance(a, str):
            return ({"@id": a} if is_iri(a) else {"@type": ["foaf:Agent"], "foaf:name": a}), None
        if not isinstance(a, dict):
            return None, None
        if "schema:Role" in types(a):
            inner = a.get("schema:contributor") or a.get("schema:creator") or a.get("schema:agent")
            node, _ = self.agent(inner[0] if isinstance(inner, list) else inner)
            return node, a.get("schema:roleName")
        if set(a) == {"@id"}:
            return {"@id": a["@id"]}, None
        t = types(a)
        cls = "schema:Person" if "schema:Person" in t else "schema:Organization"
        node = {"@type": ["foaf:Person" if cls == "schema:Person" else "foaf:Organization"]}
        ident = identifier_text(a.get("schema:identifier"))
        if is_iri(a.get("@id")):
            node["@id"] = a["@id"]
        elif ident and is_iri(ident):
            node["@id"] = ident
        src = {k: v for k, v in a.items() if k not in ("schema:identifier",)}
        if ident and node.get("@id") != ident:
            add(node, "dcterms:identifier", [ident])
        return self.apply(src, cls, node), None

    def catalog(self, c):
        key = (c.get("@id") or c.get("schema:url") or c.get("schema:name")) if isinstance(c, dict) else c
        if key not in self.catalogs:
            node = {"@id": c.get("@id") if isinstance(c, dict) and is_iri(c.get("@id")) else
                    (c if is_iri(c) else self.new_blank("catalog")), "@type": ["dcat:Catalog"]}
            if isinstance(c, dict):
                self.apply({k: v for k, v in c.items() if k != "@id"}, "schema:DataCatalog", node)
            self.catalogs[key] = node
        return self.catalogs[key]


def add(node, prop, values):
    vals = [v for v in as_list(values) if v not in (None, "", [], {})]
    if not vals:
        return
    have = as_list(node.get(prop))
    for v in vals:
        if v not in have:
            have.append(v)
    node[prop] = have[0] if len(have) == 1 else have


def prune(node):
    if isinstance(node, dict):
        return {k: prune(v) for k, v in node.items() if v not in (None, [], {}, "")}
    if isinstance(node, list):
        return [prune(v) for v in node]
    return node


def identifier_text(v):
    for x in as_list(v):
        if isinstance(x, str):
            return x
        if isinstance(x, dict):
            return x.get("schema:url") or x.get("@id") or x.get("schema:value")
    return None


def text_of(v):
    if isinstance(v, dict):
        return v.get("@value") or v.get("schema:name")
    return v if isinstance(v, (str, int, float)) else None


# --- shapers: (converter, value, rule, source node) -> values | {prop: values} | None
def tf_text(c, v, rule, src):
    return [t for t in (text_of(x) for x in as_list(v)) if t not in (None, "")] or None


def tf_copy(c, v, rule, src):
    return as_list(v)


def date_literal(d):
    d = str(d)
    if d.startswith(DATE_SENTINEL):
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return {"@value": d, "@type": "xsd:date"}
    if re.match(r"^\d{4}-\d{2}-\d{2}T", d):
        return {"@value": d, "@type": "xsd:dateTime"}
    if re.match(r"^\d{4}$", d):
        return {"@value": d, "@type": "xsd:gYear"}
    return d


def tf_date(c, v, rule, src):
    out = [date_literal(x) for x in as_list(v) if isinstance(x, str)]
    out = [x for x in out if x]
    return out or ([] if any(str(x).startswith(DATE_SENTINEL) for x in as_list(v)) else None)


def tf_iri(c, v, rule, src):
    out = []
    for x in as_list(v):
        url = x if isinstance(x, str) else (x.get("schema:url") or x.get("@id")) if isinstance(x, dict) else None
        if url == OGC_NIL:
            continue
        if not is_iri(url):
            return None
        out.append({"@id": url})
    return out


def tf_ref(c, v, rule, src):
    out = []
    for x in as_list(v):
        if isinstance(x, str) and is_iri(x):
            out.append({"@id": x})
        elif isinstance(x, dict) and is_iri(x.get("@id")) and len(x) == 1:
            out.append({"@id": x["@id"]})
        elif isinstance(x, dict) and is_iri(x.get("schema:url")):
            out.append({"@id": x["schema:url"]})
        else:
            out.append(x)
    return out


def tf_identifier(c, v, rule, src):
    ident, adms = [], []
    for x in as_list(v):
        if isinstance(x, str):
            ident.append(x)
        elif isinstance(x, dict):
            text = x.get("schema:url") or x.get("schema:value") or x.get("@id")
            if text:
                ident.append(text)
            if x.get("schema:value"):
                node = {"@type": ["adms:Identifier"], "skos:notation": x["schema:value"]}
                scheme = x.get("schema:propertyID")
                scheme = scheme.get("@id") if isinstance(scheme, dict) else scheme
                if scheme:
                    node["adms:schemaAgency"] = scheme
                adms.append(node)
    return {"dcterms:identifier": ident, "adms:identifier": adms}


def tf_otheridentifier(c, v, rule, src):
    adms, same = [], []
    for x in as_list(v):
        text = identifier_text(x)
        if not text:
            continue
        adms.append({"@type": ["adms:Identifier"], "skos:notation": text})
        if is_iri(text):
            same.append({"@id": text})
    return {"adms:identifier": adms, "owl:sameAs": same}


def concept_iri(term):
    for s in as_list(term.get("schema:sameAs")):
        if isinstance(s, dict) and is_iri(s.get("@id")):
            return s["@id"]
    for k in ("@id", "schema:url"):
        if is_iri(term.get(k)):
            return term[k]
    return None


def tf_keyword(c, v, rule, src):
    words, themes = [], []
    for x in as_list(v):
        if isinstance(x, str):
            words.append(x)
        elif isinstance(x, dict):
            name = text_of(x)
            iri = concept_iri(x)
            if iri:
                node = {"@id": iri, "@type": ["skos:Concept"]}
                if name:
                    node["skos:prefLabel"] = name
                scheme = x.get("schema:inDefinedTermSet")
                scheme = scheme.get("@id") if isinstance(scheme, dict) else scheme
                if is_iri(scheme):
                    node["skos:inScheme"] = {"@id": scheme}
                themes.append(node)
            elif name:
                words.append(name)
    return {"dcat:keyword": words, "dcat:theme": themes}


def labelled_doc(x, rdf_type):
    if isinstance(x, str):
        if x == OGC_NIL:
            return None
        return {"@id": x, "@type": [rdf_type]} if is_iri(x) else {"@type": [rdf_type], "rdfs:label": x}
    if isinstance(x, dict):
        url = x.get("schema:url") or (x.get("@id") if is_iri(x.get("@id")) else None)
        if url == OGC_NIL:
            return None
        node = {"@type": [rdf_type]}
        if is_iri(url):
            node["@id"] = url
        if x.get("schema:name"):
            node["rdfs:label"] = x["schema:name"]
        if x.get("schema:description"):
            node["rdfs:comment"] = x["schema:description"]
        same = [s["@id"] for s in as_list(x.get("schema:sameAs")) if isinstance(s, dict) and is_iri(s.get("@id"))]
        if same:
            node["owl:sameAs"] = [{"@id": s} for s in same]
        return node if len(node) > 1 else None
    return None


def tf_license(c, v, rule, src):
    return [d for d in (labelled_doc(x, "dcterms:LicenseDocument") for x in as_list(v)) if d] or []


def tf_rights(c, v, rule, src):
    return [d for d in (labelled_doc(x, "dcterms:RightsStatement") for x in as_list(v)) if d] or []


def tf_agent(c, v, rule, src):
    items = v.get("@list") if isinstance(v, dict) and "@list" in v else as_list(v)
    agents, quals = [], []
    for a in items:
        node, role = c.agent(a)
        if node is None:
            continue
        if role is not None:
            role_text = text_of(role) if not isinstance(role, str) else role
            quals.append({"@type": ["prov:Attribution"], "prov:agent": node,
                          "dcat:hadRole": {"@id": role_text} if is_iri(role_text) else
                          {"@type": ["dcat:Role"], "rdfs:label": role_text}})
        else:
            agents.append(node)
    prop = rule["object_id"]
    if isinstance(v, dict) and "@list" in v and len(agents) > 1:
        agents = [{"@list": agents}]
    return {prop: agents, "prov:qualifiedAttribution": quals}


def tf_contact(c, v, rule, src):
    out = []
    for a in as_list(v):
        if not isinstance(a, dict):
            continue
        kind = "vcard:Individual" if "schema:Person" in types(a) else "vcard:Organization"
        node = {"@type": [kind]}
        if a.get("schema:name"):
            node["vcard:fn"] = a["schema:name"]
        for cp in [a] + as_list(a.get("schema:contactPoint")):
            if isinstance(cp, dict):
                if cp.get("schema:email"):
                    node["vcard:hasEmail"] = {"@id": "mailto:" + str(cp["schema:email"]).removeprefix("mailto:")}
                if is_iri(cp.get("schema:url")):
                    node["vcard:hasURL"] = {"@id": cp["schema:url"]}
        out.append(node)
    return out or None


def wkt_box(box):
    s, w, n, e = box.replace(",", " ").split()[:4]
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def tf_place(c, v, rule, src):
    out = []
    for p in as_list(v):
        if isinstance(p, str):
            out.append({"@type": ["dcterms:Location"], "rdfs:label": p})
            continue
        if not isinstance(p, dict):
            continue
        node = {"@type": ["dcterms:Location"]}
        name = p.get("schema:name")
        if isinstance(name, dict):
            ident = name.get("schema:identifier")
            if ident:
                node["dcterms:identifier"] = ident
            name = name.get("schema:name")
        if name:
            node["rdfs:label"] = name
        if is_iri(p.get("@id")):
            node["@id"] = p["@id"]
        for g in as_list(p.get("schema:geo")):
            if not isinstance(g, dict):
                continue
            if isinstance(g.get("schema:box"), str):
                node["dcat:bbox"] = {"@value": wkt_box(g["schema:box"]), "@type": "gsp:wktLiteral"}
            elif "schema:latitude" in g and "schema:longitude" in g:
                node["dcat:centroid"] = {"@value": f"POINT({g['schema:longitude']} {g['schema:latitude']})",
                                         "@type": "gsp:wktLiteral"}
        out.append(node)
    return out


def tf_period(c, v, rule, src):
    out = []
    for t in as_list(v):
        start = end = None
        if isinstance(t, str) and "/" in t:
            start, end = t.split("/", 1)
        elif isinstance(t, dict):
            start = (t.get("time:hasBeginning") or {}).get("time:inXSDDate") or t.get("schema:startDate")
            end = (t.get("time:hasEnd") or {}).get("time:inXSDDate") or t.get("schema:endDate")
        node = {"@type": ["dcterms:PeriodOfTime"]}
        if start and start != "..":
            node["dcat:startDate"] = date_literal(start)
        if end and end != "..":
            node["dcat:endDate"] = date_literal(end)
        if len(node) == 1:
            node["rdfs:label"] = t if isinstance(t, str) else json.dumps(t)
        if isinstance(t, dict) and t.get("schema:description"):
            node["rdfs:comment"] = t["schema:description"]
        out.append(node)
    return out


def tf_type(c, v, rule, src):
    iris, keep = [], []
    for x in as_list(v):
        iri = x.get("@id") if isinstance(x, dict) and len(x) == 1 else x
        (iris if is_iri(iri) else keep).append({"@id": iri} if is_iri(iri) else x)
    return {"dcterms:type": iris, "schema:additionalType": keep}


def tf_relatedlink(c, v, rule, src):
    quals, rel = [], []
    for x in as_list(v):
        if not isinstance(x, dict):
            if is_iri(x):
                rel.append({"@id": x})
            continue
        target = x.get("schema:target") or x
        url = target.get("schema:url") if isinstance(target, dict) else target
        if not is_iri(url):
            continue
        role = x.get("schema:linkRelationship")
        role = text_of(role) if isinstance(role, dict) else role
        if role:
            quals.append({"@type": ["dcat:Relationship"], "dcterms:relation": {"@id": url},
                          "dcat:hadRole": {"@id": role} if is_iri(role) else {"@type": ["dcat:Role"], "rdfs:label": role}})
        else:
            rel.append({"@id": url})
    return {"dcat:qualifiedRelation": quals, "dcterms:relation": rel}


def tf_citation(c, v, rule, src):
    text, refs = [], []
    for x in as_list(v):
        if isinstance(x, str):
            (refs.append({"@id": x}) if is_iri(x) else text.append(x))
        elif isinstance(x, dict):
            url = x.get("schema:url") if is_iri(x.get("schema:url")) else identifier_text(x.get("schema:identifier"))
            if is_iri(url):
                refs.append({"@id": url})
            elif x.get("schema:name") or x.get("schema:citation"):
                text.append(x.get("schema:citation") or x.get("schema:name"))
    return {"dcterms:bibliographicCitation": text, "dcterms:isReferencedBy": refs}


def tf_distribution(c, v, rule, src):
    out = []
    for d in as_list(v):
        if not isinstance(d, dict):
            continue
        node = {"@type": ["dcat:Distribution"]}
        if is_iri(d.get("@id")):
            node["@id"] = d["@id"]
        if "schema:WebAPI" in types(d):
            service = {"@type": ["dcat:DataService"]}
            c.apply({k: v for k, v in d.items() if k != "@id"}, "schema:WebAPI", service)
            node["dcat:accessService"] = service
            if is_iri(d.get("schema:url")):
                node["dcat:accessURL"] = {"@id": d["schema:url"]}
        else:
            c.apply({k: v for k, v in d.items() if k != "@id"}, "schema:DataDownload", node)
        node["@type"] = ["dcat:Distribution"] + [t for t in types(d) if t not in ("schema:DataDownload", "schema:WebAPI")]
        out.append(node)
    return out


def tf_download(c, v, rule, src):
    urls = [{"@id": u} for u in as_list(v) if is_iri(u)]
    if not urls and all(u == OGC_NIL for u in as_list(v)):
        return []
    return {"dcat:downloadURL": urls, "dcat:accessURL": urls} if urls else None


def tf_mediatype(c, v, rule, src):
    media, fmt = [], []
    for x in as_list(v):
        if isinstance(x, str) and re.match(r"^[a-z]+/[A-Za-z0-9.+\-]+$", x.strip()):
            media.append({"@id": IANA + x.strip()})
        elif isinstance(x, str) and is_iri(x):
            fmt.append({"@id": x})
        elif isinstance(x, str):
            fmt.append({"@type": ["dcterms:MediaTypeOrExtent"], "rdfs:label": x})
    return {"dcat:mediaType": media, "dcterms:format": fmt}


def tf_bytes(c, v, rule, src):
    out = []
    for x in as_list(v):
        if isinstance(x, (int, float)) or (isinstance(x, str) and x.strip().isdigit()):
            out.append({"@value": str(int(float(x))), "@type": "xsd:nonNegativeInteger"})
        else:
            return None
    return out


def tf_mbox(c, v, rule, src):
    return [{"@id": "mailto:" + str(x).removeprefix("mailto:")} for x in as_list(v) if isinstance(x, str)]


def tf_record(c, v, rule, src):
    if not isinstance(v, dict) or not Converter._is_catalog_record(v):
        return None                                   # labelled links: kept as schema:subjectOf
    rid = v.get("@id")
    c.record = {"@id": rid if is_iri(rid) else "_:record", "@type": ["dcat:CatalogRecord"],
                "foaf:primaryTopic": {"@id": c.dataset["@id"]}}
    body = {k: x for k, x in v.items() if k not in ("schema:about", "@id", "@type")}
    extra = [t for t in as_list(body.pop("schema:additionalType", None))
             if (t.get("@id") if isinstance(t, dict) else t) != "dcat:CatalogRecord"]
    if extra:
        body["schema:additionalType"] = extra
    c.apply(body, "dcat:CatalogRecord", c.record)
    return {}


def tf_catalogmember(c, v, rule, src):
    for cat in as_list(v):
        add(c.catalog(cat), "dcat:dataset", [{"@id": c.dataset["@id"]}])
    return {}


def tf_catalogrecord(c, v, rule, src):
    for cat in as_list(v):
        node = c.catalog(cat)
        add(node, "dcat:record", [{"@id": c.record["@id"]}])
        add(node, "dcat:dataset", [{"@id": c.dataset["@id"]}])
    return {}


SHAPERS = {
    "text": tf_text, "copy": tf_copy, "date": tf_date, "iri": tf_iri, "ref": tf_ref,
    "identifier": tf_identifier, "otheridentifier": tf_otheridentifier, "keyword": tf_keyword,
    "license": tf_license, "rights": tf_rights, "agent": tf_agent, "contact": tf_contact,
    "place": tf_place, "period": tf_period, "type": tf_type, "relatedlink": tf_relatedlink,
    "citation": tf_citation, "distribution": tf_distribution, "download": tf_download,
    "mediatype": tf_mediatype, "bytes": tf_bytes, "mbox": tf_mbox, "record": tf_record,
    "catalogmember": tf_catalogmember, "catalogrecord": tf_catalogrecord,
}


def convert(doc):
    return Converter(doc).convert()


def check(dcat):
    """Parse the output as RDF and check the dataset and record are linked."""
    import rdflib
    g = rdflib.Graph().parse(data=json.dumps(dcat), format="json-ld")
    DCAT = rdflib.Namespace(CONTEXT["dcat"])
    FOAF = rdflib.Namespace(CONTEXT["foaf"])
    problems = []
    datasets = set(g.subjects(rdflib.RDF.type, DCAT.Dataset)) | set(g.subjects(rdflib.RDF.type, DCAT.Resource))
    if not datasets:
        problems.append("no dcat:Dataset or dcat:Resource")
    for rec in g.subjects(rdflib.RDF.type, DCAT.CatalogRecord):
        if g.value(rec, FOAF.primaryTopic) not in datasets:
            problems.append("dcat:CatalogRecord's foaf:primaryTopic is not the dataset")
    return len(g), problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="+", help="CDIF JSON-LD files or directories")
    ap.add_argument("-o", "--output", default=".", help="Output directory")
    ap.add_argument("--validate", action="store_true", help="Parse each output as RDF and check its structure")
    args = ap.parse_args()
    # (source file, its output path relative to --output); a directory is read
    # recursively and its subdirectories are mirrored in the output.
    files = []
    for p in map(Path, args.inputs):
        found = sorted(p.rglob("*.jsonld")) + sorted(p.rglob("*.json")) if p.is_dir() else [p]
        for f in found:
            if f.name != "INDEX.json":
                rel = f.relative_to(p).parent if p.is_dir() else Path()
                files.append((f, rel / (f.stem + ".dcat.jsonld")))
    out = Path(args.output)
    bad = 0
    for f, rel in files:
        dcat = convert(json.loads(f.read_text(encoding="utf-8")))
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(dcat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        if args.validate:
            n, problems = check(dcat)
            bad += bool(problems)
            print(f"{'FAIL' if problems else 'OK  '} {f.name} -> {target.name} ({n} triples)"
                  + ("".join(f"\n       {p}" for p in problems)))
        else:
            print(f"{f.name} -> {target.name}")
    print(f"{len(files)} converted" + (f"; {len(files) - bad} pass the structure check" if args.validate else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

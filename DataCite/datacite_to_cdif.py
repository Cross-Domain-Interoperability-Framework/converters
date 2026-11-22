#!/usr/bin/env python3
"""
datacite_to_cdif.py - Convert DataCite Metadata Schema XML to CDIF JSON-LD.

Reads DataCite kernel-3 and kernel-4 <resource> records, alone or inside an
OAI-PMH response (oai_datacite or a repository's own DataCite prefix); each
resource becomes one CDIF record.

The table mappings/datacite-to-cdif.sssom.tsv drives the conversion: each row
names DataCite elements by an XPath from the <resource> element
(subject_xpath; the datacite: prefix is bound to the record's own kernel
namespace), the CDIF location it lands at (object_json_path) and the shaper
that builds the value (transform). Row order is precedence for a scalar target;
array targets accumulate.

Usage:
    python DataCite/datacite_to_cdif.py record.xml -o out/
    python DataCite/datacite_to_cdif.py oai-response.xml -o out/ --catalog-name PANGAEA --catalog-url https://www.pangaea.de/
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(ROOT / "validation"))
try:
    from detect_conformance import detect_cdif_conformance, apply_conformance
    _HAVE_DETECT = True
except Exception:
    _HAVE_DETECT = False

TABLE = ROOT / "mappings" / "datacite-to-cdif.sssom.tsv"
KERNEL = "http://datacite.org/schema/kernel-"
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
VOCAB = "https://datacite-metadata-schema.readthedocs.io/en/4.6/appendices/appendix-1/%s/"

CDIF_CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
}
ARRAY_TARGETS = {
    "schema:alternateName", "schema:sameAs", "schema:contributor", "schema:provider",
    "schema:funding", "schema:temporalCoverage", "schema:keywords", "schema:additionalType",
    "schema:license", "schema:conditionsOfAccess", "schema:spatialCoverage",
    "schema:relatedLink", "prov:wasDerivedFrom", "schema:encodingFormat",
}

# identifier type -> (identifiers.org registry prefix, resolver)
RESOLVERS = {
    "doi": ("doi", "https://doi.org/"),
    "handle": ("hdl", "https://hdl.handle.net/"),
    "ark": ("ark", "https://n2t.net/"),
    "pmid": ("pubmed", "https://pubmed.ncbi.nlm.nih.gov/"),
    "orcid": ("orcid", "https://orcid.org/"),
    "ror": ("ror", "https://ror.org/"),
    "isni": ("isni", "https://isni.org/isni/"),
    "crossref funder id": ("crossref.funder", "https://doi.org/10.13039/"),
    "issn": ("issn", "https://portal.issn.org/resource/ISSN/"),
}
# how a resolvable identifier may already be written as a URL
URL_FORMS = {
    "doi": re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", re.I),
    "handle": re.compile(r"^(?:https?://hdl\.handle\.net/|hdl:)", re.I),
    "orcid": re.compile(r"^https?://orcid\.org/", re.I),
    "ror": re.compile(r"^https?://ror\.org/", re.I),
    "crossref funder id": re.compile(r"^https?://(?:dx\.)?doi\.org/10\.13039/", re.I),
}


# ---------------------------------------------------------------------------
# element helpers
# ---------------------------------------------------------------------------

def ln(el):
    return etree.QName(el).localname


def text(el):
    if el is None:
        return ""
    if isinstance(el, str):
        return " ".join(el.split())
    return " ".join("".join(el.itertext()).split())


def child(el, name):
    """The first child named name, ignoring case (some records write contributorname)."""
    name = name.lower()
    for c in el:
        if isinstance(c.tag, str) and ln(c).lower() == name:
            return c
    return None


def children(el, name):
    name = name.lower()
    return [c for c in el if isinstance(c.tag, str) and ln(c).lower() == name]


def attr(el, name):
    """An attribute by local name (DataCite attributes are unqualified; xml:lang is not used)."""
    for k, v in el.attrib.items():
        if etree.QName(k).localname == name:
            return v.strip()
    return ""


# ---------------------------------------------------------------------------
# identifiers
# ---------------------------------------------------------------------------

def bare(value, kind):
    """The identifier without a resolver prefix it may have been written with."""
    form = URL_FORMS.get(kind.lower())
    return form.sub("", value) if form else value


def resolve(value, kind):
    """A URL for this identifier, or None."""
    value = value.strip()
    k = kind.lower()
    if re.match(r"^https?://", value) and k not in ("doi", "handle"):
        return value
    if k in ("url", "purl") and re.match(r"^https?://", value):
        return value
    if k in RESOLVERS:
        return RESOLVERS[k][1] + bare(value, k)
    return None


def property_value(value, kind):
    k = kind.lower()
    pv = {"@type": ["schema:PropertyValue"]}
    if k in RESOLVERS:
        pv["schema:propertyID"] = {"@id": "https://registry.identifiers.org/registry/" + RESOLVERS[k][0]}
    elif kind:
        pv["schema:propertyID"] = kind
    pv["schema:value"] = bare(value, k)
    url = resolve(value, kind)
    if url:
        pv["schema:url"] = url
    return pv


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------

def _organization(name, idents=()):
    org = {"@type": ["schema:Organization"], "schema:name": name}
    if idents:
        org["schema:identifier"] = idents[0]
        if len(idents) > 1:
            org["schema:sameAs"] = [i.get("schema:url") or i["schema:value"] for i in idents[1:]]
    return org


def _name_identifiers(el, tag):
    out, email = [], None
    for ni in children(el, tag):
        value = text(ni)
        scheme = attr(ni, "nameIdentifierScheme") or attr(ni, "affiliationIdentifierScheme") \
            or attr(ni, "publisherIdentifierScheme")
        if not value:
            continue
        if scheme.lower() in ("e-mail", "email") or (tag == "nameIdentifier" and "@" in value
                                                     and not value.startswith("http")):
            email = value
            continue
        out.append(property_value(value, scheme))
    return out, email


def agent(el, name_tag):
    """A creator or contributor element -> Person or Organization."""
    name_el = child(el, name_tag)
    name = text(name_el)
    given, family = text(child(el, "givenName")), text(child(el, "familyName"))
    if not name:
        name = ", ".join(p for p in (family, given) if p)
    if not name:
        return None
    idents, email = _name_identifiers(el, "nameIdentifier")
    name_type = attr(name_el, "nameType") if name_el is not None else ""
    schemes = {i.get("schema:propertyID", {}).get("@id", "").rsplit("/", 1)[-1]
               for i in idents if isinstance(i.get("schema:propertyID"), dict)}
    if name_type == "Personal":
        person = True
    elif name_type == "Organizational":
        person = False
    else:
        person = bool(given or family or "orcid" in schemes) or \
            ("," in name and "ror" not in schemes)
    if not person:
        org = _organization(name, idents)
        if email:
            org["schema:email"] = email
        return org
    p = {"@type": ["schema:Person"], "schema:name": name}
    if given:
        p["schema:givenName"] = given
    if family:
        p["schema:familyName"] = family
    if idents:
        p["schema:identifier"] = idents[0]
        if len(idents) > 1:
            p["schema:sameAs"] = [i.get("schema:url") or i["schema:value"] for i in idents[1:]]
    if email:
        p["schema:contactPoint"] = {"@type": ["schema:ContactPoint"], "schema:email": email}
    affs = children(el, "affiliation")
    if affs:                               # CDIF's Person has one affiliation
        aff = affs[0]
        aname = text(aff)
        aid = attr(aff, "affiliationIdentifier")
        ascheme = attr(aff, "affiliationIdentifierScheme")
        if aname or aid:
            p["schema:affiliation"] = _organization(
                aname or aid, [property_value(aid, ascheme)] if aid else [])
    return p


# ---------------------------------------------------------------------------
# value shapers: tf_<transform>(values, ctx) -> value or list
# ---------------------------------------------------------------------------

def _identifier_of(values):
    el = values[0]
    return text(el), attr(el, "identifierType") or "DOI"


def tf_iri(values, ctx):
    value, kind = _identifier_of(values)
    if not value:
        return None
    url = resolve(value, kind)
    if url:
        return url
    return "urn:%s:%s" % (re.sub(r"[^a-z0-9]+", "-", kind.lower()), value)


def tf_identifier(values, ctx):
    value, kind = _identifier_of(values)
    return property_value(value, kind) if value else None


def tf_url(values, ctx):
    value, kind = _identifier_of(values)
    return resolve(value, kind) if value else None


def tf_altidentifier(values, ctx):
    return [property_value(text(v), attr(v, "alternateIdentifierType"))
            for v in values if text(v)]


def tf_text(values, ctx):
    for v in values:
        t = text(v)
        if t:
            return t
    return None


def tf_texts(values, ctx):
    out = []
    for v in values:
        t = text(v)
        if t and t not in out:
            out.append(t)
    return out


def tf_creators(values, ctx):
    people = [a for a in (agent(v, "creatorName") for v in values) if a]
    return {"@list": people} if people else None


def tf_publisher(values, ctx):
    el = values[0]
    name = text(el)
    if not name:
        return None
    pid = attr(el, "publisherIdentifier")
    return _organization(name, [property_value(pid, attr(el, "publisherIdentifierScheme"))]
                         if pid else [])


def tf_agent(values, ctx):
    return [a for a in (agent(v, "contributorName") for v in values) if a]


def tf_contributor(values, ctx):
    out = []
    for v in values:
        a = agent(v, "contributorName")
        if not a:
            continue
        role = {"@type": ["schema:Role"], "schema:contributor": a}
        kind = attr(v, "contributorType")
        if kind:
            role["schema:roleName"] = {"@type": ["schema:DefinedTerm"], "schema:name": kind,
                                       "schema:inDefinedTermSet": VOCAB % "contributorType"}
        out.append(role)
    return out


def tf_funder(values, ctx):
    return [{"@type": ["schema:MonetaryGrant"], "schema:funder": a}
            for a in (agent(v, "contributorName") for v in values) if a]


def tf_grant(values, ctx):
    out = []
    for v in values:
        grant = {"@type": ["schema:MonetaryGrant"]}
        fname = text(child(v, "funderName"))
        fid_el = child(v, "funderIdentifier")
        fid = text(fid_el)
        if fname or fid:
            ftype = attr(fid_el, "funderIdentifierType") if fid_el is not None else ""
            grant["schema:funder"] = _organization(fname or fid,
                                                   [property_value(fid, ftype)] if fid else [])
        award_el = child(v, "awardNumber")
        award = text(award_el)
        if award:
            pv = {"@type": ["schema:PropertyValue"], "schema:propertyID": "award number",
                  "schema:value": award}
            uri = attr(award_el, "awardURI")
            if re.match(r"^https?://", uri):
                pv["schema:url"] = uri
            grant["schema:identifier"] = pv
        title = text(child(v, "awardTitle"))
        if title:
            grant["schema:name"] = title
        if len(grant) > 1:
            out.append(grant)
    return out


_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?)?)?$")


def _date(value):
    """The start of a DataCite date or range, in the CDIF pattern (at least YYYY-MM)."""
    value = text(value).split("/")[0].strip()
    if not _DATE.match(value):
        return None
    if len(value) == 4:
        return None                        # a bare year is not a CDIF date; see tf_year
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?)$", value)
    if m:                                  # a date-time with no zone is taken as UTC
        return "%sT%sZ" % m.groups()
    return value


def tf_date(values, ctx):
    for v in values:
        d = _date(v)
        if d:
            return d
    return None


def tf_lastdate(values, ctx):
    dates = sorted(d for d in (_date(v) for v in values) if d)
    return dates[-1] if dates else None


def tf_year(values, ctx):
    y = text(values[0])
    return y + "-01-01" if re.match(r"^\d{4}$", y) else None


def tf_interval(values, ctx):
    return [t for t in (text(v) for v in values) if t]


def tf_subject(values, ctx):
    out = []
    for v in values:
        name = text(v)
        if not name:
            continue
        scheme = attr(v, "schemeURI") or attr(v, "subjectScheme")
        uri, code = attr(v, "valueURI"), attr(v, "classificationCode")
        if not (scheme or uri or code):
            if name not in out:
                out.append(name)
            continue
        term = {"@type": ["schema:DefinedTerm"], "schema:name": name}
        if uri:
            term["@id"] = uri
            term["schema:url"] = uri
        if code:
            term["schema:termCode"] = code
        if scheme:
            term["schema:inDefinedTermSet"] = scheme
        same = next((t for t in out if isinstance(t, dict) and uri and t.get("@id") == uri), None)
        if same is None:
            out.append(term)
        elif name != same["schema:name"]:  # the same concept in another language
            alt = same.setdefault("schema:alternateName", [])
            if name not in alt:
                alt.append(name)
        elif term not in out:
            out.append(term)
    return out


def tf_resourcetype(values, ctx):
    el = values[0]
    out = []
    general = attr(el, "resourceTypeGeneral")
    if general:
        out.append({"@type": ["schema:DefinedTerm"], "schema:name": general,
                    "schema:inDefinedTermSet": VOCAB % "resourceTypeGeneral"})
    specific = text(el)
    if specific and specific != general:
        out.append(specific)
    return out


def tf_license(values, ctx):
    out = []
    for v in values:
        uri, label = attr(v, "rightsURI"), text(v) or attr(v, "rightsIdentifier")
        if re.match(r"^https?://", uri):
            work = {"@type": ["schema:CreativeWork"], "schema:url": uri}
            if label and label != uri:
                work["schema:name"] = label
            out.append(work)
        elif label:
            out.append(label)
    return out


def tf_accessrights(values, ctx):
    return [attr(v, "rightsURI") for v in values]


_DESCRIPTION_LABELS = {"Methods": "Methods", "TechnicalInfo": "Technical information",
                       "SeriesInformation": "Series information",
                       "TableOfContents": "Table of contents", "Other": "Other"}


def tf_description(values, ctx):
    abstracts, others = [], []
    for v in values:
        t = text(v)
        if not t:
            continue
        kind = attr(v, "descriptionType") or "Abstract"
        if kind == "Abstract":
            abstracts.append(t)
        else:
            others.append("%s: %s" % (_DESCRIPTION_LABELS.get(kind, kind), t))
    parts = abstracts + others
    return "\n\n".join(parts) if parts else None


def _number(t):
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


def _box(el):
    if el is None:
        return None
    if len(el):                            # kernel 4: bound elements
        s, w, n, e = (_number(text(child(el, k))) for k in (
            "southBoundLatitude", "westBoundLongitude", "northBoundLatitude", "eastBoundLongitude"))
    else:                                  # kernel 3: 'S W N E'
        nums = [_number(x) for x in text(el).split()]
        s, w, n, e = nums if len(nums) == 4 else (None,) * 4
    if None in (s, w, n, e):
        return None
    return {"@type": ["schema:GeoShape"], "schema:box": "%s %s %s %s" % tuple(
        ("%g" % x) for x in (s, w, n, e))}


def _point(el):
    if el is None:
        return None
    if len(el):
        lat, lon = _number(text(child(el, "pointLatitude"))), _number(text(child(el, "pointLongitude")))
    else:                                  # kernel 3: 'lat lon'
        nums = [_number(x) for x in text(el).split()]
        lat, lon = nums if len(nums) == 2 else (None, None)
    if lat is None or lon is None:
        return None
    return {"@type": ["schema:GeoCoordinates"], "schema:latitude": lat, "schema:longitude": lon}


def _polygon(el):
    if el is None:
        return None
    pts = []
    if len(el):
        for p in children(el, "polygonPoint"):
            lat = _number(text(child(p, "pointLatitude")))
            lon = _number(text(child(p, "pointLongitude")))
            if lat is not None and lon is not None:
                pts.append("%g %g" % (lat, lon))
    else:                                  # kernel 3: 'lat lon lat lon ...'
        nums = [_number(x) for x in text(el).split()]
        if None not in nums and len(nums) % 2 == 0:
            pts = ["%g %g" % (nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]
    return {"@type": ["schema:GeoShape"], "schema:polygon": " ".join(pts)} if len(pts) >= 4 else None


def tf_place(values, ctx):
    out = []
    for v in values:
        place = {"@type": ["schema:Place"]}
        name = text(child(v, "geoLocationPlace"))
        if name:
            place["schema:name"] = name
        geo = _box(child(v, "geoLocationBox")) or _polygon(child(v, "geoLocationPolygon")) \
            or _point(child(v, "geoLocationPoint"))
        if geo:
            place["schema:geo"] = geo
        if len(place) > 1:
            out.append(place)
    return out


def tf_related(values, ctx):
    out = []
    for v in values:
        value, kind = text(v), attr(v, "relatedIdentifierType")
        if not value:
            continue
        url = resolve(value, kind)
        base = attr(v, "schemeURI")
        if not url and kind.lower() == "url" and value.startswith("/") and                 re.match(r"^https?://", base):     # a path on the schemeURI's site
            url = base.rstrip("/") + value
        if not url:                        # an EntryPoint needs a URL
            ctx.setdefault("dropped", []).append("relatedIdentifier %s %s" % (kind, value))
            continue
        target = {"@type": ["schema:EntryPoint"], "schema:url": url}
        link = {"@type": ["schema:LinkRole"], "schema:target": target,
                "schema:linkRelationship": {"@type": ["schema:DefinedTerm"],
                                            "schema:name": attr(v, "relationType") or "missing",
                                            "schema:inDefinedTermSet": VOCAB % "relationType"}}
        out.append(link)
    return out


def tf_source(values, ctx):
    out = []
    for v in values:
        value, kind = text(v), attr(v, "relatedIdentifierType")
        url = resolve(value, kind)
        out.append({"@id": url} if url else value)
    return [o for o in out if o]


TRANSFORMS = {name[3:]: fn for name, fn in globals().items()
              if name.startswith("tf_") and callable(fn)}


# ---------------------------------------------------------------------------
# table application
# ---------------------------------------------------------------------------

def load_table(path=TABLE):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)]


def _place_value(container, target, value):
    if value in (None, "", [], {}):
        return False
    if target in ARRAY_TARGETS:
        existing = container.setdefault(target, [])
        for item in value if isinstance(value, list) else [value]:
            if item not in existing:
                existing.append(item)
        return True
    if target in container:
        return False
    container[target] = value[0] if isinstance(value, list) else value
    return True


def apply_table(rows, resource, doc, record, ctx):
    ns = {"datacite": etree.QName(resource).namespace}
    changes = []
    for rule in rows:
        shaper = TRANSFORMS.get(rule.get("transform", ""))
        path = (rule.get("object_json_path") or "").strip()
        xpath = (rule.get("subject_xpath") or "").strip()
        if not shaper or not xpath or not path.startswith("$."):
            continue
        parts = path[2:].split(".")
        if parts[0] == "schema:subjectOf" and len(parts) == 2:
            container, target = record, parts[1]
        elif len(parts) == 1:
            container, target = doc, parts[0]
        else:
            continue
        try:
            values = resource.xpath(xpath, namespaces=ns)
        except etree.XPathError as exc:
            raise ValueError("bad subject_xpath %r: %s" % (xpath, exc))
        if not isinstance(values, list):
            values = [values] if values not in (None, "") else []
        if not values:
            continue
        ctx["rule"] = rule
        if _place_value(container, target, shaper(values, ctx)):
            changes.append("%s -> %s" % (rule["subject_id"], rule["object_id"]))
    return changes


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------

def resources(root):
    """Every DataCite <resource> in the document: the root itself, or those in an OAI-PMH response."""
    return root.xpath("descendant-or-self::*[local-name()='resource' and "
                      "starts-with(namespace-uri(), '%s')]" % KERNEL)


def kernel_of(resource):
    return etree.QName(resource).namespace[len(KERNEL):]


def convert_datacite_to_cdif(resource, rows=None, detect=True, catalog=None):
    """One DataCite <resource> element -> (CDIF JSON-LD dict, list of changes)."""
    rows = rows if rows is not None else load_table()
    ctx = {}
    doc = {"@context": dict(CDIF_CONTEXT)}
    record = {}
    changes = apply_table(rows, resource, doc, record, ctx)
    changes += ["dropped (no URL): %s" % d for d in ctx.get("dropped", [])]

    # --- what CDIF requires and DataCite may not supply ---------------------------
    if "@id" not in doc:
        doc["@id"] = "urn:sha1:%s" % hashlib.sha1(etree.tostring(resource)).hexdigest()
        changes.append("@id made up (the record has no identifier)")
    rid = doc["@id"]
    doc = {"@context": doc.pop("@context"), "@id": doc.pop("@id"),
           "@type": ["schema:Dataset"], **doc}
    if not doc.get("schema:name"):
        doc["schema:name"] = rid
        changes.append("schema:name from the identifier")
    if not doc.get("schema:identifier"):
        doc["schema:identifier"] = rid
    if not doc.get("schema:description"):
        doc["schema:description"] = doc["schema:name"]
        changes.append("schema:description from the name")
    if not doc.get("schema:dateModified"):
        doc["schema:dateModified"] = "1900-01-01"
        changes.append("schema:dateModified sentinel (no date or publicationYear)")
    if not doc.get("schema:url"):
        doc["schema:url"] = NIL
    if not doc.get("schema:license") and not doc.get("schema:conditionsOfAccess"):
        doc["schema:license"] = [{"@id": NIL}]
        changes.append("schema:license set to the OGC nil URI (no rights statement)")

    # --- the catalog record --------------------------------------------------------
    subject = {
        "@id": rid + "#metadata",
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:name": "DataCite metadata record for: %s" % doc["schema:name"][:120],
        "schema:about": {"@id": rid},
        "dcterms:conformsTo": [{"@id": "https://w3id.org/cdif/core/2.0"}],
        "schema:description": "Converted from DataCite Metadata Schema kernel-%s XML to CDIF "
                              "by datacite_to_cdif.py." % kernel_of(resource),
    }
    subject.update(record)
    if catalog:
        subject["schema:includedInDataCatalog"] = catalog
    doc["schema:subjectOf"] = subject

    if detect and _HAVE_DETECT:
        try:
            uris = detect_cdif_conformance(doc)
        except Exception as exc:
            print("  WARNING: detect_cdif_conformance failed (%s)" % exc, file=sys.stderr)
        else:
            if uris:
                apply_conformance(doc, uris)
    return doc, changes


def load_xml(source):
    if re.match(r"^https?://", source):
        import urllib.request
        req = urllib.request.Request(source, headers={"User-Agent": "cdif-converters/0.1"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return etree.fromstring(r.read(), PARSER)
    return etree.parse(source, PARSER).getroot()


def _slug(text_):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text_ or "").strip("_")[:100]


def main():
    ap = argparse.ArgumentParser(description="Convert DataCite Metadata Schema XML to CDIF JSON-LD")
    ap.add_argument("input", nargs="+", help="DataCite XML file(s) or URL(s), or OAI-PMH responses")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--catalog-name", help="name of the repository the records came from")
    ap.add_argument("--catalog-url", help="URL of that repository")
    ap.add_argument("--static-conformance", action="store_true",
                    help="declare core only instead of detecting conformance")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    catalog = None
    if args.catalog_name or args.catalog_url:
        catalog = {"@type": ["schema:DataCatalog"]}
        if args.catalog_name:
            catalog["schema:name"] = args.catalog_name
        if args.catalog_url:
            catalog["schema:url"] = args.catalog_url
    rows = load_table()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    for source in args.input:
        found = resources(load_xml(source))
        if not found:
            print("%s: no DataCite resource" % source, file=sys.stderr)
            continue
        for i, res in enumerate(found):
            doc, changes = convert_datacite_to_cdif(res, rows, not args.static_conformance, catalog)
            stem = _slug(doc["@id"].split("://", 1)[-1]) if len(found) > 1 or \
                source.startswith("http") else Path(source).stem
            out = out_dir / ("%s.jsonld" % stem)
            out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8", newline="\n")
            declared = [c["@id"].rsplit("/", 2)[-2] for c in
                        doc["schema:subjectOf"].get("dcterms:conformsTo", [])]
            line = "%-50s %s" % (out.name, "+".join(declared) or "no conformance")
            if args.verbose:
                line += "\n      " + "\n      ".join(changes)
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

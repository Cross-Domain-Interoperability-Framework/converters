#!/usr/bin/env python3
"""
dc_to_cdif.py - Convert Dublin Core records to CDIF JSON-LD.

Reads the two Dublin Core encodings metadata services publish:

  * csw:Record (OGC CSW 2.0.2, outputSchema csw:Record), as GeoNetwork and
    other catalogues serve it: dc: and dct: elements, an ows:BoundingBox, and
    dc:URI links with a protocol;
  * oai_dc:dc (OAI-PMH metadataPrefix oai_dc), the simple Dublin Core every
    OAI-PMH repository serves.

A record can be on its own or inside a CSW GetRecords / GetRecordById response
or an OAI-PMH ListRecords / GetRecord response; each record becomes one CDIF
record.

The table mappings/dc-to-cdif.sssom.tsv drives the conversion: each row names
Dublin Core elements by an XPath from the record element (subject_xpath, with
the dc:, dct: and ows: prefixes), the CDIF location it lands at
(object_json_path) and the shaper that builds the value (transform). Row order
is precedence for a scalar target; array targets accumulate.

Usage:
    python DublinCore/dc_to_cdif.py record.xml -o out/
    python DublinCore/dc_to_cdif.py getrecords-response.xml -o out/ --catalog-name "Swiss geocat" --catalog-url https://www.geocat.ch/
    python DublinCore/dc_to_cdif.py oai_dc.xml -o out/ --identifier oai:example.org:123
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

TABLE = ROOT / "mappings" / "dc-to-cdif.sssom.tsv"
NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "dct": "http://purl.org/dc/terms/",
    "ows": "http://www.opengis.net/ows",
    "csw": "http://www.opengis.net/cat/csw/2.0.2",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
}
RECORD_TAGS = {"{%s}Record" % NS["csw"]: "csw:Record", "{%s}dc" % NS["oai_dc"]: "oai_dc"}
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
REGISTRY = "https://registry.identifiers.org/registry/"

CDIF_CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
}
ARRAY_TARGETS = {
    "schema:alternateName", "schema:sameAs", "schema:contributor", "schema:keywords",
    "schema:additionalType", "schema:encodingFormat", "schema:license",
    "schema:conditionsOfAccess", "schema:temporalCoverage", "schema:spatialCoverage",
    "schema:relatedLink", "prov:wasDerivedFrom", "schema:distribution",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def text(el):
    if el is None:
        return ""
    if isinstance(el, str):
        return " ".join(el.split())
    return " ".join("".join(el.itertext()).split())


def _http(t):
    return bool(re.match(r"^https?://", t or ""))


_LABEL = re.compile(r"^([A-Z][A-Za-z]+):\s*(.*)$")   # 'Updated: 2024-01-05T...'


def unlabelled(t):
    """The value without a 'Word:' label, and the label (or '')."""
    m = _LABEL.match(t)
    if m and not _http(t) and not re.match(r"^(?:urn|oai|doi|hdl|ark|info):", t, re.I):
        return m.group(2), m.group(1)
    return t, ""


# ---------------------------------------------------------------------------
# identifiers
# ---------------------------------------------------------------------------

_DOI = re.compile(r"(?:^|doi\.org/|doi:\s*)(10\.\d{4,9}/[^\s<>\"]+)", re.I)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def doi_of(t):
    m = _DOI.search(t or "")
    return m.group(1).rstrip(".,;") if m else None


def resolve(t):
    """A resolver URL for an identifier, or None."""
    doi = doi_of(t)
    if doi:
        return "https://doi.org/" + doi
    if _http(t):
        return t
    m = re.match(r"^hdl:\s*(\S+)$", t, re.I)
    if m:
        return "https://hdl.handle.net/" + m.group(1)
    m = re.match(r"^(?:ark:)?(ark:/\S+)$", t, re.I)
    if m:
        return "https://n2t.net/" + m.group(1)
    return None


def _rank(t):
    """Which identifier to prefer for @id: DOI, URL, Handle/ARK, UUID, URI, other."""
    if doi_of(t):
        return 0
    if _http(t):
        return 1
    if resolve(t):
        return 2
    if _UUID.match(t):
        return 3
    if re.match(r"^(?:urn|oai|info):\S+$", t, re.I):
        return 4
    return 5


def _identifiers(values):
    out = []
    for v in values:
        t = text(v)
        if t and " " not in t.strip() and t not in out:   # a citation is not an identifier
            out.append(t)
    return out


def chosen(values, ctx):
    if "chosen" not in ctx:
        ids = _identifiers(values)
        ctx["chosen"] = min(ids, key=_rank) if ids else None
    return ctx["chosen"]


def iri_of(t):
    url = resolve(t)
    if url:
        return url
    if _UUID.match(t):
        return "urn:uuid:" + t.lower()
    if re.match(r"^(?:urn|oai|info):\S+$", t, re.I):
        return t
    return "urn:dc:" + re.sub(r"\s+", "", t)


def property_value(t):
    pv = {"@type": ["schema:PropertyValue"]}
    doi = doi_of(t)
    if doi:
        pv["schema:propertyID"] = {"@id": REGISTRY + "doi"}
        pv["schema:value"] = doi
    elif re.match(r"^hdl:", t, re.I) or "hdl.handle.net/" in t:
        pv["schema:propertyID"] = {"@id": REGISTRY + "hdl"}
        pv["schema:value"] = re.sub(r"^(?:hdl:\s*|https?://hdl\.handle\.net/)", "", t, flags=re.I)
    elif re.match(r"^(?:ark:)?ark:/", t, re.I):
        pv["schema:propertyID"] = {"@id": REGISTRY + "ark"}
        pv["schema:value"] = re.sub(r"^ark:(?=ark:)", "", t, flags=re.I)
    else:
        pv["schema:propertyID"] = "dc:identifier"
        pv["schema:value"] = t
    url = resolve(t)
    if url:
        pv["schema:url"] = url
    return pv


# ---------------------------------------------------------------------------
# agents (the FGDC converter's rule: Dublin Core, like CSDGM, gives only a name)
# ---------------------------------------------------------------------------

_ORG_WORDS = re.compile(
    r"\b(?:survey|county|district|commission|center|centre|service|university|universit|"
    r"department|institute|institut|agency|office|laboratory|lab|program|programme|division|"
    r"association|society|council|bureau|foundation|museum|network|project|team|group|inc|"
    r"ltd|corporation|company|administration|authority|ministry|state|national|federal|"
    r"states|city|board|library|libraries|statistics|repository|archive|consortium|cern)\b",
    re.I)
_FAMILY_FIRST = re.compile(r"^[^\W\d][\w'\-]+(?: [^\W\d][\w'\-]+)?, (?:[A-Z]\.\s?)+$|"
                           r"^[^\W\d][\w'\-]+(?: [^\W\d][\w'\-]+)?, [^\W\d][\w'\-]+(?: [A-Z]\.?)*$")
_GIVEN_FIRST = re.compile(r"^(?:(?:[A-Z]\.)+|[A-Z][a-z]+)(?: (?:[A-Z]\.)+| [A-Z][a-z]+)*"
                          r" [A-Z][a-z][\w'\-]+$")
_ORCID = re.compile(r"<?\s*(https?://orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX]))\s*>?")


def name_agent(raw):
    name = text(raw)
    if not name:
        return None
    orcid = _ORCID.search(name)
    if orcid:
        name = " ".join(_ORCID.sub("", name).split())
    if not name:
        return None
    person = bool(orcid) or (not _ORG_WORDS.search(name) and (
        bool(_FAMILY_FIRST.match(name)) or
        (bool(_GIVEN_FIRST.match(name)) and 2 <= len(name.split()) <= 4)))
    node = {"@type": ["schema:Person" if person else "schema:Organization"], "schema:name": name}
    if orcid:
        node["schema:identifier"] = {"@type": ["schema:PropertyValue"],
                                     "schema:propertyID": {"@id": REGISTRY + "orcid"},
                                     "schema:value": orcid.group(2), "schema:url": orcid.group(1)}
    return node


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])}


def iso_date(t):
    """A date in ISO 8601 (YYYY, YYYY-MM, YYYY-MM-DD, or a date-time with a zone), or None."""
    t = text(t)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{1,2}):(\d{2})(?::(\d{2})(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$", t)
    if m:
        d, h, mi, s, frac, zone = m.groups()
        return "%sT%02d:%s:%s%s%s" % (d, int(h), mi, s or "00", frac or "", zone or "Z")
    if re.match(r"^\d{4}(-\d{2}(-\d{2})?)?$", t):
        return t
    m = re.match(r"^(\d{1,2}) ([A-Za-z]+) (\d{4})$", t)          # '01 January 1922'
    if m and m.group(2).lower() in _MONTHS:
        return "%s-%02d-%02d" % (m.group(3), _MONTHS[m.group(2).lower()], int(m.group(1)))
    return None


def _dates(values, labelled=False):
    out = []
    for v in values:
        value, label = unlabelled(text(v))
        if label and not labelled:
            continue                       # a labelled date has its own row
        d = iso_date(value)
        if d:
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# value shapers: tf_<transform>(values, ctx) -> value or list
# ---------------------------------------------------------------------------

def tf_iri(values, ctx):
    t = chosen(values, ctx)
    return iri_of(t) if t else None


def tf_identifier(values, ctx):
    t = chosen(values, ctx)
    return property_value(t) if t else None


def tf_otheridentifiers(values, ctx):
    keep = chosen(values, ctx)
    out = []
    for v in values:
        t = text(v)
        if not t or t == keep:
            continue
        item = resolve(t) if " " not in t else None
        item = item or t
        if item not in out:
            out.append(item)
    return out


def tf_landing(values, ctx):
    ids = _identifiers(values)
    for t in ids:
        if _http(t) and not doi_of(t):
            return t
    for t in ids:
        if doi_of(t):
            return "https://doi.org/" + doi_of(t)
    return None


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
    people = [a for a in (name_agent(v) for v in values) if a]
    return {"@list": people} if people else None


def tf_agents(values, ctx):
    out = []
    for v in values:
        a = name_agent(v)
        if a and a not in out:
            out.append(a)
    return out


def tf_organization(values, ctx):
    t = tf_text(values, ctx)
    return {"@type": ["schema:Organization"], "schema:name": t} if t else None


def tf_date(values, ctx):
    d = _dates(values, labelled=True)
    return d[0] if d else None


def tf_fulldate(values, ctx):
    d = [x for x in _dates(values, labelled=True) if len(x) >= 7]
    return d[0] if d else None


def tf_lastdate(values, ctx):
    d = sorted(x for x in _dates(values) if len(x) >= 7)
    return d[-1] if d else None


def tf_firstdate(values, ctx):
    d = sorted(_dates(values))
    return d[0] if d else None


def tf_year(values, ctx):
    d = _dates(values, labelled=True)
    return d[0][:4] + "-01-01" if d else None


def tf_interval(values, ctx):
    out = []
    for v in values:
        value, _ = unlabelled(text(v))
        parts = [iso_date(p) for p in value.split("/")]
        if parts and all(parts):
            out.append("/".join(parts))
    return out


def tf_description(values, ctx):
    parts = []
    for v in values:
        t = text(v)
        if t and t not in parts:
            parts.append(t)
    return "\n\n".join(parts) if parts else None


def tf_types(values, ctx):
    out = []
    for v in values:
        t = text(v)
        if not t:
            continue
        item = {"@id": t} if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:[^\s\"]+$", t) else t
        if item not in out:
            out.append(item)
    return out


_LICENSE_URL = re.compile(r"https?://(?:www\.)?(?:creativecommons\.org|opendatacommons\.org|"
                          r"spdx\.org)/[^\s<>\"')]+", re.I)


def tf_license(values, ctx):
    out = []
    for v in values:
        t = text(v)
        urls = [t] if _http(t) and " " not in t else _LICENSE_URL.findall(t)
        for url in urls:
            work = {"@type": ["schema:CreativeWork"], "schema:url": url.rstrip(".,;")}
            if work not in out:
                out.append(work)
    return out


def tf_rights(values, ctx):
    out = []
    for v in values:
        t = text(v)
        if t and not (_http(t) and " " not in t) and t not in out:
            out.append(t)
    return out


_YEAR_RANGE = re.compile(r"(\d{1,2} [A-Za-z]+ \d{4}|\d{4}(?:-\d{2}(?:-\d{2})?)?)")
_TEMPORAL_WORDS = re.compile(r"\b(?:dynasty|century|period|era|age|epoch|reign)\b", re.I)


def _coverage_dates(t):
    """('from'|'to'|'range'|'date', [dates]) for a coverage value that is only dates, or None."""
    t = re.sub(r"^\(Time Period\)\s*", "", t, flags=re.I).strip()
    dates = [d for d in (iso_date(m) for m in _YEAR_RANGE.findall(t)) if d]
    rest = re.sub(r"\b(?:from|to)\b|[-–/,.\s]", "", _YEAR_RANGE.sub("", t), flags=re.I)
    if not dates or rest:                  # '1001514', '18th dynasty', 'Europe'
        return None
    if len(dates) >= 2:
        return "range", dates
    if re.match(r"^from\b", t, re.I):
        return "from", dates
    if re.match(r"^to\b", t, re.I):
        return "to", dates
    return "date", dates


def tf_timecoverage(values, ctx):
    out, start, end = [], None, None
    for v in values:
        parsed = _coverage_dates(text(v))
        if not parsed:
            continue
        kind, dates = parsed
        if kind == "from":
            start = dates[0]
        elif kind == "to":
            end = dates[0]
        elif kind == "range":
            out.append("%s/%s" % (dates[0], dates[-1]))
        else:
            out.append(dates[0])
    if start or end:
        out.append("%s/%s" % (start or "..", end or ".."))
    return out


def tf_placecoverage(values, ctx):
    out = []
    for v in values:
        t = text(v)
        if not t or _coverage_dates(t) or _TEMPORAL_WORDS.search(t) or not re.search(r"[A-Za-z]{2}", t):
            continue
        name = re.sub(r"^(?:geographical|spatial) coverage:\s*", "", t, flags=re.I)
        place = {"@type": ["schema:Place"], "schema:name": name}
        if place not in out:
            out.append(place)
    return out


def _fmt(n):
    """A coordinate to six decimal places (about 0.1 m), as the ISO converter writes it."""
    return ("%.6f" % float(n)).rstrip("0").rstrip(".")


def _num(t):
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


def tf_bbox(values, ctx):
    out = []
    for b in values:
        lo = [_num(x) for x in text(b.find("ows:LowerCorner", NS)).split()]
        up = [_num(x) for x in text(b.find("ows:UpperCorner", NS)).split()]
        if len(lo) != 2 or len(up) != 2 or None in lo + up:
            continue
        crs = b.get("crs") or ""
        lon_first = etree.QName(b).localname == "WGS84BoundingBox" or "CRS84" in crs.upper() \
            or (crs and not crs.lower().startswith("urn:ogc:def:crs:epsg"))
        if lon_first:
            (w, s), (e, n) = lo, up
        else:                              # EPSG:4326 by URN: latitude first
            (s, w), (n, e) = lo, up
        if w == e and s == n:
            geo = {"@type": ["schema:GeoCoordinates"], "schema:latitude": s, "schema:longitude": w}
        else:
            geo = {"@type": ["schema:GeoShape"], "schema:box": " ".join(_fmt(x) for x in (s, w, n, e))}
        place = {"@type": ["schema:Place"], "schema:geo": geo}
        if place not in out:
            out.append(place)
    return out


def _link(url, relationship, name=None):
    target = {"@type": ["schema:EntryPoint"], "schema:url": url}
    if name:
        target["schema:name"] = name
    return {"@type": ["schema:LinkRole"], "schema:linkRelationship": relationship,
            "schema:target": target}


def tf_relation(values, ctx):
    out = []
    for v in values:
        t = text(v)
        url = resolve(t) if " " not in t else (resolve(_DOI.search(t).group(0))
                                               if _DOI.search(t) else None)
        if url:
            link = _link(url, "relation", t if t != url else None)
            if link not in out:
                out.append(link)
    return out


def tf_source(values, ctx):
    out = []
    for v in values:
        t = text(v)
        if t:
            item = {"@id": t} if _http(t) and " " not in t else t
            if item not in out:
                out.append(item)
    return out


def tf_download(values, ctx):
    out = []
    for u in values:
        url = (text(u).split() or [""])[0]     # some catalogues write the URL twice
        if not _http(url):
            continue
        dl = {"@type": ["schema:DataDownload"], "schema:contentUrl": url}
        name, desc = text(u.get("name")), text(u.get("description"))
        if name:
            dl["schema:name"] = name
        if desc:
            dl["schema:description"] = desc
        if dl not in out:
            out.append(dl)
    return out


def tf_links(values, ctx):
    out = []
    for u in values:
        url = (text(u).split() or [""])[0]
        if not _http(url):
            continue
        name = text(u.get("name")) or text(u.get("description")) or None
        link = _link(url, text(u.get("protocol")) or "related", name)
        if link not in out:
            out.append(link)
    return out


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


def apply_table(rows, record_el, doc, record, ctx):
    changes = []
    for rule in rows:
        shaper = TRANSFORMS.get(rule.get("transform", ""))
        path = (rule.get("object_json_path") or "").strip()
        xpath = (rule.get("subject_xpath") or "").strip()
        if not shaper or not xpath or not path.startswith("$."):
            continue
        parts = path[2:].split(".", 1)
        if parts[0] == "schema:subjectOf" and len(parts) == 2:
            container, target = record, parts[1]
        elif len(parts) == 1:
            container, target = doc, parts[0]
        else:
            continue
        try:
            values = record_el.xpath(xpath, namespaces=NS)
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

def records(root):
    """Every csw:Record and oai_dc:dc in the input: the root itself, or those it wraps."""
    return [e for e in root.iter() if isinstance(e.tag, str) and e.tag in RECORD_TAGS]


def encoding_of(record_el):
    return RECORD_TAGS[record_el.tag]


def convert_dc_to_cdif(record_el, rows=None, detect=True, catalog=None, record_url=None,
                       identifier=None, landing_url=None):
    """One csw:Record or oai_dc:dc element -> (CDIF JSON-LD dict, list of changes).

    identifier is the record's identifier in its service (the OAI-PMH header
    identifier), used when the record has no dc:identifier; landing_url is
    schema:url when no identifier is a web page.
    """
    rows = rows if rows is not None else load_table()
    doc = {"@context": dict(CDIF_CONTEXT)}
    record = {}
    ctx = {}
    changes = apply_table(rows, record_el, doc, record, ctx)

    # --- what CDIF requires and Dublin Core may not supply -----------------------
    if "@id" not in doc and identifier and not re.search(r"\s", identifier):
        doc["@id"] = iri_of(identifier)
        doc.setdefault("schema:identifier", property_value(identifier))
        changes.append("@id from the caller's identifier")
    if "@id" not in doc:
        doc["@id"] = "urn:sha1:%s" % hashlib.sha1(etree.tostring(record_el)).hexdigest()
        changes.append("@id made up (no identifier)")
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
        changes.append("schema:dateModified sentinel (no date)")
    if not doc.get("schema:url"):
        doc["schema:url"] = landing_url or NIL
    if not doc.get("schema:license") and not doc.get("schema:conditionsOfAccess"):
        doc["schema:license"] = [{"@id": NIL}]
        changes.append("schema:license set to the OGC nil URI (no rights)")

    # --- the catalog record --------------------------------------------------------
    subject = {
        "@id": record_url or rid + "#metadata",
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:name": "Dublin Core metadata record for: %s" % doc["schema:name"][:120],
        "schema:about": {"@id": rid},
        "dcterms:conformsTo": [{"@id": "https://w3id.org/cdif/core/2.0"}],
        "schema:description": "Converted from a Dublin Core %s record to CDIF by dc_to_cdif.py."
                              % encoding_of(record_el),
    }
    if identifier:
        subject["schema:identifier"] = identifier
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
    if _http(source):
        import urllib.request
        req = urllib.request.Request(source, headers={"User-Agent": "cdif-converters/0.1"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return etree.fromstring(r.read(), PARSER)
    return etree.parse(source, PARSER).getroot()


def _slug(text_):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text_ or "").strip("_")[:100]


def main():
    ap = argparse.ArgumentParser(description="Convert Dublin Core (csw:Record, oai_dc) to CDIF JSON-LD")
    ap.add_argument("input", nargs="+", help="record file(s) or URL(s), or CSW / OAI-PMH responses")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--identifier", help="the record's identifier in its service (OAI-PMH "
                                         "header identifier), used when it has no dc:identifier")
    ap.add_argument("--landing-url", help="the dataset's page, used as schema:url when no "
                                          "identifier is a web page")
    ap.add_argument("--record-url", help="URL of the record itself (the catalog record's @id)")
    ap.add_argument("--catalog-name", help="name of the service the records came from")
    ap.add_argument("--catalog-url", help="URL of that service")
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
        found = records(load_xml(source))
        if not found:
            print("%s: no csw:Record or oai_dc record" % source, file=sys.stderr)
            continue
        one = len(found) == 1
        for i, rec in enumerate(found):
            doc, changes = convert_dc_to_cdif(
                rec, rows, not args.static_conformance, catalog,
                (args.record_url or (source if _http(source) else None)) if one else None,
                args.identifier if one else None, args.landing_url if one else None)
            stem = Path(source).stem if one and not _http(source) else \
                _slug(doc["@id"].split("://", 1)[-1]) or "dc_%d" % i
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

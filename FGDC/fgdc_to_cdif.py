#!/usr/bin/env python3
"""
fgdc_to_cdif.py - Convert FGDC CSDGM metadata to CDIF JSON-LD.

Reads FGDC Content Standard for Digital Geospatial Metadata documents
(<metadata>, FGDC-STD-001-1998 and its Biological Data Profile), as USGS,
state surveys, ORNL DAAC and other DataONE member nodes publish them; each
document becomes one CDIF record.

The table mappings/fgdc-to-cdif.sssom.tsv drives the conversion: each row names
CSDGM elements by an XPath from the <metadata> element (subject_xpath; CSDGM
elements have no namespace), the CDIF location it lands at (object_json_path)
and the shaper that builds the value (transform). Row order is precedence for a
scalar target; array targets accumulate.

CSDGM has no element for the dataset's identifier. The record's @id is a DOI
among the citation's online linkages, else the --identifier the caller gives
(the repository's identifier for the document), else the first online linkage.

Usage:
    python FGDC/fgdc_to_cdif.py metadata.xml -o out/
    python FGDC/fgdc_to_cdif.py metadata.xml -o out/ --identifier scimeta_383.xml --identifier-type "DataONE identifier"
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

TABLE = ROOT / "mappings" / "fgdc-to-cdif.sssom.tsv"
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
DOI_REGISTRY = "https://registry.identifiers.org/registry/doi"

CDIF_CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
}
OPTIONAL_PREFIXES = {
    "cdi": "http://ddialliance.org/Specification/DDI-CDI/1.0/RDF/",
}
ARRAY_TARGETS = {
    "schema:contributor", "schema:provider", "schema:keywords", "schema:license",
    "schema:conditionsOfAccess", "schema:spatialCoverage", "schema:temporalCoverage",
    "prov:wasGeneratedBy", "prov:wasDerivedFrom", "schema:relatedLink",
    "schema:distribution", "schema:variableMeasured", "schema:additionalType",
}


# ---------------------------------------------------------------------------
# element helpers
# ---------------------------------------------------------------------------

def text(el):
    if el is None:
        return ""
    if isinstance(el, str):
        return " ".join(el.split())
    return " ".join("".join(el.itertext()).split())


def block(el):
    """Free text with its paragraph breaks kept (CSDGM text is often hard-wrapped)."""
    if el is None:
        return ""
    raw = "".join(el.itertext())
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", raw)]
    return "\n\n".join(p for p in paras if p)


def first(el, path):
    return text(el.find(path)) if el is not None else ""


def _nil(t):
    return t.strip().lower() in ("", "none", "n/a", "na", "unknown", "not applicable")


def _http(t):
    return bool(re.match(r"^https?://", t or ""))


# ---------------------------------------------------------------------------
# identifiers and dates
# ---------------------------------------------------------------------------

_DOI = re.compile(r"(?:doi\.org/|doi:)(10\.\d{4,9}/\S+)", re.I)


def doi_of(value):
    m = _DOI.search(value or "")
    return m.group(1).rstrip(".") if m else None


def fgdc_date(t):
    """A CSDGM date (YYYYMMDD, YYYYMM, YYYY) in ISO 8601, or None."""
    t = text(t)
    m = re.match(r"^(\d{4})(\d{2})?(\d{2})?$", t)
    if not m:
        m2 = re.match(r"^\d{4}-\d{2}(-\d{2})?$", t)
        return t if m2 else None
    y, mo, d = m.groups()
    return "-".join(p for p in (y, mo, d) if p)


def fgdc_time(t):
    """A CSDGM time (hhmmss, hhmm, with optional zone) as hh:mm[:ss], or None."""
    t = text(t)
    m = re.match(r"^(\d{2})(\d{2})(\d{2})?", t)
    if not m or t.lower() == "unknown":
        return None
    h, mi, s = m.groups()
    return "%s:%s%s" % (h, mi, ":" + s if s else "")


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------

_ORG_WORDS = re.compile(
    r"\b(?:survey|county|district|commission|center|centre|service|university|department|"
    r"institute|agency|office|laboratory|lab|program|programme|division|association|society|"
    r"council|bureau|foundation|museum|network|project|team|group|inc|ltd|corporation|company|"
    r"administration|authority|ministry|state|national|federal|states|city|board)\b", re.I)
_FAMILY_FIRST = re.compile(r"^[A-Za-z][\w'\-]+(?: [\w'\-]+)?, (?:[A-Z]\.\s?)+$|"
                           r"^[A-Za-z][\w'\-]+, [A-Z][a-z]+(?: [A-Z]\.?)*$")
_GIVEN_FIRST = re.compile(r"^(?:(?:[A-Z]\.)+|[A-Z][a-z]+)(?: (?:[A-Z]\.)+| [A-Z][a-z]+)*"
                          r" [A-Z][a-z][\w'\-]+$")


def name_agent(name):
    """An originator or publisher name -> Person or Organization.

    CSDGM gives only a name. 'Family, Initials' ('Blodgett, R.B.') and two to
    four capitalised words with no organisation word ('B.E. Dieter', 'Karen A.
    Westphal') make a Person; anything else ('Amador County', 'NRCS') an
    Organization.
    """
    name = text(name)
    if not name or _nil(name):
        return None
    person = not _ORG_WORDS.search(name) and (
        bool(_FAMILY_FIRST.match(name)) or
        (bool(_GIVEN_FIRST.match(name)) and 2 <= len(name.split()) <= 4))
    return {"@type": ["schema:Person" if person else "schema:Organization"], "schema:name": name}


def cntinfo_agent(c):
    """A CSDGM cntinfo -> Person or Organization."""
    if c is None:
        return None
    perp, orgp = c.find("cntperp"), c.find("cntorgp")
    person, org = "", ""
    if perp is not None:
        person, org = first(perp, "cntper"), first(perp, "cntorg")
    elif orgp is not None:
        person, org = first(orgp, "cntper"), first(orgp, "cntorg")
    position = first(c, "cntpos")
    if perp is not None and person:
        node = {"@type": ["schema:Person"], "schema:name": person}
        if org:
            node["schema:affiliation"] = {"@type": ["schema:Organization"], "schema:name": org}
        if position:
            node["schema:jobTitle"] = position
    elif org:
        node = {"@type": ["schema:Organization"], "schema:name": org}
        if person:
            node["schema:description"] = "contact: " + person + (", " + position if position else "")
    elif person:
        node = {"@type": ["schema:Person"], "schema:name": person}
    elif position:
        node = {"@type": ["schema:Person"], "schema:name": position, "schema:jobTitle": position}
    else:
        return None
    email, phone = first(c, "cntemail"), first(c, "cntvoice")
    if email:
        node["schema:email"] = email
    if phone:
        node["schema:telephone"] = phone
    return node


# ---------------------------------------------------------------------------
# value shapers: tf_<transform>(values, ctx) -> value or list
# ---------------------------------------------------------------------------

def tf_doiiri(values, ctx):
    doi = doi_of(text(values[0]))
    return "https://doi.org/" + doi if doi else None


def tf_doi(values, ctx):
    doi = doi_of(text(values[0]))
    if not doi:
        return None
    return {"@type": ["schema:PropertyValue"], "schema:propertyID": {"@id": DOI_REGISTRY},
            "schema:value": doi, "schema:url": "https://doi.org/" + doi}


def tf_firsturl(values, ctx):
    for v in values:
        if _http(text(v)):
            return text(v)
    return None


def _download_urls(ctx):
    return {text(u) for u in ctx["root"].findall(
        "distinfo/stdorder/digform/digtopt/onlinopt/computer/networka/networkr")}


def _link(url, relationship, name=None):
    target = {"@type": ["schema:EntryPoint"], "schema:url": url}
    if name:
        target["schema:name"] = name
    return {"@type": ["schema:LinkRole"], "schema:linkRelationship": relationship,
            "schema:target": target}


def tf_onlinks(values, ctx):
    skip = _download_urls(ctx)
    out, seen = [], set()
    for v in values:
        url = text(v)
        if _http(url) and url not in skip and url not in seen:
            seen.add(url)
            out.append(_link(url, "online linkage"))
    return out


def tf_text(values, ctx):
    for v in values:
        t = text(v)
        if t and not _nil(t):
            return t
    return None


def tf_texts(values, ctx):
    out = []
    for v in values:
        t = text(v)
        if t and not _nil(t) and t not in out:
            out.append(t)
    return out


def tf_creators(values, ctx):
    people = [a for a in (name_agent(v) for v in values) if a]
    return {"@list": people} if people else None


def tf_organization(values, ctx):
    for v in values:
        t = text(v)
        if t and not _nil(t):
            return {"@type": ["schema:Organization"], "schema:name": t}
    return None


def tf_date(values, ctx):
    for v in values:
        d = fgdc_date(v)
        if d:
            return d
    return None


def tf_fulldate(values, ctx):
    """A date with at least year and month (CDIF's dateModified)."""
    for v in values:
        d = fgdc_date(v)
        if d and len(d) >= 7:
            return d
    return None


def tf_year(values, ctx):
    for v in values:
        d = fgdc_date(v)
        if d:
            return d[:4] + "-01-01"
    return None


_LABELS = {"purpose": "Purpose", "supplinf": "Supplemental information",
           "eaover": "Entity and attribute overview"}


def tf_description(values, ctx):
    parts = []
    for v in values:
        t = block(v)
        if not t or _nil(t):
            continue
        parts.append(t if v.tag == "abstract" else "%s: %s" % (_LABELS.get(v.tag, v.tag), t))
    return "\n\n".join(parts) if parts else None


def _timestamp(date_el, time_el):
    d = fgdc_date(date_el)
    if not d:
        return None
    t = fgdc_time(time_el) if time_el is not None else None
    return "%sT%s" % (d, t) if t and len(d) == 10 else d


def tf_interval(values, ctx):
    out = []
    for ti in values:
        for r in ti.findall("rngdates"):
            begin = _timestamp(r.find("begdate"), r.find("begtime"))
            end_raw = first(r, "enddate")
            end = ".." if end_raw.lower() == "present" else _timestamp(r.find("enddate"),
                                                                        r.find("endtime"))
            if begin or end:
                out.append("%s/%s" % (begin or "..", end or ".."))
        for s in ti.findall(".//sngdate"):
            for part in first(s, "caldate").split(","):   # some write several dates in one
                d = fgdc_date(part)
                if d and d not in out:
                    out.append(d)
    return out


def _num(t):
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


def tf_place(values, ctx):
    out = []
    for sp in values:
        place = {"@type": ["schema:Place"]}
        desc = first(sp, "descgeog")
        if desc:
            place["schema:description"] = desc
        b = sp.find("bounding")
        if b is not None:
            w, e, n, s = (_num(first(b, k)) for k in ("westbc", "eastbc", "northbc", "southbc"))
            if None not in (w, e, n, s):
                if w == e and n == s:
                    place["schema:geo"] = {"@type": ["schema:GeoCoordinates"],
                                           "schema:latitude": n, "schema:longitude": w}
                else:
                    place["schema:geo"] = {"@type": ["schema:GeoShape"],
                                           "schema:box": "%g %g %g %g" % (s, w, n, e)}
        if len(place) > 1:
            out.append(place)
    return out


_KEY_TAGS = {"theme": ("themekt", "themekey"), "place": ("placekt", "placekey"),
             "stratum": ("stratkt", "stratkey"), "temporal": ("tempkt", "tempkey")}


def tf_keywords(values, ctx):
    out = []
    for ks in values:
        kt, key = _KEY_TAGS[ks.tag]
        thesaurus = first(ks, kt)
        for k in ks.findall(key):
            name = text(k)
            if not name:
                continue
            if _nil(thesaurus):
                item = name
            else:
                item = {"@type": ["schema:DefinedTerm"], "schema:name": name,
                        "schema:inDefinedTermSet": thesaurus,
                        "schema:description": "keyword type: " + ks.tag}
                if len(name) < 3:          # CDIF wants a name of 3+ characters, else a code
                    item["schema:termCode"] = name
            if item not in out:
                out.append(item)
    return out


def tf_taxon(values, ctx):
    out = []
    for t in values:
        name = first(t, "taxonrv")
        if not name:
            continue
        term = {"@type": ["schema:DefinedTerm"], "schema:name": name}
        rank = first(t, "taxonrn")
        if rank:
            term["schema:description"] = "taxon rank: " + rank
        common = [text(c) for c in t.findall("common") if text(c)]
        if common:
            term["schema:alternateName"] = common
        if term not in out:
            out.append(term)
    return out


def tf_constraints(values, ctx):
    out = []
    for v in values:
        t = block(v)                     # 'None' is a statement here: no constraints
        if t:
            out.append(("Access constraints: " if v.tag == "accconst" else "Use constraints: ") + t)
    return out


_LICENSE_URL = re.compile(r"https?://(?:www\.)?(?:creativecommons\.org|opendatacommons\.org|"
                          r"spdx\.org)/[^\s<>\"')]+", re.I)


def tf_licenseurl(values, ctx):
    out = []
    for v in values:
        for url in _LICENSE_URL.findall(text(v)):
            url = url.rstrip(".,;")
            work = {"@type": ["schema:CreativeWork"], "schema:url": url}
            if work not in out:
                out.append(work)
    return out


def tf_contact(values, ctx):
    out = []
    for v in values:
        a = cntinfo_agent(v)
        if a:
            r = {"@type": ["schema:Role"], "schema:roleName": "pointOfContact",
                 "schema:contributor": a}
            if r not in out:
                out.append(r)
    return out


def tf_agents(values, ctx):
    out = []
    for v in values:
        a = cntinfo_agent(v)
        if a and a not in out:
            out.append(a)
    return out


def tf_agent(values, ctx):
    for v in values:
        a = cntinfo_agent(v)
        if a:
            return a
    return None


def tf_procstep(values, ctx):
    out = []
    for p in values:
        desc = block(p.find("procdesc"))
        if not desc:
            continue
        action = {"@type": ["schema:Action", "prov:Activity"], "schema:name": "Process step",
                  "schema:actionProcess": desc}
        d = fgdc_date(p.find("procdate"))
        if d:
            action["schema:endTime"] = d
        out.append(action)
    return out


def _citation_link(ci):
    title = first(ci, "title")
    url = next((text(o) for o in ci.findall("onlink") if _http(text(o))), None)
    return title, url


def tf_source(values, ctx):
    out = []
    for s in values:
        ci = s.find("srccite/citeinfo")
        if ci is None:
            continue
        title, url = _citation_link(ci)
        if url:
            ref = {"@type": ["schema:CreativeWork"], "schema:url": url}
            if title:
                ref["schema:name"] = title
            out.append(ref)
        elif title:
            out.append(title)
    return out


def tf_crossref(values, ctx):
    out = []
    for c in values:
        ci = c.find("citeinfo")
        title, url = _citation_link(ci) if ci is not None else ("", None)
        if url:
            out.append(_link(url, "crossReference", title or None))
    return out


def tf_largerwork(values, ctx):
    out = []
    for c in values:
        ci = c.find("citeinfo")
        title, url = _citation_link(ci) if ci is not None else ("", None)
        if url:
            out.append(_link(url, "largerWork", title or None))
    return out


def tf_digform(values, ctx):
    out = []
    for d in values:
        fmt = first(d, "digtinfo/formname")
        size = first(d, "digtinfo/transize")
        for u in d.findall("digtopt/onlinopt/computer/networka/networkr"):
            url = text(u)
            if not _http(url):
                continue
            dl = {"@type": ["schema:DataDownload"], "schema:contentUrl": url}
            if fmt:
                dl["schema:encodingFormat"] = [fmt]
            if _num(size) is not None:
                dl["schema:contentSize"] = "%s MB" % size
            if dl not in out:
                out.append(dl)
    return out


def _var_id(ctx, i, name):
    prefix = "#e%d/" % (i + 1) if ctx["n_entities"] > 1 else "#"
    return prefix + re.sub(r"\s+", "_", name)


def tf_variables(values, ctx):
    out, seen = [], set()
    for i, det in enumerate(values):
        for a in det.findall("attr"):
            name = first(a, "attrlabl")
            if not name:
                continue
            vid = _var_id(ctx, i, name)
            if vid in seen:
                continue
            seen.add(vid)
            var = {"@id": vid, "@type": ["schema:PropertyValue", "cdi:InstanceVariable"],
                   "schema:name": name,
                   "schema:description": block(a.find("attrdef")) or name}
            rdom = a.find("attrdomv/rdom")
            if rdom is not None:
                lo, hi = _num(first(rdom, "rdommin")), _num(first(rdom, "rdommax"))
                if lo is not None:
                    var["schema:minValue"] = lo
                if hi is not None:
                    var["schema:maxValue"] = hi
                unit = first(rdom, "attrunit")
                if unit and not _nil(unit):
                    var["schema:unitText"] = unit
            out.append(var)
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


def apply_table(rows, root, doc, record, ctx):
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
            values = root.xpath(xpath)
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

def documents(root):
    """Every CSDGM <metadata> in the input: the root itself, or those it wraps."""
    if root.tag == "metadata" and root.find("idinfo") is not None:
        return [root]
    return [e for e in root.iter("metadata") if e.find("idinfo") is not None]


def standard_of(root):
    return " ".join(p for p in (first(root, "metainfo/metstdn"),
                                first(root, "metainfo/metstdv")) if p)


def convert_fgdc_to_cdif(root, rows=None, detect=True, catalog=None, record_url=None,
                         identifier=None, identifier_type=None):
    """One CSDGM <metadata> element -> (CDIF JSON-LD dict, list of changes).

    identifier is the repository's identifier for the document (CSDGM has no
    dataset identifier); it is the @id when the citation has no DOI.
    """
    rows = rows if rows is not None else load_table()
    doc = {"@context": dict(CDIF_CONTEXT)}
    record = {}
    ctx = {"root": root, "doc": doc, "n_entities": len(root.findall("eainfo/detailed"))}
    changes = apply_table(rows, root, doc, record, ctx)

    # --- identity: a DOI (from the table), else the caller's identifier ----------
    if "@id" not in doc and identifier:
        doc["@id"] = identifier if _http(identifier) else \
            "urn:fgdc:" + re.sub(r"\s+", "", identifier)
        changes.append("@id from the caller's identifier")
    if not doc.get("schema:identifier") and identifier:
        doc["schema:identifier"] = {"@type": ["schema:PropertyValue"],
                                    "schema:propertyID": identifier_type or "identifier",
                                    "schema:value": identifier}
    if "@id" not in doc and _http(doc.get("schema:url", "")):
        doc["@id"] = doc["schema:url"]
        changes.append("@id from the first online linkage")
    if "@id" not in doc:
        doc["@id"] = "urn:sha1:%s" % hashlib.sha1(etree.tostring(root)).hexdigest()
        changes.append("@id made up (no DOI, identifier or online linkage)")

    # --- what CDIF requires and CSDGM may not supply -----------------------------
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
        changes.append("schema:dateModified sentinel (no publication or metadata date)")
    if not doc.get("schema:url"):
        doc["schema:url"] = NIL
    if not doc.get("schema:license") and not doc.get("schema:conditionsOfAccess"):
        doc["schema:license"] = [{"@id": NIL}]
        changes.append("schema:license set to the OGC nil URI (no constraints)")

    # --- the catalog record --------------------------------------------------------
    subject = {
        "@id": record_url or rid + "#metadata",
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:name": "FGDC metadata record for: %s" % doc["schema:name"][:120],
        "schema:about": {"@id": rid},
        "dcterms:conformsTo": [{"@id": "https://w3id.org/cdif/core/2.0"}],
        "schema:description": "Converted from %s to CDIF by fgdc_to_cdif.py."
                              % (standard_of(root) or "FGDC CSDGM"),
    }
    if identifier:
        subject["schema:identifier"] = identifier
    subject.update(record)
    if catalog:
        subject["schema:includedInDataCatalog"] = catalog
    doc["schema:subjectOf"] = subject

    text_ = json.dumps(doc)
    for prefix, uri in OPTIONAL_PREFIXES.items():
        if '"%s:' % prefix in text_:
            doc["@context"][prefix] = uri

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
    ap = argparse.ArgumentParser(description="Convert FGDC CSDGM metadata to CDIF JSON-LD")
    ap.add_argument("input", nargs="+", help="CSDGM file(s) or URL(s)")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--identifier", help="the repository's identifier for the document, used "
                                         "as @id when the citation has no DOI")
    ap.add_argument("--identifier-type", help="what kind of identifier that is "
                                              "(e.g. 'DataONE identifier')")
    ap.add_argument("--record-url", help="URL of the CSDGM document itself (the catalog record's @id)")
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
        found = documents(load_xml(source))
        if not found:
            print("%s: no CSDGM metadata" % source, file=sys.stderr)
            continue
        one = len(found) == 1
        record_url = args.record_url or (source if _http(source) else None)
        for i, root in enumerate(found):
            doc, changes = convert_fgdc_to_cdif(
                root, rows, not args.static_conformance, catalog,
                record_url if one else None, args.identifier if one else None,
                args.identifier_type)
            stem = Path(source).stem if one and not _http(source) else \
                _slug(doc["@id"].split("://", 1)[-1]) or "fgdc_%d" % i
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

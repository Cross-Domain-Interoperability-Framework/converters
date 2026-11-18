#!/usr/bin/env python3
"""
iso_to_cdif.py - Convert ISO 19115 metadata XML to CDIF JSON-LD.

Reads both XML encodings of ISO 19115:

  ISO 19115-3   mdb:MD_Metadata             table mappings/iso19115-3-to-cdif.sssom.tsv
  ISO 19139     gmd:MD_Metadata, and        table mappings/iso19139-to-cdif.sssom.tsv
                gmi:MI_Metadata (19115-2)

The encoding is chosen from the root element. Each table row names an ISO
element by an XPath from the metadata root (subject_xpath), the CDIF location
it lands at (object_json_path) and the shaper that builds the value
(transform). The two tables differ only in their XPaths: the shapers read
elements by local name, which the two encodings share for almost every class
(CI_ResponsibleParty versus CI_Responsibility is the exception, handled in
_parties). Row order is precedence for a scalar target; array targets
accumulate.

Where ISO has no CDIF property, the record follows the GeoNetwork DDE
conventions and the DDE building blocks (usgin/ddeBuildingBlocks):

  * mri:supplementalInformation "FUNDING:", "MEASUREMENT TECHNIQUE:" and
    "PUBLISHING PRINCIPLES:" sections -> funding, measurementTechnique,
    publishingPrinciples; the rest of the text joins the description
  * an MD_Identifier whose codeSpace is "sameAs" -> schema:sameAs
  * an online resource whose linkage is the OGC "inapplicable" nil URL ->
    an archive member (schema:hasPart) of the distribution before it
  * spatial representation, reference system and vertical extent ->
    dde:spatialRepresentationType, dde:spatialResolution,
    dde:referenceSystemType, dde:referenceSystemIdentifier and
    dde:verticalExtent PropertyValues (ddeGeographicDataset)
  * topic categories -> DefinedTerms in dde:codelist/TopicCategoryCode;
    the resource scope -> a dde:codelist/ResourceTypeCode term (ddeCore)
  * acquisition instruments and platforms -> instruments typed dde:sensorType /
    dde:platform on a prov:wasGeneratedBy activity (ddeImagery, ddeActivity)
  * service identification -> schema:WebAPI with serviceType, potentialAction,
    termsOfService and schema:dataset (DDEWebAPI); a service with no
    operations gets one "missing" Action whose urlTemplate is the OGC nil
    URL, and one with no access properties termsOfService "not specified"

Usage:
    python ISO19115/iso_to_cdif.py record.xml [more.xml ...] -o out/
    python ISO19115/iso_to_cdif.py csw-getrecords-response.xml -o out/
    python ISO19115/iso_to_cdif.py in.xml -o out/ --validate
"""

import argparse
import csv
import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(ROOT / "validation"))
try:
    from detect_conformance import detect_conformance, apply_conformance
    _HAVE_DETECT = True
except Exception:
    _HAVE_DETECT = False

TABLES = {
    "iso19115-3": ROOT / "mappings" / "iso19115-3-to-cdif.sssom.tsv",
    "iso19139": ROOT / "mappings" / "iso19139-to-cdif.sssom.tsv",
}
NS = {
    "iso19115-3": {
        "mdb": "http://standards.iso.org/iso/19115/-3/mdb/2.0",
        "cit": "http://standards.iso.org/iso/19115/-3/cit/2.0",
        "mri": "http://standards.iso.org/iso/19115/-3/mri/1.0",
        "mcc": "http://standards.iso.org/iso/19115/-3/mcc/1.0",
        "mco": "http://standards.iso.org/iso/19115/-3/mco/1.0",
        "gex": "http://standards.iso.org/iso/19115/-3/gex/1.0",
        "gco": "http://standards.iso.org/iso/19115/-3/gco/1.0",
        "gcx": "http://standards.iso.org/iso/19115/-3/gcx/1.0",
        "lan": "http://standards.iso.org/iso/19115/-3/lan/1.0",
        "mrd": "http://standards.iso.org/iso/19115/-3/mrd/1.0",
        "mrl": "http://standards.iso.org/iso/19115/-3/mrl/2.0",
        "mrs": "http://standards.iso.org/iso/19115/-3/mrs/1.0",
        "mrc": "http://standards.iso.org/iso/19115/-3/mrc/2.0",
        "msr": "http://standards.iso.org/iso/19115/-3/msr/2.0",
        "mac": "http://standards.iso.org/iso/19115/-3/mac/2.0",
        "mdq": "http://standards.iso.org/iso/19157/-2/mdq/1.0",
        "srv": "http://standards.iso.org/iso/19115/-3/srv/2.1",
        "gfc": "http://standards.iso.org/iso/19110/gfc/1.1",
        "xlink": "http://www.w3.org/1999/xlink",
    },
    "iso19139": {
        "gmd": "http://www.isotc211.org/2005/gmd",
        "gco": "http://www.isotc211.org/2005/gco",
        "gmx": "http://www.isotc211.org/2005/gmx",
        "gmi": "http://www.isotc211.org/2005/gmi",
        "srv": "http://www.isotc211.org/2005/srv",
        "gfc": "http://www.isotc211.org/2005/gfc",
        "xlink": "http://www.w3.org/1999/xlink",
    },
}
ROOTS = {
    ("http://standards.iso.org/iso/19115/-3/mdb/2.0", "MD_Metadata"): "iso19115-3",
    ("http://standards.iso.org/iso/19115/-3/mdb/1.0", "MD_Metadata"): "iso19115-3",
    ("http://www.isotc211.org/2005/gmd", "MD_Metadata"): "iso19139",
    ("http://www.isotc211.org/2005/gmi", "MI_Metadata"): "iso19139",
}
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)

NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
INAPPLICABLE = "http://www.opengis.net/def/nil/OGC/0/inapplicable"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
WIKIDATA_INSTRUMENT = "https://www.wikidata.org/entity/Q3099911"
ISO_CODELISTS = "http://standards.iso.org/iso/19115/resources/Codelists/cat/codelists.xml#"

CDIF_CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
}
OPTIONAL_PREFIXES = {
    "geosparql": "http://www.opengis.net/ont/geosparql#",
    "dde": "https://www.ddeworld.org/resource/",
}
ARRAY_TARGETS = {
    "schema:keywords", "schema:spatialCoverage", "schema:temporalCoverage",
    "schema:variableMeasured", "schema:distribution", "schema:relatedLink",
    "schema:contributor", "schema:provider", "schema:sameAs", "schema:license",
    "schema:conditionsOfAccess", "schema:measurementTechnique", "schema:funding",
    "prov:wasGeneratedBy", "prov:wasDerivedFrom", "schema:additionalProperty",
    "schema:image", "schema:additionalType", "schema:alternateName",
    "schema:potentialAction", "schema:dataset", "schema:publishingPrinciples",
}
ORDERED_TARGETS = {"schema:creator"}
PLACEHOLDERS = {"", "none", "not provided", "n/a", "na", "not applicable",
                "unknown", "missing", "null", "not available", "tbd"}


# ---------------------------------------------------------------------------
# element helpers (namespace-agnostic: by local name)
# ---------------------------------------------------------------------------

def ln(el):
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def kids(el, *path):
    """Elements reached from `el` by a path of child local names."""
    nodes = [el]
    for name in path:
        nodes = [c for n in nodes for c in n if isinstance(c.tag, str) and ln(c) == name]
    return nodes


def kid(el, *path):
    found = kids(el, *path) if el is not None else []
    return found[0] if found else None


_VALUE_TAGS = {"CharacterString", "Anchor", "Decimal", "Real", "Integer", "Boolean",
               "Date", "DateTime", "URL", "Measure", "Distance", "Length", "Scale",
               "Angle", "FileName", "MimeFileType", "LocalName", "ScopedName",
               "TM_PeriodDuration", "Record", "Number", "UnlimitedInteger"}


def txt(el):
    """The text an ISO property carries: its gco/gcx value, a codelist value,
    or its own text. None when empty or a placeholder."""
    if el is None:
        return None
    if isinstance(el, str):
        t = el
    else:
        val = next((c for c in el if isinstance(c.tag, str) and ln(c) in _VALUE_TAGS), None)
        # A codelist element's text is its label (an EDMO code's organisation
        # name), so text wins over codeListValue here; code() prefers the code.
        if val is not None:
            t = val.text or ""
        elif el.get("codeListValue") is not None:
            t = (el.text or "").strip() or el.get("codeListValue") or ""
        else:
            code = next((c for c in el if isinstance(c.tag, str)
                         and c.get("codeListValue") is not None), None)
            t = ((code.text or "").strip() or code.get("codeListValue") or "") if code is not None \
                else "".join(el.itertext()) if len(el) == 0 else (el.text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return None if t.lower() in PLACEHOLDERS else t


def href(el):
    """The xlink:href of an element or of its Anchor value."""
    if el is None:
        return None
    for e in [el] + [c for c in el if isinstance(c.tag, str)]:
        h = e.get(XLINK_HREF)
        if h and h.strip():
            return h.strip()
    return None


def code(el):
    """A codelist value: @codeListValue of the element or its child, else text."""
    if el is None:
        return None
    for e in [el] + [c for c in el if isinstance(c.tag, str)]:
        v = e.get("codeListValue")
        if v and v.strip():
            return v.strip()
    return txt(el)


def _url(text):
    if not isinstance(text, str):
        return None
    t = text.strip().replace(" ", "%20")
    if re.match(r"^(https?|ftp)://[^\s]+$", t, re.I):
        return t
    if re.match(r"^www\.[\w-]+(\.[\w-]+)+(/\S*)?$", t):
        return "https://" + t
    return None


_CDIF_DATE_RE = re.compile(
    r"^[1-2][0-9]{3}(-(0[1-9]|1[0-2])(-([0-2][0-9]|3[01])"
    r"(T([01][0-9]|2[0-3])(:[0-5][0-9])?(:[0-5][0-9](Z|[+-][0-2][0-9]:[0-5][0-9])?)?)?)?)?$")


def _date(value):
    if not isinstance(value, str):
        return None
    v = re.sub(r"\.\d+", "", value.strip())
    if re.match(r"^\d{8}$", v):                     # 20140131
        v = "%s-%s-%s" % (v[:4], v[4:6], v[6:])
    if _CDIF_DATE_RE.match(v) and len(v) >= 7:
        return v
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", v):
        return v + "Z"                               # no time zone: taken as UTC
    for n in (10, 7):
        if _CDIF_DATE_RE.match(v[:n]):
            return v[:n]
    return None


def _date_of(el):
    """A date from a CI_Date/date, a gco:Date/DateTime wrapper or text."""
    if el is None:
        return None
    if ln(el) == "CI_Date":
        el = kid(el, "date")
    return _date(txt(el))


def _term(name, scheme=None, code_=None, url=None):
    t = {"@type": ["schema:DefinedTerm"], "schema:name": name}
    if code_:
        t["schema:termCode"] = code_
    if scheme:
        t["schema:inDefinedTermSet"] = scheme
    if url:
        t["schema:url"] = url
    return t


def _pv(prop_id, name, value=None, **extra):
    p = {"@type": ["schema:PropertyValue"],
         "schema:propertyID": [{"@id": prop_id}], "schema:name": name}
    if value is not None:
        p["schema:value"] = value
    p.update(extra)
    return p


def _link(relationship, url, name=None, description=None):
    target = {"@type": ["schema:EntryPoint"], "schema:url": url}
    if name:
        target["schema:name"] = name
    if description:
        target["schema:description"] = description
    return {"@type": ["schema:LinkRole"], "schema:linkRelationship": relationship,
            "schema:target": target}


def _fmt(n):
    return ("%.6f" % float(n)).rstrip("0").rstrip(".")


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------

def _contact(contact, agent):
    """Fold a CI_Contact into a schema.org agent."""
    if contact is None:
        return agent
    for a in kids(contact, "address") or []:
        addr = kid(a, "CI_Address")
        mail = txt(kid(addr, "electronicMailAddress")) if addr is not None else None
        if mail and "schema:email" not in agent:
            agent["schema:email"] = mail
    for o in kids(contact, "onlineResource", "CI_OnlineResource"):
        u = _url(txt(kid(o, "linkage")))
        if u and "schema:url" not in agent:
            agent["schema:url"] = u
    for p in kids(contact, "phone"):
        t = txt(kid(p, "CI_Telephone", "number")) or txt(kid(p, "CI_Telephone", "voice"))
        if t and "schema:telephone" not in agent:
            agent["schema:telephone"] = t
    return agent


def _party_id(party):
    """An identifier for a party: partyIdentifier (19115-3) or an Anchor href on
    the name (ORCID / ROR in 19139)."""
    for pid in kids(party, "partyIdentifier", "MD_Identifier"):
        c = txt(kid(pid, "code"))
        if c:
            u = _url(c) or (("https://orcid.org/" + c) if re.match(
                r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$", c) else None)
            return u or c
    for name in ("name", "individualName", "organisationName"):
        h = href(kid(party, name))
        if _url(h):
            return h
    return None


def _agent(kind, name, contact=None, ident=None, affiliation=None, position=None):
    a = {"@type": ["schema:Person" if kind == "person" else "schema:Organization"],
         "schema:name": name}
    if ident:
        a["schema:identifier"] = ident
    if position and kind == "person":
        a["schema:jobTitle"] = position
    if affiliation and kind == "person":
        a["schema:affiliation"] = affiliation
    return _contact(contact, a)


def _parties(resp):
    """(role, agent) for each party of a CI_Responsibility (19115-3) or
    CI_ResponsibleParty (19139). An individual inside an organisation is a
    Person affiliated with it."""
    role = code(kid(resp, "role"))
    out = []
    if ln(resp) == "CI_ResponsibleParty":
        ind = txt(kid(resp, "individualName"))
        org = txt(kid(resp, "organisationName"))
        contact = kid(resp, "contactInfo", "CI_Contact")
        ident = _party_id(resp)
        if ind:
            aff = {"@type": ["schema:Organization"], "schema:name": org} if org else None
            out.append(_agent("person", ind, contact, ident, aff,
                              txt(kid(resp, "positionName"))))
        elif org:
            out.append(_agent("org", org, contact, ident))
        elif txt(kid(resp, "positionName")):
            out.append(_agent("org", txt(kid(resp, "positionName")), contact, ident))
        return [(role, a) for a in out]
    for party in [c for p in kids(resp, "party") for c in p if isinstance(c.tag, str)]:
        name = txt(kid(party, "name"))
        contact = kid(party, "contactInfo", "CI_Contact")
        if ln(party) == "CI_Organisation":
            org = _agent("org", name, contact, _party_id(party)) if name else None
            inds = kids(party, "individual", "CI_Individual")
            for ind in inds:
                iname = txt(kid(ind, "name")) or txt(kid(ind, "positionName"))
                if iname:
                    aff = {"@type": ["schema:Organization"], "schema:name": name} if name else None
                    ind_contact = kid(ind, "contactInfo", "CI_Contact")
                    out.append(_agent("person", iname,
                                      contact if ind_contact is None else ind_contact,
                                      _party_id(ind), aff, txt(kid(ind, "positionName"))))
            if org and not inds:
                out.append(org)
        elif ln(party) == "CI_Individual":
            iname = name or txt(kid(party, "positionName"))
            if iname:
                out.append(_agent("person", iname, contact, _party_id(party), None,
                                  txt(kid(party, "positionName"))))
    return [(role, a) for a in out]


def tf_agent(values, ctx):
    return [a for r in values for _, a in _parties(r)] or None


def tf_contributor(values, ctx):
    """Agents in a schema:Role named by the ISO role code."""
    return [{"@type": ["schema:Role"], "schema:roleName": role or "contributor",
             "schema:contributor": a}
            for r in values for role, a in _parties(r)] or None


def tf_funder(values, ctx):
    """A party with role funder -> a MonetaryGrant naming it."""
    out = []
    for r in values:
        for _, a in _parties(r):
            org = a if a["@type"] == ["schema:Organization"] else \
                a.get("schema:affiliation") or {"@type": ["schema:Organization"],
                                                "schema:name": a["schema:name"]}
            out.append({"@type": ["schema:MonetaryGrant"],
                        "schema:name": "Funding from %s" % org["schema:name"],
                        "schema:funder": org})
    return out or None


# ---------------------------------------------------------------------------
# shapers: fn(values, ctx) -> a value, a list of values, or None.
# `values` are the elements (or attribute strings) the row's XPath selects.
# ---------------------------------------------------------------------------

def tf_text(values, ctx):
    for v in values:
        t = txt(v)
        if t:
            return t
    return None


def tf_texts(values, ctx):
    out = []
    for v in values:
        t = txt(v)
        if t and t not in out:
            out.append(t)
    return out or None


def tf_describe(values, ctx):
    """Text appended to the target under the row's subject_label."""
    texts = tf_texts(values, ctx)
    return "%s: %s" % (ctx["rule"]["subject_label"].capitalize(), " ".join(texts)) \
        if texts else None


def tf_date(values, ctx):
    for v in values:
        d = _date_of(v)
        if d:
            return d
    return None


def tf_lang(values, ctx):
    """An ISO 639-2 LanguageCode -> a BCP 47 tag where it is one of the common
    ones, else the code as given."""
    common = {"eng": "en", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de",
              "spa": "es", "chi": "zh", "zho": "zh", "jpn": "ja", "por": "pt",
              "ita": "it", "dut": "nl", "nld": "nl", "rus": "ru", "kor": "ko"}
    for v in values:
        c = code(v)
        if c:
            c = c.lower().split(";")[0].strip()
            return common.get(c, c)
    return None


def tf_identifier(values, ctx):
    """The first MD_Identifier whose codeSpace is not 'sameAs' -> a
    PropertyValue (propertyID = codeSpace, url when the code resolves)."""
    for v in values:
        c = txt(kid(v, "code"))
        space = txt(kid(v, "codeSpace"))
        if not c or (space or "").lower() == "sameas":
            continue
        pv = {"@type": ["schema:PropertyValue"], "schema:value": c}
        doi = re.search(r"(10\.\d{4,9}/\S+)", c)
        if space:
            # A URI or CURIE codeSpace is an IRI reference, a label a string
            pv["schema:propertyID"] = {"@id": space} if re.match(
                r"^[A-Za-z][\w.+-]*:\S+$", space) else space
        elif doi:
            pv["schema:propertyID"] = {"@id": "https://registry.identifiers.org/registry/doi"}
        u = _url(c) or _url(href(kid(v, "code")))
        if doi and not u:
            u = "https://doi.org/" + doi.group(1)
        if u:
            pv["schema:url"] = u
        return pv
    return None


def tf_sameas(values, ctx):
    """Every identifier but the primary one -> sameAs (a URL, or the code)."""
    primary = (ctx["doc"].get("schema:identifier") or {}).get("schema:value")
    out = []
    for v in values:
        c = txt(kid(v, "code"))
        if not c or c == primary:
            continue
        doi = re.search(r"^(?:doi:)?(10\.\d{4,9}/\S+)$", c)
        out.append(_url(c) or _url(href(kid(v, "code"))) or
                   ("https://doi.org/" + doi.group(1) if doi else c))
    return out or None


def tf_recordid(values, ctx):
    """The metadata record's own identifier (catalog record schema:identifier)."""
    for v in values:
        c = txt(kid(v, "code")) if ln(v) == "MD_Identifier" else txt(v)
        if c:
            return c
    return None


# --- classification -----------------------------------------------------------

DDE_RESOURCE_TYPES = {
    "aggregate", "application", "collection", "dataset", "nonGeographicDataset",
    "document", "fieldSession", "initiative", "model", "repository", "series",
    "service", "software"}
DDE_FROM_SCOPE = {"series": "collection"}


def tf_scope(values, ctx):
    """MD_ScopeCode -> a ResourceTypeCode DefinedTerm (ddeCore) when DDE has the
    term (series is a DDE collection), else an ISO MD_ScopeCode term. A
    dataset with a geographic extent is a DDE geographicDataset."""
    out = []
    for v in values:
        c = code(v)
        if not c:
            continue
        if c == "dataset" and ctx["xml"].xpath(ctx["geo_xpath"], namespaces=ctx["ns"]):
            c = "geographicDataset"
        dde = DDE_FROM_SCOPE.get(c, c)
        if dde in DDE_RESOURCE_TYPES | {"geographicDataset"}:
            out.append(_term(dde, "dde:codelist/ResourceTypeCode", dde))
        else:
            out.append(_term(c, ISO_CODELISTS + "MD_ScopeCode", c))
    return out or None


def tf_topic(values, ctx):
    return [_term(c, "dde:codelist/TopicCategoryCode", c)
            for c in (txt(v) for v in values) if c] or None


def tf_keywords(values, ctx):
    """MD_Keywords -> DefinedTerms in the thesaurus named by thesaurusName, or
    plain strings when there is none. An Anchor's href is the term's url."""
    out = []
    for mk in values:
        th = kid(mk, "thesaurusName", "CI_Citation")
        scheme = None
        if th is not None:
            scheme = (href(kid(th, "title")) or
                      _url(txt(kid(th, "identifier", "MD_Identifier", "code"))) or
                      txt(kid(th, "title")))
        ktype = code(kid(mk, "type"))
        for k in kids(mk, "keyword"):
            name = txt(k)
            if not name:
                continue
            u = _url(href(k))
            if scheme or u:
                # A code shorter than a CDIF name (SeaDataNet "13") is also the termCode
                term = _term(name, scheme, name if len(name) < 3 else None, u)
                if ktype:
                    term["schema:description"] = "keyword type: %s" % ktype
                out.append(term)
            elif name not in out:
                out.append(name)
    return out or None


# --- extents -------------------------------------------------------------------

def _place(name=None, geo=None, wkt=None, description=None):
    p = {"@type": ["schema:Place"]}
    if name:
        p["schema:name"] = name
    if description:
        p["schema:description"] = description
    if geo:
        p["schema:geo"] = geo
    if wkt:
        p["geosparql:hasGeometry"] = {
            "@type": ["geosparql:Geometry"],
            "geosparql:asWKT": {"@type": ["geosparql:wktLiteral"], "@value": wkt}}
    return p


def tf_bbox(values, ctx):
    out = []
    for b in values:
        w, e = _num(txt(kid(b, "westBoundLongitude"))), _num(txt(kid(b, "eastBoundLongitude")))
        s, n = _num(txt(kid(b, "southBoundLatitude"))), _num(txt(kid(b, "northBoundLatitude")))
        if None in (w, e, s, n) or not (-90 <= s <= n <= 90 and -180 <= w <= 180
                                        and -180 <= e <= 180):
            continue
        ext = _ancestor(b, "EX_Extent")
        desc = txt(kid(ext, "description")) if ext is not None else None
        if s == n and w == e:
            geo = {"@type": ["schema:GeoCoordinates"], "schema:latitude": s,
                   "schema:longitude": w}
        else:
            geo = {"@type": ["schema:GeoShape"],
                   "schema:box": " ".join(_fmt(x) for x in (s, w, n, e))}
        out.append(_place(geo=geo, description=desc))
    return out or None


def tf_polygon(values, ctx):
    """A gml polygon (posList or pos, lat-lon or lon-lat as the CRS says) ->
    a WKT POLYGON in lon-lat."""
    out = []
    for poly in values:
        for ring in poly.iter("{*}exterior"):
            pos = [p.text for p in ring.iter("{*}posList")] or \
                  [" ".join(p.text.split()) for p in ring.iter("{*}pos") if p.text]
            nums = [_num(x) for x in " ".join(pos).split()]
            if len(nums) < 8 or None in nums:
                continue
            pts = list(zip(nums[0::2], nums[1::2]))
            srs = next((e.get("srsName") for e in poly.iter() if e.get("srsName")), "") or ""
            if "4326" in srs and "CRS84" not in srs:     # EPSG:4326 axis order: lat lon
                pts = [(y, x) for x, y in pts]
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            # CDIF's schema:geo is a box, point or line, and its discovery Place
            # rule reads schema:geo: the polygon's bounding box goes there, the
            # polygon itself in the WKT
            xs, ys = [x for x, _ in pts], [y for _, y in pts]
            geo = {"@type": ["schema:GeoShape"], "schema:box": " ".join(
                _fmt(v) for v in (min(ys), min(xs), max(ys), max(xs)))}
            out.append(_place(geo=geo, wkt="POLYGON((%s))" % ", ".join(
                "%s %s" % (_fmt(x), _fmt(y)) for x, y in pts)))
    return out or None


def tf_geodescription(values, ctx):
    out = []
    for g in values:
        c = txt(kid(g, "geographicIdentifier", "MD_Identifier", "code"))
        if c:
            place = _place(name=c)
            u = _url(href(kid(g, "geographicIdentifier", "MD_Identifier", "code")))
            if u:
                place["schema:identifier"] = u
            out.append(place)
    return out or None


def _gml_time(el):
    """(begin, end) from a gml TimePeriod or TimeInstant, as CDIF dates; an
    open end ('now', 'unknown', indeterminatePosition) is '..'."""
    def point(node):
        if node is None:
            return None
        v = (node.text or "").strip()
        if not v or node.get("indeterminatePosition") in ("now", "unknown"):
            return ".."
        return _date(v)
    for tp in el.iter("{*}TimePeriod"):
        b = point(kid(tp, "beginPosition") if kid(tp, "beginPosition") is not None
                  else kid(tp, "begin", "TimeInstant", "timePosition"))
        e = point(kid(tp, "endPosition") if kid(tp, "endPosition") is not None
                  else kid(tp, "end", "TimeInstant", "timePosition"))
        return b, e
    for ti in el.iter("{*}TimeInstant"):
        p = point(kid(ti, "timePosition"))
        return p, None
    return None, None


def tf_temporal(values, ctx):
    out = []
    for ext in values:
        b, e = _gml_time(ext)
        if b and b != "..":
            out.append(b if e is None else "%s/%s" % (b, e or ".."))
        elif e and e != "..":
            out.append("../%s" % e)
    return out or None


def tf_vertical(values, ctx):
    """EX_VerticalExtent -> a dde:verticalExtent PropertyValue."""
    out = []
    for v in values:
        lo, hi = _num(txt(kid(v, "minimumValue"))), _num(txt(kid(v, "maximumValue")))
        if lo is None and hi is None:
            continue
        q = {"@type": ["schema:QuantitativeValue"]}
        if lo is not None:
            q["schema:minValue"] = lo
        if hi is not None:
            q["schema:maxValue"] = hi
        uom = next((e.get("uom") for e in v.iter() if e.get("uom")), None)
        if uom:
            q["schema:unitCode"] = uom
        pv = _pv("dde:verticalExtent", "Vertical Extent", q)
        crs = kid(v, "verticalCRSId", "MD_ReferenceSystem", "referenceSystemIdentifier",
                  "MD_Identifier")
        crs_code = txt(kid(crs, "code")) if crs is not None else None
        crs_href = href(kid(v, "verticalCRS")) or (href(kid(crs, "code")) if crs is not None else None)
        if crs_code or _url(crs_href):
            ref = {"@type": ["schema:DefinedTerm"],
                   "schema:name": crs_code or crs_href}
            if crs_code:
                ref["schema:termCode"] = crs_code
            if _url(crs_href):
                ref["schema:url"] = crs_href
            pv["schema:valueReference"] = ref
        out.append(pv)
    return out or None


# --- spatial representation and reference systems (ddeGeographicDataset) --------

SPATIAL_REP = {"vector", "grid", "textTable", "tin", "stereoModel", "video"}


def tf_spatialreptype(values, ctx):
    out = []
    for v in values:
        c = code(v)
        if ln(v) in ("MD_VectorSpatialRepresentation",):
            c = "vector"
        elif ln(v) in ("MD_GridSpatialRepresentation", "MD_Georectified",
                       "MD_Georeferenceable", "MI_Georectified", "MI_Georeferenceable"):
            c = "grid"
        if c in SPATIAL_REP and c not in [p["schema:value"] for p in out]:
            out.append(_pv("dde:spatialRepresentationType", "Spatial Representation Type", c))
    return out or None


def tf_resolution(values, ctx):
    out = []
    for v in values:
        den = txt(kid(v, "equivalentScale", "MD_RepresentativeFraction", "denominator"))
        dist = kid(v, "distance")
        if den:
            val = "1:%s" % den
        elif dist is not None and txt(dist):
            uom = next((e.get("uom") for e in dist.iter() if e.get("uom")), "")
            val = ("%s %s" % (txt(dist), uom.rsplit("#", 1)[-1].rsplit("/", 1)[-1])).strip()
        else:
            continue
        out.append(_pv("dde:spatialResolution", "Spatial Resolution", val))
    return out or None


def tf_refsys(values, ctx):
    """MD_ReferenceSystem -> dde:referenceSystemIdentifier (+ dde:referenceSystemType)."""
    out = []
    for v in values:
        ident = kid(v, "referenceSystemIdentifier", "MD_Identifier")
        if ident is None:
            ident = kid(v, "referenceSystemIdentifier", "RS_Identifier")
        if ident is None:
            continue
        c = txt(kid(ident, "code"))
        space = txt(kid(ident, "codeSpace"))
        if not c:
            continue
        val = c if (not space or ":" in c or c.startswith("http")) else "%s:%s" % (space, c)
        pv = _pv("dde:referenceSystemIdentifier", "Coordinate Reference System", val)
        u = _url(c) or _url(href(kid(ident, "code")))
        m = re.search(r"(?:EPSG\D*)(\d{4,6})$", val, re.I)
        if not u and m:
            u = "https://epsg.io/%s" % m.group(1)
        if u:
            pv["schema:url"] = u
        out.append(pv)
        rtype = code(kid(v, "referenceSystemType"))
        if rtype:
            out.append(_pv("dde:referenceSystemType", "Reference System Type", rtype))
    return out or None


# --- graphics, constraints ------------------------------------------------------

def tf_image(values, ctx):
    out = []
    for g in values:
        u = _url(txt(kid(g, "fileName"))) or _url(href(kid(g, "fileName"))) or \
            _url(txt(kid(g, "linkage", "CI_OnlineResource", "linkage")))
        if not u:
            continue
        img = {"@type": ["schema:ImageObject"], "schema:contentUrl": u}
        cap = txt(kid(g, "fileDescription"))
        if cap:
            img["schema:caption"] = cap
        fmt = txt(kid(g, "fileType"))
        if fmt:
            img["schema:encodingFormat"] = fmt
        out.append(img)
    return out or None


_LICENSE_URL = re.compile(r"(https?://(?:www\.)?(?:creativecommons\.org/(?:licenses|publicdomain)"
                          r"|opendatacommons\.org/licenses|spdx\.org/licenses|"
                          r"opensource\.org/licenses?)/[^\s\"'<>)]+)", re.I)


def _license_from(text, link=None, title=None):
    u = _url(link) if link else None
    m = _LICENSE_URL.search(text or "") if not u else None
    if m:
        u = m.group(1).rstrip(".,;")
    if u:
        lic = {"@type": ["schema:CreativeWork"], "schema:url": u}
        lic["schema:name"] = title or (text if text and len(text) < 200 else u)
        return lic
    return None


def tf_license(values, ctx):
    """MD_LegalConstraints -> licenses: a reference citation's online resource
    (19115-3), or a licence URL in otherConstraints / useLimitation / an Anchor.
    Text with no licence URL is left to conditionsOfAccess."""
    out = []
    for c in values:
        for ref in kids(c, "reference", "CI_Citation"):
            link = txt(kid(ref, "onlineResource", "CI_OnlineResource", "linkage"))
            lic = _license_from(txt(kid(ref, "title")), link, txt(kid(ref, "title")))
            if lic:
                out.append(lic)
        for el in kids(c, "otherConstraints") + kids(c, "useLimitation"):
            lic = _license_from(txt(el), href(el) if _LICENSE_URL.search(href(el) or "") else None)
            if lic and lic["schema:url"] not in [x["schema:url"] for x in out]:
                out.append(lic)
    return out or None


def tf_access(values, ctx):
    """Constraint texts and restriction codes -> conditionsOfAccess strings."""
    out = []
    for c in values:
        codes = [code(e) for e in kids(c, "accessConstraints") + kids(c, "useConstraints")]
        texts = [txt(e) for e in kids(c, "otherConstraints") + kids(c, "useLimitation")]
        for t in [x for x in texts if x] + ["%s: %s" % ("access" if kids(c, "accessConstraints")
                                                       else "use", x) for x in codes
                                            if x and x not in ("otherRestrictions",)]:
            if t not in out:
                out.append(t)
        cls = code(kid(c, "classification"))
        if cls:
            out.append("security classification: %s" % cls)
    return out or None


# --- supplemental information (GeoNetwork DDE convention) -------------------------

_SUPP_LABELS = ("FUNDING", "MEASUREMENT TECHNIQUE", "PUBLISHING PRINCIPLES",
                "VARIABLES MEASURED")
_SUPP_RE = re.compile(r"(?:^|\n|\s)(%s):\s*" % "|".join(_SUPP_LABELS))


def _supplemental(text):
    """{label: [items]} and the remaining text."""
    parts = _SUPP_RE.split(text or "")
    rest, out = parts[0].strip(), {}
    for label, body in zip(parts[1::2], parts[2::2]):
        out.setdefault(label, []).extend(
            x.strip() for x in re.split(r"\s*;\s*|\n", body.strip()) if x.strip())
    return out, rest


def _supp_text(values):
    raw = []
    for v in values:
        val = next((c for c in v if isinstance(c.tag, str) and ln(c) in _VALUE_TAGS), None)
        t = (val.text if val is not None else v.text) or ""
        if t.strip():
            raw.append(t.strip())
    return "\n".join(raw)


def tf_supp_funding(values, ctx):
    items = _supplemental(_supp_text(values))[0].get("FUNDING", [])
    out = []
    for it in items:
        m = re.match(r"^(.*?)\s*\(funder:\s*([^)]+)\)\s*(?:\[([^\]]+)\])?", it)
        name, funder, ident = (m.group(1), m.group(2), m.group(3)) if m else (it, None, None)
        g = {"@type": ["schema:MonetaryGrant"], "schema:name": name or it}
        if funder:
            g["schema:funder"] = {"@type": ["schema:Organization"], "schema:name": funder}
        if ident:
            g["schema:identifier"] = ident
        out.append(g)
    return out or None


def tf_supp_technique(values, ctx):
    return _supplemental(_supp_text(values))[0].get("MEASUREMENT TECHNIQUE") or None


def tf_supp_principles(values, ctx):
    return _supplemental(_supp_text(values))[0].get("PUBLISHING PRINCIPLES") or None


def tf_supp_rest(values, ctx):
    rest = _supplemental(_supp_text(values))[1]
    rest = re.sub(r"\s+", " ", rest).strip()
    return "Supplemental information: %s" % rest if rest else None


# --- online resources and distributions -------------------------------------------

OGC_SERVICE = re.compile(r"OGC[:\s-]*(WMS|WFS|WCS|WMTS|CSW|SOS|WPS)|ESRI:(REST|ArcGIS)|"
                         r"OPeNDAP|THREDDS|ERDDAP|(?:^|\W)(WMS|WFS|WCS|WMTS)(?:\W|$)|"
                         r"service=(WMS|WFS|WCS|WMTS|CSW)", re.I)


def _formats(dist):
    """Format names of an MD_Distribution (19115-3 formatSpecificationCitation
    title, or 19139 MD_Format name)."""
    out = []
    if dist is None:
        return out
    for f in kids(dist, "distributionFormat", "MD_Format"):
        name = txt(kid(f, "formatSpecificationCitation", "CI_Citation", "title")) or \
            txt(kid(f, "name"))
        if name and name not in out:
            out.append(name)
    return out


def _resource(o):
    """(url, name, description, protocol, function) of a CI_OnlineResource."""
    return (txt(kid(o, "linkage")), txt(kid(o, "name")), txt(kid(o, "description")),
            txt(kid(o, "protocol")), code(kid(o, "function")))


def _ancestor(el, name):
    for a in el.iterancestors():
        if ln(a) == name:
            return a
    return None


def tf_distribution(values, ctx):
    """Online resources of distributionInfo -> distributions.

    A service protocol (OGC:WMS, ESRI REST, OPeNDAP...) is a WebAPI; function
    information / search / browse links are left to relatedLink; an online
    resource whose linkage is the OGC 'inapplicable' nil URL is an archive
    member (hasPart) of the distribution before it (DDE convention)."""
    out = []
    for o in values:
        url, name, desc, proto, func = _resource(o)
        dist = _ancestor(o, "MD_Distribution")
        if url == INAPPLICABLE:
            if out:
                part = {"@type": ["schema:MediaObject"], "schema:name": name or "member"}
                if proto:
                    part["schema:encodingFormat"] = proto
                if desc:
                    part["schema:description"] = desc
                out[-1].setdefault("schema:hasPart", []).append(part)
            continue
        u = _url(url)
        if not u or func in ("information", "search", "browse", "fileAccess", "emailService",
                             "completeMetadata", "browseGraphic"):
            continue
        if OGC_SERVICE.search(" ".join(x for x in (proto, u, name) if x)):
            kind = (OGC_SERVICE.search(" ".join(x for x in (proto, u, name) if x)).group(0)
                    .strip(" =?&").split("=")[-1])
            out.append({
                "@type": ["schema:WebAPI"],
                "schema:name": name or "%s service" % kind,
                "schema:serviceType": proto or kind,
                "schema:termsOfService": "not specified",
                "schema:potentialAction": [{
                    "@type": ["schema:Action"],
                    "schema:name": name or "access",
                    "schema:target": {"@type": ["schema:EntryPoint"],
                                      "schema:urlTemplate": u,
                                      "schema:httpMethod": ["GET"]}}],
                **({"schema:description": desc} if desc else {})})
            continue
        d = {"@type": ["schema:DataDownload"], "schema:contentUrl": u}
        if name:
            d["schema:name"] = name
        if desc:
            d["schema:description"] = desc
        fmts = _formats(dist)
        if fmts:
            d["schema:encodingFormat"] = fmts
        size = txt(kid(_ancestor(o, "MD_DigitalTransferOptions"), "transferSize")) \
            if _ancestor(o, "MD_DigitalTransferOptions") is not None else None
        if _num(size):
            d["schema:contentSize"] = "%s MB" % size
        out.append(d)
    return out or None


def tf_landing(values, ctx):
    """The first information/browse link (or the citation's online resource) ->
    schema:url."""
    for o in values:
        url, _, _, _, func = _resource(o)
        u = _url(url)
        if u and func in (None, "information", "browse", "search"):
            return u
    return None


def tf_onlinelink(values, ctx):
    """Online resources not used as the url or a distribution -> relatedLink."""
    used = {ctx["doc"].get("schema:url")}
    for d in ctx["doc"].get("schema:distribution", []):
        used.add(d.get("schema:contentUrl"))
        for a in d.get("schema:potentialAction", []):
            used.add(a["schema:target"]["schema:urlTemplate"])
    out = []
    for o in values:
        url, name, desc, proto, func = _resource(o)
        u = _url(url)
        if u and u not in used:
            out.append(_link(func or proto or "related", u, name, desc))
            used.add(u)
    return out or None


def tf_metadatalinkage(values, ctx):
    for o in values:
        u = _url(txt(kid(o, "linkage")))
        if u:
            return u
    return None


def tf_associated(values, ctx):
    """MD_AssociatedResource / MD_AggregateInformation -> LinkRole with the
    association type as the relationship."""
    out = []
    for a in values:
        rel = code(kid(a, "associationType")) or "related"
        cit = kid(a, "name", "CI_Citation")
        if cit is None:
            cit = kid(a, "aggregateDataSetName", "CI_Citation")
        title = txt(kid(cit, "title")) if cit is not None else None
        u = None
        if cit is not None:
            u = _url(txt(kid(cit, "onlineResource", "CI_OnlineResource", "linkage")))
            for i in kids(cit, "identifier", "MD_Identifier"):
                u = u or _url(txt(kid(i, "code"))) or _url(href(kid(i, "code")))
        for i in kids(a, "aggregateDataSetIdentifier", "MD_Identifier") + \
                kids(a, "metadataReference", "CI_Citation", "identifier", "MD_Identifier"):
            u = u or _url(txt(kid(i, "code")))
        u = u or _url(href(kid(a, "metadataReference")))
        if u:
            out.append(_link(rel, u, title))
    return out or None


# --- content: variables -----------------------------------------------------------

def tf_variable(values, ctx):
    """MD_SampleDimension / MD_Band (coverage attributes), or gfc FC_FeatureAttribute
    -> variableMeasured PropertyValues."""
    out = []
    for v in values:
        name = (txt(kid(v, "sequenceIdentifier", "MemberName", "aName")) or
                txt(kid(v, "name")) or txt(kid(v, "memberName")) or
                txt(kid(v, "code")) or txt(kid(v, "description")))
        if not name:
            continue
        pv = {"@type": ["schema:PropertyValue"], "schema:name": name}
        desc = txt(kid(v, "description")) or txt(kid(v, "definition"))
        if desc and desc != name:
            pv["schema:description"] = desc
        unit = kid(v, "units")
        if unit is None:
            unit = kid(v, "valueMeasurementUnit")
        if unit is not None:
            ut = href(unit) or next((txt(e) for e in unit.iter("{*}identifier", "{*}name",
                                                            "{*}catalogSymbol") if txt(e)), None)
            if ut:
                pv["schema:unitText"] = ut
        lo, hi = _num(txt(kid(v, "minValue"))), _num(txt(kid(v, "maxValue")))
        if lo is not None:
            pv["schema:minValue"] = lo
        if hi is not None:
            pv["schema:maxValue"] = hi
        if name not in [p["schema:name"] for p in out]:
            out.append(pv)
    return out or None


# --- lineage and acquisition ------------------------------------------------------

def tf_lineage(values, ctx):
    """LI_Lineage -> prov:wasGeneratedBy: the statement as one activity, and each
    process step as another. Each is a cdifProv Action whose schema:actionProcess
    is its text (the procedure as performed); a step's sources are its
    schema:object."""
    out = []
    for lin in values:
        stmt = txt(kid(lin, "statement"))
        if stmt:
            out.append({"@type": ["schema:Action", "prov:Activity"], "schema:name": "Lineage",
                        "schema:actionProcess": stmt})
        for step in kids(lin, "processStep"):
            st = next((c for c in step if isinstance(c.tag, str)), None)
            if st is None:
                continue
            desc = txt(kid(st, "description"))
            if not desc:
                continue
            act = {"@type": ["schema:Action", "prov:Activity"],
                   "schema:name": desc if len(desc) <= 120 else desc[:117] + "...",
                   "schema:actionProcess": desc}
            when = _date_of(kid(st, "stepDateTime")) or _date_of(kid(st, "dateTime"))
            if not when and kid(st, "stepDateTime") is not None:
                b, _ = _gml_time(kid(st, "stepDateTime"))
                when = b if b and b != ".." else None
            if when:
                act["schema:endTime"] = when
            objs = []
            for src in kids(st, "source"):
                s = next((c for c in src if isinstance(c.tag, str)), None)
                d = txt(kid(s, "description")) if s is not None else None
                t = txt(kid(s, "sourceCitation", "CI_Citation", "title")) if s is not None else None
                if d or t:
                    objs.append({"@type": ["schema:CreativeWork"], "schema:name": t or d,
                                 **({"schema:description": d} if d and t else {})})
            if objs:
                act["schema:object"] = objs
            agents = [a for r in kids(st, "processor") for c in r if isinstance(c.tag, str)
                      for _, a in _parties(c)]
            if agents:
                act["schema:agent"] = agents[0]
            out.append(act)
    return out or None


def tf_source(values, ctx):
    """LI_Source (lineage-level) -> prov:wasDerivedFrom: a labelled link
    (CreativeWork with a url) when the source citation has an address, else the
    source as text ('title: description')."""
    out = []
    for s in values:
        d = txt(kid(s, "description"))
        cit = kid(s, "sourceCitation", "CI_Citation")
        t = txt(kid(cit, "title")) if cit is not None else None
        if not (d or t):
            continue
        u = None
        if cit is not None:
            u = _url(txt(kid(cit, "onlineResource", "CI_OnlineResource", "linkage")))
            for i in kids(cit, "identifier", "MD_Identifier"):
                u = u or _url(txt(kid(i, "code")))
        if u:
            w = {"@type": ["schema:CreativeWork"], "schema:name": t or d, "schema:url": u}
            if d and t:
                w["schema:description"] = d
        else:
            w = "%s: %s" % (t, d) if d and t and d != t else (t or d)
        if w not in out:
            out.append(w)
    return out or None


def _instrument(el, dde_type):
    cit = kid(el, "citation", "CI_Citation")
    name = (txt(kid(cit, "title")) if cit is not None else None) or \
        txt(kid(el, "identifier", "MD_Identifier", "code")) or txt(kid(el, "description"))
    if not name:
        return None
    if len(name) < 3:
        name = "%s %s" % (dde_type.split(":")[-1], name)
    inst = {"@type": ["schema:Product"], "schema:name": name,
            "schema:additionalType": [{"@id": dde_type}, {"@id": WIKIDATA_INSTRUMENT}]}
    d = txt(kid(el, "description"))
    if d and d != name:
        inst["schema:description"] = d
    return inst


def tf_acquisition(values, ctx):
    """MI_AcquisitionInformation (19115-2 gmi:, 19115-3 mac:) -> one acquisition
    activity whose instruments are typed dde:sensorType (instruments) and
    dde:platform (platforms), as ddeImagery describes."""
    insts = []
    for acq in values:
        for i in acq.iter("{*}MI_Instrument", "{*}MI_Sensor"):
            x = _instrument(i, "dde:sensorType")
            if x and x["schema:name"] not in [y["schema:name"] for y in insts]:
                insts.append(x)
        for p in acq.iter("{*}MI_Platform"):
            x = _instrument(p, "dde:platform")
            if x and x["schema:name"] not in [y["schema:name"] for y in insts]:
                insts.append(x)
    if not insts:
        return None
    return [{"@type": ["schema:Action", "prov:Activity"], "schema:name": "Data acquisition",
             "schema:instrument": insts}]


# --- service identification (DDEWebAPI) ---------------------------------------------

def tf_servicetype(values, ctx):
    for v in values:
        t = txt(v)
        if t:
            return _term(t, "dde:codelist/ServiceTypeCode" if t in _DDE_SERVICE_TYPES else None, t)
    return None


_DDE_SERVICE_TYPES = {"VocabularyService", "RegistryService", "DiscoveryService",
                      "ViewService", "OtherService"}


def tf_operation(values, ctx):
    """SV_OperationMetadata -> potentialAction. A missing connect point is the
    OGC nil URL; a missing name is 'missing'."""
    out = []
    for op in values:
        name = txt(kid(op, "operationName"))
        point = next((u for u in (_url(txt(kid(c, "linkage"))) for c in
                                  kids(op, "connectPoint", "CI_OnlineResource")) if u), None)
        if not name and not point:
            continue
        act = {"@type": ["schema:Action"], "schema:name": name or "missing",
               "schema:target": {"@type": ["schema:EntryPoint"],
                                 "schema:urlTemplate": point or NIL}}
        desc = txt(kid(op, "operationDescription"))
        if desc:
            act["schema:description"] = desc
        out.append(act)
    return out or None


def tf_operateson(values, ctx):
    out = []
    for v in values:
        ident = _url(href(v)) or v.get("uuidref") or txt(kid(v, "MD_DataIdentification",
                                                             "citation", "CI_Citation", "title"))
        if ident and ident not in out:
            out.append(ident)
    return out or None


def tf_termsofservice(values, ctx):
    """MD_StandardOrderProcess -> termsOfService text."""
    parts = []
    for p in values:
        for name in ("fees", "orderingInstructions", "turnaround"):
            t = txt(kid(p, name))
            if t:
                parts.append("%s: %s" % (name, t))
    return "; ".join(parts) or None


TRANSFORMS = {name[3:]: fn for name, fn in globals().items()
              if name.startswith("tf_") and callable(fn)}


# ---------------------------------------------------------------------------
# table application
# ---------------------------------------------------------------------------

def load_table(path):
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
    if target in ORDERED_TARGETS:
        container[target] = {"@list": value if isinstance(value, list) else [value]}
    else:
        container[target] = value[0] if isinstance(value, list) else value
    return True


def apply_table(rows, xml, ns, doc, record, encoding):
    changes, appended = [], []
    ctx = {"xml": xml, "ns": ns, "doc": doc, "record": record, "encoding": encoding,
           "geo_xpath": next((r["subject_xpath"] for r in rows
                              if r.get("transform") == "bbox"), "/..")}
    for rule in rows:
        transform = rule.get("transform", "")
        path = (rule.get("object_json_path") or "").strip()
        shaper = TRANSFORMS.get(transform)
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
            values = xml.xpath(xpath, namespaces=ns)
        except etree.XPathError as exc:
            raise ValueError("bad subject_xpath %r: %s" % (xpath, exc))
        if not values:
            continue
        ctx.update(rule=rule, target=target)
        shaped = shaper(values, ctx)
        if transform in ("describe", "supp_rest"):
            if shaped:
                appended.append((container, target, shaped))
                changes.append("%s -> %s" % (rule["subject_id"], rule["object_id"]))
            continue
        if _place_value(container, target, shaped):
            changes.append("%s -> %s" % (rule["subject_id"], rule["object_id"]))
    for container, target, text in appended:
        container[target] = (container[target] + "\n\n" + text
                             if container.get(target) else text)
    return changes


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------

def encoding_of(root):
    q = etree.QName(root)
    return ROOTS.get((q.namespace, q.localname))


def metadata_records(tree_root):
    """The ISO metadata elements in a document: the root itself, or every one
    inside a CSW GetRecords / GetRecordById response or other wrapper."""
    if encoding_of(tree_root):
        return [tree_root]
    return [e for e in tree_root.iter() if isinstance(e.tag, str) and encoding_of(e)]


def _slug(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text or "").strip("_")[:100]


def convert_iso_to_cdif(xml, rows=None, detect=True, catalog=None):
    """One ISO metadata element -> a CDIF JSON-LD dict."""
    encoding = encoding_of(xml)
    if not encoding:
        raise ValueError("not an ISO 19115-3 or 19139 metadata element: %s" % xml.tag)
    rows = rows if rows is not None else load_table(TABLES[encoding])
    ns = NS[encoding]
    doc = {"@context": dict(CDIF_CONTEXT), "@id": None, "@type": ["schema:Dataset"]}
    record = {}
    changes = apply_table(rows, xml, ns, doc, record, encoding)

    rec_ident = record.pop("schema:identifier", None)
    ident = doc.get("schema:identifier")

    # @id: a resolvable identifier of the resource, else the record's
    rid = ident.get("schema:url") if isinstance(ident, dict) else None
    if not rid and rec_ident:
        if _url(rec_ident):
            rid = _url(rec_ident)
        elif re.match(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?"
                      r"[0-9a-fA-F]{12}$", rec_ident):
            rid = "urn:uuid:%s" % rec_ident.lower()
        else:
            rid = "urn:iso19115:%s" % _slug(rec_ident)
    doc["@id"] = rid or "urn:iso19115:%s" % _slug(doc.get("schema:name") or "record")

    # A service record is a WebAPI (DDEWebAPI); the sentinels fill what CDIF's
    # webAPI block requires and the record lacks.
    if xml.xpath(next((r["subject_xpath"] for r in rows if r.get("transform") == "servicetype"),
                      "/.."), namespaces=ns) or doc.get("schema:serviceType"):
        doc["@type"] = ["schema:Dataset", "schema:WebAPI"]
        doc.setdefault("schema:serviceType", _term("unspecified service"))
        if not doc.get("schema:potentialAction"):
            doc["schema:potentialAction"] = [{
                "@type": ["schema:Action"], "schema:name": "missing",
                "schema:target": {"@type": ["schema:EntryPoint"], "schema:urlTemplate": NIL}}]
            changes.append("schema:potentialAction sentinel (no operations)")
        if not doc.get("schema:termsOfService"):
            doc["schema:termsOfService"] = "not specified"

    # --- what CDIF requires and ISO may not supply --------------------------------
    if not doc.get("schema:name"):
        doc["schema:name"] = "Untitled"
    if not doc.get("schema:identifier"):
        doc["schema:identifier"] = {"@type": ["schema:PropertyValue"],
                                    "schema:propertyID": "metadataIdentifier",
                                    "schema:value": rec_ident or doc["@id"]}
        changes.append("schema:identifier from the metadata identifier")
    if not doc.get("schema:dateModified"):
        d = doc.get("schema:datePublished") or doc.get("schema:dateCreated") or \
            record.get("schema:dateModified")
        doc["schema:dateModified"] = d or "1900-01-01"
        changes.append("schema:dateModified from %s" % ("another date" if d else "the sentinel"))
    if not doc.get("schema:url"):
        rid = doc["@id"]
        doc["schema:url"] = rid if rid.startswith("http") else \
            (record.get("schema:url") or NIL)
    if not doc.get("schema:license") and not doc.get("schema:conditionsOfAccess"):
        doc["schema:license"] = [{"@id": NIL}]
        changes.append("schema:license set to the OGC nil URI (no constraints)")

    # --- the catalog record --------------------------------------------------------
    std = "ISO 19115-3" if encoding == "iso19115-3" else (
        "ISO 19115-2 (19139 encoding)" if ln(xml) == "MI_Metadata" else "ISO 19139")
    rec_id = record.get("schema:url") or (doc["@id"] + "#metadata")
    subject = {
        "@id": rec_id,
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:name": "ISO metadata record for: %s" % doc["schema:name"][:120],
        "schema:about": {"@id": doc["@id"]},
        "dcterms:conformsTo": [{"@id": "https://w3id.org/cdif/core/2.0"}],
        "schema:description": "Converted from %s to CDIF by iso_to_cdif.py." % std,
    }
    if rec_ident:
        subject["schema:identifier"] = rec_ident
    subject.update({k: v for k, v in record.items() if k != "schema:url"})
    if catalog:
        subject["schema:includedInDataCatalog"] = catalog
    doc["schema:subjectOf"] = subject

    text = json.dumps(doc)
    for prefix, uri in OPTIONAL_PREFIXES.items():
        if '"%s:' % prefix in text:
            doc["@context"][prefix] = uri

    if detect and _HAVE_DETECT:
        try:
            uris = detect_conformance(doc)
        except Exception as exc:
            print("  WARNING: detect_conformance failed (%s)" % exc, file=sys.stderr)
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


def validate(path):
    tools = ROOT / "validation"
    cmd = [sys.executable, str(tools / "tools" / "FrameAndValidate.py"), str(path), "-v",
           "--schema", str(tools / "CDIFDiscoverySchema.json"),
           "--frame", str(tools / "CDIF-frame-2026.jsonld")]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return res.returncode == 0, (res.stdout + res.stderr).strip()


def main():
    ap = argparse.ArgumentParser(description="Convert ISO 19115-3 / 19139 XML to CDIF JSON-LD")
    ap.add_argument("input", nargs="+", help="ISO XML file(s), CSW responses, or URL(s)")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--catalog-name", help="name of the catalog the records came from")
    ap.add_argument("--catalog-url", help="URL of that catalog")
    ap.add_argument("--static-conformance", action="store_true",
                    help="declare core only instead of detecting conformance")
    ap.add_argument("--validate", action="store_true",
                    help="frame and validate each output against the CDIF Discovery schema")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    catalog = None
    if args.catalog_name or args.catalog_url:
        catalog = {"@type": ["schema:DataCatalog"]}
        if args.catalog_name:
            catalog["schema:name"] = args.catalog_name
        if args.catalog_url:
            catalog["schema:url"] = args.catalog_url
    tables = {k: load_table(v) for k, v in TABLES.items()}
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for source in args.input:
        for i, xml in enumerate(metadata_records(load_xml(source))):
            doc, changes = convert_iso_to_cdif(xml, tables[encoding_of(xml)],
                                               not args.static_conformance, catalog)
            stem = Path(source).stem if not source.startswith("http") else _slug(doc["@id"])
            out = out_dir / ("%s%s.jsonld" % (stem, "" if i == 0 else "_%d" % i))
            out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8", newline="\n")
            declared = [c["@id"].rsplit("/", 2)[-2] for c in
                        doc["schema:subjectOf"].get("dcterms:conformsTo", [])]
            line = "%-50s %s" % (out.name, "+".join(declared) or "no conformance")
            if args.verbose:
                line += "\n      " + "\n      ".join(changes)
            if args.validate:
                ok, report = validate(out)
                failures += not ok
                line += "  " + ("VALID" if ok else "INVALID")
                if not ok:
                    line += "\n" + "\n".join("      " + l for l in report.splitlines()[-15:])
            print(line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
cdif_to_iso.py - Convert CDIF JSON-LD records to ISO 19115-3 XML.

Each CDIF record (a schema:Dataset with its schema:subjectOf catalog record)
becomes one mdb:MD_Metadata. The mapping is a port of the DDE GeoNetwork's
convert/fromJsonCdif.xsl (iso19115-3.2018 schema plugin), written as the
table mappings/cdif-to-iso19115-3.sssom.tsv, which this script reads at run
time: each row names CDIF values by a path from the record root
(subject_json_path), the ISO element they become (object_xpath, a path from
mdb:MD_Metadata) and the shaper that builds it (transform).

A subject_json_path is a dot-separated list of CDIF property names (CURIEs as
CDIF writes them, schema:name, prov:wasGeneratedBy); '[*]' takes every item and
'@id' / '@type' the node's own keywords. A value's JSON-LD list (an array or an
@list) is always expanded into its items, and a node that is only an @id
reference to another node of the document's @graph is replaced by that node.

The object_xpath names where in the record the shaper's elements go. The record
is assembled in the order ISO 19115-3's XML Schema requires (SLOTS below), not
in table order; within one location, rows run in table order. A location that
holds one element (SINGLE) keeps the first row's; the others accumulate. The
pieces for mri:supplementalInformation are joined into one text.

A transform may carry arguments in parentheses, party(author,first): the
shaper tf_party is called with ('author', 'first').

Usage:
    python ISO19115/cdif_to_iso.py record.jsonld -o out/
    python ISO19115/cdif_to_iso.py DataCite/cdifJSON-LD/ -o out/ --xsd ../../geonetwork/core-geonetwork/schemas/iso19115-3.2018/src/main/plugin/iso19115-3.2018/schema.xsd
"""

import argparse
import csv
import json
import re
import sys
import uuid
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TABLE = ROOT / "mappings" / "cdif-to-iso19115-3.sssom.tsv"
NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
INAPPLICABLE = "http://www.opengis.net/def/nil/OGC/0/inapplicable"
DATE_SENTINEL = "1900-01-01"
CODELISTS = "https://schemas.isotc211.org/schemas/19115/resources/Codelists/cat/codelists.xml"
DEFAULT_XSD = (ROOT.parent.parent / "geonetwork" / "core-geonetwork" / "schemas" / "iso19115-3.2018"
               / "src" / "main" / "plugin" / "iso19115-3.2018" / "schema.xsd")

NS = {
    "mdb": "http://standards.iso.org/iso/19115/-3/mdb/2.0",
    "cit": "http://standards.iso.org/iso/19115/-3/cit/2.0",
    "mri": "http://standards.iso.org/iso/19115/-3/mri/1.0",
    "mrd": "http://standards.iso.org/iso/19115/-3/mrd/1.0",
    "mrl": "http://standards.iso.org/iso/19115/-3/mrl/2.0",
    "mrc": "http://standards.iso.org/iso/19115/-3/mrc/2.0",
    "mco": "http://standards.iso.org/iso/19115/-3/mco/1.0",
    "mcc": "http://standards.iso.org/iso/19115/-3/mcc/1.0",
    "lan": "http://standards.iso.org/iso/19115/-3/lan/1.0",
    "gex": "http://standards.iso.org/iso/19115/-3/gex/1.0",
    "gco": "http://standards.iso.org/iso/19115/-3/gco/1.0",
    "mdq": "http://standards.iso.org/iso/19157/-2/mdq/1.0",
    "gfc": "http://standards.iso.org/iso/19110/gfc/1.1",
    "cat": "http://standards.iso.org/iso/19115/-3/cat/1.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "xlink": "http://www.w3.org/1999/xlink",
}

DI = "mdb:identificationInfo/mri:MD_DataIdentification"
CI = DI + "/mri:citation/cit:CI_Citation"
EX = DI + "/mri:extent/gex:EX_Extent"
DS = "mdb:distributionInfo/mrd:MD_Distribution"
LI = "mdb:resourceLineage/mrl:LI_Lineage"

# Every location a row can write to, in the order the XML Schema requires.
SLOTS = [
    "mdb:metadataIdentifier", "mdb:defaultLocale", "mdb:metadataScope", "mdb:contact",
    "mdb:dateInfo", "mdb:metadataStandard", "mdb:metadataProfile", "mdb:metadataLinkage",
    CI + "/cit:title", CI + "/cit:date", CI + "/cit:edition", CI + "/cit:identifier",
    CI + "/cit:citedResponsibleParty",
    DI + "/mri:abstract", DI + "/mri:pointOfContact",
    EX + "/gex:geographicElement", EX + "/gex:temporalElement",
    DI + "/mri:descriptiveKeywords", DI + "/mri:resourceConstraints", DI + "/mri:associatedResource",
    DI + "/mri:defaultLocale", DI + "/mri:supplementalInformation",
    "mdb:contentInfo",
    DS + "/mrd:distributionFormat", DS + "/mrd:distributor", DS + "/mrd:transferOptions",
    "mdb:dataQualityInfo",
    LI + "/mrl:statement", LI + "/mrl:scope", LI + "/mrl:source", LI + "/mrl:processStep",
]
SINGLE = {"mdb:metadataIdentifier", "mdb:defaultLocale", "mdb:metadataScope", "mdb:metadataStandard",
          CI + "/cit:title", CI + "/cit:edition", DI + "/mri:abstract", DI + "/mri:defaultLocale",
          "mdb:contentInfo", "mdb:dataQualityInfo", LI + "/mrl:statement", LI + "/mrl:scope"}
JOINED = {DI + "/mri:supplementalInformation"}


# ---------------------------------------------------------------------------
# CDIF access
# ---------------------------------------------------------------------------

def as_list(v):
    if v is None:
        return []
    if isinstance(v, dict) and "@list" in v:
        return as_list(v["@list"])
    return v if isinstance(v, list) else [v]


def text(v):
    """A value's text: a string, a number, or a node's @value / @id."""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (str, int, float)):
        return " ".join(str(v).split())
    if isinstance(v, dict):
        for k in ("@value", "@id"):
            if k in v:
                return text(v[k])
    return ""


def types(node):
    return [text(t) for t in as_list(node.get("@type"))] if isinstance(node, dict) else []


def field(node, key):
    """The first non-empty text of node[key]."""
    if not isinstance(node, dict):
        return ""
    for v in as_list(node.get(key)):
        t = text(v)
        if t:
            return t
    return ""


class Doc:
    """The record root, with @graph references resolved."""

    def __init__(self, doc):
        graph = doc.get("@graph") if isinstance(doc, dict) else None
        nodes = graph if isinstance(graph, list) else [doc]
        self.index = {n["@id"]: n for n in nodes if isinstance(n, dict) and n.get("@id")}
        self.root = self._root(nodes)

    @staticmethod
    def _is_record(node):
        marks = [text(x) for k in ("@type", "schema:additionalType") for x in as_list(node.get(k))]
        return "dcat:CatalogRecord" in marks

    def _root(self, nodes):
        for n in nodes:
            if isinstance(n, dict) and isinstance(n.get("schema:subjectOf"), dict):
                return n
        for n in nodes:
            if isinstance(n, dict) and "schema:Dataset" in types(n) and not self._is_record(n):
                return n
        return nodes[0]

    def resolve(self, v, depth=0):
        if isinstance(v, dict) and set(v) == {"@id"} and v["@id"] in self.index and depth < 5:
            return self.resolve(self.index[v["@id"]], depth + 1)
        return v

    def select(self, path, node=None):
        current = [self.root if node is None else node]
        for seg in path.split("."):
            seg = seg[:-3] if seg.endswith("[*]") else seg
            nxt = []
            for n in current:
                n = self.resolve(n)
                if isinstance(n, dict) and seg in n:
                    nxt.extend(self.resolve(x) for x in as_list(n[seg]))
            current = nxt
        return [v for v in current if v not in (None, "", [], {})]


# ---------------------------------------------------------------------------
# ISO element builders
# ---------------------------------------------------------------------------

def qn(name):
    prefix, local = name.split(":")
    return "{%s}%s" % (NS[prefix], local)


def E(name, *children, **attrs):
    el = etree.Element(qn(name))
    for k, v in attrs.items():
        el.set(qn(k.replace("__", ":")) if "__" in k else k, v)
    for c in children:
        if c is None:
            continue
        if isinstance(c, str):
            el.text = (el.text or "") + c
        else:
            el.append(c)
    return el


def cs(t):
    return E("gco:CharacterString", t)


def code(list_name, value, prefix="cit"):
    return E("%s:%s" % (prefix, list_name), codeList="%s#%s" % (CODELISTS, list_name),
             codeListValue=value)


def date_el(value):
    """gco:DateTime for a value with a time, else gco:Date; None for no date."""
    t = text(value)
    if not t or t.startswith(DATE_SENTINEL):
        return None
    if "T" in t:
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", t):
            return E("gco:DateTime", t)
        t = t.split("T")[0]
    if re.match(r"^\d{4}(-\d{2}(-\d{2})?)?$", t):
        return E("gco:Date", t)
    return None


def ci_date(value, kind):
    d = date_el(value)
    if d is None:
        return None
    return E("cit:CI_Date", E("cit:date", d), E("cit:dateType", code("CI_DateTypeCode", kind)))


def online(url, name=None, description=None, function=None, protocol=None):
    return E("cit:CI_OnlineResource",
             E("cit:linkage", cs(url)),
             E("cit:protocol", cs(protocol)) if protocol else None,
             E("cit:name", cs(name)) if name else None,
             E("cit:description", cs(description)) if description else None,
             E("cit:function", code("CI_OnLineFunctionCode", function)) if function else None)


def md_identifier(value, space=None):
    return E("mcc:MD_Identifier", E("mcc:code", cs(value)),
             E("mcc:codeSpace", cs(space)) if space else None)


def ident_parts(v):
    """(code, codeSpace) of a CDIF identifier: a PropertyValue or a string."""
    if isinstance(v, dict):
        return field(v, "schema:value") or field(v, "schema:url") or text(v), field(v, "schema:propertyID")
    return text(v), ""


def _is_http(t):
    return bool(re.match(r"^(https?|ftp)://", t or ""))


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------

def _email(agent):
    return field(agent, "schema:email") or next(
        (field(c, "schema:email") for c in as_list(agent.get("schema:contactPoint"))
         if field(c, "schema:email")), "")


def _contact_info(agent):
    email = _email(agent)
    if not email:
        return None
    return E("cit:contactInfo", E("cit:CI_Contact", E("cit:address", E("cit:CI_Address",
             E("cit:electronicMailAddress", cs(email))))))


def _party_id(agent, with_space):
    ids = as_list(agent.get("schema:identifier"))
    if not ids:
        return None
    value, space = ident_parts(ids[0])
    if not value:
        return None
    return E("cit:partyIdentifier", md_identifier(value, space if with_space else None))


def party(agent):
    """A CDIF Person / Organization -> cit:party content.

    As fromJsonCdif.xsl builds it, except that a Person with no affiliation is a
    CI_Individual (the stylesheet named an organisation after the person) and an
    agent with no type is a CI_Organisation alone (the stylesheet added an
    individual of the same name)."""
    if not isinstance(agent, dict):
        return E("cit:CI_Organisation", E("cit:name", cs(text(agent))))
    t = " ".join(types(agent))
    name = field(agent, "schema:name")
    if "Person" in t:
        affiliation = next((field(a, "schema:name") for a in as_list(agent.get("schema:affiliation"))
                            if isinstance(a, dict) and field(a, "schema:name")), "")
        if not affiliation:
            return E("cit:CI_Individual", E("cit:name", cs(name)), _contact_info(agent),
                     _party_id(agent, True))
        return E("cit:CI_Organisation", E("cit:name", cs(affiliation)), _contact_info(agent),
                 E("cit:individual", E("cit:CI_Individual", E("cit:name", cs(name)),
                                       _party_id(agent, True))))
    return E("cit:CI_Organisation", E("cit:name", cs(name)), _contact_info(agent),
             _party_id(agent, False))


def responsibility(agent, role):
    return E("cit:CI_Responsibility", E("cit:role", code("CI_RoleCode", role)),
             E("cit:party", party(agent)))


ROLE_CODES = {
    "contributor": "contributor", "editor": "editor", "funder": "funder", "funding agency": "funder",
    "principal investigator": "principalInvestigator", "principalinvestigator": "principalInvestigator",
    "pi": "principalInvestigator", "publisher": "publisher", "author": "author", "creator": "author",
    "custodian": "custodian", "distributor": "distributor", "originator": "originator",
    "point of contact": "pointOfContact", "pointofcontact": "pointOfContact", "contact": "pointOfContact",
    "processor": "processor", "resource provider": "resourceProvider", "resourceprovider": "resourceProvider",
    "user": "user", "sponsor": "sponsor", "collaborator": "collaborator", "stakeholder": "stakeholder",
    "coauthor": "coAuthor", "co-author": "coAuthor", "rights holder": "rightsHolder",
    "rightsholder": "rightsHolder", "mediator": "mediator",
}

ASSOCIATION_CODES = {
    "ispartof": "largerWorkCitation", "is part of": "largerWorkCitation",
    "haspart": "partOfSeamlessDatabase", "has part": "partOfSeamlessDatabase",
    "references": "crossReference", "isreferencedby": "crossReference",
    "is referenced by": "crossReference", "isbasedon": "dependency", "is based on": "dependency",
    "isbasisfor": "revisionOf", "is basis for": "revisionOf",
    "issupplementto": "isComposedOf", "is supplement to": "isComposedOf",
    "issupplementedby": "isComposedOf", "is supplemented by": "isComposedOf",
}

LANGUAGES = {
    "en": "eng", "fr": "fra", "de": "deu", "es": "spa", "it": "ita", "pt": "por", "nl": "nld",
    "ru": "rus", "zh": "zho", "ja": "jpn", "ko": "kor", "ar": "ara", "pl": "pol", "sv": "swe",
    "no": "nor", "da": "dan", "fi": "fin", "el": "gre", "cs": "cze", "tr": "tur", "hu": "hun",
    "ro": "rum", "uk": "ukr", "vi": "vie", "th": "tha", "hi": "hin",
}


# ---------------------------------------------------------------------------
# shapers: tf_<transform>(values, ctx, *args) -> element(s), or text for a JOINED slot
# ---------------------------------------------------------------------------

def tf_text(values, ctx):
    t = next((text(v) for v in values if text(v)), "")
    return E(ctx["tag"], cs(t)) if t else None


def tf_recordid(values, ctx):
    value, space = ident_parts(values[0])
    return E("mdb:metadataIdentifier", md_identifier(value, space or None)) if value else None


def tf_uuid(values, ctx):
    rid = text(values[0])
    if not rid:
        return None
    return E("mdb:metadataIdentifier", md_identifier(str(uuid.uuid5(uuid.NAMESPACE_URL, rid)),
                                                    "urn:uuid"))


def locale(lang="eng"):
    return E("lan:PT_Locale",
             E("lan:language", code("LanguageCode", lang, "lan")),
             E("lan:characterEncoding", code("MD_CharacterSetCode", "utf8", "lan")))


def tf_locale(values, ctx):
    lc = text(values[0]).lower()
    lang = lc if len(lc) == 3 else LANGUAGES.get(lc.split("-")[0], "eng")
    return E(ctx["tag"], locale(lang))


def tf_scope(values, ctx):
    t = " ".join(text(v) for v in values)
    if "Dataset" in t:
        scope = "dataset"
    elif "Service" in t or "WebAPI" in t:
        scope = "service"
    elif "SoftwareApplication" in t or "SoftwareSourceCode" in t:
        scope = "software"
    else:
        scope = "dataset"
    return E("mdb:metadataScope", E("mdb:MD_MetadataScope",
             E("mdb:resourceScope", code("MD_ScopeCode", scope, "mcc"))))


def tf_party(values, ctx, role="author", which=None):
    agents = [v for v in values if isinstance(v, dict) and field(v, "schema:name")]
    if which == "first":
        agents = agents[:1]
    return [E(ctx["tag"], responsibility(a, role)) for a in agents]


def tf_contributor(values, ctx):
    out = []
    for c in values:
        if not isinstance(c, dict):
            continue
        role = "contributor"
        names = [text(r) for r in as_list(c.get("schema:roleName")) if text(r)]
        if names:
            role = ROLE_CODES.get(names[0].lower(), "contributor")
        inner = [ctx["doc"].resolve(x) for x in as_list(c.get("schema:contributor"))]
        for agent in inner or ([c] if field(c, "schema:name") and not names else []):
            if isinstance(agent, dict) and field(agent, "schema:name"):
                out.append(E(ctx["tag"], responsibility(agent, role)))
    return out


def tf_date(values, ctx, kind):
    out = []
    for v in values:
        d = ci_date(v, kind)
        if d is not None:
            out.append(E(ctx["tag"], d))
    return out[:1]


def tf_profile(values, ctx):
    out = []
    for p in values:
        title = field(p, "schema:name") or text(p)
        url = field(p, "schema:url") or (text(p) if _is_http(text(p)) else "")
        if title:
            out.append(E("mdb:metadataProfile", E("cit:CI_Citation", E("cit:title", cs(title)),
                         E("cit:onlineResource", online(url)) if url else None)))
    return out


def tf_cataloglink(values, ctx):
    out = []
    for c in values:
        url = field(c, "schema:url") or (text(c) if _is_http(text(c)) else "")
        if url:
            out.append(E("mdb:metadataLinkage", online(url, field(c, "schema:name") or None,
                                                       function="completeMetadata")))
    return out


def tf_identifier(values, ctx):
    out = []
    for v in values:
        value, space = ident_parts(v)
        if value:
            out.append(E("cit:identifier", md_identifier(value, space or None)))
    return out


def tf_sameas(values, ctx):
    return [E("cit:identifier", md_identifier(text(v), "sameAs")) for v in values if text(v)]


def _box(geo):
    """(W, E, S, N) of a GeoShape box or a GeoCoordinates point, or None."""
    box = field(geo, "schema:box")
    if box:
        parts = re.split(r"[\s,]+", box.strip())
        if len(parts) == 4:
            try:
                s, w, n, e = (float(x) for x in parts)
                return parts[1], parts[3], parts[0], parts[2]
            except ValueError:
                return None
        return None
    lat, lon = field(geo, "schema:latitude"), field(geo, "schema:longitude")
    try:
        float(lat), float(lon)
    except ValueError:
        return None
    return lon, lon, lat, lat


def tf_bbox(values, ctx):
    out = []
    for place in values:
        for geo in as_list(place.get("schema:geo")) if isinstance(place, dict) else []:
            b = _box(geo) if isinstance(geo, dict) else None
            if not b:
                continue
            w, e, s, n = b
            out.append(E("gex:geographicElement", E("gex:EX_GeographicBoundingBox",
                         E("gex:westBoundLongitude", E("gco:Decimal", w)),
                         E("gex:eastBoundLongitude", E("gco:Decimal", e)),
                         E("gex:southBoundLatitude", E("gco:Decimal", s)),
                         E("gex:northBoundLatitude", E("gco:Decimal", n)))))
    return out


def tf_geodescription(values, ctx):
    out = []
    for place in values:
        if not isinstance(place, dict):
            place = {"schema:name": place}
        term = ctx["doc"].resolve(next(iter(as_list(place.get("schema:name"))), ""))
        if isinstance(term, dict):                       # a DefinedTerm naming the place
            name, space = field(term, "schema:name"), field(term, "schema:termCode")
        else:
            name, space = text(term), field(place, "schema:termCode") or text(place.get("@id"))
        if not name:
            continue
        if space.startswith("_:"):
            space = ""
        out.append(E("gex:geographicElement", E("gex:EX_GeographicDescription",
                     E("gex:geographicIdentifier", md_identifier(name, space or None)))))
    return out


def _gml_id(ctx, stem):
    ctx["ids"] = ctx.get("ids", 0) + 1
    return "%s-%d" % (stem, ctx["ids"])


def _instant(ctx, uri, name, num, trs):
    pos = E("gml:timePosition", num)
    if trs:
        pos.set("frame", trs)
    return E("gml:TimeInstant",
             E("gml:identifier", uri, codeSpace="http://www.ddeworld.org/resource/") if uri else None,
             E("gml:name", name) if name else None, pos, gml__id=_gml_id(ctx, "temporal-instant"))


def _position(tag, value):
    el = E(tag)
    if value and value != "..":
        el.text = value
    else:
        el.set("indeterminatePosition", "unknown")
    return el


def _xsd_interval(t, ctx):
    """(start, end) of a time:ProperInterval whose instants carry XSD dates, or None."""
    out = []
    for key in ("time:hasBeginning", "time:hasEnd"):
        node = ctx["doc"].resolve(next(iter(as_list(t.get(key))), {}))
        out.append(next((field(node, k) for k in ("time:inXSDDate", "time:inXSDDateTimeStamp",
                                                   "time:inXSDDateTime", "time:inXSDgYear")
                         if field(node, k)), "") if isinstance(node, dict) else "")
    return tuple(out) if any(out) else None


def tf_temporal(values, ctx):
    out = []
    for t in values:
        if isinstance(t, dict) and _xsd_interval(t, ctx):           # time:Instants with dates
            start, end = _xsd_interval(t, ctx)
            period = E("gml:TimePeriod", _position("gml:beginPosition", start),
                       _position("gml:endPosition", end), gml__id=_gml_id(ctx, "temporal-extent"))
        elif isinstance(t, dict):                                   # geologic time:ProperInterval
            ends = {}
            for side, key in (("begin", "time:hasBeginning"), ("end", "time:hasEnd")):
                node = ctx["doc"].resolve(next(iter(as_list(t.get(key))), {}))
                pos = ctx["doc"].resolve(next(iter(as_list(node.get("time:inTimePosition"))), {})) \
                    if isinstance(node, dict) else {}
                ends[side] = (field(t, "time:intervalStartedBy" if side == "begin" else "time:intervalFinishedBy"),
                              field(node, "schema:name"),
                              field(pos, "time:numericPosition") or field(pos, "schema:value"),
                              field(pos, "time:hasTRS"))
            if not any(any(e) for e in ends.values()):
                continue
            period = E("gml:TimePeriod", E("gml:begin", _instant(ctx, *ends["begin"])),
                       E("gml:end", _instant(ctx, *ends["end"])),
                       gml__id=_gml_id(ctx, "temporal-extent-geologic"))
        else:
            value = text(t)
            if not value:
                continue
            start, _, end = value.partition("/")
            if not _:
                end = start                                          # an instant
            period = E("gml:TimePeriod", _position("gml:beginPosition", start.strip()),
                       _position("gml:endPosition", end.strip()),
                       gml__id=_gml_id(ctx, "temporal-extent"))
        out.append(E("gex:temporalElement", E("gex:EX_TemporalExtent", E("gex:extent", period))))
    return out


def tf_keywords(values, ctx):
    groups = {}
    for k in values:
        if isinstance(k, dict):
            name = field(k, "schema:name") or text(k)
            vocab = field(k, "schema:inDefinedTermSet")
        else:
            name, vocab = text(k), ""
        if name:
            groups.setdefault(vocab, [])
            if name not in groups[vocab]:
                groups[vocab].append(name)
    out = []
    for vocab, words in groups.items():
        out.append(E("mri:descriptiveKeywords", E("mri:MD_Keywords",
                     *[E("mri:keyword", cs(w)) for w in words],
                     E("mri:type", code("MD_KeywordTypeCode", "theme", "mri")),
                     E("mri:thesaurusName", E("cit:CI_Citation", E("cit:title", cs(vocab)),
                       E("cit:onlineResource", online(vocab)) if _is_http(vocab) else None))
                     if vocab else None)))
    return out


def _restriction(tag):
    return E(tag, E("mco:MD_RestrictionCode", codeList="%s#MD_RestrictionCode" % CODELISTS,
                    codeListValue="otherRestrictions"))


def tf_license(values, ctx):
    out = []
    for lic in values:
        if isinstance(lic, dict):
            url = field(lic, "schema:url") or (text(lic.get("@id")) if _is_http(text(lic.get("@id"))) else "")
            title = field(lic, "schema:name") or field(lic, "schema:text") or url or text(lic)
        else:
            title = text(lic)
            url = title if _is_http(title) else ""
        if not title or title == NIL or url == NIL:
            continue
        out.append(E(ctx["tag"], E("mco:MD_LegalConstraints",
                     E("mco:reference", E("cit:CI_Citation", E("cit:title", cs(title)),
                       E("cit:onlineResource", online(url)) if url else None)),
                     _restriction("mco:accessConstraints"), _restriction("mco:useConstraints"))))
    return out


def tf_access(values, ctx):
    out = []
    for v in values:
        t = field(v, "schema:name") or field(v, "schema:description") or text(v) \
            if isinstance(v, dict) else text(v)
        if t:
            out.append(E(ctx["tag"], E("mco:MD_LegalConstraints",
                         _restriction("mco:accessConstraints"),
                         E("mco:otherConstraints", cs(t)))))
    return out


def tf_associated(values, ctx):
    out = []
    for link in values:
        if not isinstance(link, dict):
            continue
        rel = ctx["doc"].resolve(next(iter(as_list(link.get("schema:linkRelationship"))), ""))
        rel_text = (field(rel, "schema:name") or field(rel, "schema:termCode")) if isinstance(rel, dict) \
            else text(rel)
        lc = rel_text.lower()
        assoc = ASSOCIATION_CODES.get(lc, "stereoMate" if "stereo" in lc else "crossReference")
        target = ctx["doc"].resolve(next(iter(as_list(link.get("schema:target"))), ""))
        if isinstance(target, dict):
            url = field(target, "schema:url") or text(target.get("@id"))
            tname = field(target, "schema:name")
        else:
            url, tname = text(target), ""
        title = field(link, "schema:name") or tname or rel_text or url
        if not title:
            continue
        out.append(E(ctx["tag"], E("mri:MD_AssociatedResource",
                     E("mri:name", E("cit:CI_Citation", E("cit:title", cs(title)),
                       E("cit:onlineResource", online(url)) if _is_http(url) else None)),
                     E("mri:associationType", code("DS_AssociationTypeCode", assoc, "mri")))))
    return out


def tf_fundingtext(values, ctx):
    parts = []
    for g in values:
        if not isinstance(g, dict):
            continue
        p = field(g, "schema:name") or field(g, "schema:description")
        funder = next((field(f, "schema:name") for f in as_list(g.get("schema:funder"))
                       if isinstance(f, dict) and field(f, "schema:name")), "")
        if funder:
            p += " (funder: %s)" % funder
        ids = as_list(g.get("schema:identifier"))
        if ids and ident_parts(ids[0])[0]:
            p += " [%s]" % ident_parts(ids[0])[0]
        if p.strip():
            parts.append(p.strip())
    return "FUNDING: " + "; ".join(parts) if parts else None


def tf_techniquetext(values, ctx):
    parts = []
    for m in values:
        if isinstance(m, dict):
            p = field(m, "schema:name")
            if p:
                ids = as_list(m.get("schema:identifier"))
                if ids and ident_parts(ids[0])[0]:
                    p += " [%s]" % ident_parts(ids[0])[0]
            else:
                p = field(m, "schema:description") or text(m)
        else:
            p = text(m)
        if p:
            parts.append(p)
    return "MEASUREMENT TECHNIQUE: " + "; ".join(parts) if parts else None


def tf_principlestext(values, ctx):
    parts = [field(p, "schema:name") or text(p) if isinstance(p, dict) else text(p) for p in values]
    parts = [p for p in parts if p]
    return "PUBLISHING PRINCIPLES: " + "; ".join(parts) if parts else None


def tf_variabletext(values, ctx):
    parts = []
    for v in values:
        name = field(v, "schema:name") if isinstance(v, dict) else ""
        if not name:
            continue
        unit, lo, hi = field(v, "schema:unitText"), field(v, "schema:minValue"), field(v, "schema:maxValue")
        if unit:
            name += " (%s)" % unit
        if lo or hi:
            name += " [%s..%s]" % (lo, hi)
        parts.append(name)
    return "VARIABLES MEASURED: " + "; ".join(parts) if parts else None


def tf_featurecatalogue(values, ctx):
    root = ctx["doc"].root
    attrs = []
    for v in values:
        if not isinstance(v, dict):
            name, v = text(v), {}
        else:
            name = field(v, "schema:name") or text(v)
        if not name:
            continue
        unit_code, unit_text = field(v, "schema:unitCode"), field(v, "schema:unitText")
        # gco:UomIdentifier is a symbol with no space or colon, or a URI
        uom = next((u for u in (unit_text, unit_code) if u and (_is_http(u) or not re.search(r"[:\s]", u))), "")
        facets = "; ".join("%s: %s" % (k, x) for k, x in (
            ("minValue", field(v, "schema:minValue")), ("maxValue", field(v, "schema:maxValue")),
            ("unitCode", unit_code), ("unitText", unit_text if unit_text != uom else "")) if x)
        prop = ctx["doc"].resolve(next(iter(as_list(v.get("schema:propertyID"))), ""))
        prop_code = (text(prop.get("@id")) or field(prop, "schema:value")) if isinstance(prop, dict) else text(prop)
        dtype = field(v, "cdif:hasIntendedDataType") or field(v, "cdi:intendedDataType")
        attrs.append(E("gfc:carrierOfCharacteristics", E("gfc:FC_FeatureAttribute",
            E("gfc:constrainedBy", E("gfc:FC_Constraint", E("gfc:description", cs(facets)))) if facets else None,
            E("gfc:memberName", name),
            E("gfc:definition", cs(field(v, "schema:description"))) if field(v, "schema:description") else None,
            E("gfc:cardinality", cs("0..*")),
            E("gfc:code", cs(prop_code)) if prop_code else None,
            E("gfc:valueMeasurementUnit", E("gco:UomIdentifier", uom)) if uom else None,
            E("gfc:valueType", E("gco:TypeName", E("gco:aName", cs(dtype)))) if dtype else None)))
    if not attrs:
        return None
    version_date = next((d for d in (field(root, "schema:datePublished"), field(root, "schema:dateModified"))
                         if d and not d.startswith(DATE_SENTINEL)), DATE_SENTINEL)[:10]
    name = field(root, "schema:name") or "dataset"
    return E("mdb:contentInfo", E("mrc:MD_FeatureCatalogue", E("mrc:featureCatalogue",
             E("gfc:FC_FeatureCatalogue",
               E("cat:name", cs(name)), E("cat:scope", cs("dataset")),
               E("cat:versionNumber", cs(field(root, "schema:version") or "1.0")),
               E("cat:versionDate", E("gco:Date", version_date)),
               E("gfc:producer"),
               E("gfc:featureType", E("gfc:FC_FeatureType", E("gfc:typeName", name),
                 E("gfc:isAbstract", E("gco:Boolean", "false")), *attrs,
                 E("gfc:featureCatalogue")))))))


def tf_format(values, ctx):
    seen = []
    for v in values:
        t = text(v)
        if t and t not in seen:
            seen.append(t)
    return [E("mrd:distributionFormat", E("mrd:MD_Format", E("mrd:formatSpecificationCitation",
              E("cit:CI_Citation", E("cit:title", cs(t)))))) for t in seen]


def _megabytes(size, unit):
    try:
        n = float(size)
    except (TypeError, ValueError):
        return None
    u = (unit or "").strip().lower()
    factor = {"bytes": 1 / 1048576, "byte": 1 / 1048576, "b": 1 / 1048576,
              "kb": 1 / 1024, "kilobytes": 1 / 1024, "kilobyte": 1 / 1024,
              "gb": 1024, "gigabytes": 1024, "gigabyte": 1024,
              "tb": 1048576, "terabytes": 1048576, "terabyte": 1048576}.get(u, 1)
    return ("%.6f" % (n * factor)).rstrip("0").rstrip(".") or "0"


def tf_transfer(values, ctx):
    out = []
    for d in values:
        if not isinstance(d, dict):
            continue
        url = field(d, "schema:contentUrl")
        if not url:
            continue
        size = field(d, "cdif:fileSize") or field(d, "cdi:fileSize")
        mb = _megabytes(size, field(d, "cdif:fileSizeUofM") or field(d, "cdi:fileSizeUofM")) if size else None
        scheme = url.split(":", 1)[0].lower()
        protocol = scheme if scheme in ("https", "http", "ftp") else "https"
        desc = field(d, "schema:description")
        charset = field(d, "cdi:characterSet") or field(d, "cdif:characterSet")
        if charset:
            desc = (desc + " " if desc else "") + "[characterSet: %s]" % charset
        parts = [p for p in (ctx["doc"].resolve(x) for x in as_list(d.get("schema:hasPart")))
                 if isinstance(p, dict) and field(p, "schema:name")]
        lines = [E("mrd:onLine", online(url, field(d, "schema:name") or None, desc or None,
                                        "download" if parts else None, protocol))]
        for p in parts:
            pdesc = field(p, "schema:description")
            size_node = next(iter(as_list(p.get("schema:size"))), {})
            if isinstance(size_node, dict) and field(size_node, "schema:value"):
                s = "[size: %s%s]" % (field(size_node, "schema:value"),
                                      " " + field(size_node, "schema:unitText") if field(size_node, "schema:unitText") else "")
                pdesc = (pdesc + " " if pdesc else "") + s
            lines.append(E("mrd:onLine", online(INAPPLICABLE, field(p, "schema:name"), pdesc or None,
                                                "information", field(p, "schema:encodingFormat") or None)))
        out.append(E("mrd:transferOptions", E("mrd:MD_DigitalTransferOptions",
                     E("mrd:transferSize", E("gco:Real", mb)) if mb else None, *lines)))
    return out


def tf_urlfallback(values, ctx):
    if ctx["doc"].select("schema:distribution.schema:contentUrl"):
        return None
    url = text(values[0])
    if not url:
        return None
    return E("mrd:transferOptions", E("mrd:MD_DigitalTransferOptions", E("mrd:onLine", online(url))))


def tf_distributor(values, ctx):
    out = []
    for p in values:
        name = field(p, "schema:name") if isinstance(p, dict) else text(p)
        if not name:
            continue
        kind = "cit:CI_Individual" if "Person" in " ".join(types(p)) else "cit:CI_Organisation"
        out.append(E("mrd:distributor", E("mrd:MD_Distributor", E("mrd:distributorContact",
                     E("cit:CI_Responsibility", E("cit:role", code("CI_RoleCode", "distributor")),
                       E("cit:party", E(kind, E("cit:name", cs(name)))))))))
    return out


def tf_quality(values, ctx):
    reports = []
    for m in values:
        if not isinstance(m, dict):
            continue
        of = ctx["doc"].resolve(next(iter(as_list(m.get("dqv:isMeasurementOf"))), None))
        val = ctx["doc"].resolve(next(iter(as_list(m.get("dqv:value"))), None))
        measure = (field(of, "schema:name") or text(of)) if isinstance(of, dict) else text(of)
        result = (field(val, "schema:value") or field(val, "schema:name") or text(val)) \
            if isinstance(val, dict) else text(val)
        if not (measure or result):
            continue
        reports.append(E("mdq:report", E("mdq:DQ_UsabilityElement",
            E("mdq:measure", E("mdq:DQ_MeasureReference", E("mdq:nameOfMeasure", cs(measure)))) if measure else None,
            E("mdq:result", E("mdq:DQ_DescriptiveResult", E("mdq:statement", cs(result)))) if result else None)))
    if not reports:
        return None
    return E("mdb:dataQualityInfo", E("mdq:DQ_DataQuality",
             E("mdq:scope", E("mcc:MD_Scope", E("mcc:level", code("MD_ScopeCode", "dataset", "mcc")))),
             *reports))


def _describe(node, fallback=""):
    if isinstance(node, dict):
        return field(node, "schema:description") or field(node, "schema:name") or text(node.get("@id")) or fallback
    return text(node) or fallback


def tf_lineagestatement(values, ctx):
    parts = [field(a, "schema:description") or field(a, "schema:name") for a in values if isinstance(a, dict)]
    parts = [p for p in parts if p]
    return E("mrl:statement", cs("; ".join(parts))) if parts else None


def tf_lineagesource(values, ctx):
    out = []
    for s in values:
        name = field(s, "schema:name") if isinstance(s, dict) else ""
        rid = text(s.get("@id")) if isinstance(s, dict) else text(s)
        url = (field(s, "schema:url") if isinstance(s, dict) else "") or (rid if _is_http(rid) else "")
        cite = E("mrl:sourceCitation", E("cit:CI_Citation", E("cit:title", cs(name or url)),
                 E("cit:onlineResource", online(url)) if url else None)) if (name or url) else None
        out.append(E("mrl:source", E("mrl:LI_Source", E("mrl:description", cs(_describe(s))), cite)))
    return out


def tf_processstep(values, ctx):
    out = []
    for a in values:
        if not isinstance(a, dict):
            continue
        end = field(a, "schema:endTime")
        used = [ctx["doc"].resolve(u) for u in as_list(a.get("prov:used"))]
        out.append(E("mrl:processStep", E("mrl:LI_ProcessStep",
            E("mrl:description", cs(field(a, "schema:description") or field(a, "schema:name")
                                    or "Processing activity")),
            E("mrl:stepDateTime", E("gml:TimeInstant", E("gml:timePosition", end),
                                    gml__id=_gml_id(ctx, "step-time"))) if end else None,
            *[E("mrl:source", E("mrl:LI_Source", E("mrl:description", cs(_describe(u)))))
              for u in used if _describe(u)])))
    return out


TRANSFORMS = {name[3:]: fn for name, fn in globals().items()
              if name.startswith("tf_") and callable(fn)}


# ---------------------------------------------------------------------------
# table application and assembly
# ---------------------------------------------------------------------------

def load_table(path=TABLE):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)]


def _shaper(spec):
    m = re.match(r"^(\w+)(?:\((.*)\))?$", spec or "")
    if not m or m.group(1) not in TRANSFORMS:
        return None, ()
    args = tuple(a.strip() for a in m.group(2).split(",")) if m.group(2) else ()
    return TRANSFORMS[m.group(1)], args


def apply_table(rows, doc, ctx):
    filled = {s: [] for s in SLOTS}
    changes = []
    for rule in rows:
        fn, args = _shaper(rule.get("transform"))
        slot = (rule.get("object_xpath") or "").strip()
        path = (rule.get("subject_json_path") or "").strip()
        if not fn or slot not in filled or not path:
            continue
        if slot in SINGLE and filled[slot]:
            continue
        values = doc.select(path)
        if not values:
            continue
        ctx["tag"] = slot.rsplit("/", 1)[-1]
        result = fn(values, ctx, *args)
        items = [r for r in (result if isinstance(result, list) else [result]) if r is not None]
        if not items:
            continue
        filled[slot].extend(items[:1] if slot in SINGLE else items)
        changes.append("%s -> %s" % (path, slot.rsplit("/", 1)[-1]))
    return filled, changes


def assemble(filled):
    md = E("mdb:MD_Metadata")
    for slot in SLOTS:
        items = filled[slot]
        if not items:
            continue
        steps = slot.split("/")
        parent = md
        for step in steps[:-1]:
            found = [c for c in parent if c.tag == qn(step)]
            parent = found[-1] if found else etree.SubElement(parent, qn(step))
        if slot in JOINED:
            parent.append(E(steps[-1], cs("\n".join(items))))
        else:
            for item in items:
                parent.append(item)
    return md


def convert_cdif_to_iso(record, rows=None):
    """A CDIF JSON-LD dict -> (mdb:MD_Metadata element, list of changes)."""
    rows = rows if rows is not None else load_table()
    doc = Doc(record)
    ctx = {"doc": doc}
    filled, changes = apply_table(rows, doc, ctx)

    # --- what ISO 19115-3 requires, and fromJsonCdif.xsl always writes ---------------
    if not filled["mdb:metadataIdentifier"]:
        digest = json.dumps(record, sort_keys=True)
        filled["mdb:metadataIdentifier"].append(E("mdb:metadataIdentifier",
            md_identifier(str(uuid.uuid5(uuid.NAMESPACE_OID, digest)), "urn:uuid")))
        changes.append("metadataIdentifier made from the record's content (no @id)")
    if not filled["mdb:defaultLocale"]:
        filled["mdb:defaultLocale"].append(E("mdb:defaultLocale", locale()))
    if not filled["mdb:metadataScope"]:
        filled["mdb:metadataScope"].append(tf_scope([], ctx))
    if not filled["mdb:contact"]:
        filled["mdb:contact"].append(E("mdb:contact", E("cit:CI_Responsibility",
            E("cit:role", code("CI_RoleCode", "author")),
            E("cit:party", E("cit:CI_Organisation", E("cit:name", cs("")))))))
        changes.append("empty author contact (no creator or provider)")
    if not filled["mdb:dateInfo"]:
        d = ci_date(field(doc.root, "schema:dateModified"), "revision")
        if d is None:
            d = E("cit:CI_Date", E("cit:date", gco__nilReason="missing"),
                  E("cit:dateType", code("CI_DateTypeCode", "revision")))
            changes.append("dateInfo nil (no date)")
        filled["mdb:dateInfo"].append(E("mdb:dateInfo", d))
    filled["mdb:metadataStandard"] = [E("mdb:metadataStandard", E("cit:CI_Citation",
                                        E("cit:title", cs("ISO 19115-3"))))]
    if not filled[CI + "/cit:title"]:
        filled[CI + "/cit:title"].append(E("cit:title", cs("")))
    if not filled[DI + "/mri:abstract"]:
        name = field(doc.root, "schema:name")
        filled[DI + "/mri:abstract"].append(E("mri:abstract", cs(name)))
    if not filled[DI + "/mri:defaultLocale"]:
        filled[DI + "/mri:defaultLocale"].append(E("mri:defaultLocale", locale()))
    if filled[LI + "/mrl:source"] or filled[LI + "/mrl:processStep"]:
        if not filled[LI + "/mrl:statement"]:
            filled[LI + "/mrl:statement"].append(E("mrl:statement", cs("")))
        filled[LI + "/mrl:scope"] = [E("mrl:scope", E("mcc:MD_Scope",
                                       E("mcc:level", code("MD_ScopeCode", "dataset", "mcc"))))]
    elif not filled[LI + "/mrl:statement"]:
        filled[LI + "/mrl:statement"].append(E("mrl:statement", cs("")))
    md = assemble(filled)
    return md, changes


def serialize(md):
    nsmap = {k: v for k, v in NS.items()}
    out = etree.Element(md.tag, nsmap=nsmap)
    out.extend(list(md))
    etree.cleanup_namespaces(out, top_nsmap=nsmap, keep_ns_prefixes=list(nsmap))
    return etree.tostring(out, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def load_xsd(path):
    path = Path(path)
    return etree.XMLSchema(etree.parse(str(path))) if path.is_file() else None


def main():
    ap = argparse.ArgumentParser(description="Convert CDIF JSON-LD to ISO 19115-3 XML")
    ap.add_argument("input", nargs="+", help="CDIF JSON-LD file(s) or folder(s)")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--xsd", help="ISO 19115-3 schema.xsd to validate each output against "
                                  "(default: the GeoNetwork checkout beside this repository, if any)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    rows = load_table()
    xsd = load_xsd(args.xsd or DEFAULT_XSD)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for source in args.input:
        p = Path(source)
        files.extend(sorted(x for x in p.rglob("*.json*") if x.name != "INDEX.json") if p.is_dir() else [p])
    for f in files:
        record = json.loads(f.read_text(encoding="utf-8"))
        md, changes = convert_cdif_to_iso(record, rows)
        out = out_dir / (f.stem + ".xml")
        out.write_bytes(serialize(md))
        line = out.name
        if xsd is not None:
            ok = xsd.validate(etree.fromstring(out.read_bytes()))
            line += "  XSD " + ("valid" if ok else "INVALID: " + str(xsd.error_log.last_error)[-200:])
        if args.verbose:
            line += "\n      " + "\n      ".join(changes)
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

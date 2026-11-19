#!/usr/bin/env python3
"""
sdmx_to_cdif.py - Convert SDMX-ML 2.1 structure messages to CDIF JSON-LD.

The input is a message:Structure holding one or more dataflows and, for each,
its data structure definition (DSD) and the concept schemes and codelists the
DSD uses: the response to an SDMX-REST query such as
  {endpoint}dataflow/{agency}/{id}/{version}?references=all&detail=referencepartial
Each dataflow becomes one CDIF record:

  * the dataflow -> a schema:Dataset (name, description, identifier, version,
    publisher, providers, categories as keywords, the time range of its
    content constraint as temporalCoverage)
  * each DSD component (dimension, time dimension, primary measure, attribute)
    -> a cdi:InstanceVariable in schema:variableMeasured, whose cdif:role is
    Dimension, Measure or Attribute, whose propertyID is its concept, and
    whose codelist is an enumerated value domain referencing the codelist
  * the DSD -> a cdi:DimensionalDataStructure, attached by cdi:isStructuredBy
    to the data distribution, with the dimensions as its key

The table mappings/sdmx-to-cdif.sssom.tsv drives the conversion: each row names
an SDMX element by an XPath from the Dataflow element (subject_xpath; $dsd is
the dataflow's DSD and $df the dataflow), the CDIF location it lands at
(object_json_path) and the shaper that builds the value (transform). Row order
is precedence for a scalar target; array targets accumulate.

Without --endpoint the record has no SDMX-REST addresses: its @id is the
dataflow URN and it has no distribution. With --endpoint (the REST root, as the
SDMX Global Discovery Service gives it) the @id is the dataflow's REST URL and
the data query {endpoint}data/{agency},{id},{version}/all is the distribution;
--no-data drops the distribution for a structure-only registry.

Usage:
    python SDMX/sdmx_to_cdif.py structure.xml -o out/
    python SDMX/sdmx_to_cdif.py structure.xml -o out/ --endpoint https://stats.bis.org/api/v1/
    python SDMX/sdmx_to_cdif.py structure.xml -o out/ --endpoint https://registry.sdmx.org/ws/public/sdmxapi/rest/ --no-data
"""

import argparse
import csv
import datetime as _dt
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

TABLE = ROOT / "mappings" / "sdmx-to-cdif.sssom.tsv"
NS = {
    "mes": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "str": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "com": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
}
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
URN = "urn:sdmx:org.sdmx.infomodel."
SDMX_ML_DATA = "application/vnd.sdmx.genericdata+xml;version=2.1"

CDIF_CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
}
OPTIONAL_PREFIXES = {
    "cdi": "http://ddialliance.org/Specification/DDI-CDI/1.0/RDF/",
    "cdif": "https://w3id.org/cdif/",
}
ARRAY_TARGETS = {
    "schema:alternateName", "schema:keywords", "schema:temporalCoverage",
    "schema:variableMeasured", "schema:distribution", "schema:relatedLink",
    "schema:provider", "schema:license",
}

# SDMX text types -> XML Schema datatypes; SDMX time periods (2020-Q1,
# 2020-W05) have no XSD type and keep their SDMX name.
XSD_TYPES = {
    "String": "xsd:string", "Alpha": "xsd:string", "AlphaNumeric": "xsd:string",
    "Numeric": "xsd:string", "BigInteger": "xsd:integer", "Integer": "xsd:int",
    "Long": "xsd:long", "Short": "xsd:short", "Decimal": "xsd:decimal",
    "Float": "xsd:float", "Double": "xsd:double", "Boolean": "xsd:boolean",
    "URI": "xsd:anyURI", "Count": "xsd:integer", "DateTime": "xsd:dateTime",
    "Date": "xsd:date", "Time": "xsd:time", "Year": "xsd:gYear",
    "Month": "xsd:gMonth", "Day": "xsd:gDay", "MonthDay": "xsd:gMonthDay",
    "YearMonth": "xsd:gYearMonth", "Duration": "xsd:duration",
}
ROLES = {"Dimension": "Dimension", "TimeDimension": "Dimension",
         "MeasureDimension": "Dimension", "PrimaryMeasure": "Measure",
         "Attribute": "Attribute"}
COMPONENT_TYPES = {"Dimension": "cdi:DimensionComponent",
                   "TimeDimension": "cdi:DimensionComponent",
                   "MeasureDimension": "cdi:DimensionComponent",
                   "PrimaryMeasure": "cdi:MeasureComponent",
                   "Attribute": "cdi:AttributeComponent"}
URN_CLASSES = {"Dimension": "Dimension", "TimeDimension": "TimeDimension",
               "MeasureDimension": "MeasureDimension",
               "PrimaryMeasure": "PrimaryMeasure", "Attribute": "DataAttribute"}


# ---------------------------------------------------------------------------
# element helpers
# ---------------------------------------------------------------------------

def ln(el):
    return etree.QName(el).localname


def texts_by_lang(el, name):
    """{lang: text} for the c:<name> children of el, in document order."""
    out = {}
    for child in el.findall("com:" + name, NS):
        text = " ".join((child.text or "").split())
        if text:
            out.setdefault(child.get(XML_LANG) or "", text)
    return out


def english(values):
    """The English text of a {lang: text} map, else the first."""
    if not values:
        return None
    for lang in ("en", "en-GB", "en-US", ""):
        if lang in values:
            return values[lang]
    return next(iter(values.values()))


def name_of(el):
    return english(texts_by_lang(el, "Name")) if el is not None else None


def description_of(el):
    return english(texts_by_lang(el, "Description")) if el is not None else None


def _ref(el):
    """The unqualified Ref child of a reference element, or el itself."""
    if el is None:
        return None
    if ln(el) == "Ref":
        return el
    return el.find("Ref")


def maintainable_urn(el, package, cls):
    return el.get("urn") or "%s%s.%s=%s:%s(%s)" % (
        URN, package, cls, el.get("agencyID"), el.get("id"), el.get("version") or "1.0")


def rest_url(endpoint, resource, el):
    if not endpoint:
        return None
    return "%s%s/%s/%s/%s" % (endpoint, resource, el.get("agencyID"), el.get("id"),
                              el.get("version") or "latest")


class Message:
    """Lookups across one structure message: maintainables by (class, agency,
    id), items within their schemes."""

    PACKAGES = {"Codelist": ("Codelists", "Codelist"),
                "ConceptScheme": ("Concepts", "ConceptScheme"),
                "DataStructure": ("DataStructures", "DataStructure"),
                "CategoryScheme": ("CategorySchemes", "CategoryScheme"),
                "AgencyScheme": ("OrganisationSchemes", "AgencyScheme"),
                "DataProviderScheme": ("OrganisationSchemes", "DataProviderScheme")}

    def __init__(self, root):
        self.root = root
        self.index = {}
        for cls, (group, tag) in self.PACKAGES.items():
            for el in root.iterfind(".//str:%s/str:%s" % (group, tag), NS):
                key = (cls, el.get("agencyID"), el.get("id"))
                self.index.setdefault(key, []).append(el)

    def maintainable(self, cls, agency, ident, version=None):
        found = self.index.get((cls, agency, ident), [])
        exact = [e for e in found if e.get("version") == version]
        return (exact or found or [None])[0]

    def resolve(self, ref, cls):
        ref = _ref(ref)
        if ref is None:
            return None
        return self.maintainable(cls, ref.get("agencyID"), ref.get("id"), ref.get("version"))

    def concept(self, ref):
        ref = _ref(ref)
        if ref is None:
            return None, None
        scheme = self.maintainable("ConceptScheme", ref.get("agencyID"),
                                   ref.get("maintainableParentID"),
                                   ref.get("maintainableParentVersion"))
        concept = None
        if scheme is not None:
            concept = scheme.find("str:Concept[@id='%s']" % ref.get("id"), NS)
        return scheme, concept

    def concept_urn(self, ref):
        ref = _ref(ref)
        _, concept = self.concept(ref)
        if concept is not None and concept.get("urn"):
            return concept.get("urn")
        return "%sconceptscheme.Concept=%s:%s(%s).%s" % (
            URN, ref.get("agencyID"), ref.get("maintainableParentID"),
            ref.get("maintainableParentVersion") or "1.0", ref.get("id"))

    def agency(self, agency_id):
        """The Agency element for an agency id (a dotted id names a sub-agency)."""
        parent, _, local = (agency_id or "").rpartition(".")
        for scheme in self.root.iterfind(".//str:AgencyScheme", NS):
            if parent and scheme.get("agencyID") != parent:
                continue
            found = scheme.find("str:Agency[@id='%s']" % local, NS)
            if found is not None:
                return found
        return None

    def category(self, ref):
        """The Category a Ref names; its id may be a dotted path of nested ids."""
        ref = _ref(ref)
        scheme = self.maintainable("CategoryScheme", ref.get("agencyID"),
                                   ref.get("maintainableParentID"),
                                   ref.get("maintainableParentVersion"))
        if scheme is None:
            return None, None
        node = scheme
        for part in ref.get("id", "").split("."):
            node = node.find("str:Category[@id='%s']" % part, NS)
            if node is None:
                return scheme, None
        return scheme, node


# ---------------------------------------------------------------------------
# value shapers: tf_<transform>(values, ctx) -> value or list
# ---------------------------------------------------------------------------

def tf_name(values, ctx):
    """The English text of these c:Name / c:Description elements, else the first."""
    texts = {}
    for el in values:
        text = " ".join((el.text or "").split())
        if text:
            texts.setdefault(el.get(XML_LANG) or "", text)
    return english(texts)


def tf_othernames(values, ctx):
    """Every language's text but the one tf_name chose."""
    chosen = tf_name(values, ctx)
    out = []
    for el in values:
        text = " ".join((el.text or "").split())
        if text and text != chosen and text not in out:
            out.append(text)
    return out


def tf_text(values, ctx):
    for v in values:
        text = v if isinstance(v, str) else (v.text or "")
        text = " ".join(str(text).split())
        if text:
            return text
    return None


def tf_urn(values, ctx):
    df = values[0]
    pv = {"@type": ["schema:PropertyValue"], "schema:propertyID": "SDMX URN",
          "schema:value": maintainable_urn(df, "datastructure", "Dataflow")}
    url = rest_url(ctx["endpoint"], "dataflow", df)
    if url:
        pv["schema:url"] = url
    return pv


def tf_resturl(values, ctx):
    return rest_url(ctx["endpoint"], "dataflow", values[0])


def tf_structureurl(values, ctx):
    url = rest_url(ctx["endpoint"], "dataflow", values[0])
    return url and url + "?references=all&detail=referencepartial"


_CDIF_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?)?$")


def _date(text):
    text = (text or "").strip()
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}:\d{2}:\d{2}))?$", text)
    if m:      # the .Stat Suite LAST_UPDATE annotation: dd/mm/yyyy hh:mm:ss
        d, mo, y, t = m.groups()
        return "%s-%s-%s" % (y, mo, d) + ("T%sZ" % t if t else "")
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?$", text)
    if m:      # a date-time with no zone is taken as UTC
        return "%sT%s%s" % (m.group(1), m.group(2), m.group(4) or "Z")
    return text if _CDIF_DATE.match(text) else None


def tf_date(values, ctx):
    for v in values:
        d = _date(v if isinstance(v, str) else v.text)
        if d:
            return d
    return None


def _contact(contact):
    point = {"@type": ["schema:ContactPoint"]}
    name = name_of(contact)
    if name:
        point["schema:name"] = name
    role = english(texts_by_lang_s(contact, "Role"))
    if role:
        point["schema:contactType"] = role
    email = contact.findtext("str:Email", namespaces=NS)
    if email:
        point["schema:email"] = email.strip()
    uri = contact.findtext("str:URI", namespaces=NS)
    if uri and uri.strip().startswith("http"):
        point["schema:url"] = uri.strip()
    phone = contact.findtext("str:Telephone", namespaces=NS)
    if phone:
        point["schema:telephone"] = phone.strip()
    return point if len(point) > 1 else None


def texts_by_lang_s(el, name):
    out = {}
    for child in el.findall("str:" + name, NS):
        text = " ".join((child.text or "").split())
        if text:
            out.setdefault(child.get(XML_LANG) or "", text)
    return out


def _organization(el, fallback_id):
    org = {"@type": ["schema:Organization"],
           "schema:name": name_of(el) if el is not None else fallback_id}
    if el is None:
        return org
    org["schema:name"] = org["schema:name"] or el.get("id") or fallback_id
    if el.get("urn"):
        org["@id"] = el.get("urn")
    if el.get("id") and el.get("id") != org["schema:name"]:
        org["schema:alternateName"] = el.get("id")
    desc = description_of(el)
    if desc:
        org["schema:description"] = desc
    points = [p for p in (_contact(c) for c in el.findall("str:Contact", NS)) if p]
    if points:
        org["schema:contactPoint"] = points
    return org


def tf_agency(values, ctx):
    """An agency id -> the Agency in the message's agency schemes, as an Organization."""
    agency_id = values[0] if isinstance(values[0], str) else values[0].text
    return _organization(ctx["msg"].agency(agency_id), agency_id)


def tf_dataprovider(values, ctx):
    """DataProvider Refs -> the DataProviders in the message, as Organizations."""
    out = []
    for ref in values:
        scheme = ctx["msg"].maintainable("DataProviderScheme", ref.get("agencyID"),
                                         ref.get("maintainableParentID"),
                                         ref.get("maintainableParentVersion"))
        el = scheme.find("str:DataProvider[@id='%s']" % ref.get("id"), NS) \
            if scheme is not None else None
        org = _organization(el, ref.get("id"))
        if org not in out:
            out.append(org)
    return out


def tf_category(values, ctx):
    """Category Refs -> DefinedTerms in their category scheme."""
    out = []
    for ref in values:
        scheme, cat = ctx["msg"].category(ref)
        name = name_of(cat) or ref.get("id")
        term = {"@type": ["schema:DefinedTerm"], "schema:name": name,
                "schema:termCode": ref.get("id")}
        if scheme is not None:
            term["schema:inDefinedTermSet"] = name_of(scheme) or scheme.get("id")
        if cat is not None and cat.get("urn"):
            term["@id"] = cat.get("urn")
        if term not in out:
            out.append(term)
    return out


def tf_timerange(values, ctx):
    """com:TimeRange -> an ISO 8601 interval (open-ended with '..')."""
    out = []
    for tr in values:
        start = tr.findtext("com:StartPeriod", namespaces=NS) or \
            tr.findtext("com:AfterPeriod", namespaces=NS)
        end = tr.findtext("com:EndPeriod", namespaces=NS) or \
            tr.findtext("com:BeforePeriod", namespaces=NS)
        start, end = (_period(start), _period(end))
        if start or end:
            interval = "%s/%s" % (start or "..", end or "..")
            if interval not in out:
                out.append(interval)
    return out


def _period(text):
    text = (text or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T00:00:00(\.0+)?(Z|[+-]00:00)?$", text)
    return m.group(1) if m else text


def tf_extresource(values, ctx):
    """EXT_RESOURCE annotations ('label|url|image') -> LinkRoles."""
    out = []
    for el in values:
        parts = [p.strip() for p in (el.text or "").split("|")]
        url = next((p for p in parts if p.startswith("http")), None)
        if not url:
            continue
        link = {"@type": ["schema:LinkRole"],
                "schema:linkRelationship": parts[0] if not parts[0].startswith("http")
                else "related",
                "schema:target": {"@type": ["schema:EntryPoint"], "schema:url": url}}
        if link not in out:
            out.append(link)
    return out


# --- variables and the data structure --------------------------------------------

def _representation(comp, msg):
    """(codelist element or Ref, SDMX text type) for a component: its local
    representation, else its concept's core representation."""
    for rep in (comp.find("str:LocalRepresentation", NS),):
        if rep is not None:
            enum = rep.find("str:Enumeration", NS)
            if enum is not None:
                return enum, None
            tf = rep.find("str:TextFormat", NS)
            if tf is not None and tf.get("textType"):
                return None, tf.get("textType")
    _, concept = msg.concept(comp.find("str:ConceptIdentity", NS))
    if concept is not None:
        rep = concept.find("str:CoreRepresentation", NS)
        if rep is not None:
            enum = rep.find("str:Enumeration", NS)
            if enum is not None:
                return enum, None
            tf = rep.find("str:TextFormat", NS)
            if tf is not None and tf.get("textType"):
                return None, tf.get("textType")
    return None, None


def component_urn(comp, dsd):
    if comp.get("urn"):
        return comp.get("urn")
    return "%sdatastructure.%s=%s:%s(%s).%s" % (
        URN, URN_CLASSES[ln(comp)], dsd.get("agencyID"), dsd.get("id"),
        dsd.get("version") or "1.0", comp.get("id"))


def _value_domain(enum, ctx):
    msg, endpoint = ctx["msg"], ctx["endpoint"]
    ref = _ref(enum)
    cl = msg.resolve(ref, "Codelist")
    el = cl if cl is not None else ref
    urn = cl.get("urn") if cl is not None and cl.get("urn") else \
        "%scodelist.Codelist=%s:%s(%s)" % (URN, ref.get("agencyID"), ref.get("id"),
                                           ref.get("version") or "1.0")
    domain = {"@type": ["cdif:EnumerationDomain"],
              "schema:name": name_of(cl) or ref.get("id"),
              "cdif:identifier": {"@type": ["schema:PropertyValue"],
                                  "schema:propertyID": "SDMX URN", "schema:value": urn},
              "cdif:references": {"@id": rest_url(endpoint, "codelist", el) or urn}}
    if cl is not None:
        n = len(cl.findall("str:Code", NS))
        desc = description_of(cl)
        partial = ctx["partial"]
        domain["schema:description"] = "%s%s" % (
            (desc + ". ") if desc else "",
            "%d code%s%s" % (n, "" if n == 1 else "s",
                             " used by this dataflow" if partial else ""))
    return {"@type": ["cdif:SubstantiveValueDomain"], "cdif:takesValuesFrom": domain}


def tf_variable(values, ctx):
    """DSD components -> cdi:InstanceVariables (schema:variableMeasured)."""
    msg, dsd, base = ctx["msg"], ctx["dsd"], ctx["var_base"]
    comps = [c for c in values if ln(c) in ROLES]
    measure = next((c for c in comps if ln(c) == "PrimaryMeasure"), None)
    out = []
    for comp in comps:
        cid = comp.get("id")
        ident = comp.find("str:ConceptIdentity", NS)
        _, concept = msg.concept(ident)
        var = {"@id": base + cid, "@type": ["schema:PropertyValue", "cdi:InstanceVariable"],
               "schema:name": cid}
        label = name_of(concept)
        if label and label != cid:
            var["schema:alternateName"] = [label]
        desc = description_of(concept) or label
        var["schema:description"] = desc or "SDMX %s %s" % (ln(comp), cid)
        if ident is not None and _ref(ident) is not None:
            var["schema:propertyID"] = {"@id": msg.concept_urn(ident)}
        var["cdif:role"] = ROLES[ln(comp)]
        enum, text_type = _representation(comp, msg)
        if enum is not None:
            var["cdif:physicalDataType"] = "xsd:string"
            var["cdi:takesSubstantiveValuesFrom"] = _value_domain(enum, ctx)
        elif ln(comp) == "TimeDimension":
            var["cdif:physicalDataType"] = XSD_TYPES.get(text_type, text_type or
                                                         "ObservationalTimePeriod")
        else:
            var["cdif:physicalDataType"] = XSD_TYPES.get(text_type, text_type or "xsd:string")
        if ln(comp) == "Attribute":
            target = _qualified(comp, measure)
            if target is not None:
                var["cdif:qualifies"] = {"@id": base + target}
        out.append(var)
    return out


def _qualified(attr, measure):
    """The id of the component an attribute qualifies: the first dimension it
    is attached to, else the primary measure (observation, group and dataset
    attachment all qualify the observations)."""
    rel = attr.find("str:AttributeRelationship", NS)
    if rel is not None:
        for dim in rel.findall("str:Dimension", NS):
            ref = _ref(dim) if dim.find("Ref") is not None else dim
            ident = ref.get("id") if ref is not None else None
            if ident:
                return ident
    return measure.get("id") if measure is not None else None


def _structure(dsd, ctx):
    """The DSD -> a cdi:DimensionalDataStructure over the record's variables."""
    base = ctx["var_base"]
    comps = [c for c in dsd.iterfind("str:DataStructureComponents/*/*", NS) if ln(c) in ROLES]
    urns = {c.get("id"): component_urn(c, dsd) for c in comps}
    out, key = [], []
    for comp in comps:
        node = {"@type": [COMPONENT_TYPES[ln(comp)]], "@id": urns[comp.get("id")],
                "cdif:isDefinedBy_Variable": {"@id": base + comp.get("id")}}
        if ln(comp) == "Attribute":
            rel = comp.find("str:AttributeRelationship", NS)
            targets = []
            if rel is not None:
                for dim in rel.findall("str:Dimension", NS):
                    ref = dim.find("Ref")
                    ident = (ref if ref is not None else dim).get("id")
                    if ident in urns:
                        targets.append({"@id": urns[ident]})
                if rel.find("str:PrimaryMeasure", NS) is not None or not targets:
                    measure = next((c for c in comps if ln(c) == "PrimaryMeasure"), None)
                    if measure is not None:
                        targets.append({"@id": urns[measure.get("id")]})
            if targets:
                node["cdif:qualifies"] = targets
        out.append(node)
        if ln(comp) in ("Dimension", "TimeDimension", "MeasureDimension"):
            key.append(comp)
    struct = {"@type": ["cdi:DimensionalDataStructure"],
              "@id": maintainable_urn(dsd, "datastructure", "DataStructure"),
              "cdi:has_DataStructureComponent": out}
    if key:
        key.sort(key=lambda c: int(c.get("position") or 10 ** 6))
        struct["cdif:has_PrimaryKey"] = {
            "@type": ["cdif:Key"],
            "cdif:isComposedOf": [{"@type": ["cdi:ComponentPosition"],
                                   "cdi:indexes": {"@id": base + c.get("id")},
                                   "cdi:value": i}
                                  for i, c in enumerate(key, 1)]}
    return struct


def tf_distribution(values, ctx):
    """The dataflow's SDMX-REST data query -> a DataDownload structured by the DSD."""
    df, dsd, endpoint = values[0], ctx["dsd"], ctx["endpoint"]
    if not endpoint or not ctx["has_data"] or dsd is None:
        return None
    flow = "%s,%s,%s" % (df.get("agencyID"), df.get("id"), df.get("version") or "latest")
    return {"@type": ["schema:DataDownload"],
            "schema:name": "SDMX-ML data for %s" % flow,
            "schema:contentUrl": "%sdata/%s/all" % (endpoint, flow),
            "schema:encodingFormat": [SDMX_ML_DATA],
            "schema:description": "SDMX-REST data query for the whole dataflow; add a series "
                                  "key in place of 'all', startPeriod/endPeriod, or an Accept "
                                  "header (e.g. application/vnd.sdmx.data+csv) to narrow or "
                                  "reformat it.",
            "cdi:isStructuredBy": _structure(dsd, ctx)}


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


def apply_table(rows, df, doc, record, ctx):
    changes = []
    dsd = ctx["dsd"]
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
        if "$dsd" in xpath and dsd is None:
            continue
        try:
            values = df.xpath(xpath, namespaces=NS, df=df,
                              dsd=dsd if dsd is not None else df)
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

def is_structure_message(root):
    return etree.QName(root) == etree.QName(NS["mes"], "Structure")


def dataflows(root):
    return root.findall("mes:Structures/str:Dataflows/str:Dataflow", NS)


def convert_sdmx_to_cdif(df, msg, rows=None, endpoint=None, has_data=True,
                         partial=False, detect=True, catalog=None):
    """One Dataflow element of a structure message -> a CDIF JSON-LD dict."""
    rows = rows if rows is not None else load_table()
    if endpoint and not endpoint.endswith("/"):
        endpoint += "/"
    dsd = msg.resolve(df.find("str:Structure", NS), "DataStructure")
    rid = rest_url(endpoint, "dataflow", df) or maintainable_urn(df, "datastructure", "Dataflow")
    ctx = {"msg": msg, "dsd": dsd, "endpoint": endpoint, "has_data": has_data,
           "partial": partial, "var_base": rid + "#"}
    doc = {"@context": dict(CDIF_CONTEXT), "@id": rid, "@type": ["schema:Dataset"]}
    record = {}
    changes = apply_table(rows, df, doc, record, ctx)
    if dsd is None:
        changes.append("no DSD in the message: no variables or structure")

    # --- what CDIF requires and SDMX may not supply --------------------------------
    if not doc.get("schema:name"):
        doc["schema:name"] = df.get("id")
    if not doc.get("schema:description"):
        doc["schema:description"] = doc["schema:name"]
        changes.append("schema:description from the name")
    if not doc.get("schema:dateModified"):
        doc["schema:dateModified"] = "1900-01-01"
        changes.append("schema:dateModified sentinel (no LAST_UPDATE or validFrom)")
    if not doc.get("schema:url"):
        doc["schema:url"] = NIL
    if not doc.get("schema:license") and not doc.get("schema:conditionsOfAccess"):
        doc["schema:license"] = [{"@id": NIL}]
        changes.append("schema:license set to the OGC nil URI (SDMX carries no licence)")

    # --- the catalog record --------------------------------------------------------
    subject = {
        "@id": record.get("schema:url") or rid + "#metadata",
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:name": "SDMX structure metadata for: %s" % doc["schema:name"][:120],
        "schema:about": {"@id": rid},
        "dcterms:conformsTo": [{"@id": "https://w3id.org/cdif/core/2.0"}],
        "schema:description": "Converted from an SDMX-ML 2.1 structure message (dataflow "
                              "and data structure definition) to CDIF by sdmx_to_cdif.py.",
    }
    subject.update(record)
    if catalog:
        subject["schema:includedInDataCatalog"] = catalog
    doc["schema:subjectOf"] = subject

    text = json.dumps(doc)
    for prefix, uri in OPTIONAL_PREFIXES.items():
        if '"%s:' % prefix in text:
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
    if re.match(r"^https?://", source):
        import urllib.request
        req = urllib.request.Request(source, headers={
            "User-Agent": "cdif-converters/0.1",
            "Accept": "application/vnd.sdmx.structure+xml;version=2.1"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return etree.fromstring(r.read(), PARSER)
    return etree.parse(source, PARSER).getroot()


def _slug(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text or "").strip("_")[:100]


def main():
    ap = argparse.ArgumentParser(description="Convert SDMX-ML 2.1 structure messages to CDIF JSON-LD")
    ap.add_argument("input", nargs="+", help="structure message file(s) or SDMX-REST URL(s)")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--endpoint", help="SDMX-REST root the message came from, e.g. "
                                       "https://stats.bis.org/api/v1/")
    ap.add_argument("--no-data", action="store_true",
                    help="the endpoint is a structure-only registry: no data distribution")
    ap.add_argument("--partial", action="store_true",
                    help="codelists were fetched with detail=referencepartial "
                         "(only the codes the dataflow uses)")
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
        root = load_xml(source)
        if not is_structure_message(root):
            print("%s: not an SDMX-ML 2.1 structure message" % source, file=sys.stderr)
            continue
        msg = Message(root)
        for i, df in enumerate(dataflows(root)):
            doc, changes = convert_sdmx_to_cdif(df, msg, rows, args.endpoint, not args.no_data,
                                                args.partial, not args.static_conformance,
                                                catalog)
            stem = Path(source).stem if not source.startswith("http") else \
                _slug("%s-%s-%s" % (df.get("agencyID"), df.get("id"), df.get("version")))
            out = out_dir / ("%s%s.jsonld" % (stem, "" if i == 0 else "_%d" % i))
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

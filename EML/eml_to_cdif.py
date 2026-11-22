#!/usr/bin/env python3
"""
eml_to_cdif.py - Convert Ecological Metadata Language (EML) to CDIF JSON-LD.

Reads EML 2.0.1 to 2.2.0 documents (<eml:eml>), as GBIF, DataONE member nodes
and LTER publish them; each document becomes one CDIF record. A dataTable with
an attributeList becomes a tabular distribution with one cdi:InstanceVariable
per attribute, so a DataONE package can reach CDIF data_description.

The table mappings/eml-to-cdif.sssom.tsv drives the conversion: each row names
EML elements by an XPath from the <eml:eml> element (subject_xpath; EML's
child elements are unqualified, so the paths serve every EML version), the CDIF
location it lands at (object_json_path) and the shaper that builds the value
(transform). Row order is precedence for a scalar target; array targets
accumulate.

Usage:
    python EML/eml_to_cdif.py eml.xml -o out/
    python EML/eml_to_cdif.py eml.xml -o out/ --record-url https://api.gbif.org/v1/dataset/<key>/document --catalog-name GBIF --catalog-url https://www.gbif.org/
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

TABLE = ROOT / "mappings" / "eml-to-cdif.sssom.tsv"
EML_NS = ("eml://ecoinformatics.org/eml-", "https://eml.ecoinformatics.org/eml-")
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
    "cdif": "https://w3id.org/cdif/",
    "csvw": "http://www.w3.org/ns/csvw#",
}
ARRAY_TARGETS = {
    "schema:alternateName", "schema:sameAs", "schema:contributor", "schema:funding",
    "schema:keywords", "schema:license", "schema:conditionsOfAccess", "schema:spatialCoverage",
    "schema:temporalCoverage", "prov:wasGeneratedBy", "schema:relatedLink",
    "schema:distribution", "schema:variableMeasured",
}

# EML storageType (often an XSD name) and numberType -> XSD
XSD_TYPES = {t.lower(): "xsd:" + t for t in (
    "string", "boolean", "decimal", "float", "double", "integer", "int", "long", "short",
    "byte", "date", "dateTime", "time", "gYear", "anyURI", "nonNegativeInteger",
    "positiveInteger")}
NUMBER_TYPES = {"natural": "xsd:nonNegativeInteger", "whole": "xsd:nonNegativeInteger",
                "integer": "xsd:integer", "real": "xsd:decimal"}
ISO_DATE_FORMAT = re.compile(r"^YYYY(-MM(-DD(Thh:mm(:ss)?)?)?)?$", re.I)


# ---------------------------------------------------------------------------
# element helpers
# ---------------------------------------------------------------------------

def text(el):
    if el is None:
        return ""
    if isinstance(el, str):
        return " ".join(el.split())
    return " ".join("".join(el.itertext()).split())


def paras(el):
    """An EML TextType (para / section / plain text) as paragraphs joined by blank lines."""
    if el is None:
        return ""
    blocks = el.findall(".//para")
    if blocks:
        out = [text(p) for p in blocks]
        return "\n\n".join(t for t in out if t)
    return text(el)


def first(el, path):
    return text(el.find(path)) if el is not None else ""


def resolve_ref(el, ctx):
    """Follow an EML <references> to the element whose id it names."""
    ref = el.find("references")
    if ref is not None and text(ref):
        hit = ctx["root"].xpath("//*[@id=$r]", r=text(ref))
        if hit:
            return hit[0]
    return el


# ---------------------------------------------------------------------------
# identifiers
# ---------------------------------------------------------------------------

_DOI = re.compile(r"^(?:doi:|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)$", re.I)


def doi_of(value):
    m = _DOI.match(value.strip())
    return m.group(1) if m else None


def iri(value):
    value = text(value)
    doi = doi_of(value)
    if doi:
        return "https://doi.org/" + doi
    if re.match(r"^https?://", value):
        return value
    return "urn:eml:" + re.sub(r"\s+", "", value) if value else None


def doi_pv(doi):
    return {"@type": ["schema:PropertyValue"], "schema:propertyID": {"@id": DOI_REGISTRY},
            "schema:value": doi, "schema:url": "https://doi.org/" + doi}


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------

def _user_id(el):
    """userId -> PropertyValue; an ORCID directory gives the orcid registry entry."""
    for u in el.findall("userId"):
        value, directory = text(u), (u.get("directory") or "").strip()
        if not value:
            continue
        if "orcid.org" in directory or "orcid.org" in value:
            bare = re.sub(r"^https?://orcid\.org/", "", value)
            return {"@type": ["schema:PropertyValue"],
                    "schema:propertyID": {"@id": "https://registry.identifiers.org/registry/orcid"},
                    "schema:value": bare, "schema:url": "https://orcid.org/" + bare}
        pv = {"@type": ["schema:PropertyValue"], "schema:value": value}
        if directory:
            pv["schema:propertyID"] = {"@id": directory} if re.match(r"^https?://", directory) \
                else directory
        if re.match(r"^https?://", value):
            pv["schema:url"] = value
        return pv
    return None


def _contacts(el, node):
    email = first(el, "electronicMailAddress")
    phone = first(el, "phone")
    url = first(el, "onlineUrl")
    if email:
        node["schema:email"] = email
    if phone:
        node["schema:telephone"] = phone
    if re.match(r"^https?://", url):
        node["schema:url"] = url


def agent(el, ctx):
    """An EML ResponsibleParty -> Person or Organization."""
    el = resolve_ref(el, ctx)
    ind = el.find("individualName")
    org_name = first(el, "organizationName")
    position = first(el, "positionName")
    if ind is not None and (first(ind, "surName") or first(ind, "givenName")):
        given = " ".join(text(g) for g in ind.findall("givenName") if text(g))
        family = first(ind, "surName")
        node = {"@type": ["schema:Person"],
                "schema:name": ", ".join(p for p in (family, given) if p)}
        if given:
            node["schema:givenName"] = given
        if family:
            node["schema:familyName"] = family
        if position:
            node["schema:jobTitle"] = position
        if org_name:
            node["schema:affiliation"] = {"@type": ["schema:Organization"], "schema:name": org_name}
    elif org_name:
        node = {"@type": ["schema:Organization"], "schema:name": org_name}
    elif position:                         # a role-holder, as the ISO converter writes one
        node = {"@type": ["schema:Person"], "schema:name": position, "schema:jobTitle": position}
    else:
        return None
    ident = _user_id(el)
    if ident:
        node["schema:identifier"] = ident
    _contacts(el, node)
    return node


# ---------------------------------------------------------------------------
# value shapers: tf_<transform>(values, ctx) -> value or list
# ---------------------------------------------------------------------------

def tf_iri(values, ctx):
    return iri(values[0])


def tf_gbifdataset(values, ctx):
    key = text(values[0])
    return "https://www.gbif.org/dataset/" + key if key else None


def tf_identifier(values, ctx):
    doi = doi_of(text(values[0]))
    return doi_pv(doi) if doi else None


def tf_packageid(values, ctx):
    pid = text(values[0])
    system = (ctx["root"].get("system") or "").strip()
    pv = {"@type": ["schema:PropertyValue"],
          "schema:propertyID": ({"@id": system} if re.match(r"^https?://", system)
                                else system or "packageId"),
          "schema:value": pid}
    if system == "http://gbif.org":
        pv["schema:url"] = "https://www.gbif.org/dataset/" + pid
    elif doi_of(pid):
        pv["schema:url"] = "https://doi.org/" + doi_of(pid)
    return pv


def tf_altidentifier(values, ctx):
    used = ctx["doc"].get("schema:identifier", {})
    used = used.get("schema:value") if isinstance(used, dict) else used
    out = []
    for v in values:
        value = text(v)
        if not value or value == used or doi_of(value) == used:
            continue
        doi = doi_of(value)
        item = "https://doi.org/" + doi if doi else value
        if item not in out:
            out.append(item)
    return out


def tf_doiurl(values, ctx):
    doi = doi_of(text(values[0]))
    return "https://doi.org/" + doi if doi else None


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


def tf_paras(values, ctx):
    return [t for t in (paras(v) for v in values) if t]


def tf_creators(values, ctx):
    people = [a for a in (agent(v, ctx) for v in values) if a]
    return {"@list": people} if people else None


def tf_agent(values, ctx):
    return [a for a in (agent(v, ctx) for v in values) if a]


def tf_contributor(values, ctx):
    out = []
    for v in values:
        a = agent(v, ctx)
        if not a:
            continue
        role = text(v.find("role")) or ("contact" if v.tag == "contact" else "")
        r = {"@type": ["schema:Role"], "schema:contributor": a}
        if role:
            r["schema:roleName"] = role
        if r not in out:
            out.append(r)
    return out


def tf_funding(values, ctx):
    out = []
    for v in values:
        t = paras(v)
        if not _is_nil(t):
            out.append({"@type": ["schema:MonetaryGrant"], "schema:name": t})
    return out


_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?)?$")


def tf_date(values, ctx):
    for v in values:
        t = text(v)
        m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?)$", t)
        if m:                              # a date-time with no zone is taken as UTC
            return "%sT%sZ" % m.groups()
        if _DATE.match(t):
            return t
    return None


def tf_year(values, ctx):
    y = text(values[0])
    return y + "-01-01" if re.match(r"^\d{4}$", y) else None


_LABELS = {"purpose": "Purpose", "additionalInfo": "Additional information",
           "title": "Project", "studyAreaDescription": "Study area",
           "designDescription": "Study design"}


def tf_description(values, ctx):
    parts = []
    for v in values:
        t = paras(v)
        if not t:
            continue
        tag = v.tag
        if tag == "abstract" and v.getparent().tag == "project":
            parts.append("Project abstract: " + t)
        elif tag == "abstract":
            parts.append(t)
        else:
            parts.append("%s: %s" % (_LABELS.get(tag, tag), t))
    return "\n\n".join(parts) if parts else None


def _is_nil(t):
    """An empty or placeholder value (GBIF's IPT writes N/A and NA)."""
    return t.strip().lower() in ("", "n/a", "na", "none")


def tf_keywords(values, ctx):
    out = []
    for ks in values:
        thesaurus = first(ks, "keywordThesaurus")
        for k in ks.findall("keyword"):
            name = text(k)
            if not name:
                continue
            if _is_nil(thesaurus):
                item = name
            else:
                item = {"@type": ["schema:DefinedTerm"], "schema:name": name,
                        "schema:inDefinedTermSet": thesaurus}
            if item not in out:
                out.append(item)
    return out


def tf_taxon(values, ctx):
    out = []
    for t in values:
        name = first(t, "taxonRankValue")
        if not name:
            continue
        term = {"@type": ["schema:DefinedTerm"], "schema:name": name}
        rank = first(t, "taxonRankName")
        if rank:
            term["schema:description"] = "taxon rank: " + rank
        common = [text(c) for c in t.findall("commonName") if text(c)]
        if common:
            term["schema:alternateName"] = common
        if term not in out:
            out.append(term)
    return out


def tf_licensed(values, ctx):
    out = []
    for v in values:
        url, name = first(v, "url"), first(v, "licenseName")
        if re.match(r"^https?://", url):
            work = {"@type": ["schema:CreativeWork"], "schema:url": url}
            if name:
                work["schema:name"] = name
            out.append(work)
        elif name:
            out.append(name)
    return out


def tf_ulink(values, ctx):
    out = []
    for v in values:
        url = (v.get("url") or "").strip()
        if re.match(r"^https?://", url):
            work = {"@type": ["schema:CreativeWork"], "schema:url": url}
            label = first(v, "citetitle") or text(v)
            if label:
                work["schema:name"] = label
            out.append(work)
    return out


def _num(t):
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


def tf_place(values, ctx):
    out = []
    for g in values:
        place = {"@type": ["schema:Place"]}
        desc = first(g, "geographicDescription")
        if desc:
            place["schema:description"] = desc
        b = g.find("boundingCoordinates")
        if b is not None:
            w, e, n, s = (_num(first(b, k + "BoundingCoordinate"))
                          for k in ("west", "east", "north", "south"))
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


def _calendar(el):
    return first(el, "calendarDate") if el is not None else ""


def tf_interval(values, ctx):
    out = []
    for t in values:
        for r in t.findall("rangeOfDates"):
            a, b = _calendar(r.find("beginDate")), _calendar(r.find("endDate"))
            if a or b:
                out.append("%s/%s" % (a or "..", b or ".."))
        for d in t.findall("singleDateTime"):
            c = _calendar(d)
            if c:
                out.append(c)
    return out


_METHOD_NAMES = {"methodStep": "Method step", "sampling": "Sampling",
                 "qualityControl": "Quality control"}


def tf_method(values, ctx):
    out = []
    for v in values:
        if v.tag == "sampling":
            t = "\n\n".join(x for x in (paras(v.find("studyExtent/description")),
                                        paras(v.find("samplingDescription"))) if x)
        else:
            t = paras(v.find("description"))
        if t:
            out.append({"@type": ["schema:Action", "prov:Activity"],
                        "schema:name": _METHOD_NAMES[v.tag], "schema:actionProcess": t})
    return out


def tf_bibliography(values, ctx):
    out = []
    for c in values:
        ident = (c.get("identifier") or "").strip()
        doi = doi_of(re.sub(r"^doi:\s*", "doi:", ident, flags=re.I))
        url = "https://doi.org/" + doi if doi else (ident if re.match(r"^https?://", ident) else None)
        if not url:
            continue
        target = {"@type": ["schema:EntryPoint"], "schema:url": url}
        if text(c):
            target["schema:name"] = text(c)
        out.append({"@type": ["schema:LinkRole"], "schema:linkRelationship": "bibliography",
                    "schema:target": target})
    return out


def tf_download(values, ctx):
    out = []
    for u in values:
        url = text(u)
        if re.match(r"^https?://", url):
            out.append({"@type": ["schema:DataDownload"], "schema:contentUrl": url})
    return out


# --- data tables ---------------------------------------------------------------

def _xsd(attr_el):
    scale = attr_el.find("measurementScale")
    kinds = [c for c in scale if isinstance(c.tag, str)] if scale is not None else []
    kind = kinds[0].tag if kinds else ""
    if kind == "dateTime":                 # the format string decides, whatever storageType says
        fmt = first(kinds[0], "formatString")
        if ISO_DATE_FORMAT.match(fmt):
            return "xsd:dateTime" if "T" in fmt.upper() else "xsd:date"
        return "xsd:string"                # mm/dd/yyyy and the like are not XSD dates
    st = first(attr_el, "storageType")
    if st.lower() in XSD_TYPES:
        return XSD_TYPES[st.lower()]
    if kind in ("interval", "ratio"):
        return NUMBER_TYPES.get(first(kinds[0], ".//numberType"), "xsd:decimal")
    return "xsd:string"


def _unit(attr_el):
    for path in (".//unit/standardUnit", ".//unit/customUnit"):
        u = first(attr_el, path)
        if u:
            return u
    return ""


def _var_id(ctx, table_index, name):
    prefix = "#t%d/" % (table_index + 1) if ctx["n_tables"] > 1 else "#"
    return prefix + re.sub(r"\s+", "_", name)


def _attributes(table):
    return table.findall("attributeList/attribute")


def tf_variables(values, ctx):
    out = []
    for i, table in enumerate(values):
        for a in _attributes(table):
            name = first(a, "attributeName")
            if not name:
                continue
            var = {"@id": _var_id(ctx, i, name),
                   "@type": ["schema:PropertyValue", "cdi:InstanceVariable"],
                   "schema:name": name}
            label = first(a, "attributeLabel")
            if label and label != name:
                var["schema:alternateName"] = [label]
            definition = paras(a.find("attributeDefinition"))
            var["schema:description"] = definition or name
            unit = _unit(a)
            if unit:
                var["schema:unitText"] = unit
            var["cdi:hasIntendedDataType"] = _xsd(a)
            out.append(var)
    return out


def _delimiter(t):
    t = t.strip() if t not in ("\t", " ") else t
    return {"\\t": "\t", "tab": "\t", "0x09": "\t", "#x09": "\t"}.get(t.lower(), t)


def tf_datatable(values, ctx):
    out = []
    for i, table in enumerate(values):
        dist = {"@type": ["schema:DataDownload", "cdi:TabularTextDataSet"]}
        name = first(table, "entityName")
        if name:
            dist["schema:name"] = name
        desc = paras(table.find("entityDescription"))
        if desc:
            dist["schema:description"] = desc
        phys = table.find("physical")
        url = first(phys, "distribution/online/url") if phys is not None else ""
        dist["schema:contentUrl"] = url if re.match(r"^https?://", url) else NIL
        delimited = phys.find("dataFormat/textFormat/simpleDelimited") if phys is not None else None
        if delimited is not None:
            fd = _delimiter(delimited.findtext("fieldDelimiter") or "")
            dist["cdi:isDelimited"] = True
            if fd:
                dist["csvw:delimiter"] = fd
                fmt = {",": "text/csv", "\t": "text/tab-separated-values"}.get(fd)
                if fmt:
                    dist["schema:encodingFormat"] = [fmt]
            q = first(delimited, "quoteCharacter")
            if q:
                dist["csvw:quoteChar"] = q
            header = first(phys, "dataFormat/textFormat/numHeaderLines")
            if header.isdigit():
                dist["csvw:headerRowCount"] = int(header)
        mappings = []
        for j, a in enumerate(_attributes(table)):
            name = first(a, "attributeName")
            if name:
                mappings.append({"@type": ["cdif:PhysicalMapping"], "cdif:index": j,
                                 "cdif:physicalDataType": _xsd(a),
                                 "cdif:formats_InstanceVariable": {"@id": _var_id(ctx, i, name)}})
        if mappings:
            dist["cdif:hasPhysicalMapping"] = mappings
        out.append(dist)
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
    """Every <eml:eml> in the input: the root itself, or those it wraps."""
    return [e for e in root.iter() if isinstance(e.tag, str)
            and etree.QName(e).localname == "eml"
            and (etree.QName(e).namespace or "").startswith(EML_NS)]


def version_of(root):
    return (etree.QName(root).namespace or "").rsplit("eml-", 1)[-1]


def convert_eml_to_cdif(root, rows=None, detect=True, catalog=None, record_url=None,
                        landing_url=None):
    """One <eml:eml> element -> (CDIF JSON-LD dict, list of changes).

    landing_url is the dataset's page in the repository it came from, used as
    schema:url when the EML names none (DataONE: https://search.dataone.org/view/<id>).
    """
    rows = rows if rows is not None else load_table()
    doc = {"@context": dict(CDIF_CONTEXT)}
    record = {}
    ctx = {"root": root, "doc": doc,
           "n_tables": len(root.findall("dataset/dataTable"))}
    changes = apply_table(rows, root, doc, record, ctx)

    # --- what CDIF requires and EML may not supply -------------------------------
    if "@id" not in doc:
        doc["@id"] = "urn:sha1:%s" % hashlib.sha1(etree.tostring(root)).hexdigest()
        changes.append("@id made up (no packageId or DOI)")
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
        changes.append("schema:dateModified sentinel (no pubDate)")
    if not doc.get("schema:url"):
        doc["schema:url"] = landing_url or NIL
    if not doc.get("schema:license") and not doc.get("schema:conditionsOfAccess"):
        doc["schema:license"] = [{"@id": NIL}]
        changes.append("schema:license set to the OGC nil URI (no intellectualRights)")

    # --- the catalog record --------------------------------------------------------
    subject = {
        "@id": record_url or rid + "#metadata",
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:name": "EML metadata record for: %s" % doc["schema:name"][:120],
        "schema:about": {"@id": rid},
        "dcterms:conformsTo": [{"@id": "https://w3id.org/cdif/core/2.0"}],
        "schema:description": "Converted from EML %s to CDIF by eml_to_cdif.py." % version_of(root),
    }
    pid = (root.get("packageId") or "").strip()
    if pid:
        subject["schema:identifier"] = pid
    subject.update(record)
    if isinstance(subject.get("schema:maintainer"), list):
        subject["schema:maintainer"] = subject["schema:maintainer"][0]
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
    if re.match(r"^https?://", source):
        import urllib.request
        req = urllib.request.Request(source, headers={"User-Agent": "cdif-converters/0.1"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return etree.fromstring(r.read(), PARSER)
    return etree.parse(source, PARSER).getroot()


def _slug(text_):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text_ or "").strip("_")[:100]


def main():
    ap = argparse.ArgumentParser(description="Convert EML to CDIF JSON-LD")
    ap.add_argument("input", nargs="+", help="EML file(s) or URL(s)")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--record-url", help="URL of the EML document itself (the catalog record's @id)")
    ap.add_argument("--landing-url", help="the dataset's page in its repository, used as "
                                          "schema:url when the EML names none")
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
            print("%s: no EML document" % source, file=sys.stderr)
            continue
        record_url = args.record_url or (source if source.startswith("http") else None)
        for root in found:
            doc, changes = convert_eml_to_cdif(root, rows, not args.static_conformance,
                                               catalog, record_url if len(found) == 1 else None,
                                               args.landing_url if len(found) == 1 else None)
            stem = _slug(root.get("packageId")) if len(found) > 1 or source.startswith("http") \
                else Path(source).stem
            out = out_dir / ("%s.jsonld" % (stem or "eml"))
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

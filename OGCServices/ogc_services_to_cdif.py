#!/usr/bin/env python3
"""
ogc_services_to_cdif.py - Convert OGC WMS / WFS capabilities documents to CDIF JSON-LD.

Reads WMS 1.3.0 (WMS_Capabilities) and WFS 2.0 / 1.1 (WFS_Capabilities)
GetCapabilities responses; each document becomes one CDIF record describing the
service, as the ISO converter describes a service record:

  * @type schema:Dataset and schema:WebAPI, with the webAPI block's
    serviceType, termsOfService, documentation (the capabilities URL) and one
    potentialAction per operation;
  * each named WMS layer or WFS feature type under schema:dataset (what the
    service operates on), with its title, name, abstract, keywords and box. It
    is typed schema:CreativeWork, with additionalType 'WMS layer' or 'WFS
    feature type': as a schema:Dataset, CDIF would require each layer to carry
    a full record of its own (dates, licence, catalog record);
  * the service's extent, provider, contact and constraints on the record.

The table mappings/ogc-services-to-cdif.sssom.tsv drives the conversion: each
row names capabilities elements by an XPath from the document root
(subject_xpath; wms: is the WMS 1.3 namespace, and wfs: and ows: are bound at
run time to the document's own WFS and OWS Common versions, so one row serves
WMS 1.3, WFS 2.0 and WFS 1.1), the CDIF location it lands at
(object_json_path) and the shaper that builds the value (transform). Row order
is precedence for a scalar target; array targets accumulate.

Usage:
    python OGCServices/ogc_services_to_cdif.py capabilities.xml -o out/
    python OGCServices/ogc_services_to_cdif.py "https://ogc.bgs.ac.uk/cgi-bin/BGS_1GE_Geology/ows?service=WFS&request=GetCapabilities" -o out/
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

from lxml import etree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(ROOT / "validation"))
try:
    from detect_conformance import detect_cdif_conformance, apply_conformance
    _HAVE_DETECT = True
except Exception:
    _HAVE_DETECT = False

TABLE = ROOT / "mappings" / "ogc-services-to-cdif.sssom.tsv"
WMS = "http://www.opengis.net/wms"
WFS_NS = ("http://www.opengis.net/wfs/2.0", "http://www.opengis.net/wfs")
OWS_NS = ("http://www.opengis.net/ows/1.1", "http://www.opengis.net/ows/2.0",
          "http://www.opengis.net/ows")
XLINK = "http://www.w3.org/1999/xlink"
BASE_NS = {
    "wms": WMS, "xlink": XLINK,
    "inspire_common": "http://inspire.ec.europa.eu/schemas/common/1.0",
    "fes": "http://www.opengis.net/fes/2.0",
}
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
SERVICES = {"WMS_Capabilities": ("WMS", "OGC Web Map Service"),
            "WFS_Capabilities": ("WFS", "OGC Web Feature Service")}

CDIF_CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
}
ARRAY_TARGETS = {
    "schema:keywords", "schema:provider", "schema:contributor", "schema:conditionsOfAccess",
    "schema:license", "schema:potentialAction", "schema:dataset", "schema:spatialCoverage",
    "schema:relatedLink",
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


def namespaces(root):
    """The XPath prefixes for this document: wfs: and ows: bound to its own versions."""
    ns = dict(BASE_NS)
    used = {e.nsmap.get(p) for e in root.iter() if isinstance(e.tag, str) for p in e.nsmap}
    used |= {etree.QName(e).namespace for e in root.iter() if isinstance(e.tag, str)}
    ns["wfs"] = next((u for u in WFS_NS if u in used), WFS_NS[0])
    ns["ows"] = next((u for u in OWS_NS if u in used), OWS_NS[0])
    return ns


def kind_of(root):
    """('WMS'|'WFS', long name, version) for a capabilities root, or None."""
    entry = SERVICES.get(etree.QName(root).localname)
    return (entry[0], entry[1], root.get("version") or "") if entry else None


def local(el, name, ctx):
    """The first child named name in the WMS namespace or the document's OWS namespace."""
    for ns in (WMS, ctx["ns"]["ows"]):
        c = el.find("{%s}%s" % (ns, name))
        if c is not None:
            return c
    return None


def ltext(el, path, ctx):
    """Text at a /-separated path of local names, in WMS or OWS."""
    for part in path.split("/"):
        if el is None:
            return ""
        el = local(el, part, ctx)
    return text(el)


def _fmt(n):
    """A coordinate to six decimal places (about 0.1 m), as the ISO converter writes it."""
    return ("%.6f" % float(n)).rstrip("0").rstrip(".")


def _num(t):
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


def box_of(el, ctx):
    """(S, W, N, E) from a WMS EX_GeographicBoundingBox or an OWS WGS84BoundingBox, or None."""
    if el is None:
        return None
    if etree.QName(el).localname == "EX_GeographicBoundingBox":
        w, e, s, n = (_num(ltext(el, k, ctx)) for k in (
            "westBoundLongitude", "eastBoundLongitude", "southBoundLatitude", "northBoundLatitude"))
    else:                                  # WGS84BoundingBox: longitude first
        lo = [_num(x) for x in ltext(el, "LowerCorner", ctx).split()]
        up = [_num(x) for x in ltext(el, "UpperCorner", ctx).split()]
        if len(lo) != 2 or len(up) != 2:
            return None
        (w, s), (e, n) = lo, up
    if None in (s, w, n, e):
        return None
    return s, w, n, e


def place(box):
    s, w, n, e = box
    if s == n and w == e:
        geo = {"@type": ["schema:GeoCoordinates"], "schema:latitude": s, "schema:longitude": w}
    else:
        geo = {"@type": ["schema:GeoShape"], "schema:box": " ".join(_fmt(x) for x in (s, w, n, e))}
    return {"@type": ["schema:Place"], "schema:geo": geo}


def keyword_items(els, ctx):
    """WMS Keyword elements or OWS Keywords blocks -> DefinedTerms or strings."""
    out = []
    for el in els:
        if etree.QName(el).localname == "Keywords":            # OWS: <Keywords><Keyword/>..<Type/>
            vocab = ltext(el, "Type", ctx)
            type_el = local(el, "Type", ctx)
            if type_el is not None and type_el.get("codeSpace"):
                vocab = type_el.get("codeSpace")
            words = [text(k) for k in el.findall("{%s}Keyword" % ctx["ns"]["ows"])]
        else:                                                    # WMS: <Keyword vocabulary="">
            vocab, words = (el.get("vocabulary") or "").strip(), [text(el)]
        for w in words:
            if not w:
                continue
            item = {"@type": ["schema:DefinedTerm"], "schema:name": w,
                    "schema:inDefinedTermSet": vocab} if vocab else w
            if isinstance(item, dict) and len(w) < 3:
                item["schema:termCode"] = w     # CDIF wants a name of 3+ characters, else a code
            if item not in out:
                out.append(item)
    return out


def _link(url, relationship, name=None):
    target = {"@type": ["schema:EntryPoint"], "schema:url": url}
    if name:
        target["schema:name"] = name
    return {"@type": ["schema:LinkRole"], "schema:linkRelationship": relationship,
            "schema:target": target}


# ---------------------------------------------------------------------------
# value shapers: tf_<transform>(values, ctx) -> value or list
# ---------------------------------------------------------------------------

def tf_servicetype(values, ctx):
    code, name, version = ctx["kind"]
    term = {"@type": ["schema:DefinedTerm"], "schema:name": "%s %s" % (name, version),
            "schema:termCode": "OGC:" + code}
    return term


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


def tf_keywords(values, ctx):
    return keyword_items(values, ctx)


def tf_url(values, ctx):
    for v in values:
        t = text(v)
        if _http(t):
            return t
    return None


def _organization(c, ctx):
    """WMS ContactInformation or OWS ServiceProvider -> Organization."""
    if etree.QName(c).localname == "ContactInformation":
        name = ltext(c, "ContactPersonPrimary/ContactOrganization", ctx)
        email, phone = ltext(c, "ContactElectronicMailAddress", ctx), ltext(c, "ContactVoiceTelephone", ctx)
    else:
        name = ltext(c, "ProviderName", ctx)
        email = ltext(c, "ServiceContact/ContactInfo/Address/ElectronicMailAddress", ctx)
        phone = ltext(c, "ServiceContact/ContactInfo/Phone/Voice", ctx)
    if not name:
        return None
    org = {"@type": ["schema:Organization"], "schema:name": name}
    if email:
        org["schema:email"] = email
    if phone:
        org["schema:telephone"] = phone
    return org


def tf_provider(values, ctx):
    return [o for o in (_organization(v, ctx) for v in values) if o]


def tf_contact(values, ctx):
    out = []
    for c in values:
        if etree.QName(c).localname == "ContactInformation":
            name = ltext(c, "ContactPersonPrimary/ContactPerson", ctx)
            org = ltext(c, "ContactPersonPrimary/ContactOrganization", ctx)
            position = ltext(c, "ContactPosition", ctx)
            email, phone = ltext(c, "ContactElectronicMailAddress", ctx), ltext(c, "ContactVoiceTelephone", ctx)
        else:                                                  # OWS ServiceContact
            name, position = ltext(c, "IndividualName", ctx), ltext(c, "PositionName", ctx)
            org = ltext(c.getparent(), "ProviderName", ctx)
            email = ltext(c, "ContactInfo/Address/ElectronicMailAddress", ctx)
            phone = ltext(c, "ContactInfo/Phone/Voice", ctx)
        if not (name or position):
            continue
        person = {"@type": ["schema:Person"], "schema:name": name or position}
        if position:
            person["schema:jobTitle"] = position
        if org:
            person["schema:affiliation"] = {"@type": ["schema:Organization"], "schema:name": org}
        if email:
            person["schema:email"] = email
        if phone:
            person["schema:telephone"] = phone
        out.append({"@type": ["schema:Role"], "schema:roleName": "pointOfContact",
                    "schema:contributor": person})
    return out


def tf_terms(values, ctx):
    parts = []
    for v in values:
        t = text(v)
        if t:
            label = "Fees" if etree.QName(v).localname == "Fees" else "Access constraints"
            part = "%s: %s" % (label, t)
            if part not in parts:
                parts.append(part)
    return "; ".join(parts) if parts else None


_LICENSE_URL = re.compile(r"https?://(?:www\.)?(?:creativecommons\.org|opendatacommons\.org|"
                          r"spdx\.org|etalab\.gouv\.fr|data\.gov\.uk|"
                          r"www\.nationalarchives\.gov\.uk/doc/open-government-licence)"
                          r"[^\s<>\"')]*", re.I)


def tf_licenseurl(values, ctx):
    out = []
    for v in values:
        for url in _LICENSE_URL.findall(text(v)):
            work = {"@type": ["schema:CreativeWork"], "schema:url": url.rstrip(".,;")}
            if work not in out:
                out.append(work)
    return out


def _dcp(op, ctx):
    """(href, method) of an operation's HTTP Get, else Post."""
    ows = ctx["ns"]["ows"]
    for method in ("Get", "Post"):
        for el in op.iter("{%s}%s" % (WMS, method), "{%s}%s" % (ows, method)):
            href = el.get("{%s}href" % XLINK)
            if href is None:
                res = el.find("{%s}OnlineResource" % WMS)
                href = res.get("{%s}href" % XLINK) if res is not None else None
            if _http(href or ""):
                return href.strip(), method.upper()
    return None, None


def tf_operations(values, ctx):
    out = []
    for op in values:
        name = op.get("name") or etree.QName(op).localname
        href, method = _dcp(op, ctx)
        if not href:
            continue
        target = {"@type": ["schema:EntryPoint"], "schema:urlTemplate": href,
                  "schema:httpMethod": [method]}
        formats = [text(f) for f in op.findall("{%s}Format" % WMS) if text(f)]
        if not formats:                    # OWS: Parameter name=outputFormat / AcceptFormats
            for p in op.iter("{%s}Parameter" % ctx["ns"]["ows"]):
                if (p.get("name") or "").lower() in ("outputformat", "acceptformats", "format"):
                    formats += [text(v) for v in p.iter("{%s}Value" % ctx["ns"]["ows"]) if text(v)]
        if formats:
            target["schema:contentType"] = list(dict.fromkeys(formats))
        out.append({"@type": ["schema:Action"], "schema:name": name, "schema:target": target})
    return out


def _inherited_box(layer, ctx):
    """A WMS layer's own EX_GeographicBoundingBox, else its nearest ancestor's."""
    while layer is not None and etree.QName(layer).localname == "Layer":
        b = box_of(layer.find("{%s}EX_GeographicBoundingBox" % WMS), ctx)
        if b:
            return b
        layer = layer.getparent()
    return None


def tf_layers(values, ctx):
    out = []
    for lyr in values:
        name = ltext(lyr, "Name", ctx) or text(lyr.find("{%s}Name" % ctx["ns"]["wfs"]))
        title = ltext(lyr, "Title", ctx) or text(lyr.find("{%s}Title" % ctx["ns"]["wfs"]))
        abstract = ltext(lyr, "Abstract", ctx) or text(lyr.find("{%s}Abstract" % ctx["ns"]["wfs"]))
        ds = {"@id": "%s#layer/%s" % (ctx["rid"], quote(name or title, safe=":._-")),
              "@type": ["schema:CreativeWork"],
              "schema:additionalType": ["WMS layer" if etree.QName(lyr).localname == "Layer"
                                        else "WFS feature type"],
              "schema:name": title or name}
        if name:
            ds["schema:identifier"] = name
        if abstract:
            ds["schema:description"] = abstract
        kw = keyword_items(lyr.findall("{%s}KeywordList/{%s}Keyword" % (WMS, WMS)) +
                           lyr.findall("{%s}Keywords" % ctx["ns"]["ows"]), ctx)
        if kw:
            ds["schema:keywords"] = kw
        if etree.QName(lyr).localname == "Layer":
            box = _inherited_box(lyr, ctx)
        else:
            box = box_of(lyr.find("{%s}WGS84BoundingBox" % ctx["ns"]["ows"]), ctx)
        if box:
            ds["schema:spatialCoverage"] = [place(box)]
        links = []
        for m in lyr.findall("{%s}MetadataURL" % WMS) + lyr.findall("{%s}MetadataURL" % ctx["ns"]["wfs"]):
            res = m.find("{%s}OnlineResource" % WMS)
            href = (res.get("{%s}href" % XLINK) if res is not None else m.get("{%s}href" % XLINK)) or ""
            if _http(href.strip()):
                links.append(_link(href.strip(), "describedby"))
        if links:
            ds["schema:relatedLink"] = links
        if ds["schema:name"]:
            out.append(ds)
    return out


def tf_extent(values, ctx):
    boxes = [b for b in (box_of(v, ctx) for v in values) if b]
    if not boxes:
        return None
    s = min(b[0] for b in boxes)
    w = min(b[1] for b in boxes)
    n = max(b[2] for b in boxes)
    e = max(b[3] for b in boxes)
    return [place((s, w, n, e))]


def tf_describedby(values, ctx):
    out = []
    for v in values:
        t = text(v)
        if _http(t):
            link = _link(t, "describedby", "INSPIRE service metadata")
            if link not in out:
                out.append(link)
    return out


def tf_date(values, ctx):
    for v in values:
        t = text(v)
        if re.match(r"^\d{4}-\d{2}(-\d{2})?$", t):
            return t
        m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?)(Z|[+-]\d{2}:?\d{2})?$", t)
        if m:
            return "%sT%s%s" % (m.group(1), m.group(2), m.group(3) or "Z")
    return None


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
            values = root.xpath(xpath, namespaces=ctx["ns"])
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

def capabilities_url(root, ctx, given=None):
    """The GetCapabilities URL: the one given, else the operation's address with the query."""
    if given:
        return given
    for op in root.xpath("wms:Capability/wms:Request/wms:GetCapabilities | "
                         "ows:OperationsMetadata/ows:Operation[@name='GetCapabilities']",
                         namespaces=ctx["ns"]):
        href, _ = _dcp(op, ctx)
        if href:
            sep = "&" if "?" in href else "?"
            if not href.endswith(("?", "&")):
                href += sep
            return href + "service=%s&request=GetCapabilities" % ctx["kind"][0]
    return None


def endpoint_of(url):
    if not url:
        return None
    parts = urlsplit(url)
    return "%s://%s%s" % (parts.scheme, parts.netloc, parts.path)


def convert_capabilities_to_cdif(root, rows=None, detect=True, catalog=None,
                                 capabilities=None, retrieved=None):
    """A WMS or WFS capabilities root -> (CDIF JSON-LD dict, list of changes).

    capabilities is the GetCapabilities URL the document was fetched from;
    retrieved, the date it was fetched, dates the catalog record.
    """
    rows = rows if rows is not None else load_table()
    kind = kind_of(root)
    if kind is None:
        raise ValueError("not a WMS_Capabilities or WFS_Capabilities document")
    ctx = {"ns": namespaces(root), "kind": kind}
    cap = capabilities_url(root, ctx, capabilities)
    rid = cap or "urn:sha1:%s" % hashlib.sha1(etree.tostring(root)).hexdigest()
    ctx["rid"] = rid                       # layers are identified as <rid>#layer/<Name>
    doc = {"@context": dict(CDIF_CONTEXT)}
    record = {}
    changes = apply_table(rows, root, doc, record, ctx)
    if not cap:
        changes.append("@id made up (no GetCapabilities address)")

    doc = {"@context": doc.pop("@context"), "@id": rid,
           "@type": ["schema:Dataset", "schema:WebAPI"], **doc}

    # --- what CDIF and the webAPI block require ----------------------------------
    if not doc.get("schema:name"):
        doc["schema:name"] = "%s at %s" % (kind[1], endpoint_of(cap) or "an unknown address")
        changes.append("schema:name made up")
    doc.setdefault("schema:identifier", rid)
    if not doc.get("schema:description"):
        doc["schema:description"] = doc["schema:name"]
        changes.append("schema:description from the name")
    if not doc.get("schema:dateModified"):
        doc["schema:dateModified"] = "1900-01-01"
        changes.append("schema:dateModified sentinel (capabilities carry no date)")
    if not doc.get("schema:url"):
        doc["schema:url"] = endpoint_of(cap) or NIL
    if not doc.get("schema:license") and not doc.get("schema:conditionsOfAccess"):
        doc["schema:license"] = [{"@id": NIL}]
        changes.append("schema:license set to the OGC nil URI (no constraints)")
    doc.setdefault("schema:termsOfService", "not specified")
    if cap:
        doc["schema:documentation"] = {"@type": ["schema:CreativeWork"], "schema:url": cap,
                                       "schema:name": "%s %s capabilities" % (kind[0], kind[2])}
    if not doc.get("schema:potentialAction"):
        doc["schema:potentialAction"] = [{"@type": ["schema:Action"], "schema:name": "missing",
                                          "schema:target": {"@type": ["schema:EntryPoint"],
                                                            "schema:urlTemplate": NIL}}]
        changes.append("schema:potentialAction sentinel (no operation addresses)")

    # --- the catalog record --------------------------------------------------------
    subject = {
        "@id": rid + "#metadata",
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:name": "OGC %s capabilities for: %s" % (kind[0], doc["schema:name"][:120]),
        "schema:about": {"@id": rid},
        "dcterms:conformsTo": [{"@id": "https://w3id.org/cdif/core/2.0"}],
        "schema:description": "Converted from an OGC %s %s GetCapabilities response to CDIF by "
                              "ogc_services_to_cdif.py." % (kind[0], kind[2]),
    }
    if retrieved:
        subject["schema:dateModified"] = retrieved
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
    ap = argparse.ArgumentParser(description="Convert OGC WMS / WFS capabilities to CDIF JSON-LD")
    ap.add_argument("input", nargs="+", help="capabilities file(s) or GetCapabilities URL(s)")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--capabilities-url", help="the GetCapabilities URL a file was fetched from "
                                               "(the record's @id)")
    ap.add_argument("--catalog-name", help="name of the catalogue that listed the service")
    ap.add_argument("--catalog-url", help="URL of that catalogue")
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
        if kind_of(root) is None:
            print("%s: not a WMS or WFS capabilities document" % source, file=sys.stderr)
            continue
        cap = args.capabilities_url or (source if _http(source) else None)
        doc, changes = convert_capabilities_to_cdif(root, rows, not args.static_conformance,
                                                    catalog, cap)
        stem = _slug(urlsplit(source).netloc + urlsplit(source).path) if _http(source) \
            else Path(source).stem
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

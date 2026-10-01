#!/usr/bin/env python3
"""
umm_to_cdif.py - Convert NASA CMR UMM-C collection metadata to CDIF.

Reads UMM-C JSON -- a CMR search result (`collections.umm_json[_vX_Y_Z]`), one
search-result item ({"meta": ..., "umm": ...}), or a bare UMM-C document --
from a file or a URL, and writes one CDIF core/discovery JSON-LD record per
collection.

The property mapping is the SSSOM table converters/mappings/ummc-to-cdif.sssom.tsv,
read at runtime: each row names a UMM-C path (subject_id + subject_filter), the
CDIF location it lands at (object_json_path) and the shaper that builds the
value (transform). This file holds the shapers and the structural work no table
can express: @id, the catalog record, fallbacks for what CDIF requires, and
passthrough of UMM-C properties the table does not map.

Usage:
    # A CMR search result (one or many collections), from a file or URL
    python UMM/umm_to_cdif.py examples/umm-c-aq/lcs_aq_airgradient.json -o out/
    python UMM/umm_to_cdif.py "https://cmr.earthdata.nasa.gov/search/collections.umm_json?concept-id[]=C4178560190-LARC_CLOUD" -o out/

    # Frame and validate each output against the CDIF Discovery schema
    python UMM/umm_to_cdif.py in.json -o out/ --validate
"""

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load_table(path):
    """Rows of the SSSOM TSV as dicts, in file order.

    Parsed as quoted TSV rather than split on tabs: a spreadsheet save wraps
    every cell that contains a comma in double quotes, and subject_filter
    values ('URLContentType=DistributionURL,Type=GET DATA') contain commas."""
    import csv
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh, delimiter="\t")]

# detect_conformance lives in the `validation` submodule; best-effort, as in
# the other format -> CDIF converters.
sys.path.insert(0, str(ROOT / "validation"))
try:
    from detect_conformance import detect_conformance, apply_conformance
    _HAVE_DETECT = True
except Exception:
    _HAVE_DETECT = False

TABLE = ROOT / "mappings" / "ummc-to-cdif.sssom.tsv"
NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
CMR_CONCEPT = "https://cmr.earthdata.nasa.gov/search/concepts/"
KMS_SCHEME = "https://cmr.earthdata.nasa.gov/kms/concepts/concept_scheme/"

CDIF_CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
}
# Declared only when the record uses them.
OPTIONAL_PREFIXES = {
    "geosparql": "http://www.opengis.net/ont/geosparql#",
    "ummc": "https://cdn.earthdata.nasa.gov/umm/collection/v1.18.6#",
}

# Targets that gather every source instead of keeping the first.
ARRAY_TARGETS = {
    "schema:keywords", "schema:spatialCoverage", "schema:temporalCoverage",
    "schema:variableMeasured", "schema:distribution", "schema:relatedLink",
    "schema:contributor", "schema:provider", "schema:sameAs", "schema:license",
    "schema:conditionsOfAccess", "schema:measurementTechnique",
    "prov:wasGeneratedBy", "schema:additionalProperty", "schema:image",
}
ORDERED_TARGETS = {"schema:creator"}
PLACEHOLDERS = {"", "none", "not provided", "n/a", "na", "not applicable",
                "not specified", "unknown"}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _is_placeholder(text):
    return not isinstance(text, str) or text.strip().lower() in PLACEHOLDERS


def _url(text):
    """A usable absolute URL, adding https:// to bare host names
    ('www.airgradient.com'), or None."""
    if not isinstance(text, str) or not text.strip():
        return None
    t = text.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", t):
        return t
    if re.match(r"^[\w-]+(\.[\w-]+)+(/.*)?$", t):
        return "https://" + t
    return None


# The CDIF date pattern: YYYY-MM .. YYYY-MM-DDThh:mm:ss[Z|offset], no fractions.
_CDIF_DATE_RE = re.compile(
    r"^[1-2][0-9]{3}-(0[1-9]|1[0-2])(-([0-2][0-9]|3[01])"
    r"(T([01][0-9]|2[0-3])(:[0-5][0-9])?(:[0-5][0-9](Z|[+-][0-2][0-9]:[0-5][0-9])?)?)?)?$")


def _date(value):
    if not isinstance(value, str):
        return None
    v = re.sub(r"\.\d+", "", value.strip())      # drop fractional seconds
    if _CDIF_DATE_RE.match(v):
        return v
    for n in (10, 7):
        if _CDIF_DATE_RE.match(v[:n]):
            return v[:n]
    return None


def _term(name, scheme=None, code=None, url=None):
    t = {"@type": ["schema:DefinedTerm"], "schema:name": name}
    if code:
        t["schema:termCode"] = code
    if scheme:
        t["schema:inDefinedTermSet"] = scheme
    if url:
        t["schema:url"] = url
    return t


def _link(relationship, url, name=None, fmt=None):
    target = {"@type": ["schema:EntryPoint"], "schema:url": url}
    if name:
        target["schema:name"] = name
    if fmt and not _is_placeholder(fmt):
        target["schema:encodingFormat"] = fmt
    return {"@type": ["schema:LinkRole"],
            "schema:linkRelationship": relationship,
            "schema:target": target}


def _doi_url(umm):
    doi = (umm.get("DOI") or {}).get("DOI")
    if not doi:
        return None
    if doi.lower().startswith("http"):
        return doi
    base = (umm.get("DOI") or {}).get("Authority") or "https://doi.org/"
    base = _url(base) or "https://doi.org/"
    return base.rstrip("/") + "/" + doi


# ---------------------------------------------------------------------------
# selecting UMM values for a table row
# ---------------------------------------------------------------------------

def _parse_filters(text):
    """'URLContentType=CollectionURL,Type=DATA SET LANDING PAGE' ->
    [('URLContentType', {'collectionurl'}), ('Type', {'data set landing page'})]."""
    out = []
    for part in (text or "").split(";"):
        for cond in part.split(","):
            if "=" in cond:
                key, alts = cond.split("=", 1)
                out.append((key.strip(),
                            {a.strip().lower() for a in alts.split("|")}))
    return out


def _passes(node, filters):
    for key, alts in filters:
        vals = [str(v).lower() for v in _as_list(node.get(key))]
        if not any(v in alts for v in vals):
            return False
    return True


def select(umm, meta, subject_id, subject_filter, umm_vars=None):
    """Every value at `subject_id`, keeping only array members that pass the
    filter. The filter applies at the first level whose items carry any of
    the filter keys. ummc: paths start at the UMM-C record, cmrmeta: at the
    search-result meta, ummvar: at {"Variable": [UMM-Var records]}."""
    prefix, path = subject_id.split(":", 1)
    root = {"cmrmeta": meta, "ummvar": umm_vars}.get(prefix, umm)
    if root is None:
        return []
    filters = _parse_filters(subject_filter)
    nodes, applied = [root], not filters
    for seg in path.split("."):
        nxt = []
        for n in nodes:
            if isinstance(n, dict) and seg in n:
                nxt.extend(_as_list(n[seg]))
        if not applied and any(isinstance(x, dict) and
                               any(k in x for k, _ in filters) for x in nxt):
            nxt = [x for x in nxt if isinstance(x, dict) and _passes(x, filters)]
            applied = True
        nodes = nxt
    return [n for n in nodes if n not in (None, "", [], {})]


# ---------------------------------------------------------------------------
# shapers: fn(values, ctx) -> a value, a list of values, or None
# ---------------------------------------------------------------------------

def tf_text(values, ctx):
    for v in values:
        if isinstance(v, (str, int, float)) and not _is_placeholder(str(v)):
            return str(v).strip()
    return None


def tf_describe(values, ctx):
    """A labelled paragraph for schema:description. Dated entries (DataDates
    items) are listed as 'TYPE date'."""
    dated = ["%s %s" % (v.get("Type"), _date(v.get("Date")) or v.get("Date"))
             for v in values if isinstance(v, dict) and v.get("Date")]
    # 'Unknown' is dropped elsewhere as a placeholder, but in a labelled
    # paragraph it is the answer (DOI.MissingReason = 'Unknown').
    text = "; ".join(dated) if dated else next(
        (str(v).strip() for v in values if isinstance(v, (str, int, float))
         and (not _is_placeholder(str(v)) or str(v).strip().lower() == "unknown")),
        None)
    if text:
        label = ctx["rule"]["subject_label"]
        return "%s: %s" % (label[:1].upper() + label[1:], text)
    return None


_LANG = {"eng": "en", "english": "en", "fre": "fr", "fra": "fr", "french": "fr",
         "ger": "de", "deu": "de", "german": "de", "spa": "es", "spanish": "es",
         "ita": "it", "por": "pt", "rus": "ru", "jpn": "ja", "chi": "zh",
         "zho": "zh", "kor": "ko", "dut": "nl", "nld": "nl"}


def tf_langcode(values, ctx):
    text = tf_text(values, ctx)
    if not text:
        return None
    t = text.lower()
    return _LANG.get(t, t if re.match(r"^[a-z]{2}(-[A-Za-z0-9]+)*$", t) else None)


def tf_date(values, ctx):
    dates = [d for d in (_date(v) for v in values) if d]
    if not dates:
        return None
    if ctx["target"].endswith("dateModified"):
        return max(dates)
    if ctx["target"] == "schema:temporalCoverage":
        return dates
    return min(dates)


def tf_boolean(values, ctx):
    for v in values:
        if isinstance(v, bool):
            return v
    return None


def tf_list(values, ctx):
    out = []
    for v in values:
        if isinstance(v, str) and not _is_placeholder(v) and v not in out:
            out.append(v.strip())
    return out


def tf_iri(values, ctx):
    for v in values:
        u = _url(v)
        if u:
            return [{"@id": u}] if ctx["target"] == "schema:license" else u
    return None


def tf_prefixedtext(values, ctx):
    label = ctx["rule"]["subject_id"].split(":", 1)[1].split(".")[0]
    return ["%s: %s" % (label, v.strip()) for v in values
            if isinstance(v, str) and not _is_placeholder(v)]


# --- identity ---------------------------------------------------------------

def tf_doi(values, ctx):
    doi = tf_text(values, ctx)
    if not doi:
        return None
    return {"@type": ["schema:PropertyValue"],
            "schema:propertyID": "https://registry.identifiers.org/registry/doi",
            "schema:value": "doi:" + doi,
            "schema:url": _doi_url(ctx["umm"])}


def tf_cmrid(values, ctx):
    cid = tf_text(values, ctx)
    if not cid:
        return None
    return {"@type": ["schema:PropertyValue"],
            "schema:propertyID": "NASA CMR concept id",
            "schema:value": cid, "schema:url": CMR_CONCEPT + cid}


def tf_cmrurl(values, ctx):
    cid = tf_text(values, ctx)
    return [CMR_CONCEPT + cid] if cid else None


def tf_otheridentifier(values, ctx):
    out = []
    for v in values:
        if isinstance(v, dict) and v.get("Identifier"):
            kind = v.get("DescriptionOfOtherType") if v.get("Type") == "Other" else v.get("Type")
            out.append("%s:%s" % (kind, v["Identifier"]) if kind else v["Identifier"])
    return out


def tf_associateddoi(values, ctx):
    out = []
    for v in values:
        if isinstance(v, dict) and v.get("DOI"):
            rel = v.get("Type") or "Related Dataset"
            if rel == "Other" and v.get("DescriptionOfOtherType"):
                rel = v["DescriptionOfOtherType"]
            url = v["DOI"] if v["DOI"].startswith("http") else "https://doi.org/" + v["DOI"]
            out.append(_link(rel, url, v.get("Title")))
    return out


# --- agents -------------------------------------------------------------------

def _contact_details(info, agent):
    """Fold UMM ContactInformation into a schema.org agent."""
    if not isinstance(info, dict):
        return agent
    for mech in _as_list(info.get("ContactMechanisms")):
        kind, val = (mech.get("Type") or "").lower(), mech.get("Value")
        if not val:
            continue
        if kind == "email" and "schema:contactPoint" not in agent:
            agent["schema:contactPoint"] = {"@type": ["schema:ContactPoint"],
                                            "schema:email": val}
        elif kind in ("telephone", "direct line", "mobile", "u.s. toll free") \
                and "schema:telephone" not in agent:
            agent["schema:telephone"] = val
    for ru in _as_list(info.get("RelatedUrls")):
        u = _url(ru.get("URL"))
        if u and ru.get("URLContentType") in ("DataCenterURL", "DataContactURL") \
                and "schema:url" not in agent:
            agent["schema:url"] = u
    for addr in _as_list(info.get("Addresses"))[:1]:
        pa = {"@type": ["schema:PostalAddress"]}
        street = ", ".join(_as_list(addr.get("StreetAddresses")))
        for key, val in (("schema:streetAddress", street),
                         ("schema:addressLocality", addr.get("City")),
                         ("schema:addressRegion", addr.get("StateProvince")),
                         ("schema:postalCode", addr.get("PostalCode")),
                         ("schema:addressCountry", addr.get("Country"))):
            if val:
                pa[key] = val
        if len(pa) > 1:
            agent["schema:address"] = pa
    return agent


def _organization(dc):
    org = {"@type": ["schema:Organization"],
           "schema:name": dc.get("LongName") or dc.get("ShortName")}
    if dc.get("LongName") and dc.get("ShortName"):
        org["schema:alternateName"] = dc["ShortName"]
    return _contact_details(dc.get("ContactInformation"), org)


def _person_or_group(c):
    if c.get("GroupName"):
        agent = {"@type": ["schema:Organization"], "schema:name": c["GroupName"]}
    else:
        parts = [c.get("FirstName"), c.get("MiddleName"), c.get("LastName")]
        agent = {"@type": ["schema:Person"],
                 "schema:name": " ".join(p for p in parts if p)}
        if c.get("FirstName"):
            agent["schema:givenName"] = c["FirstName"]
        agent["schema:familyName"] = c.get("LastName")
    affiliation = c.get("_affiliation") or c.get("NonDataCenterAffiliation")
    if affiliation and agent["@type"] == ["schema:Person"]:
        agent["schema:affiliation"] = {"@type": ["schema:Organization"],
                                       "schema:name": affiliation}
    return _contact_details(c.get("ContactInformation"), agent)


def _role(agent, role_name):
    return {"@type": ["schema:Role"], "schema:roleName": role_name,
            "schema:contributor": agent}


def tf_datacenter(values, ctx):
    orgs = [_organization(v) for v in values if isinstance(v, dict)]
    return orgs or None


def tf_datacenterrole(values, ctx):
    out = []
    for v in values:
        if isinstance(v, dict):
            for role in _as_list(v.get("Roles")):
                out.append(_role(_organization(v), role))
    return out


def tf_contact(values, ctx):
    agents = [(v, _person_or_group(v)) for v in values if isinstance(v, dict)]
    if ctx["target"] == "schema:contributor":
        return [_role(a, ", ".join(_as_list(v.get("Roles")))) for v, a in agents]
    return [a for _, a in agents] or None


def tf_agentname(values, ctx):
    name = tf_text(values, ctx)
    return {"@type": ["schema:Organization"], "schema:name": name} if name else None


_ORG_WORDS = re.compile(
    r"\b(team|office|center|centre|project|group|nasa|noaa|university|"
    r"laboratory|institute|agency|daac|program|consortium|survey|service|"
    r"division|department|science)\b|[()/]", re.I)


def tf_citationcreators(values, ctx):
    """Split a free-text author string into an ordered list of agents.

    'A B, C D and E F' -> three Persons; 'Thornton, M.M., R. Shrestha' keeps
    'Thornton, M.M.' together (a piece of bare initials joins the name before
    it). A string that reads as an organization stays one Organization."""
    text = tf_text(values, ctx)
    if not text:
        return None
    if _ORG_WORDS.search(text) and ";" not in text:
        return [{"@type": ["schema:Organization"], "schema:name": text}]
    pieces = re.split(r"\s*;\s*", text) if ";" in text else \
        re.split(r"\s*,\s*|\s+and\s+|\s*&\s*", text)
    names = []
    for p in (x.strip().rstrip(".") for x in pieces):
        if not p:
            continue
        if names and re.match(r"^([A-Z]\.?\s*-?){1,4}$", p):
            names[-1] = "%s, %s" % (names[-1], p)       # 'Thornton' + 'M.M'
        else:
            names.append(p)
    return [{"@type": ["schema:Person"], "schema:name": n} for n in names]


# --- keywords -------------------------------------------------------------

_SK_LEVELS = ("Category", "Topic", "Term", "VariableLevel1", "VariableLevel2",
              "VariableLevel3", "DetailedVariable")


def _sk_path(kw):
    return [kw[k] for k in _SK_LEVELS if kw.get(k)]


def tf_gcmdkeyword(values, ctx):
    out = []
    for kw in values:
        if isinstance(kw, dict):
            path = _sk_path(kw)
            out.append(_term(" > ".join(path), KMS_SCHEME + "sciencekeywords",
                             url=ctx["kms"](path[-1], "sciencekeywords")))
    return out


def tf_ummvar(values, ctx):
    """UMM-Var records -> variableMeasured PropertyValues."""
    out = []
    for v in values:
        if not isinstance(v, dict) or not v.get("Name"):
            continue
        pv = {"@type": ["schema:PropertyValue"], "schema:name": v["Name"]}
        if v.get("LongName") and v["LongName"] != v["Name"]:
            pv["schema:alternateName"] = v["LongName"]
        if v.get("Definition"):
            pv["schema:description"] = v["Definition"]
        if v.get("StandardName"):
            pv["schema:propertyID"] = ("http://vocab.nerc.ac.uk/standard_name/%s/"
                                       % v["StandardName"])
        if v.get("Units") and not _is_placeholder(v["Units"]):
            pv["schema:unitText"] = v["Units"]
        # ValidRanges are in stored (packed) units, as CF valid_range is; with
        # Scale/Offset they are unpacked so they agree with unitText.
        rng = (_as_list(v.get("ValidRanges")) or [{}])[0]
        scale = v.get("Scale") if isinstance(v.get("Scale"), (int, float)) else 1
        offset = v.get("Offset") if isinstance(v.get("Offset"), (int, float)) else 0
        for src, dst in (("Min", "schema:minValue"), ("Max", "schema:maxValue")):
            if isinstance(rng.get(src), (int, float)):
                val = rng[src] * scale + offset
                pv[dst] = round(val, 6) if (scale, offset) != (1, 0) else rng[src]
        if v.get("_concept_id"):
            pv["schema:url"] = CMR_CONCEPT + v["_concept_id"]
        out.append(pv)
    return out


def tf_variable(values, ctx):
    """Fallback when there are no UMM-Var records: the deepest level of each
    science keyword that reaches VariableLevel1 (Term-level keywords are
    topics, not variables)."""
    if ctx.get("umm_vars", {}).get("Variable"):
        return None
    out, seen = [], set()
    for kw in values:
        if isinstance(kw, dict) and kw.get("VariableLevel1"):
            path = _sk_path(kw)
            if path[-1] in seen:
                continue
            seen.add(path[-1])
            pv = {"@type": ["schema:PropertyValue"], "schema:name": path[-1],
                  "schema:description": "GCMD science keyword: " + " > ".join(path)}
            uri = ctx["kms"](path[-1], "sciencekeywords")
            if uri:
                pv["schema:propertyID"] = uri
            out.append(pv)
    return out


def _gcmd_named(values, ctx, scheme):
    out = []
    for v in values:
        if isinstance(v, dict) and not _is_placeholder(v.get("ShortName")):
            out.append(_term(v.get("LongName") or v["ShortName"],
                             KMS_SCHEME + scheme, code=v["ShortName"],
                             url=ctx["kms"](v["ShortName"], scheme)))
    return out


def tf_gcmdplatform(values, ctx):
    return _gcmd_named(values, ctx, "platforms")


def tf_gcmdinstrument(values, ctx):
    return _gcmd_named(values, ctx, "instruments")


def tf_gcmdproject(values, ctx):
    return _gcmd_named(values, ctx, "projects")


def tf_isotopic(values, ctx):
    scheme = ("https://standards.iso.org/iso/19115/resources/Codelists/cat/"
              "codelists.xml#MD_TopicCategoryCode")
    return [_term(v, scheme) for v in values if isinstance(v, str)]


def tf_processinglevel(values, ctx):
    level = tf_text(values, ctx)
    if not level:
        return None
    return [_term("Processing level " + level,
                  "https://www.earthdata.nasa.gov/learn/earth-observation-data-basics/data-processing-levels",
                  code=level)]


def tf_acquisition(values, ctx):
    used = []
    for p in values:
        if not isinstance(p, dict):
            continue
        plat = p.get("LongName") or p.get("ShortName")
        insts = [i for i in _as_list(p.get("Instruments"))
                 if not _is_placeholder(i.get("ShortName"))]
        if not insts:
            ent = {"@type": ["prov:Entity", "schema:Thing"], "schema:name": plat}
            if p.get("Type"):
                ent["schema:description"] = "Platform type: " + p["Type"]
            used.append(ent)
        for i in insts:
            ent = {"@type": ["prov:Entity", "schema:Thing"],
                   "schema:name": "%s on %s" % (i.get("LongName") or i["ShortName"], plat)}
            parts = [{"@type": ["schema:Thing"],
                      "schema:name": c.get("LongName") or c.get("ShortName")}
                     for c in _as_list(i.get("ComposedOf")) if c.get("ShortName")]
            if parts:
                ent["schema:hasPart"] = parts
            used.append(ent)
    if not used:
        return None
    return [{"@type": ["prov:Activity"],
             "schema:name": "Data acquisition", "prov:used": used}]


# --- extents ----------------------------------------------------------------

def _place(name=None, geo=None, wkt=None):
    p = {"@type": ["schema:Place"]}
    if name:
        p["schema:name"] = name
    if geo:
        p["schema:geo"] = geo
    if wkt:
        p["geosparql:hasGeometry"] = {
            "@type": ["geosparql:Geometry"],
            "geosparql:asWKT": {"@type": ["geosparql:wktLiteral"], "@value": wkt}}
    return p


def _fmt(n):
    return ("%.6f" % float(n)).rstrip("0").rstrip(".")


def tf_bbox(values, ctx):
    out = []
    for b in values:
        try:
            s, w = b["SouthBoundingCoordinate"], b["WestBoundingCoordinate"]
            n, e = b["NorthBoundingCoordinate"], b["EastBoundingCoordinate"]
        except (KeyError, TypeError):
            continue
        if not (-90 <= s <= n <= 90 and -180 <= w <= 180 and -180 <= e <= 180):
            continue
        out.append(_place(geo={"@type": ["schema:GeoShape"],
                               "schema:box": " ".join(_fmt(x) for x in (s, w, n, e))}))
    return out


def _ring(points):
    pts = [(p["Longitude"], p["Latitude"]) for p in _as_list(points)]
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return "(" + ", ".join("%s %s" % (_fmt(x), _fmt(y)) for x, y in pts) + ")"


def tf_polygon(values, ctx):
    out = []
    for g in values:
        try:
            rings = [_ring(g["Boundary"]["Points"])]
            for hole in _as_list((g.get("ExclusiveZone") or {}).get("Boundaries")):
                rings.append(_ring(hole["Points"]))
        except (KeyError, TypeError):
            continue
        out.append(_place(wkt="POLYGON(%s)" % ", ".join(rings)))
    return out


def tf_point(values, ctx):
    return [_place(geo={"@type": ["schema:GeoCoordinates"],
                        "schema:latitude": p["Latitude"],
                        "schema:longitude": p["Longitude"]})
            for p in values if isinstance(p, dict) and "Latitude" in p]


def tf_line(values, ctx):
    out = []
    for ln in values:
        pts = _as_list((ln or {}).get("Points"))
        if len(pts) >= 2:
            out.append(_place(geo={"@type": ["schema:GeoShape"], "schema:line": " ".join(
                "%s %s" % (_fmt(p["Latitude"]), _fmt(p["Longitude"])) for p in pts)}))
    return out


_LOC_LEVELS = ("Category", "Type", "Subregion1", "Subregion2", "Subregion3",
               "DetailedLocation")


def tf_gcmdlocation(values, ctx):
    return [_place(name=" > ".join(v[k] for k in _LOC_LEVELS if v.get(k)))
            for v in values if isinstance(v, dict)]


def tf_placename(values, ctx):
    return [_place(name=v) for v in values if isinstance(v, str)]


def tf_period(values, ctx):
    out = []
    for r in values:
        if not isinstance(r, dict):
            continue
        begin = _date(r.get("BeginningDateTime") or r.get("StartDate"))
        end = _date(r.get("EndingDateTime") or r.get("EndDate"))
        if begin:
            out.append("%s/%s" % (begin, end or ".."))
    return out


# --- links and distributions ----------------------------------------------

_FORMAT_MIME = {
    "netcdf-4": "application/x-netcdf", "netcdf-3": "application/x-netcdf",
    "netcdf": "application/x-netcdf", "hdf4": "application/x-hdf",
    "hdf5": "application/x-hdf5", "hdf-eos2": "application/x-hdf",
    "hdf-eos5": "application/x-hdf5", "cog": "image/tiff",
    "geotiff": "image/tiff", "tiff": "image/tiff", "png": "image/png",
    "jpeg": "image/jpeg", "pdf": "application/pdf", "csv": "text/csv",
    "ascii": "text/plain", "json": "application/json",
    "geojson": "application/geo+json", "kml": "application/vnd.google-earth.kml+xml",
    "grib": "application/x-grib", "zarr": "application/vnd+zarr",
}


def _formats(ctx):
    """Distribution formats from ArchiveAndDistributionInformation, as MIME
    types where known and the UMM format name otherwise (e.g. ICARTT)."""
    out = []
    info = ctx["umm"].get("ArchiveAndDistributionInformation") or {}
    for f in _as_list(info.get("FileDistributionInformation")):
        name = f.get("Format")
        if name and not _is_placeholder(name):
            val = _FORMAT_MIME.get(name.lower(), name)
            if val not in out:
                out.append(val)
    return out


def tf_distribution(values, ctx):
    out = []
    for ru in values:
        u = _url(ru.get("URL"))
        if not u:
            continue
        d = {"@type": ["schema:DataDownload", "schema:Collection"],
             "schema:name": ru.get("Subtype") or ru.get("Type"),
             "schema:url": u}
        if ru.get("Description"):
            d["schema:description"] = ru["Description"]
        mime = (ru.get("GetData") or {}).get("MimeType")
        fmts = [mime] if mime and not _is_placeholder(mime) else _formats(ctx)
        if fmts:
            d["schema:encodingFormat"] = fmts
        out.append(d)
    return out


def tf_service(values, ctx):
    terms = (ctx["umm"].get("AccessConstraints") or {}).get("Description")
    out = []
    for ru in values:
        u = _url(ru.get("URL"))
        if not u:
            continue
        api = {"@type": ["schema:WebAPI"],
               "schema:name": ru.get("Subtype") or ru.get("Type"),
               "schema:serviceType": ru.get("Subtype") or ru.get("Type"),
               "schema:termsOfService": terms if not _is_placeholder(terms) else "not specified",
               "schema:potentialAction": [{
                   "@type": ["schema:Action"],
                   "schema:target": {"@type": ["schema:EntryPoint"],
                                     "schema:urlTemplate": u,
                                     "schema:httpMethod": ["GET"]}}]}
        if ru.get("Description"):
            api["schema:description"] = ru["Description"]
        out.append(api)
    return out


def tf_s3(values, ctx):
    out = []
    for s in values:
        if not isinstance(s, dict):
            continue
        prefixes = _as_list(s.get("S3BucketAndObjectPrefixNames"))
        desc = ("Direct in-region access from AWS %s. S3 prefixes: %s. Temporary "
                "credentials: %s" % (s.get("Region"), ", ".join(prefixes),
                                     s.get("S3CredentialsAPIEndpoint")))
        d = {"@type": ["schema:DataDownload", "schema:Collection"],
             "schema:name": "AWS S3 direct access (%s)" % s.get("Region"),
             "schema:description": desc,
             "schema:url": _url(s.get("S3CredentialsAPIDocumentationURL"))
             or _url(s.get("S3CredentialsAPIEndpoint"))}
        fmts = _formats(ctx)
        if fmts:
            d["schema:encodingFormat"] = fmts
        out.append(d)
    return out


def tf_relatedurl(values, ctx):
    out = []
    for ru in values:
        if isinstance(ru, str):                   # CollectionCitations.OnlineResource.Linkage
            u = _url(ru)
            if u and u not in (ctx["doc"].get("schema:url"), _doi_url(ctx["umm"])):
                out.append(_link(ctx["rule"]["subject_label"], u))
            continue
        u = _url(ru.get("URL"))
        if not u or u == ctx["doc"].get("schema:url"):
            continue
        rel = ru.get("Subtype") or ru.get("Type") or ru.get("URLContentType")
        if ru.get("URLContentType") == "DistributionURL":
            rel = "%s: %s" % (ru.get("Type"), ru["Subtype"]) if ru.get("Subtype") else ru.get("Type")
        out.append(_link(rel, u, ru.get("Description"),
                         (ru.get("GetData") or {}).get("MimeType")))
    return out


def tf_image(values, ctx):
    out = []
    for ru in values:
        u = _url(ru.get("URL"))
        if u:
            img = {"@type": ["schema:ImageObject"], "schema:contentUrl": u}
            if ru.get("Description"):
                img["schema:caption"] = ru["Description"]
            out.append(img)
    ctx["used_urls"].update(i["schema:contentUrl"] for i in out)
    return out


def tf_citation(values, ctx):
    for c in values:
        if not isinstance(c, dict):
            continue
        if c.get("OtherCitationDetails"):
            return c["OtherCitationDetails"].strip()
        umm, doc = ctx["umm"], ctx["doc"]
        year = (_date(c.get("ReleaseDate")) or "")[:4]
        publisher = c.get("Publisher") or (doc.get("schema:publisher") or {}).get("schema:name")
        parts = [c.get("Creator"), "(%s)" % year if year else None,
                 c.get("Title") or umm.get("EntryTitle"),
                 "Version " + (c.get("Version") or umm.get("Version") or ""),
                 publisher, _doi_url(umm)]
        parts = [p.strip().rstrip(".") for p in parts if p and p.strip() not in ("Version",)]
        return ". ".join(parts) + "." if parts else None
    return None


def tf_seriesname(values, ctx):
    name = tf_text(values, ctx)
    return {"@type": ["schema:CreativeWorkSeries"], "schema:name": name} if name else None


def tf_publication(values, ctx):
    out = []
    for p in values:
        if not isinstance(p, dict):
            continue
        doi = (p.get("DOI") or {}).get("DOI")
        u = ("https://doi.org/" + doi if doi and not doi.startswith("http") else doi) \
            or _url((p.get("OnlineResource") or {}).get("Linkage"))
        if not u:
            continue
        year = (_date(p.get("PublicationDate")) or "")[:4]
        name = " ".join(x for x in (p.get("Author"), "(%s)" % year if year else None,
                                    p.get("Title")) if x)
        out.append(_link("dcterms:isReferencedBy", u, name or None))
    return out


def tf_additionalproperty(values, ctx):
    out = []
    for a in values:
        if not isinstance(a, dict) or not a.get("Name"):
            continue
        pv = {"@type": ["schema:PropertyValue"], "schema:name": a["Name"]}
        for src, dst in (("Description", "schema:description"),
                         ("Value", "schema:value"),
                         ("ParameterUnitsOfMeasure", "schema:unitText"),
                         ("ParameterRangeBegin", "schema:minValue"),
                         ("ParameterRangeEnd", "schema:maxValue")):
            if a.get(src) not in (None, ""):
                pv[dst] = a[src]
        out.append(pv)
    return out


def tf_association(values, ctx):
    out = []
    for m in values:
        if isinstance(m, dict) and m.get("EntryId"):
            name = m["EntryId"] + (" version " + m["Version"] if m.get("Version") else "")
            link = _link(m.get("Type") or "RELATED", NIL, name)
            out.append(link)
    return out


def tf_catalog_source(values, ctx):
    for v in values:
        if isinstance(v, dict) and v.get("URL"):
            return {"@id": v["URL"]}
    return None


TRANSFORMS = {name[3:].replace("_", "-"): fn for name, fn in globals().items()
              if name.startswith("tf_") and callable(fn)}


# ---------------------------------------------------------------------------
# applying the table
# ---------------------------------------------------------------------------

def _hoist_contacts(umm):
    """A copy of `umm` whose root ContactPersons/ContactGroups also list the
    ones nested under DataCenters, affiliated to that data center, so one
    table row reaches every contact."""
    u = dict(umm)
    for key in ("ContactPersons", "ContactGroups"):
        flat = [dict(c) for c in _as_list(umm.get(key))]
        for dc in _as_list(umm.get("DataCenters")):
            for c in _as_list(dc.get(key)):
                c = dict(c)
                c["_affiliation"] = dc.get("LongName") or dc.get("ShortName")
                flat.append(c)
        if flat:
            u[key] = flat
    return u


def _place_value(container, target, value, transform):
    """Put `value` at `target`. Row order is precedence for scalar targets;
    array targets accumulate. (describe values are appended by apply_table
    after the table pass.)"""
    if value in (None, "", [], {}):
        return False
    if target in ARRAY_TARGETS:
        existing = container.setdefault(target, [])
        for item in _as_list(value):
            if item not in existing:
                existing.append(item)
        return True
    if target in container:
        return False
    if target in ORDERED_TARGETS:
        container[target] = {"@list": _as_list(value)}
    else:
        container[target] = value[0] if isinstance(value, list) else value
    return True


def apply_table(rows, umm, meta, doc, record, kms, umm_vars=None):
    """Apply every row with a registered transform. Returns (UMM top-level
    keys consumed, change log)."""
    consumed, changes = set(), []
    umm_vars = {"Variable": umm_vars or []}
    ctx = {"umm": umm, "meta": meta, "doc": doc, "kms": kms, "used_urls": set(),
           "umm_vars": umm_vars}
    appended = []          # describe paragraphs, added after the table pass
    for rule in rows:
        transform = rule.get("transform", "")
        path = (rule.get("object_json_path") or "").strip()
        shaper = TRANSFORMS.get(transform)
        if not shaper or not rule.get("object_id") or not path.startswith("$."):
            continue
        parts = path[2:].split(".")
        if parts[0] == "schema:subjectOf" and len(parts) == 2:
            container, target = record, parts[1]
        elif len(parts) == 1 and "[" not in path:
            container, target = doc, parts[0]
        else:
            continue
        values = select(umm, meta, rule["subject_id"], rule.get("subject_filter"),
                        umm_vars)
        if not values:
            continue
        ctx.update(rule=rule, target=target)
        shaped = shaper(values, ctx)
        if transform == "describe":
            placed = bool(shaped)
            if placed:
                appended.append((container, target, shaped))
        else:
            placed = _place_value(container, target, shaped, transform)
        if placed:
            changes.append("%s -> %s" % (rule["subject_id"], rule["object_id"]))
            if rule["subject_id"].startswith("ummc:"):
                consumed.add(rule["subject_id"].split(":", 1)[1].split(".")[0])
    # Labelled paragraphs go after the main text (Abstract), so a describe row
    # can sit anywhere in the table without taking the field first.
    for container, target, text in appended:
        container[target] = (container[target] + "\n\n" + text
                             if container.get(target) else text)
    return consumed, changes


# The table's passthrough rows name UMM parts with no CDIF slot. Most top-level
# keys are either mapped whole or not at all; these are mapped in part, and
# the residual function returns only what the table did not carry.
def _without(d, *keys):
    return {k: v for k, v in (d or {}).items() if k not in keys} or None


def _residual_temporal(extents):
    rest = [_without(t, "RangeDateTimes", "SingleDateTimes", "EndsAtPresentFlag")
            for t in _as_list(extents)]
    return [t for t in rest if t] or None


def _residual_spatial(se):
    rest = _without(se, "HorizontalSpatialDomain", "SpatialCoverageType")
    hsd = _without((se or {}).get("HorizontalSpatialDomain"), "Geometry")
    if hsd:
        rest = dict(rest or {}, HorizontalSpatialDomain=hsd)
    return rest


_RESIDUAL = {
    "DOI": lambda v: _without(v, "MissingReason") if not (v or {}).get("DOI") else None,
    "DataDates": lambda v: [d for d in _as_list(v) if d.get("Type") not in
                            ("CREATE", "UPDATE", "REVIEW", "DELETE")] or None,
    "AccessConstraints": lambda v: _without(v, "Description"),
    "UseConstraints": lambda v: _without(v, "Description", "LicenseText",
                                         "FreeAndOpenData", "LicenseURL"),
    "TemporalExtents": _residual_temporal,
    "SpatialExtent": _residual_spatial,
    "ArchiveAndDistributionInformation": lambda v: v,   # sizes, media, fees
}


def _passthrough(umm, consumed, doc):
    for key, value in umm.items():
        if key.startswith("_") or key == "MetadataSpecification":
            continue
        if key in _RESIDUAL:
            value = _RESIDUAL[key](value)
        elif key in consumed:
            continue
        if value in (None, [], {}):
            continue
        if isinstance(value, (dict, list)):
            doc["ummc:" + key] = {"@type": "@json", "@value": value}
        else:
            doc["ummc:" + key] = value


# ---------------------------------------------------------------------------
# GCMD KMS lookups (opt-in)
# ---------------------------------------------------------------------------

def make_kms(enabled):
    cache = {}

    def lookup(label, scheme):
        if not enabled or not label:
            return None
        key = (scheme, label.upper())
        if key not in cache:
            cache[key] = None
            try:
                import requests
                from urllib.parse import quote
                r = requests.get("https://cmr.earthdata.nasa.gov/kms/concepts/"
                                 "concept_scheme/%s/pattern/%s?format=json"
                                 % (scheme, quote(label)), timeout=20)
                for c in r.json().get("concepts", []):
                    if c.get("prefLabel", "").upper() == label.upper():
                        cache[key] = "https://cmr.earthdata.nasa.gov/kms/concept/" + c["uuid"]
                        break
            except Exception as exc:
                print("  WARNING: KMS lookup failed for %r (%s)" % (label, exc),
                      file=sys.stderr)
        return cache[key]
    return lookup


# ---------------------------------------------------------------------------
# UMM-Var records associated with a collection
# ---------------------------------------------------------------------------

CMR_SEARCH = "https://cmr.earthdata.nasa.gov/search/"


def fetch_umm_vars(meta):
    """The UMM-Var records associated with a collection, each with its
    concept id under '_concept_id'. CMR cannot search variables by
    collection, so the ids come from the collection's
    meta.association-details (re-fetching the collection when the input
    lacks them). Returns [] when there are none or CMR cannot be reached."""
    if not meta or not meta.get("has-variables") or not meta.get("concept-id"):
        return []
    import requests
    try:
        details = meta.get("association-details")
        if details is None:
            r = requests.get(CMR_SEARCH + "collections.umm_json",
                             params={"concept_id": meta["concept-id"]}, timeout=60)
            r.raise_for_status()
            items = r.json().get("items") or [{}]
            details = items[0].get("meta", {}).get("association-details") or {}
        ids = [v["concept-id"] for v in details.get("variables", [])]
        out = []
        for i in range(0, len(ids), 100):
            r = requests.get(CMR_SEARCH + "variables.umm_json",
                             params={"concept_id[]": ids[i:i + 100], "page_size": 100},
                             timeout=60)
            r.raise_for_status()
            for item in r.json().get("items", []):
                var = dict(item["umm"])
                var["_concept_id"] = item["meta"]["concept-id"]
                out.append(var)
        return sorted(out, key=lambda v: v.get("Name", ""))
    except Exception as exc:
        print("  WARNING: could not fetch UMM-Var records for %s (%s); using "
              "science keywords for variableMeasured" % (meta["concept-id"], exc),
              file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# one collection
# ---------------------------------------------------------------------------

def convert_umm_to_cdif(umm, meta=None, rows=None, detect=True, kms=None,
                        umm_vars=None):
    """`umm_vars`: the collection's UMM-Var records (see fetch_umm_vars);
    without them variableMeasured falls back to science keywords."""
    rows = rows if rows is not None else load_table(TABLE)
    kms = kms or make_kms(False)
    meta = meta or {}
    src = _hoist_contacts(umm)
    cid = meta.get("concept-id")

    doc = {"@context": dict(CDIF_CONTEXT)}
    doc["@id"] = _doi_url(umm) or (CMR_CONCEPT + cid if cid else
                                   "urn:nasa:cmr:%s_%s" % (umm.get("ShortName"),
                                                           umm.get("Version")))
    doc["@type"] = ["schema:Dataset"]
    record = {}

    consumed, changes = apply_table(rows, src, meta or None, doc, record, kms,
                                    umm_vars)

    # --- what CDIF requires and UMM-C may not supply ------------------------
    if not doc.get("schema:name"):
        doc["schema:name"] = umm.get("ShortName") or "Untitled"
    if not doc.get("schema:identifier"):
        doc["schema:identifier"] = "%s_%s" % (umm.get("ShortName"), umm.get("Version"))
        changes.append("schema:identifier from ShortName_Version (no DOI or concept-id)")
    if "schema:dateModified" not in doc:
        created = _date(next((d.get("Date") for d in _as_list(umm.get("DataDates"))
                              if d.get("Type") == "CREATE"), None))
        revised = _date(meta.get("revision-date"))
        doc["schema:dateModified"] = created or revised or _dt.datetime.now(
            _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        changes.append("schema:dateModified from %s" % (
            "DataDates CREATE" if created else
            "CMR revision-date" if revised else "the conversion time"))
    if not doc.get("schema:url"):
        doc["schema:url"] = _doi_url(umm) or (CMR_CONCEPT + cid + ".html" if cid else NIL)
    if not doc.get("schema:license") and not doc.get("schema:conditionsOfAccess"):
        doc["schema:license"] = [{"@id": NIL}]
        changes.append("schema:license set to the OGC nil URI (no use or access constraints)")

    # --- the catalog record ---------------------------------------------------
    rec_id = (CMR_CONCEPT + cid + ".umm_json") if cid else doc["@id"] + "#metadata"
    subject = {
        "@id": rec_id,
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:name": "CMR metadata record for: %s" % doc["schema:name"][:120],
        "schema:about": {"@id": doc["@id"]},
        "dcterms:conformsTo": [{"@id": "https://w3id.org/cdif/core/1.1"},
                               {"@id": "https://w3id.org/cdif/discovery/1.1"}],
        "schema:includedInDataCatalog": {
            "@type": ["schema:DataCatalog"],
            "schema:name": "NASA Common Metadata Repository (CMR)",
            "schema:url": "https://cmr.earthdata.nasa.gov/search"},
        "schema:description": "Converted from UMM-C %s to CDIF by umm_to_cdif.py. "
                              "Unmapped UMM-C properties preserved as ummc: JSON literals."
                              % ((umm.get("MetadataSpecification") or {}).get("Version", "")),
    }
    subject.update(record)
    if "schema:dateModified" not in subject and _date(meta.get("revision-date")):
        subject["schema:dateModified"] = _date(meta["revision-date"])
    doc["schema:subjectOf"] = subject

    _passthrough(umm, consumed, doc)

    # Declare the optional prefixes the record uses.
    text = json.dumps(doc)
    for prefix, ns in OPTIONAL_PREFIXES.items():
        if '"%s:' % prefix in text:
            doc["@context"][prefix] = ns

    if detect and _HAVE_DETECT:
        try:
            uris = detect_conformance(doc)
        except Exception as exc:
            print("  WARNING: detect_conformance failed (%s); keeping the "
                  "fallback conformsTo" % exc, file=sys.stderr)
        else:
            if uris:
                apply_conformance(doc, uris)
            else:
                doc["schema:subjectOf"].pop("dcterms:conformsTo", None)
    return doc


# ---------------------------------------------------------------------------
# input handling and CLI
# ---------------------------------------------------------------------------

def load_input(source):
    if re.match(r"^https?://", source):
        import requests
        r = requests.get(source, timeout=60)
        r.raise_for_status()
        return r.json()
    with open(source, encoding="utf-8") as fh:
        return json.load(fh)


def collections_in(data):
    """(meta, umm) pairs from a search result, a single item or bare UMM-C."""
    if isinstance(data, dict) and "items" in data:
        return [(i.get("meta") or {}, i["umm"]) for i in data["items"] if "umm" in i]
    if isinstance(data, dict) and "umm" in data:
        return [(data.get("meta") or {}, data["umm"])]
    if isinstance(data, dict) and "ShortName" in data:
        return [({}, data)]
    return []


def validate(path):
    """Frame and validate with the validation submodule's FrameAndValidate."""
    tools = ROOT / "validation"
    cmd = [sys.executable, str(tools / "tools" / "FrameAndValidate.py"), str(path), "-v",
           "--schema", str(tools / "CDIFDiscoverySchema.json"),
           "--frame", str(tools / "CDIF-frame-2026.jsonld")]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return res.returncode == 0, (res.stdout + res.stderr).strip()


def main():
    ap = argparse.ArgumentParser(description="Convert NASA CMR UMM-C JSON to CDIF JSON-LD")
    ap.add_argument("input", nargs="+", help="UMM-C JSON file(s) or CMR search URL(s)")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--kms", action="store_true",
                    help="look up GCMD keyword concept URIs in KMS (network)")
    ap.add_argument("--no-umm-var", action="store_true",
                    help="do not fetch associated UMM-Var records; build "
                         "variableMeasured from science keywords only")
    ap.add_argument("--static-conformance", action="store_true",
                    help="keep the built-in conformsTo instead of detecting it")
    ap.add_argument("--validate", action="store_true",
                    help="frame and validate each output against the CDIF Discovery schema")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    rows = load_table(TABLE)
    kms = make_kms(args.kms)
    os.makedirs(args.output, exist_ok=True)
    failures = 0
    for source in args.input:
        for meta, umm in collections_in(load_input(source)):
            umm_vars = [] if args.no_umm_var else fetch_umm_vars(meta)
            doc = convert_umm_to_cdif(umm, meta, rows, not args.static_conformance,
                                      kms, umm_vars)
            stem = re.sub(r"[^A-Za-z0-9._-]+", "_",
                          "%s_%s" % (umm.get("ShortName"), umm.get("Version")))
            out = Path(args.output) / (stem + ".jsonld")
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, ensure_ascii=False)
            declared = [c["@id"].rsplit("/", 2)[-2] for c in
                        doc["schema:subjectOf"].get("dcterms:conformsTo", [])]
            line = "%-45s %s" % (out.name, "+".join(declared) or "no conformance")
            if args.validate:
                ok, report = validate(out)
                failures += not ok
                line += "  " + ("VALID" if ok else "INVALID")
                if args.verbose or not ok:
                    line += "\n" + "\n".join("      " + l for l in report.splitlines()[-15:])
            print(line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

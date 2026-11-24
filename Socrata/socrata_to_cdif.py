#!/usr/bin/env python3
"""
socrata_to_cdif.py - Convert Socrata views to CDIF JSON-LD.

Reads a Socrata asset's native metadata, the "view" that every Socrata portal
serves at https://{host}/api/views/{id}.json (a JSON file, or that URL); each
becomes one CDIF record. A tabular view also gets its columns, as
cdi:InstanceVariables, and its CSV export described column by column, which the
portal's DCAT-US feed (/data.json) and the schema.org markup of its pages do
not carry.

The table mappings/socrata-to-cdif.sssom.tsv drives the conversion: each row
names view members by a path from the view root (subject_json_path), the CDIF
location it lands at (object_json_path) and the shaper that builds the value
(transform). Row order is precedence for a scalar target; array targets
accumulate.

A subject_json_path is a dot-separated list of member names; '*' takes every
member of an object and '[*]' every item of an array. Member names may contain
spaces (metadata.custom_fields.*.Contact Name).

A portal defines its own metadata fields, in groups, under
metadata.custom_fields. A row whose path is metadata.custom_fields.*.<field>
maps that field, whatever group a portal puts it in; the fields no row maps
become schema:additionalProperty values named '<group>: <field>'.

A view does not name its portal, so a file needs --host.

Usage:
    python Socrata/socrata_to_cdif.py "https://data.wa.gov/api/views/f6w7-q2d2.json" -o out/
    python Socrata/socrata_to_cdif.py view.json --host data.wa.gov -o out/
"""

import argparse
import csv
import datetime
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(ROOT / "validation"))
try:
    from detect_conformance import detect_cdif_conformance, apply_conformance
    _HAVE_DETECT = True
except Exception:
    _HAVE_DETECT = False

TABLE = ROOT / "mappings" / "socrata-to-cdif.sssom.tsv"
NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
CUSTOM = "metadata.custom_fields.*."

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
    "schema:keywords", "schema:publisher", "schema:contributor", "schema:license",
    "schema:conditionsOfAccess", "schema:spatialCoverage", "schema:temporalCoverage",
    "schema:distribution", "schema:relatedLink", "schema:variableMeasured",
    "schema:additionalProperty", "schema:inLanguage", "cdif:statistics",
}

# Socrata column types -> the XSD type of the value as the CSV export writes it.
# The geometry types are written as WKT text.
XSD = {
    "number": "xsd:decimal", "double": "xsd:double", "money": "xsd:decimal",
    "percent": "xsd:decimal", "stars": "xsd:decimal",
    "calendar_date": "xsd:dateTime", "date": "xsd:dateTime",
    "checkbox": "xsd:boolean", "url": "xsd:anyURI",
}
NUMERIC = {"number", "double", "money", "percent", "stars"}


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def select(node, path):
    """Every value at a subject_json_path (see the module docstring)."""
    current = [node]
    for seg in path.split("."):
        every = seg.endswith("[*]")
        name = seg[:-3] if every else seg
        nxt = []
        for n in current:
            if not isinstance(n, dict):
                continue
            if name == "*":
                nxt.extend(n.values())
            elif name in n:
                nxt.append(n[name])
        if every:
            nxt = [i for v in nxt if isinstance(v, list) for i in v]
        current = nxt
    return [v for v in current if v not in (None, "", [], {})]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def text(v):
    return " ".join(str(v).split()) if isinstance(v, (str, int, float)) else ""


def _http(t):
    return bool(re.match(r"^https?://\S+$", t or ""))


def _epoch(value):
    """Socrata's seconds since 1970 -> an ISO 8601 UTC date-time."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _num(value):
    """A number Socrata wrote as a number or as text ('88128'); None for anything else."""
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return int(n) if n.is_integer() else n


def _isodate(t):
    """An ISO 8601 date or date-time as written, or None."""
    m = re.match(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?)?)?$", t)
    return t if m else None


def _columns(view):
    """The columns of the CSV export, in its order: not hidden, and not the
    ':@computed_region_...' columns the portal adds."""
    cols = [c for c in view.get("columns") or []
            if isinstance(c, dict) and text(c.get("fieldName"))
            and not c["fieldName"].startswith(":")
            and "hidden" not in (c.get("flags") or [])]
    return sorted(cols, key=lambda c: c.get("position") or 0)


def _xsd(col):
    return XSD.get(col.get("dataTypeName"), "xsd:string")


def _var_id(col):
    return "#" + col["fieldName"]


def _linkrole(url, rel, name=None, fmt=None):
    target = {"@type": ["schema:EntryPoint"], "schema:url": url}
    if name:
        target["schema:name"] = name
    if fmt:
        target["schema:encodingFormat"] = fmt
    return {"@type": ["schema:LinkRole"], "schema:linkRelationship": rel, "schema:target": target}


# ---------------------------------------------------------------------------
# shapers: tf_<transform>(values, ctx) -> value or list of values
# ---------------------------------------------------------------------------

def tf_text(values, ctx):
    for v in values:
        if text(v):
            return text(v)
    return None


def tf_texts(values, ctx):
    out = []
    for v in values:
        if text(v) and text(v) not in out:
            out.append(text(v))
    return out


def tf_htmltext(values, ctx):
    """The first non-empty value, without the HTML markup Socrata allows in a description.
    Line breaks are kept."""
    for v in values:
        if isinstance(v, str) and v.strip():
            t = re.sub(r"<br\s*/?>|</p>|</li>", "\n", v, flags=re.I)
            t = html.unescape(re.sub(r"<[^>]+>", "", t))
            return re.sub(r"\n{3,}", "\n\n", t).strip() or None
    return None


def tf_epoch(values, ctx):
    for v in values:
        if _epoch(v):
            return _epoch(v)
    return None


def tf_date(values, ctx):
    for v in values:
        if _isodate(text(v)):
            return text(v)
    return None


def tf_keywords(values, ctx):
    out = []
    for v in values:
        for k in (v if isinstance(v, list) else [v]):
            if text(k) and text(k) not in out:
                out.append(text(k))
    return out


def tf_category(values, ctx):
    """The portal's category -> a DefinedTerm in the portal's category list."""
    return [{"@type": ["schema:DefinedTerm"], "schema:name": text(v),
             "schema:inDefinedTermSet": "%s categories" % ctx["host"]}
            for v in values if text(v)]


def tf_license(values, ctx):
    """license with a termsLink -> a CreativeWork."""
    out = []
    for lic in values:
        if isinstance(lic, dict) and _http(lic.get("termsLink")):
            work = {"@type": ["schema:CreativeWork"], "schema:url": lic["termsLink"]}
            if text(lic.get("name")):
                work["schema:name"] = text(lic["name"])
            out.append(work)
    return out


def tf_licensetext(values, ctx):
    """A licence with no link to its terms -> 'License: <name or id>'."""
    lic = ctx["view"].get("license") or {}
    if _http(lic.get("termsLink")):
        return None
    name = text(lic.get("name")) or text(values[0])
    return ["License: " + name] if name else None


def tf_creator(values, ctx):
    """attribution (with attributionLink) -> the creator list."""
    name = tf_text(values, ctx)
    if not name:
        return None
    org = {"@type": ["schema:Organization"], "schema:name": name}
    if _http(ctx["view"].get("attributionLink")):
        org["schema:url"] = ctx["view"]["attributionLink"]
    return {"@list": [org]}


def tf_organizations(values, ctx):
    return [{"@type": ["schema:Organization"], "schema:name": t} for t in tf_texts(values, ctx)]


def tf_contact(values, ctx):
    """A custom-field group's 'Contact Name' (and its 'Contact Email') -> Role pointOfContact."""
    out = []
    for group in ((ctx["view"].get("metadata") or {}).get("custom_fields") or {}).values():
        name = text((group or {}).get("Contact Name"))
        if not name:
            continue
        agent = {"@type": ["schema:Organization"], "schema:name": name}
        email = text(group.get("Contact Email")).replace("mailto:", "")
        if "@" in email:
            agent["schema:email"] = email
            ctx["claimed"].add("Contact Email")
        role = {"@type": ["schema:Role"], "schema:roleName": "pointOfContact",
                "schema:contributor": agent}
        if role not in out:
            out.append(role)
    return out


def tf_places(values, ctx):
    return [{"@type": ["schema:Place"], "schema:name": t} for t in tf_texts(values, ctx)]


def tf_interval(values, ctx):
    """An ISO 8601 date or start/end interval; any other wording is left as a custom field."""
    out = []
    for v in values:
        parts = text(v).split("/")
        if len(parts) <= 2 and all(_isodate(p) for p in parts) and text(v) not in out:
            out.append(text(v))
    return out


def tf_homepage(values, ctx):
    return [_linkrole(text(v), "homepage") for v in values if _http(text(v))]


def tf_attachments(values, ctx):
    """metadata.attachments -> LinkRoles 'attachment' to the file on the portal."""
    out = []
    for a in values:
        if not isinstance(a, dict):
            continue
        name = text(a.get("name")) or text(a.get("filename"))
        if text(a.get("assetId")):
            url = "https://%s/api/views/%s/files/%s?download=true&filename=%s" % (
                ctx["host"], ctx["view"]["id"], a["assetId"], quote(text(a.get("filename")) or name))
        elif text(a.get("blobId")):
            url = "https://%s/api/assets/%s?download=true" % (ctx["host"], a["blobId"])
        else:
            continue
        out.append(_linkrole(url, "attachment", name or None))
    return out


def tf_variables(values, ctx):
    """The columns of a tabular view -> cdi:InstanceVariables.

    The name is the column's display name, which heads it in the CSV export;
    the alternate name is its API field name."""
    out = []
    for col in _columns(ctx["view"]):
        name = text(col.get("name")) or col["fieldName"]
        var = {"@id": _var_id(col), "@type": ["schema:PropertyValue", "cdi:InstanceVariable"],
               "schema:name": name}
        if col["fieldName"] != name:
            var["schema:alternateName"] = [col["fieldName"]]
        var["schema:description"] = text(col.get("description")) or name
        var["cdif:physicalDataType"] = _xsd(col)
        if col.get("id") is not None and col.get("id") == ctx["view"].get("rowIdentifierColumnId"):
            var["cdif:role"] = "UnitIdentifier"
        cached = col.get("cachedContents") or {}
        if col.get("dataTypeName") in NUMERIC:
            for key, target in (("smallest", "schema:minValue"), ("largest", "schema:maxValue")):
                if _num(cached.get(key)) is not None:
                    var[target] = _num(cached[key])
        stats = _column_statistics(cached)
        if stats:
            var["cdif:isDescribedBy_StatisticsCollection"] = {
                "@type": ["cdi:StatisticsCollection"], "cdif:has_Statistics": stats}
        out.append(var)
    return out


def _column_statistics(cached):
    """A column's cachedContents -> cdi:Statistics bundles.

    'count': the number of rows, of rows with a value and of rows without one,
    told apart by cdi:computationBase; the most frequent values (top) break it
    down as cdi:CategoryStatistics. 'distinct count': the cardinality."""
    out = []
    counts = [{"cdi:content": _num(cached.get(key)), "cdi:computationBase": base}
              for key, base in (("count", "Total"), ("non_null", "ValidOnly"), ("null", "MissingOnly"))
              if _num(cached.get(key)) is not None]
    if counts:
        bundle = {"@type": ["cdi:Statistics"], "cdif:typeOfStatistic": "count", "cdi:statistic": counts}
        top = [{"@type": ["cdi:CategoryStatistics"],
                "cdi:for": {"@type": ["cdi:Category"], "cdif:name": str(t["item"])},
                "cdif:typeOfStatistic": "count",
                "cdi:statistic": [{"cdi:content": _num(t.get("count"))}]}
               for t in cached.get("top") or []
               if isinstance(t, dict) and isinstance(t.get("item"), (str, int, float))
               and not isinstance(t.get("item"), bool) and str(t["item"]).strip()
               and _num(t.get("count")) is not None]
        if top:
            bundle["cdif:has_CategoryStatistics"] = top
        out.append(bundle)
    if _num(cached.get("cardinality")) is not None:
        out.append({"@type": ["cdi:Statistics"], "cdif:typeOfStatistic": "distinct count",
                    "cdi:statistic": [{"cdi:content": _num(cached["cardinality"])}]})
    return out


def tf_rowcount(values, ctx):
    """The number of rows, when every column's cachedContents gives the same count
    -> a dataset-level cdi:Statistics."""
    counts = {_num((c.get("cachedContents") or {}).get("count")) for c in _columns(ctx["view"])
              if c.get("cachedContents")}
    counts.discard(None)
    if len(counts) != 1:
        return None
    return [{"@type": ["cdi:Statistics"], "cdif:typeOfStatistic": "row count",
             "cdi:statistic": [{"cdi:content": counts.pop()}]}]


def tf_primarykey(values, ctx):
    """rowIdentifierColumnId -> a cdif:Key on that column's variable."""
    for col in _columns(ctx["view"]):
        if col.get("id") in values:
            return {"@type": ["cdif:Key"], "cdif:isComposedOf": [
                {"@type": ["cdi:ComponentPosition"], "cdi:indexes": {"@id": _var_id(col)},
                 "cdi:value": 1}]}
    return None


def tf_rowlabel(values, ctx):
    """metadata.rowLabel, what one row is -> a PropertyValue 'Row label'."""
    label = tf_text(values, ctx)
    return [{"@type": ["schema:PropertyValue"], "schema:name": "Row label",
             "schema:value": label}] if label else None


def tf_soda(values, ctx):
    """A tabular view -> its SODA query endpoint as a schema:WebAPI."""
    view = ctx["view"]
    if view.get("viewType") != "tabular" or not _columns(view):
        return None
    params = [
        ("format", "The response format: json, csv or geojson.", True, "json|csv|geojson"),
        ("select", "SoQL $select: the columns to return, by API field name. Empty for all.", False, None),
        ("where", "SoQL $where: a filter on the rows. Empty for none.", False, None),
        ("order", "SoQL $order: the column(s) to sort by. Empty for the default order.", False, None),
        ("limit", "The number of rows to return. The endpoint returns 1000 when it is not given.", False, "[0-9]+"),
        ("offset", "The number of rows to skip, for paging.", False, "[0-9]+"),
    ]
    inputs = []
    for name, description, required, pattern in params:
        spec = {"@type": ["schema:PropertyValueSpecification"], "schema:valueName": name,
                "schema:description": description, "schema:valueRequired": required}
        if pattern:
            spec["schema:valuePattern"] = pattern
        inputs.append(spec)
    return [{
        "@type": ["schema:WebAPI"],
        "schema:name": "Socrata Open Data API (SODA) endpoint",
        "schema:serviceType": "Socrata Open Data API (SODA)",
        "schema:termsOfService": "not specified",
        "schema:documentation": "https://dev.socrata.com/foundry/%s/%s" % (ctx["host"], view["id"]),
        "schema:potentialAction": [{
            "@type": ["schema:SearchAction"],
            "schema:name": "Query rows",
            "schema:target": {
                "@type": ["schema:EntryPoint"],
                "schema:description": "Columns are named by their API field name (the alternate "
                                      "name of each variable). Results are paged.",
                "schema:urlTemplate": "https://%s/resource/%s.{format}?$select={select}&$where={where}"
                                      "&$order={order}&$limit={limit}&$offset={offset}"
                                      % (ctx["host"], view["id"]),
                "schema:httpMethod": ["GET"],
                # The formats are here and not in a schema:result: a result is a
                # schema:DataDownload, and core's SHACL requires a contentUrl of
                # every DataDownload, which a generated response does not have.
                "schema:contentType": ["application/json", "text/csv", "application/geo+json"],
            },
            "schema:query-input": inputs,
        }],
    }]


def _account(user):
    """A portal user account -> a Person, or an Organization for a team account."""
    name = text((user or {}).get("displayName")) if isinstance(user, dict) else ""
    if not name:
        return None
    kind = "schema:Organization" if user.get("type") == "team" else "schema:Person"
    agent = {"@type": [kind], "schema:name": name}
    if text(user.get("id")):
        agent["schema:identifier"] = text(user["id"])
    return agent


def tf_author(values, ctx):
    """tableAuthor, the account that created the table -> a creator, after the attribution."""
    agents = [a for a in (_account(v) for v in values) if a]
    return {"@list": agents} if agents else None


def tf_maintainer(values, ctx):
    """owner, the account that owns the asset on the portal -> the catalog record's maintainer."""
    for v in values:
        if _account(v):
            return _account(v)
    return None


def tf_csv(values, ctx):
    """A tabular view -> its CSV export, with one physical mapping per column."""
    view = ctx["view"]
    cols = _columns(view)
    if view.get("viewType") != "tabular" or not cols:
        return None
    dist = {"@type": ["schema:DataDownload", "cdi:TabularTextDataSet"],
            "schema:name": "CSV export",
            "schema:contentUrl": "https://%s/api/views/%s/rows.csv?accessType=DOWNLOAD"
                                 % (ctx["host"], view["id"]),
            "schema:encodingFormat": ["text/csv"],
            "cdi:isDelimited": True, "csvw:delimiter": ",", "csvw:quoteChar": "\"",
            "csvw:headerRowCount": 1,
            "cdif:hasPhysicalMapping": [
                {"@type": ["cdif:PhysicalMapping"], "cdif:index": i,
                 "cdif:physicalDataType": _xsd(c),
                 "cdif:formats_InstanceVariable": {"@id": _var_id(c)}}
                for i, c in enumerate(cols)]}
    return [dist]


def tf_blob(values, ctx):
    """A file asset -> the file as a DataDownload."""
    view = ctx["view"]
    mime = text(view.get("blobMimeType")).split(";")[0].strip()
    dist = {"@type": ["schema:DataDownload"],
            "schema:contentUrl": "https://%s/download/%s/%s"
                                 % (ctx["host"], view["id"], quote(mime or "application/octet-stream", safe=""))}
    if text(view.get("blobFilename")):
        dist["schema:name"] = text(view["blobFilename"])
    if mime:
        dist["schema:encodingFormat"] = [mime]
    if isinstance(view.get("blobFileSize"), int):
        dist["schema:contentSize"] = "%d B" % view["blobFileSize"]
    return [dist]


def tf_accesspoints(values, ctx):
    """An external-link asset's access points -> DataDownloads.

    metadata.additionalAccessPoints is a list of {title, description, urls};
    metadata.accessPoints repeats the first one's urls, and is read only when
    the list is missing. urls is {media type or label: URL}."""
    out = []
    for meta in values:
        points = meta.get("additionalAccessPoints") or [{"urls": meta.get("accessPoints")}]
        for point in points:
            urls = point.get("urls") if isinstance(point, dict) else None
            for fmt, url in (urls.items() if isinstance(urls, dict) else []):
                if not _http(text(url)):
                    continue
                dist = {"@type": ["schema:DataDownload"], "schema:contentUrl": text(url)}
                name = text(point.get("title")) or ("" if "/" in text(fmt) else text(fmt))
                if name:
                    dist["schema:name"] = name
                if text(point.get("description")):
                    dist["schema:description"] = text(point["description"])
                if "/" in text(fmt):
                    dist["schema:encodingFormat"] = [text(fmt)]
                if dist not in out:
                    out.append(dist)
    return out


def tf_customfields(values, ctx):
    """The custom fields no other row mapped -> PropertyValues named '<group>: <field>'."""
    out = []
    for groups in values:
        for group, fields in (groups.items() if isinstance(groups, dict) else []):
            for field, value in (fields.items() if isinstance(fields, dict) else []):
                if field in ctx["claimed"] or not text(value):
                    continue
                out.append({"@type": ["schema:PropertyValue"],
                            "schema:name": "%s: %s" % (text(group), text(field)),
                            "schema:value": text(value)})
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
        # a creator list grows: later rows add the agents it does not yet name
        if isinstance(value, dict) and "@list" in value and "@list" in container[target]:
            names = {a.get("schema:name") for a in container[target]["@list"]}
            new = [a for a in value["@list"] if a.get("schema:name") not in names]
            container[target]["@list"].extend(new)
            return bool(new)
        return False
    container[target] = value[0] if isinstance(value, list) else value
    return True


def apply_table(rows, view, doc, record, ctx):
    changes = []
    for rule in rows:
        shaper = TRANSFORMS.get(rule.get("transform", ""))
        path = (rule.get("object_json_path") or "").strip()
        jpath = (rule.get("subject_json_path") or "").strip()
        if not shaper or not jpath or not path.startswith("$."):
            continue
        parts = path[2:].split(".", 1)
        if parts[0] == "schema:subjectOf" and len(parts) == 2:
            container, target = record, parts[1]
        elif len(parts) == 1:
            container, target = doc, parts[0]
        else:
            continue
        values = select(view, jpath)
        if not values:
            continue
        if _place_value(container, target, shaper(values, ctx)):
            changes.append("%s -> %s" % (jpath, rule["object_id"]))
            if jpath.startswith(CUSTOM):          # this custom field is mapped
                ctx["claimed"].add(jpath[len(CUSTOM):])
    return changes


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------

def is_view(view):
    return isinstance(view, dict) and bool(view.get("id")) and "viewType" in view


def convert_socrata_to_cdif(view, host, rows=None, detect=True, catalog=None, retrieved=None):
    """A Socrata view dict -> (CDIF JSON-LD dict, list of changes).

    host is the portal's domain, which the view does not carry; retrieved, the
    date the view was fetched, dates the catalog record when the view has no
    viewLastModified.
    """
    rows = rows if rows is not None else load_table()
    if not is_view(view):
        raise ValueError("not a Socrata view")
    ctx = {"view": view, "host": host, "claimed": set()}
    doc = {"@context": dict(CDIF_CONTEXT)}
    record = {}
    changes = apply_table(rows, view, doc, record, ctx)

    rid = "https://%s/d/%s" % (host, view["id"])
    doc = {"@context": doc.pop("@context"), "@id": rid, "@type": ["schema:Dataset"], **doc}
    used = json.dumps(doc)
    for prefix, iri in OPTIONAL_PREFIXES.items():
        if '"%s:' % prefix in used:
            doc["@context"][prefix] = iri

    # --- what CDIF requires ----------------------------------------------------------
    doc.setdefault("schema:identifier", view["id"])
    if not doc.get("schema:name"):
        doc["schema:name"] = view["id"]
        changes.append("schema:name from the id")
    if not doc.get("schema:description"):
        doc["schema:description"] = doc["schema:name"]
        changes.append("schema:description from the name")
    if not doc.get("schema:dateModified"):
        doc["schema:dateModified"] = "1900-01-01"
        changes.append("schema:dateModified sentinel (the view has no date)")
    doc.setdefault("schema:url", rid)
    if not doc.get("schema:license") and not doc.get("schema:conditionsOfAccess"):
        doc["schema:license"] = [{"@id": NIL}]
        changes.append("schema:license set to the OGC nil URI (the view names no licence)")

    # --- the catalog record --------------------------------------------------------
    subject = {
        "@id": rid + "#metadata",
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:name": "Socrata view for: %s" % doc["schema:name"][:120],
        "schema:about": {"@id": rid},
        "dcterms:conformsTo": [{"@id": "https://w3id.org/cdif/core/2.0"}],
        "schema:description": "Converted from the Socrata view https://%s/api/views/%s.json "
                              "to CDIF by socrata_to_cdif.py." % (host, view["id"]),
    }
    if retrieved:
        subject["schema:dateModified"] = retrieved
    subject.update(record)
    subject["schema:includedInDataCatalog"] = catalog or {
        "@type": ["schema:DataCatalog"], "schema:name": host, "schema:url": "https://%s/" % host}
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


def load_json(source):
    if _http(source):
        import urllib.request
        req = urllib.request.Request(source, headers={"User-Agent": "cdif-converters/0.1",
                                                      "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    return json.loads(Path(source).read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser(description="Convert Socrata views to CDIF JSON-LD")
    ap.add_argument("input", nargs="+", help="view JSON file(s), or https://{host}/api/views/{id}.json URL(s)")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--host", help="the portal's domain (required for a file: a view does not name it)")
    ap.add_argument("--catalog-name", help="name of the portal (default: its domain)")
    ap.add_argument("--static-conformance", action="store_true",
                    help="declare core only instead of detecting conformance")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    rows = load_table()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    for source in args.input:
        host = args.host or (urlsplit(source).netloc if _http(source) else None)
        if not host:
            sys.exit("%s: --host is required for a file" % source)
        view = load_json(source)
        if not is_view(view):
            print("%s: not a Socrata view" % source, file=sys.stderr)
            continue
        catalog = None
        if args.catalog_name:
            catalog = {"@type": ["schema:DataCatalog"], "schema:name": args.catalog_name,
                       "schema:url": "https://%s/" % host}
        doc, changes = convert_socrata_to_cdif(view, host, rows, not args.static_conformance, catalog)
        out = out_dir / ("%s-%s.jsonld" % (host, view["id"]))
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

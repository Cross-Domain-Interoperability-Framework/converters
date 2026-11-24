#!/usr/bin/env python3
"""
ckan_to_cdif.py - Convert a CKAN package to CDIF JSON-LD.

Reads a CKAN dataset ("package"), the object the Action API returns from
https://{host}/api/3/action/package_show?id={name} (as a JSON file holding that
object, or that URL); each becomes one CDIF record. The native package dict is
universal across CKAN installations, unlike the DCAT a portal serves only when
it runs ckanext-dcat -- for those, DCAT/dcat_to_cdif.py is the better path.

The table mappings/ckan-to-cdif.sssom.tsv drives the conversion: each row names
package members by a path from the package root (subject_json_path), the CDIF
location it lands at (object_json_path) and the shaper that builds the value
(transform). Row order is precedence for a scalar target; array targets
accumulate.

A package carries its metadata in three places, all handled:
  * base CKAN fields (title, notes, tags, resources, organization, license_*);
  * promoted DCAT fields a scheming portal adds at the top level (spatial,
    temporal_start, issued, theme, the *_translated multilingual pairs);
  * extras, a list of {key, value}: the ckanext-spatial keys (bbox-*, spatial,
    dataset-reference-date, responsible-party) and anything else a portal adds.
Extras are reached by the path _extras.<key>; every extra no row maps becomes a
schema:additionalProperty, so nothing is dropped.

A package does not name its portal, so a file needs --host.

Usage:
    python CKAN/ckan_to_cdif.py "https://data.gov.ie/api/3/action/package_show?id=barony-boundaries" -o out/
    python CKAN/ckan_to_cdif.py package.json --host data.gov.ie -o out/
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(ROOT / "validation"))
try:
    from detect_conformance import detect_cdif_conformance, apply_conformance
    _HAVE_DETECT = True
except Exception:
    _HAVE_DETECT = False

TABLE = ROOT / "mappings" / "ckan-to-cdif.sssom.tsv"
NIL = "http://www.opengis.net/def/nil/OGC/0/missing"
LANGS = ("en", "en-US", "en-GB")          # preferred languages for a scalar value

CDIF_CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
}
ARRAY_TARGETS = {
    "schema:keywords", "schema:publisher", "schema:creator", "schema:contributor",
    "schema:license", "schema:conditionsOfAccess", "schema:spatialCoverage",
    "schema:temporalCoverage", "schema:distribution", "schema:relatedLink",
    "schema:additionalProperty", "schema:inLanguage",
}


# ---------------------------------------------------------------------------
# paths (a dot-separated list of member names; '[*]' takes every item of an array)
# ---------------------------------------------------------------------------

def select(node, path):
    current = [node]
    for seg in path.split("."):
        every = seg.endswith("[*]")
        name = seg[:-3] if every else seg
        nxt = []
        for n in current:
            if isinstance(n, dict) and name in n:
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


def _lang(value):
    """A value that may be a plain string or a language map {lang: v} -> a string,
    preferring English, then the first non-empty."""
    if isinstance(value, dict):
        for lang in LANGS:
            if text(value.get(lang)):
                return text(value[lang])
        for v in value.values():
            if text(v):
                return text(v)
        return ""
    return text(value)


def _isodate(value):
    """A date or date-time Socrata/CKAN wrote with a space or a T, normalised to
    ISO 8601 (T, trailing Z dropped to naive); None for anything else."""
    t = text(value)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}(?::\d{2})?))?", t)
    if not m:
        return None
    return m.group(1) + ("T" + m.group(2) if m.group(2) else "")


def _bbox_of(geojson):
    """(S, W, N, E) of a GeoJSON geometry's coordinates, or None."""
    coords = []

    def walk(x):
        if (isinstance(x, list) and len(x) >= 2
                and all(isinstance(n, (int, float)) for n in x[:2])):
            coords.append((x[0], x[1]))
        elif isinstance(x, list):
            for i in x:
                walk(i)
    geom = geojson.get("geometry", geojson) if isinstance(geojson, dict) else None
    if not isinstance(geom, dict):
        return None
    walk(geom.get("coordinates"))
    if not coords:
        return None
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    s, w, n, e = min(lats), min(lons), max(lats), max(lons)
    if not (-90 <= s <= n <= 90 and -180 <= w <= 180 and -180 <= e <= 180):
        return None
    return s, w, n, e


def _fmt(x):
    return ("%.6f" % float(x)).rstrip("0").rstrip(".")


def _place(box=None, name=None):
    p = {"@type": ["schema:Place"]}
    if name:
        p["schema:name"] = name
    if box:
        s, w, n, e = box
        if s == n and w == e:
            p["schema:geo"] = {"@type": ["schema:GeoCoordinates"],
                               "schema:latitude": s, "schema:longitude": w}
        else:
            p["schema:geo"] = {"@type": ["schema:GeoShape"],
                               "schema:box": " ".join(_fmt(v) for v in (s, w, n, e))}
    return p


def _person_or_org(name, email=None, url=None):
    kind = "schema:Person" if name and " " in name.strip() and "," not in name else "schema:Organization"
    agent = {"@type": [kind], "schema:name": name}
    if email and "@" in email:
        agent["schema:email"] = email.replace("mailto:", "")
    if _http(url):
        agent["schema:url"] = url
    return agent


# ---------------------------------------------------------------------------
# shapers: tf_<transform>(values, ctx) -> value or list of values
# ---------------------------------------------------------------------------

def tf_text(values, ctx):
    for v in values:
        if text(v):
            return text(v)
    return None


def tf_langtext(values, ctx):
    """A plain string or a {lang: value} map -> the preferred-language string."""
    for v in values:
        t = _lang(v)
        if t:
            return t
    return None


def tf_date(values, ctx):
    for v in values:
        d = _isodate(v)
        if d:
            return d
    return None


def tf_tags(values, ctx):
    """CKAN tags -> keywords: a DefinedTerm when the tag names a vocabulary,
    else a plain string."""
    out = []
    for taglist in values:
        for t in (taglist if isinstance(taglist, list) else [taglist]):
            if not isinstance(t, dict):
                continue
            name = text(t.get("display_name") or t.get("name"))
            if not name:
                continue
            if text(t.get("vocabulary_id")):
                term = {"@type": ["schema:DefinedTerm"], "schema:name": name,
                        "schema:inDefinedTermSet": text(t["vocabulary_id"])}
                item = term
            else:
                item = name
            if item not in out:
                out.append(item)
    return out


def tf_langkeywords(values, ctx):
    """A keyword field that is a list, a string, or a {lang: [list]} map."""
    out = []
    for v in values:
        lists = v.values() if isinstance(v, dict) else [v]
        for lst in lists:
            for k in (lst if isinstance(lst, list) else [lst]):
                if text(k) and text(k) not in out:
                    out.append(text(k))
    return out


def tf_definedterms(values, ctx):
    """theme / subject / topic_category / groups -> DefinedTerms named by the field."""
    label = ctx["rule"]["subject_json_path"].split(".")[-1].replace("[*]", "")
    out = []
    for v in values:
        for item in (v if isinstance(v, list) else [v]):
            name = (_lang(item.get("title") or item.get("display_name") or item.get("name"))
                    if isinstance(item, dict) else text(item))
            if not name:
                continue
            term = {"@type": ["schema:DefinedTerm"], "schema:name": name,
                    "schema:inDefinedTermSet": "CKAN %s" % label}
            if isinstance(item, dict) and text(item.get("name")):
                term["schema:termCode"] = text(item["name"])
            if term not in out:
                out.append(term)
    return out


def tf_license(values, ctx):
    """license_title / license_id / license_url -> a CreativeWork when there is a
    URL, otherwise the licence id as a string.

    CDIF's schema:license item is a string, an {@id} reference, or a CreativeWork
    (labeledLink) -- and a CreativeWork requires schema:url, so a licence with no
    URL can only be the identifier string."""
    pkg = ctx["pkg"]
    name = _lang(pkg.get("license_title"))
    lid = text(pkg.get("license_id"))
    url = pkg.get("license_url")
    if _http(url):
        work = {"@type": ["schema:CreativeWork"], "schema:url": url}
        if name or lid:
            work["schema:name"] = name or lid
        return [work]
    ident = lid or name                         # a bare string: the licence id, ideally a URI
    return [ident] if ident else None


def tf_creator(values, ctx):
    """author (+author_email) -> the creator list."""
    name = tf_text(values, ctx)
    if not name:
        return None
    return {"@list": [_person_or_org(name, ctx["pkg"].get("author_email"))]}


def tf_maintainer(values, ctx):
    """maintainer (+maintainer_email) -> a schema:Role pointOfContact."""
    name = tf_text(values, ctx)
    if not name:
        return None
    return [{"@type": ["schema:Role"], "schema:roleName": "pointOfContact",
             "schema:contributor": _person_or_org(name, ctx["pkg"].get("maintainer_email"))}]


def tf_contact(values, ctx):
    """contact_name / contact_email (DCAT-AP scheming) -> a Role pointOfContact."""
    pkg = ctx["pkg"]
    name = _lang(pkg.get("contact_name"))
    email = text(pkg.get("contact_email"))
    if not name and "@" not in email:
        return None
    agent = _person_or_org(name or email.split("@")[0], email)
    return [{"@type": ["schema:Role"], "schema:roleName": "pointOfContact",
             "schema:contributor": agent}]


def tf_publisher(values, ctx):
    """organization -> a schema:Organization publisher."""
    out = []
    for org in values:
        if not isinstance(org, dict):
            continue
        name = _lang(org.get("title") or org.get("name"))
        if not name:
            continue
        pub = {"@type": ["schema:Organization"], "schema:name": name}
        if text(org.get("id")):
            pub["schema:identifier"] = text(org["id"])
        if _http(org.get("image_url")):
            pub["schema:logo"] = org["image_url"]
        desc = _lang(org.get("description"))
        if desc:
            pub["schema:description"] = desc
        if pub not in out:
            out.append(pub)
    return out


def tf_spatial(values, ctx):
    """A GeoJSON geometry (top-level `spatial` or an extra) -> a Place with its
    bounding box."""
    out = []
    for v in values:
        geo = v
        if isinstance(v, str):
            try:
                geo = json.loads(v)
            except ValueError:
                continue
        box = _bbox_of(geo) if isinstance(geo, dict) else None
        if box:
            p = _place(box=box)
            if p not in out:
                out.append(p)
    return out


def tf_bbox(values, ctx):
    """The ckanext-spatial extras bbox-west-long / -east-long / -south-lat /
    -north-lat -> a Place with that box."""
    ex = ctx["pkg"].get("_extras", {})
    try:
        w = float(ex["bbox-west-long"]); e = float(ex["bbox-east-long"])
        s = float(ex["bbox-south-lat"]); n = float(ex["bbox-north-lat"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= s <= n <= 90 and -180 <= w <= 180 and -180 <= e <= 180):
        return None
    ctx["claimed"].update(("bbox-west-long", "bbox-east-long", "bbox-south-lat", "bbox-north-lat"))
    return [_place(box=(s, w, n, e))]


def tf_places(values, ctx):
    out = []
    for v in values:
        for item in (v if isinstance(v, list) else [v]):
            name = _lang(item) if isinstance(item, dict) else text(item)
            if name and _place(name=name) not in out:
                out.append(_place(name=name))
    return out


def tf_interval(values, ctx):
    """A start field, optionally paired with its end, -> an ISO 8601 interval."""
    pkg = ctx["pkg"]
    pairs = [("time_period_coverage_start", "time_period_coverage_end"),
             ("temporal_start", "temporal_end"),
             ("temporal_coverage_start", "temporal_coverage_end")]
    out = []
    for start_key, end_key in pairs:
        start = _isodate(pkg.get(start_key)) or _isodate((pkg.get("_extras") or {}).get(start_key))
        if not start:
            continue
        end = _isodate(pkg.get(end_key)) or _isodate((pkg.get("_extras") or {}).get(end_key))
        ctx["claimed"].update((start_key, end_key))
        iv = "%s/%s" % (start, end) if end else start
        if iv not in out:
            out.append(iv)
    return out


def tf_resources(values, ctx):
    """resources -> schema:DataDownloads."""
    out = []
    for reslist in values:
        for r in (reslist if isinstance(reslist, list) else [reslist]):
            if not isinstance(r, dict) or not _http(r.get("url")):
                continue
            dd = {"@type": ["schema:DataDownload"], "schema:contentUrl": r["url"]}
            name = _lang(r.get("name")) or text(r.get("format"))
            if name:
                dd["schema:name"] = name
            if _lang(r.get("description")):
                dd["schema:description"] = _lang(r["description"])
            fmt = text(r.get("mimetype") or r.get("format"))
            if fmt:
                dd["schema:encodingFormat"] = [fmt]
            try:
                size = int(r.get("size"))
                dd["schema:contentSize"] = "%d B" % size
            except (TypeError, ValueError):
                pass
            if dd not in out:
                out.append(dd)
    return out


def tf_extras(values, ctx):
    """Every extra no other row mapped -> a PropertyValue named by its key."""
    out = []
    for e in values:
        for item in (e if isinstance(e, list) else [e]):
            if not isinstance(item, dict):
                continue
            key = text(item.get("key"))
            val = item.get("value")
            if not key or key in ctx["claimed"] or val in (None, "", [], {}):
                continue
            if key.startswith("harvest"):          # harvester bookkeeping, not metadata
                continue
            out.append({"@type": ["schema:PropertyValue"], "schema:name": key,
                        "schema:value": text(val) if not isinstance(val, (dict, list))
                        else json.dumps(val, ensure_ascii=False)})
    return out


def tf_homepage(values, ctx):
    """The package `url` field (its source landing page) -> a LinkRole 'homepage'."""
    for v in values:
        if _http(text(v)):
            return [{"@type": ["schema:LinkRole"], "schema:linkRelationship": "homepage",
                     "schema:target": {"@type": ["schema:EntryPoint"], "schema:url": text(v)}}]
    return None


def tf_openaccess(values, ctx):
    """isopen = true -> a conditionsOfAccess note (no licence may be named)."""
    for v in values:
        if v is True or text(v).lower() == "true":
            return ["Open data: the portal marks this dataset's licence as open."]
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


def apply_table(rows, pkg, doc, record, ctx):
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
        values = select(pkg, jpath)
        if not values:
            continue
        ctx["rule"] = rule
        if _place_value(container, target, shaper(values, ctx)):
            changes.append("%s -> %s" % (jpath, rule["object_id"]))
            if jpath.startswith("_extras."):
                ctx["claimed"].add(jpath.split(".", 1)[1])
    return changes


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------

def is_package(pkg):
    return isinstance(pkg, dict) and bool(pkg.get("name") or pkg.get("id")) and "resources" in pkg


def _doi(pkg):
    for key in ("digital_object_identifier", "doi"):
        m = re.search(r"(10\.\d{4,9}/\S+)", text(pkg.get(key)))
        if m:
            return m.group(1).rstrip(".,;")
    return None


def convert_ckan_to_cdif(pkg, host, rows=None, detect=True, catalog=None, retrieved=None):
    """A CKAN package dict -> (CDIF JSON-LD dict, list of changes).

    host is the portal domain, which the package does not carry; retrieved, the
    date it was fetched, dates the catalog record when metadata_modified is absent.
    """
    rows = rows if rows is not None else load_table()
    if not is_package(pkg):
        raise ValueError("not a CKAN package")
    pkg = dict(pkg)
    pkg["_extras"] = {e["key"]: e["value"] for e in pkg.get("extras") or []
                      if isinstance(e, dict) and e.get("key")}
    slug = pkg.get("name") or pkg.get("id")
    doi = _doi(pkg)
    ctx = {"pkg": pkg, "host": host, "claimed": set()}
    doc = {"@context": dict(CDIF_CONTEXT)}
    record = {}
    changes = apply_table(rows, pkg, doc, record, ctx)

    rid = "https://doi.org/" + doi if doi else "https://%s/dataset/%s" % (host, slug)
    doc = {"@context": doc.pop("@context"), "@id": rid, "@type": ["schema:Dataset"], **doc}
    if "cdi:" in json.dumps(doc):
        doc["@context"]["cdi"] = "http://ddialliance.org/Specification/DDI-CDI/1.0/RDF/"

    # --- identifier (a single PropertyValue; core's schema:identifier is not a list) ---
    if doi:
        doc["schema:identifier"] = {
            "@type": ["schema:PropertyValue"],
            "schema:propertyID": {"@id": "https://registry.identifiers.org/registry/doi"},
            "schema:value": doi, "schema:url": "https://doi.org/" + doi}
    else:
        doc["schema:identifier"] = {"@type": ["schema:PropertyValue"],
                                    "schema:propertyID": "CKAN id",
                                    "schema:value": text(pkg.get("id") or slug)}

    # --- what CDIF requires -----------------------------------------------------------
    if not doc.get("schema:name"):
        doc["schema:name"] = _lang(pkg.get("title")) or slug
        changes.append("schema:name from the title or slug")
    if not doc.get("schema:description"):
        doc["schema:description"] = doc["schema:name"]
        changes.append("schema:description from the name")
    if not doc.get("schema:dateModified"):
        doc["schema:dateModified"] = _isodate(pkg.get("metadata_modified")) or "1900-01-01"
        if doc["schema:dateModified"] == "1900-01-01":
            changes.append("schema:dateModified sentinel")
    doc.setdefault("schema:url", "https://%s/dataset/%s" % (host, slug))
    if not doc.get("schema:license") and not doc.get("schema:conditionsOfAccess"):
        doc["schema:license"] = [{"@id": NIL}]
        changes.append("schema:license set to the OGC nil URI (no licence named)")

    # --- the catalog record -----------------------------------------------------------
    subject = {
        "@id": rid + "#metadata",
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:name": "CKAN package for: %s" % doc["schema:name"][:120],
        "schema:about": {"@id": rid},
        "dcterms:conformsTo": [{"@id": "https://w3id.org/cdif/core/2.0"}],
        "schema:description": "Converted from the CKAN package "
                              "https://%s/api/3/action/package_show?id=%s to CDIF by "
                              "ckan_to_cdif.py." % (host, slug),
    }
    if _isodate(pkg.get("metadata_modified")) or retrieved:
        subject["schema:dateModified"] = _isodate(pkg.get("metadata_modified")) or retrieved
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
            data = json.loads(r.read().decode("utf-8"))
    else:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    # unwrap an Action API envelope {"success": true, "result": {...}}
    if isinstance(data, dict) and "result" in data and isinstance(data["result"], dict):
        return data["result"]
    return data


def _host_of(source):
    if _http(source):
        return urlsplit(source).netloc
    return None


def main():
    ap = argparse.ArgumentParser(description="Convert CKAN packages to CDIF JSON-LD")
    ap.add_argument("input", nargs="+", help="package JSON file(s), or package_show URL(s)")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--host", help="the portal domain (required for a file: a package "
                                   "does not name its portal)")
    ap.add_argument("--catalog-name", help="name of the portal (default: its domain)")
    ap.add_argument("--static-conformance", action="store_true",
                    help="declare core only instead of detecting conformance")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    rows = load_table()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    for source in args.input:
        host = args.host or _host_of(source)
        if not host:
            sys.exit("%s: --host is required for a file" % source)
        pkg = load_json(source)
        if not is_package(pkg):
            print("%s: not a CKAN package" % source, file=sys.stderr)
            continue
        catalog = None
        if args.catalog_name:
            catalog = {"@type": ["schema:DataCatalog"], "schema:name": args.catalog_name,
                       "schema:url": "https://%s/" % host}
        doc, changes = convert_ckan_to_cdif(pkg, host, rows, not args.static_conformance, catalog)
        slug = pkg.get("name") or pkg.get("id")
        out = out_dir / ("%s-%s.jsonld" % (host, slug))
        out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8", newline="\n")
        declared = [c["@id"].rsplit("/", 2)[-2] for c in
                    doc["schema:subjectOf"].get("dcterms:conformsTo", [])]
        line = "%-55s %s" % (out.name, "+".join(declared) or "no conformance")
        if args.verbose:
            line += "\n      " + "\n      ".join(changes)
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

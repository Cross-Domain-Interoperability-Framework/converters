#!/usr/bin/env python3
"""
stac_to_cdif.py - Convert STAC Collections to CDIF JSON-LD.

Reads a STAC 1.x Collection (a JSON file, or the URL of one); each becomes one
CDIF record describing the collection as a dataset. Its Items are not
converted: a Collection already carries the dataset-level description (extent,
licence, providers, keywords), and its item links are not listed.

The table mappings/stac-to-cdif.sssom.tsv drives the conversion: each row names
Collection members by a path from the Collection root (subject_json_path), the
CDIF location it lands at (object_json_path) and the shaper that builds the
value (transform). Row order is precedence for a scalar target; array targets
accumulate.

A subject_json_path is a dot-separated list of member names; '*' takes every
member of an object, '[*]' every item of an array, and '[?key=a|b]' the array
items whose key is a or b (or, for a list-valued key such as roles, contains
one of them). Member names may contain ':' (sci:doi).

Usage:
    python STAC/stac_to_cdif.py collection.json -o out/
    python STAC/stac_to_cdif.py "https://data.geo.admin.ch/api/stac/v1/collections/ch.bafu.bundesinventare-bln" -o out/
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(ROOT / "validation"))
try:
    from detect_conformance import detect_cdif_conformance, apply_conformance
    _HAVE_DETECT = True
except Exception:
    _HAVE_DETECT = False

TABLE = ROOT / "mappings" / "stac-to-cdif.sssom.tsv"
NIL = "http://www.opengis.net/def/nil/OGC/0/missing"

CDIF_CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
}
ARRAY_TARGETS = {
    "schema:keywords", "schema:provider",
    "schema:contributor", "schema:license", "schema:conditionsOfAccess",
    "schema:spatialCoverage", "schema:temporalCoverage", "schema:distribution",
    "schema:relatedLink", "schema:citation",
}

# SPDX identifiers as STAC writers spell them (lower-cased) -> the SPDX id
SPDX = {re.sub(r"[^a-z0-9]", "", i.lower()): i for i in (
    "CC0-1.0", "CC-BY-4.0", "CC-BY-3.0", "CC-BY-SA-4.0", "CC-BY-SA-3.0", "CC-BY-NC-4.0",
    "CC-BY-NC-SA-4.0", "CC-BY-ND-4.0", "CC-BY-NC-ND-4.0", "PDDL-1.0", "ODbL-1.0",
    "ODC-By-1.0", "NLOD-2.0", "etalab-2.0", "OGL-UK-3.0", "DL-DE-BY-2.0", "DL-DE-ZERO-2.0",
    "MIT", "Apache-2.0", "GPL-3.0-only", "BSD-3-Clause", "CDLA-Permissive-2.0", "Unlicense",
)}
# licence values that say "see the license link" rather than name a licence
NOT_A_LICENSE = {"proprietary", "other", "various", ""}
# asset roles that make an asset a distribution of the data
DATA_ROLES = {"data", "archive", "default", "visual", "overview", "stac-items", "collection-mirror"}


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

_SEG = re.compile(r"^([^\[\]]*)(?:\[(\*|\?([^=\]]+)=([^\]]*))\])?$")


def select(node, path):
    """Every value at a subject_json_path (see the module docstring)."""
    current = [node]
    for seg in path.split("."):
        m = _SEG.match(seg)
        if not m:
            raise ValueError("bad subject_json_path segment %r" % seg)
        name, brk, key, wanted = m.groups()
        nxt = []
        for n in current:
            if not isinstance(n, dict):
                continue
            if name == "*":
                nxt.extend(n.values())
            elif name in n:
                nxt.append(n[name])
        if brk:
            items = [i for v in nxt if isinstance(v, list) for i in v]
            if brk != "*":
                opts = set(wanted.split("|"))
                items = [i for i in items if isinstance(i, dict) and _has(i.get(key), opts)]
            nxt = items
        current = nxt
    return [v for v in current if v not in (None, "", [], {})]


def _has(value, opts):
    if isinstance(value, list):
        return any(v in opts for v in value)
    return value in opts


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def text(v):
    return " ".join(str(v).split()) if isinstance(v, (str, int, float)) else ""


def _http(t):
    return bool(re.match(r"^https?://", t or ""))


def _fmt(n):
    """A coordinate to six decimal places (about 0.1 m), as the ISO converter writes it."""
    return ("%.6f" % float(n)).rstrip("0").rstrip(".")


def _href(link, ctx):
    href = (link or {}).get("href") if isinstance(link, dict) else None
    if not href:
        return None
    url = urljoin(ctx["base"], href) if ctx.get("base") else href
    return url if _http(url) else None


def _doi(value):
    m = re.search(r"(10\.\d{4,9}/\S+)", text(value))
    return m.group(1).rstrip(".,;") if m else None


def _doi_pv(doi):
    return {"@type": ["schema:PropertyValue"],
            "schema:propertyID": {"@id": "https://registry.identifiers.org/registry/doi"},
            "schema:value": doi, "schema:url": "https://doi.org/" + doi}


def _date(value):
    """An ISO 8601 date or date-time, normalised; None for anything else."""
    t = text(value)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?)(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$", t)
    if not m:
        return None
    if not m.group(2):
        return m.group(1)
    zone = m.group(3) or "Z"
    if zone in ("+00:00", "+0000"):
        zone = "Z"
    return "%sT%s%s" % (m.group(1), m.group(2), zone)


def _organization(p):
    org = {"@type": ["schema:Organization"], "schema:name": text(p.get("name"))}
    url = p.get("url")
    if _http(url):
        org["schema:url"] = url
    if text(p.get("description")):
        org["schema:description"] = text(p["description"])
    return org


def place(box):
    s, w, n, e = box
    if s == n and w == e:
        geo = {"@type": ["schema:GeoCoordinates"], "schema:latitude": s, "schema:longitude": w}
    else:
        geo = {"@type": ["schema:GeoShape"], "schema:box": " ".join(_fmt(x) for x in (s, w, n, e))}
    return {"@type": ["schema:Place"], "schema:geo": geo}


# ---------------------------------------------------------------------------
# shapers: tf_<transform>(values, ctx) -> value or list of values
# ---------------------------------------------------------------------------

def tf_text(values, ctx):
    for v in values:
        if text(v):
            return text(v)
    return None


def tf_multitext(values, ctx):
    """The first non-empty value, keeping its line breaks (descriptions are Markdown)."""
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def tf_texts(values, ctx):
    out = []
    for v in values:
        for t in (v if isinstance(v, list) else [v]):
            if text(t) and text(t) not in out:
                out.append(text(t))
    return out


def tf_date(values, ctx):
    for v in values:
        d = _date(v)
        if d:
            return d
    return None


def tf_doi(values, ctx):
    """sci:doi: the DOI of the data, unless it is also one of sci:publications."""
    pubs = {(_doi(p.get("doi")) or "").lower() for p in ctx["stac"].get("sci:publications") or []
            if isinstance(p, dict)}
    for v in values:
        d = _doi(v)
        if d and d.lower() not in pubs:
            return _doi_pv(d)
    return None


def tf_keywords(values, ctx):
    out = []
    for v in values:
        for k in (v if isinstance(v, list) else [v]):
            t = text(k)
            if t and t not in out:
                out.append(t)
    return out


def tf_themes(values, ctx):
    """themes extension: concepts in their scheme -> DefinedTerms."""
    out = []
    for theme in values:
        if not isinstance(theme, dict):
            continue
        scheme = text(theme.get("scheme"))
        for c in theme.get("concepts") or []:
            name = text(c.get("title") or c.get("id"))
            if not name:
                continue
            term = {"@type": ["schema:DefinedTerm"], "schema:name": name}
            if text(c.get("id")):
                term["schema:termCode"] = text(c["id"])
            if scheme:
                term["schema:inDefinedTermSet"] = scheme
            if _http(c.get("url")):
                term["schema:url"] = c["url"]
            if term not in out:
                out.append(term)
    return out


def tf_summaryterms(values, ctx):
    """summaries platform / instruments / constellation -> DefinedTerms named by the summary."""
    label = ctx["rule"]["subject_json_path"].rsplit(".", 1)[-1]
    out = []
    for v in values:
        for t in (v if isinstance(v, list) else [v]):
            t = text(t)
            if not t:
                continue
            term = {"@type": ["schema:DefinedTerm"], "schema:name": t,
                    "schema:inDefinedTermSet": "STAC " + label}
            if len(t) < 3:
                term["schema:termCode"] = t      # CDIF wants a name of 3+ characters, else a code
            if term not in out:
                out.append(term)
    return out


def tf_license(values, ctx):
    """An SPDX id -> its spdx.org page. proprietary / other name no licence: the license link does."""
    out = []
    for v in values:
        t = text(v)
        key = re.sub(r"[^a-z0-9]", "", t.lower())
        if key in SPDX:
            out.append({"@id": "https://spdx.org/licenses/%s" % SPDX[key]})
    return out


def tf_licensetext(values, ctx):
    """A licence value that is not an SPDX id, nor proprietary / other / various -> its text."""
    out = []
    for v in values:
        t = text(v)
        if t.lower() not in NOT_A_LICENSE and re.sub(r"[^a-z0-9]", "", t.lower()) not in SPDX:
            out.append("License: " + t)
    return out


def tf_licenselink(values, ctx):
    out = []
    for ln in values:
        url = _href(ln, ctx)
        if url:
            work = {"@type": ["schema:CreativeWork"], "schema:url": url}
            if text(ln.get("title")):
                work["schema:name"] = text(ln["title"])
            out.append(work)
    return out


def tf_organizations(values, ctx):
    return [_organization(p) for p in values if isinstance(p, dict) and text(p.get("name"))]


def tf_creators(values, ctx):
    """Producers -> the creator list ({"@list": [...]}, as CDIF keeps author order)."""
    orgs = tf_organizations(values, ctx)
    return {"@list": orgs} if orgs else None


def tf_providerroles(values, ctx):
    """Providers with roles licensor or processor -> schema:Role contributors."""
    out = []
    for p in values:
        if not isinstance(p, dict) or not text(p.get("name")):
            continue
        for role in p.get("roles") or []:
            if role in ("licensor", "processor"):
                out.append({"@type": ["schema:Role"], "schema:roleName": role,
                            "schema:contributor": _organization(p)})
    return out


def tf_contacts(values, ctx):
    """contacts extension -> schema:Role pointOfContact."""
    out = []
    for c in values:
        if not isinstance(c, dict):
            continue
        name, org = text(c.get("name")), text(c.get("organization"))
        if not (name or org):
            continue
        agent = ({"@type": ["schema:Person"], "schema:name": name} if name
                 else {"@type": ["schema:Organization"], "schema:name": org})
        if name and org:
            agent["schema:affiliation"] = {"@type": ["schema:Organization"], "schema:name": org}
        if text(c.get("position")):
            agent["schema:jobTitle"] = text(c["position"])
        emails = [text(e.get("value")) for e in c.get("emails") or [] if isinstance(e, dict)]
        if emails and emails[0]:
            agent["schema:email"] = emails[0]
        phones = [text(e.get("value")) for e in c.get("phones") or [] if isinstance(e, dict)]
        if phones and phones[0]:
            agent["schema:telephone"] = phones[0]
        out.append({"@type": ["schema:Role"], "schema:roleName": "pointOfContact",
                    "schema:contributor": agent})
    return out


def _box(b):
    """(S, W, N, E) from a STAC bbox (W, S, E, N, or W, S, zmin, E, N, zmax), or None."""
    if not isinstance(b, list) or len(b) not in (4, 6):
        return None
    try:
        nums = [float(x) for x in b]
    except (TypeError, ValueError):
        return None
    w, s, e, n = (nums[0], nums[1], nums[2], nums[3]) if len(nums) == 4 else \
        (nums[0], nums[1], nums[3], nums[4])
    if not (-90 <= s <= n <= 90 and -180 <= w <= 180 and -180 <= e <= 180):
        return None
    return s, w, n, e


def tf_bbox(values, ctx):
    """extent.spatial.bbox: the first box is the whole extent; the others are its parts.
    A part that repeats the whole is dropped."""
    out = []
    for boxes in values:
        for b in boxes if isinstance(boxes, list) else []:
            box = _box(b)
            if box:
                p = place(box)
                if p not in out:
                    out.append(p)
    return out


def tf_interval(values, ctx):
    """extent.temporal.interval -> ISO 8601 intervals; an open end is '..'."""
    out = []
    for intervals in values:
        for iv in intervals if isinstance(intervals, list) else []:
            if not isinstance(iv, list) or len(iv) != 2:
                continue
            start, end = (_date(x) for x in iv)
            if not (start or end):
                continue
            t = start if start == end else "%s/%s" % (start or "..", end or "..")
            if t not in out:
                out.append(t)
    return out


def tf_assets(values, ctx):
    """Assets whose roles make them data (or that have no roles) -> DataDownloads."""
    out = []
    for a in values:
        if not isinstance(a, dict):
            continue
        roles = set(a.get("roles") or [])
        url = _href(a, ctx)
        if not url or (roles and not roles & DATA_ROLES):
            continue
        dd = {"@type": ["schema:DataDownload"], "schema:contentUrl": url}
        if text(a.get("title")):
            dd["schema:name"] = text(a["title"])
        if text(a.get("description")):
            dd["schema:description"] = text(a["description"])
        if text(a.get("type")):
            dd["schema:encodingFormat"] = [text(a["type"])]
        size = a.get("file:size")
        if isinstance(size, (int, float)):
            dd["schema:contentSize"] = "%d B" % size
        if dd not in out:
            out.append(dd)
    return out


def _linkrole(url, rel, name=None, fmt=None):
    target = {"@type": ["schema:EntryPoint"], "schema:url": url}
    if name:
        target["schema:name"] = name
    if fmt:
        target["schema:encodingFormat"] = fmt
    return {"@type": ["schema:LinkRole"], "schema:linkRelationship": rel, "schema:target": target}


def tf_assetlinks(values, ctx):
    """The other assets (thumbnails, metadata, documentation, styles) -> LinkRoles by role."""
    out = []
    for a in values:
        if not isinstance(a, dict):
            continue
        roles = [r for r in a.get("roles") or []]
        url = _href(a, ctx)
        if not url or not roles or set(roles) & DATA_ROLES:
            continue
        lr = _linkrole(url, roles[0], text(a.get("title")) or None, text(a.get("type")) or None)
        if lr not in out:
            out.append(lr)
    return out


def tf_links(values, ctx):
    out = []
    for ln in values:
        url = _href(ln, ctx)
        if not url:
            continue
        lr = _linkrole(url, text(ln.get("rel")), text(ln.get("title")) or None,
                       text(ln.get("type")) or None)
        if lr not in out:
            out.append(lr)
    return out


def tf_landing(values, ctx):
    """An HTML alternate (a STAC Browser page or the publisher's own) -> schema:url."""
    for ln in values:
        if "html" in text(ln.get("type")).lower():
            url = _href(ln, ctx)
            if url:
                return url
    return None


def tf_publications(values, ctx):
    """sci:publications -> CreativeWorks: their citation, with the DOI as identifier."""
    out = []
    for p in values:
        if not isinstance(p, dict):
            continue
        work = {"@type": ["schema:CreativeWork"]}
        if text(p.get("title")):
            work["schema:name"] = text(p["title"])
        if text(p.get("citation")):
            work["schema:description"] = text(p["citation"])
        d = _doi(p.get("doi"))
        if d:
            work["schema:identifier"] = _doi_pv(d)
            work["schema:url"] = "https://doi.org/" + d
        if len(work) > 1 and work not in out:
            out.append(work)
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


def apply_table(rows, stac, doc, record, ctx):
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
        values = select(stac, jpath)
        if not values:
            continue
        ctx["rule"] = rule
        if _place_value(container, target, shaper(values, ctx)):
            changes.append("%s -> %s" % (jpath, rule["object_id"]))
    return changes


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------

def is_collection(stac):
    return isinstance(stac, dict) and stac.get("type") == "Collection" and bool(stac.get("id"))


def self_url(stac, source=None):
    for ln in stac.get("links") or []:
        if ln.get("rel") == "self" and _http(ln.get("href")):
            return ln["href"]
    return source if _http(source) else None


def convert_stac_to_cdif(stac, rows=None, detect=True, catalog=None, source=None, retrieved=None):
    """A STAC Collection dict -> (CDIF JSON-LD dict, list of changes).

    source is the URL the Collection was fetched from (its @id when it has no
    DOI and no self link, and the base for relative hrefs); retrieved, the date
    it was fetched, dates the catalog record.
    """
    rows = rows if rows is not None else load_table()
    if not is_collection(stac):
        raise ValueError("not a STAC Collection")
    here = self_url(stac, source)
    ctx = {"stac": stac, "base": here or source}
    doc = {"@context": dict(CDIF_CONTEXT)}
    record = {}
    changes = apply_table(rows, stac, doc, record, ctx)

    ident = doc.get("schema:identifier")
    if isinstance(ident, dict):                      # a DOI
        rid = ident["schema:url"]
    elif here:
        rid = here
    else:
        rid = "urn:sha1:%s" % hashlib.sha1(json.dumps(stac, sort_keys=True).encode()).hexdigest()
        changes.append("@id made up (no DOI, no self link)")
    doc = {"@context": doc.pop("@context"), "@id": rid, "@type": ["schema:Dataset"], **doc}
    doc.setdefault("schema:identifier", stac["id"])

    # --- what CDIF requires ----------------------------------------------------------
    if not doc.get("schema:name"):
        doc["schema:name"] = stac["id"]
        changes.append("schema:name from the id")
    if not doc.get("schema:description"):
        doc["schema:description"] = doc["schema:name"]
        changes.append("schema:description from the name")
    if not doc.get("schema:dateModified"):
        doc["schema:dateModified"] = "1900-01-01"
        changes.append("schema:dateModified sentinel (no updated date)")
    if not doc.get("schema:url"):
        doc["schema:url"] = here or NIL
    if not doc.get("schema:license") and not doc.get("schema:conditionsOfAccess"):
        doc["schema:license"] = [{"@id": NIL}]
        changes.append("schema:license set to the OGC nil URI (no licence named or linked)")

    # --- the catalog record --------------------------------------------------------
    version = text(stac.get("stac_version")) or "1.x"
    subject = {
        "@id": rid + "#metadata",
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:name": "STAC Collection for: %s" % doc["schema:name"][:120],
        "schema:about": {"@id": rid},
        "dcterms:conformsTo": [{"@id": "https://w3id.org/cdif/core/2.0"}],
        "schema:description": "Converted from a STAC %s Collection to CDIF by stac_to_cdif.py."
                              % version,
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


def load_json(source):
    if _http(source):
        import urllib.request
        req = urllib.request.Request(source, headers={"User-Agent": "cdif-converters/0.1",
                                                      "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _slug(text_):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text_ or "").strip("_")[:100]


def main():
    ap = argparse.ArgumentParser(description="Convert STAC Collections to CDIF JSON-LD")
    ap.add_argument("input", nargs="+", help="Collection JSON file(s) or URL(s)")
    ap.add_argument("--output", "-o", default=".", help="output directory")
    ap.add_argument("--source-url", help="the URL a file was fetched from (the base for "
                                         "relative links, and the @id when there is no self link)")
    ap.add_argument("--catalog-name", help="name of the STAC catalog the Collection is in")
    ap.add_argument("--catalog-url", help="URL of that catalog")
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
        stac = load_json(source)
        if not is_collection(stac):
            print("%s: not a STAC Collection" % source, file=sys.stderr)
            continue
        src = args.source_url or (source if _http(source) else None)
        doc, changes = convert_stac_to_cdif(stac, rows, not args.static_conformance, catalog, src)
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

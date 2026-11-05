#!/usr/bin/env python3
"""harvest_niaid_to_cdif.py - Harvest Dataset records from the NIAID Data Discovery
Portal API and convert them to CDIF JSON-LD.

The portal (https://data.niaid.nih.gov/) aggregates about 14.9 million records
from some 55 repositories, of which about 5.7 million are @type Dataset. Its API
(https://api.data.niaid.nih.gov/v1, OpenAPI at /v1/spec) is a BioThings service:
Lucene-style queries against /query, with fetch_all/scroll_id paging for result
sets beyond its 10,000-record window.

Records are bare schema.org names with extensions from NIAID's own schema
(https://github.com/NIAID-Data-Ecosystem/nde-schemas), so conversion is:

  1. clean: drop the index's internal fields (_id, _score, _ignored, _meta,
     metadata_score, the ibmGranite* embedding fields);
  2. name: give the record a @context saying which names are nde: and that the
     rest are schema.org, which soso/ConvertFromSOSO.py's naming step then honours
     (28 records in 96 sampled carry no @context of their own);
  3. shape: fit values to the forms CDIF's schemas expect, from what the record
     holds (see _shape): a DOI becomes a PropertyValue identifier, `date` is the
     last fallback for schema:dateModified, a grant with several funders becomes
     one per funder, url-less usageInfo becomes conditionsOfAccess, and so on;
  4. convert with convert_soso_to_cdif;
  5. finish the catalog record: where this record came from (the portal, with
     the index's build date), then conformsTo detected from content.

The repositories holding the dataset stay on the dataset as
schema:includedInDataCatalog; the catalog record's includedInDataCatalog is the
portal, which is where this metadata was harvested from.

Usage:
    python harvest_niaid_to_cdif.py --list-sources
    python harvest_niaid_to_cdif.py --source "Harvard Dataverse" -n 20 -o out/
    python harvest_niaid_to_cdif.py -q 'healthCondition.name:"influenza"' -n 100 -o out/
    python harvest_niaid_to_cdif.py --source Zenodo --since 2026-10-01 -n 100 -o out/
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "soso"))
from ConvertFromSOSO import convert_soso_to_cdif  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "validation"))
try:
    from detect_conformance import detect_cdif_conformance, apply_conformance
    _HAVE_DETECT = True
except Exception:
    _HAVE_DETECT = False

API = "https://api.data.niaid.nih.gov/v1"
PORTAL = "https://data.niaid.nih.gov/"
NDE_NS = "https://discovery.biothings.io/ns/nde/"
DOI_REGISTRY = "https://registry.identifiers.org/registry/doi"
OGC_NIL_MISSING = "http://www.opengis.net/def/nil/OGC/0/missing"

# The index's own bookkeeping, not metadata about the dataset.
INTERNAL_FIELDS = {"_id", "_score", "_ignored", "_meta", "_version", "@version",
                   "metadata_score", "ibmGraniteEmbedding",
                   "ibmGraniteEmbeddingModel", "ibmGraniteEmbeddingTextHash",
                   "ibmGraniteEmbeddingTextVersion"}

# Names that are not schema.org terms, so must not come out as schema:. The
# first group is declared in NIAID's published schema; the second appears in the
# API's records and field list but is declared in neither vocabulary, and is
# kept under nde: because the portal is what defines it.
NDE_NAMES = {
    "analyticalMethod", "associatedGenotype", "associatedPhenotype", "cellType",
    "citedBy", "collectionType", "curatedBy", "date", "doi", "hasAPI",
    "hasDownload", "inversePredicate", "isBasisFor", "isCurated", "isSymmetric",
    "nctid", "observationType", "originalName", "relationship", "sample",
    "semanticMapping", "species", "topicCategory", "tripleObject",
    "tripleObjectQualifier", "triplePredicate", "triplePredicateQualifier",
    "tripleSubject", "tripleSubjectQualifier", "Sample",
} | {
    "activityCode", "additionalPhenotype", "administrativeType", "assayformat",
    "alternateIdentifier", "altitude", "anatomicalStructure", "anatomicalSystem",
    "applTypeCode", "class", "classification", "collectionMethod", "collector",
    "commonName", "correctionApproved", "country", "dateCollected",
    "dateProcessed", "developmentalStage", "displayName", "environmentalSystem",
    "experimentalPurpose", "field", "fromEXTRACT", "fromGPT", "fromNCT",
    "fromPMID", "fullSupportYear", "icCode", "input", "isControl", "journalName",
    "journalNameAbbrev", "lineage", "locationOfOrigin", "locationType", "output",
    "parent_taxon", "pmid", "project", "projectNumSplit", "role",
    "sampleAvailability", "sampleProcess", "sampleQuantity", "sampleState",
    "sampleStorageTemperature", "schema", "serialNum", "sex", "sourceInfo",
    "studyID", "studyTitle", "suffixCode", "supportYear", "taxon", "temporalType",
    "versionDate", "SampleCollection", "TemporalInterval", "alternateNames",
    "unlinkedHasPart", "temporalIntervals",
}

NAMING_CONTEXT = {"@vocab": "http://schema.org/", "nde": NDE_NS,
                  **{name: "nde:" + name for name in NDE_NAMES}}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def api_get(path, **params):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "cdif-niaid-harvester"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def build_date():
    """The index build the records come from, e.g. 2026-10-28."""
    return api_get("/metadata").get("build_date", "")[:10]


def list_sources():
    """(count, name) of Dataset records per source repository, largest first."""
    data = api_get("/query", q="@type:Dataset", size=0,
                   facets="includedInDataCatalog.name", facet_size=100)
    terms = data["facets"]["includedInDataCatalog.name"]["terms"]
    return [(t["count"], t["term"]) for t in terms]


def build_query(query=None, sources=(), since=None):
    """Always Dataset records; the other clauses narrow that."""
    clauses = ["@type:Dataset"]
    if query:
        clauses.append(f"({query})")
    if sources:
        names = " OR ".join('"%s"' % s.replace('"', '\\"') for s in sources)
        clauses.append(f"includedInDataCatalog.name:({names})")
    if since:
        clauses.append(f"date:[{since} TO *]")
    return " AND ".join(clauses)


def harvest(query, count, pause=0.5):
    """Yield up to `count` raw records (count=None for all), by scroll."""
    data = api_get("/query", q=query, fetch_all="true")
    seen = 0
    while True:
        hits = data.get("hits") or []
        if not hits:
            return
        for hit in hits:
            yield hit
            seen += 1
            if count is not None and seen >= count:
                return
        scroll_id = data.get("_scroll_id")
        if not scroll_id:
            return
        time.sleep(pause)
        data = api_get("/query", scroll_id=scroll_id)


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def _as_list(value):
    return value if isinstance(value, list) else [] if value is None else [value]


def _bare_doi(value):
    """The DOI in a DOI, doi: or doi.org string, else None."""
    match = re.search(r"\b(10\.\d{4,9}/\S+)", str(value))
    return match.group(1).rstrip(".,;") if match else None


def _types(node):
    return _as_list(node.get("@type")) if isinstance(node, dict) else []


def _clean(obj):
    """Drop internal fields and empty strings; repair the source's `sameas`
    spelling and scheme-less URLs (curatedBy has url "www.immport.org"); remove
    leftover CDATA markers from text (dbGaP descriptions hold stray "]]>", which
    also breaks any tool that handles the JSON as XML)."""
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, str):
        return obj.replace("<![CDATA[", "").replace("]]>", "")
    if not isinstance(obj, dict):
        return obj
    out = {}
    for key, value in obj.items():
        if key in INTERNAL_FIELDS or value == "":
            continue
        if key == "url" and isinstance(value, str) and value.startswith("www."):
            value = "https://" + value
        out["sameAs" if key == "sameas" else key] = _clean(value)
    return out


def _shape_agents(obj):
    """CDIF's agents take one alternateName (the rest go to nde:alternateNames)
    and an Organization needs a name or identifier: a url alone identifies it."""
    if isinstance(obj, list):
        for item in obj:
            _shape_agents(item)
    elif isinstance(obj, dict):
        types = _types(obj)
        if "Person" in types or "Organization" in types:
            names = obj.get("alternateName")
            if isinstance(names, list):
                if names:
                    obj["alternateName"] = names[0]
                    if names[1:]:
                        obj["alternateNames"] = names[1:]
                else:
                    del obj["alternateName"]
            if "Organization" in types:
                # Parsed affiliations can leave a name that is only punctuation.
                if isinstance(obj.get("name"), str) and not re.search(r"\w", obj["name"]):
                    del obj["name"]
                if not obj.get("name") and not obj.get("identifier") and obj.get("url"):
                    obj["identifier"] = obj["url"]
        for value in obj.values():
            _shape_agents(value)
        affiliations = obj.get("affiliation")
        if affiliations is not None:
            # An organization left with nothing naming it says nothing.
            kept = [a for a in _as_list(affiliations)
                    if not isinstance(a, dict) or [k for k in a if k != "@type"]]
            if kept:
                obj["affiliation"] = kept if isinstance(affiliations, list) else kept[0]
            else:
                del obj["affiliation"]


def _shape_funding(rec):
    """Drop grants naming no funder, identifier or name, which is what a CDIF
    grant has to say; CDIF's funder is single, so a grant with several funders
    becomes one grant per funder."""
    grants = []
    for grant in _as_list(rec.get("funding")):
        if not isinstance(grant, dict) or not any(grant.get(k) for k in ("funder", "identifier", "name")):
            continue
        funders = _as_list(grant.get("funder"))
        if len(funders) > 1:
            grants += [{**grant, "funder": funder} for funder in funders]
        else:
            if funders:
                grant["funder"] = funders[0]
            grants.append(grant)
    if grants:
        rec["funding"] = grants
    else:
        rec.pop("funding", None)


def _link_url(work):
    """A URL for a CreativeWork from its own DOI, PMID or URL-shaped identifier."""
    doi = _bare_doi(work.get("doi") or "")
    if doi:
        return f"https://doi.org/{doi}"
    if work.get("pmid"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{work['pmid']}/"
    for value in _as_list(work.get("identifier")):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def _shape_links(rec):
    """CDIF reads a schema:CreativeWork as a labelled link, which needs a url;
    the portal also uses the type for things with no address. By meaning:
    url-less usageInfo is an access statement, a citation becomes its text, a
    part moves to nde:unlinkedHasPart, and anything else is retyped schema:Thing,
    so the data stays and only the claim to be a link goes."""
    conditions = _as_list(rec.get("conditionsOfAccess"))
    kept = []
    for info in _as_list(rec.get("usageInfo")):
        if isinstance(info, dict) and not info.get("url"):
            text = ": ".join(str(v) for k in ("name", "description")
                             for v in _as_list(info.get(k)) if v)
            if text and text not in conditions:
                conditions.append(text)
        else:
            kept.append(info)
    if conditions:
        rec["conditionsOfAccess"] = conditions
    if kept:
        rec["usageInfo"] = kept
    else:
        rec.pop("usageInfo", None)

    citations = []
    for cite in _as_list(rec.get("citation")):
        if isinstance(cite, dict) and "CreativeWork" in _types(cite) and not cite.get("url"):
            url = _link_url(cite)
            if url:
                cite["url"] = url
            else:
                cite = cite.get("citation") or cite.get("name") or cite
        citations.append(cite)
    if citations:
        rec["citation"] = citations

    # A part with no address cannot be a CDIF manifest part.
    parts = _as_list(rec.get("hasPart"))
    unlinked = [p for p in parts if isinstance(p, dict) and "CreativeWork" in _types(p)
                and not p.get("url") and not _link_url(p)]
    if unlinked:
        rec["unlinkedHasPart"] = unlinked
        linked = [p for p in parts if not any(p is u for u in unlinked)]
        if linked:
            rec["hasPart"] = linked
        else:
            del rec["hasPart"]
    _resolve_works(rec)


def _resolve_works(obj):
    """Everywhere else: give a url-less CreativeWork the URL its DOI, PMID or
    identifier provides; failing that it is a named thing, not a link."""
    if isinstance(obj, list):
        for item in obj:
            _resolve_works(item)
    elif isinstance(obj, dict):
        if "CreativeWork" in _types(obj) and not obj.get("url"):
            url = _link_url(obj)
            if url:
                obj["url"] = url
            else:
                obj["@type"] = ["Thing" if t == "CreativeWork" else t for t in _types(obj)]
        for value in obj.values():
            _resolve_works(value)


def _shape_temporal(rec):
    """schema:temporalCoverage becomes ISO 8601 interval strings, written as DCAT's
    converter writes them (start/end, start/.., ../end). The portal's interval also
    has a temporalType and name, and its duration is usually not an interval
    ("24107 days, 0:00:00"), so an interval holding more than its dates is kept
    whole as nde:temporalIntervals."""
    strings, detailed = [], []
    for span in _as_list(rec.get("temporalCoverage")):
        if not isinstance(span, dict):
            strings.append(span)
            continue
        start, end, duration = span.get("startDate"), span.get("endDate"), span.get("duration")
        is_interval = isinstance(duration, str) and re.fullmatch(r"\d{4}[-\d]*/\d{4}[-\d]*", duration)
        if start or end:
            strings.append(f"{start or '..'}/{end or '..'}")
        elif is_interval:
            strings.append(duration)
        if span.get("temporalType") or span.get("name") or (duration and not is_interval) \
                or not (start or end or is_interval):
            detailed.append(span)
    rec.pop("temporalCoverage", None)
    if strings:
        rec["temporalCoverage"] = strings
    if detailed:
        rec["temporalIntervals"] = detailed


def _shape_coverage_and_variables(rec):
    """Place and PropertyValue are what CDIF's schemas look for; the portal's
    more specific type (AdministrativeArea, DefinedTerm) is kept beside them."""
    places = []
    for place in _as_list(rec.get("spatialCoverage")):
        if isinstance(place, dict) and "Place" not in _types(place):
            place["@type"] = ["Place"] + _types(place)
        places.append(place)
    if places:
        rec["spatialCoverage"] = places

    variables = []
    for var in _as_list(rec.get("variableMeasured")):
        if isinstance(var, str):
            var = {"name": var}
        if isinstance(var, dict) and "PropertyValue" not in _types(var):
            var["@type"] = ["PropertyValue"] + _types(var)
        variables.append(var)
    if variables:
        rec["variableMeasured"] = variables


def _shape_distribution(rec):
    """A file the portal names without an address gets the OGC nil value, so the
    file stays described and the missing URL is said rather than implied. A
    distribution's hasPart as bare URLs becomes CDIF's archive-part objects."""
    for dist in _as_list(rec.get("distribution")):
        if not isinstance(dist, dict):
            continue
        if not dist.get("contentUrl") and not dist.get("url"):
            dist["contentUrl"] = OGC_NIL_MISSING
        parts = dist.get("hasPart")
        if parts is not None:
            dist["hasPart"] = [
                {"@type": ["MediaObject"], "url": part} if isinstance(part, str) else part
                for part in _as_list(parts)]


def _shape(rec):
    """Fit a cleaned record to the shapes CDIF's schemas expect, using only what
    the record holds. Runs on bare names, before convert_soso_to_cdif."""
    _shape_identifiers(rec)
    _shape_agents(rec)
    _shape_funding(rec)
    _shape_links(rec)
    _shape_coverage_and_variables(rec)
    _shape_temporal(rec)
    _shape_distribution(rec)

    language = rec.get("inLanguage")
    if isinstance(language, list):
        language = language[0] if language else None
    if isinstance(language, dict):
        language = language.get("name")
    if language:
        rec["inLanguage"] = language
    else:
        rec.pop("inLanguage", None)

    if not any(rec.get(k) for k in ("dateModified", "datePublished", "dateCreated")) \
            and rec.get("date"):
        rec["dateModified"] = rec["date"]
    if not rec.get("license") and not rec.get("conditionsOfAccess"):
        rec["license"] = OGC_NIL_MISSING
    return rec


def _shape_identifiers(rec):
    """A DOI becomes the CDIF identifier, and any further identifiers are kept
    as nde:alternateIdentifier rather than dropped by the single-value rule."""
    values = _as_list(rec.get("doi")) + _as_list(rec.get("identifier"))
    doi = next((d for d in map(_bare_doi, values) if d), None)
    primary = None
    if doi:
        primary = {"@type": ["PropertyValue"], "propertyID": {"@id": DOI_REGISTRY},
                   "value": doi, "url": f"https://doi.org/{doi}"}
    elif values:
        primary = values[0]
    extras = []
    for value in values:
        if isinstance(value, str) and value != primary and _bare_doi(value) != doi \
                and value not in extras:
            extras.append(value)
    rec.pop("doi", None)
    if primary is not None:
        rec["identifier"] = primary
    if extras:
        rec["alternateIdentifier"] = extras + _as_list(rec.get("alternateIdentifier"))


def convert_record(hit, build=""):
    """Convert one API hit to a CDIF record. Returns (cdif, changes)."""
    rec = _shape(_clean(hit))
    rec["@context"] = NAMING_CONTEXT

    doc, changes = convert_soso_to_cdif(rec, source_label="NIAID Data Discovery Portal",
                                        detect=False)

    record = doc["schema:subjectOf"]
    portal_id = hit.get("_id", "")
    record["schema:description"] = (
        "CDIF metadata harvested from the NIAID Data Discovery Portal API"
        + (f" (index build {build})" if build else "")
        + f", record {portal_id}, and converted by harvest_niaid_to_cdif.py. The "
          "portal aggregates this record from the repositories named in "
          "schema:includedInDataCatalog; nde: properties, including the portal's "
          "term annotations, are as the portal publishes them.")
    record["schema:includedInDataCatalog"] = {
        "@type": ["schema:DataCatalog"], "schema:name": "NIAID Data Discovery Portal",
        "schema:url": PORTAL}
    record["schema:url"] = f"{PORTAL}resources?id={urllib.parse.quote(portal_id)}"
    if build:
        record["schema:sdDatePublished"] = build

    if _HAVE_DETECT:
        try:
            uris = detect_cdif_conformance(doc)
            if uris:
                apply_conformance(doc, uris)
        except Exception as exc:
            changes.append(f"detect_cdif_conformance failed ({exc}); kept the default conformsTo")
    return doc, changes


def _safe_name(portal_id):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", portal_id)[:150] + ".json"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Harvest NIAID Data Discovery Portal Dataset records to CDIF JSON-LD")
    ap.add_argument("--list-sources", action="store_true",
                    help="List source repositories with their Dataset counts, and exit")
    ap.add_argument("-q", "--query", help="Lucene query, ANDed with @type:Dataset")
    ap.add_argument("-s", "--source", action="append", default=[],
                    help="Source repository name (repeatable); see --list-sources")
    ap.add_argument("--since", help="Only records whose `date` is on or after YYYY-MM-DD")
    ap.add_argument("-n", "--count", type=int, default=10,
                    help="Records to harvest (default 10; 0 for all that match)")
    ap.add_argument("-o", "--output", default=".", help="Output directory")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.list_sources:
        for count, name in list_sources():
            print(f"{count:>9}  {name}")
        return

    query = build_query(args.query, args.source, args.since)
    total = api_get("/query", q=query, size=0).get("total", 0)
    print(f"{total} records match: {query}", file=sys.stderr)
    if not args.count and total > 100000:
        print("Refusing to harvest more than 100,000 records with -n 0; narrow the "
              "query or set -n.", file=sys.stderr)
        sys.exit(2)

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    build = build_date()
    done = failed = 0
    for hit in harvest(query, args.count or None):
        try:
            doc, changes = convert_record(hit, build)
        except Exception as exc:
            failed += 1
            print(f"FAILED {hit.get('_id')}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        (out / _safe_name(hit["_id"])).write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        done += 1
        if args.verbose:
            print(f"  {hit['_id']}: {doc.get('schema:name', '')[:70]}", file=sys.stderr)
            for change in changes:
                if change.startswith("WARNING"):
                    print(f"    {change}", file=sys.stderr)
    print(f"Written: {done} records to {out}" + (f"; {failed} failed" if failed else ""),
          file=sys.stderr)


if __name__ == "__main__":
    main()

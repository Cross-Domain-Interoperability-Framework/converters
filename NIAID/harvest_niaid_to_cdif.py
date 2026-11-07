#!/usr/bin/env python3
"""harvest_niaid_to_cdif.py - Harvest Dataset records from the NIAID Data Discovery
Portal API and convert them to CDIF JSON-LD.

The portal (https://data.niaid.nih.gov/) aggregates about 14.9 million records
from some 55 repositories, of which about 5.7 million are @type Dataset. Its API
(https://api.data.niaid.nih.gov/v1, OpenAPI at /v1/spec) is a BioThings service:
Lucene-style queries against /query, with fetch_all/scroll_id paging for result
sets beyond its 10,000-record window.

Records are bare schema.org names with extensions from NIAID's own schema
(https://github.com/NIAID-Data-Ecosystem/nde-schemas). The conversion is driven by
mappings/niaid-to-cdif.sssom.tsv, read at run time, so a mapping change is a table
edit: each node's rows are applied in table order, a row's `transform` names the
shaper to use (an empty one is a plain copy), and the first row to fill a scalar
target wins. Code holds only the shapers, the API, and:

  1. clean: drop the index's internal fields (_score, _ignored, _meta,
     metadata_score, the ibmGranite* embedding fields) and repair spelling that
     applies to every value;
  2. the catalog record: where this record came from (the portal, with the index's
     build date), then conformsTo detected from content, plus the cdifNIAID profile.

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
import mimetypes
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from schemaorg_names import TYPE_MAP  # noqa: E402
from sssom_engine import load_table  # noqa: E402

sys.path.insert(0, str(ROOT / "validation"))
try:
    from detect_conformance import detect_cdif_conformance, apply_conformance
    _HAVE_DETECT = True
except Exception:
    _HAVE_DETECT = False

API = "https://api.data.niaid.nih.gov/v1"
PORTAL = "https://data.niaid.nih.gov/"
NDE_NS = "https://discovery.biothings.io/ns/nde/"
DOI_REGISTRY = "https://registry.identifiers.org/registry/doi"
BIOPROJECT_REGISTRY = "https://registry.identifiers.org/registry/bioproject"
BIOPROJECT_RE = re.compile(r"PRJ[DEN][A-Z]\d+$")
OGC_NIL_MISSING = "http://www.opengis.net/def/nil/OGC/0/missing"

# The index's own bookkeeping, not metadata about the dataset.
INTERNAL_FIELDS = {"_id", "_score", "_ignored", "_meta", "_version", "@version",
                   "metadata_score", "ibmGraniteEmbedding",
                   "ibmGraniteEmbeddingModel", "ibmGraniteEmbeddingTextHash",
                   "ibmGraniteEmbeddingTextVersion"}

TABLE = ROOT / "mappings" / "niaid-to-cdif.sssom.tsv"
CORE_URI = "https://w3id.org/cdif/core/1.1"
DISCOVERY_URI = "https://w3id.org/cdif/discovery/1.1"
NIAID_URI = "https://w3id.org/cdif/niaid/1.0"
EXTENSION_PREFIX = "cdifniaid"
CDIF_CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
    "nde": NDE_NS,
    "bios": "https://bioschemas.org/",
    EXTENSION_PREFIX: "https://w3id.org/cdif/niaid/terms/",
}

ROOT_CLASS = "nde:Dataset"
# The class an untyped node's rows are filed under, from the property holding it.
UNTYPED_CLASS = {
    "funding": "schema:MonetaryGrant", "distribution": "schema:DataDownload",
    "includedInDataCatalog": "schema:DataCatalog", "measurementTechnique": "nde:DefinedTerm",
    "variableMeasured": "nde:DefinedTerm", "associatedPhenotype": "nde:DefinedTerm",
    "additionalType": "nde:DefinedTerm", "hasPart": "schema:CreativeWork",
    "isBasedOn": "schema:CreativeWork", "isPartOf": "schema:CreativeWork",
    "citation": "schema:CreativeWork", "citedBy": "schema:CreativeWork",
    "itemListElement": "nde:Sample", "aggregateElement": "nde:Sample",
    "author": "schema:Person", "curatedBy": "schema:Organization",
    "sdPublisher": "schema:Organization", "inLanguage": "schema:Language",
    "projectNumSplit": "ndeapi:projectNumSplit", "sameas": "ndeapi:sameas",
    "spatialCoverage": "schema:AdministrativeArea",
}
NDE_CLASSES = {"Sample", "SampleCollection", "DefinedTerm", "TemporalInterval"}
# @type values that are not schema.org types. DefinedTerm is, so it is not listed.
TYPE_NAMES = {
    "Sample": ["nde:Sample", "bios:Sample"],
    "SampleCollection": ["nde:SampleCollection"],
    "TemporalInterval": ["nde:TemporalInterval"],
}


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


def _says_nothing(value):
    """A node left with no property but its id and type, e.g. a term with no name."""
    return isinstance(value, dict) and not [k for k in value if k not in ("@id", "@type")]


def _clean(obj):
    """Drop internal fields and empty strings; repair scheme-less URLs (curatedBy
    has url "www.immport.org") and keys with stray spaces (one record has
    "sdPublisher "); remove leftover CDATA markers from text (dbGaP descriptions
    hold stray "]]>", which also breaks any tool that handles the JSON as XML)."""
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
        out[key.strip()] = _clean(value)
    return out


def class_of(node, parent):
    """The subject_class of a node's rows: its first @type, or for an untyped node
    what the property holding it implies."""
    types = _types(node)
    if not types:
        return UNTYPED_CLASS.get(parent, "ndeapi:" + str(parent))
    if types[0] in TYPE_MAP:
        return TYPE_MAP[types[0]]
    return ("nde:" if types[0] in NDE_CLASSES else "schema:") + str(types[0])


def _type_names(types):
    names = []
    for t in _as_list(types):
        if t in TYPE_NAMES:
            names += TYPE_NAMES[t]
        elif t in TYPE_MAP:
            names.append(TYPE_MAP[t])
        else:
            names.append(t if ":" in str(t) else f"schema:{t}")
    return names


def convert_node(node, cls, notes):
    """Apply the rows for `cls` to one node, in table order, then carry any name the
    class has no row for under the target the table gives that name elsewhere."""
    out = {"@id": node["@id"]} if "@id" in node else {}
    if "@type" in node:
        out["@type"] = _type_names(node["@type"])
    claimed = set()
    for rule in RULES.get(cls, ()):
        label = rule["subject_label"]
        claimed.add(label)
        value = node.get(label)
        if not rule["object_id"] or (value is None and rule["transform"] not in RUNS_WITHOUT_VALUE):
            continue
        _place(out, rule, SHAPERS[rule["transform"]](value, node, rule, notes))
    for key, value in node.items():
        if key in claimed or key.startswith("@"):
            continue
        rule = BY_NAME.get(key)
        if rule:
            _place(out, rule, SHAPERS[rule["transform"]](value, node, rule, notes))
        elif key not in DROPPED:
            target = key if ":" in key else f"{EXTENSION_PREFIX}:{key}"
            notes.append(f"{key} is not in the mapping table; kept as {target}")
            out.setdefault(target, _convert_value(value, key, notes))
    return out


DIST_PATH = "$.schema:distribution[0]."
CONCAT_TRANSFORMS = {"concat_text", "metadata_source"}
_DEFERRED = []  # (rule, value) for a row whose path leaves the node it was converted on


def _place_deferred(doc):
    """Rows on a nested class with a $. path write to the dataset; a $.schema:distribution[0].
    path writes to the record's only distribution, and a bundle of archive parts makes
    that distribution a schema:Collection."""
    deferred, _DEFERRED[:] = list(_DEFERRED), []
    for rule, value in deferred:
        path = rule["object_json_path"]
        if not path.startswith(DIST_PATH):
            _place(doc, rule, value, root=True)
            continue
        dists = _as_list(doc.get("schema:distribution"))
        if len(dists) != 1 or not isinstance(dists[0], dict):
            continue  # the shaper only gives a value when there is exactly one
        dist = dists[0]
        _place(dist, dict(rule, object_json_path="@." + path[len(DIST_PATH):]), value, root=True)
        if rule["object_id"] == "schema:hasPart":
            dist["@type"] = _as_list(dist.get("@type")) + (
                [] if "schema:Collection" in _as_list(dist.get("@type")) else ["schema:Collection"])


def _place(out, rule, value, root=False):
    """Row order is precedence: a scalar target keeps the first value, and an array
    target gathers every row's values. A concat_text row joins its text onto what an
    earlier row put there."""
    if value is None or value == "" or value == []:
        return
    path = rule["object_json_path"]
    if not root and path.startswith("$.") and (
            rule["subject_class"] != ROOT_CLASS or path.startswith(DIST_PATH)):
        _DEFERRED.append((rule, value))  # placed by _place_deferred once the root exists
        return
    if path.startswith("$.schema:subjectOf."):
        out = out.setdefault("schema:subjectOf", {})
    target = rule["object_id"]
    if target not in out or target == "@type":
        out[target] = value
    elif rule["transform"] in CONCAT_TRANSFORMS and isinstance(out[target], str) \
            and isinstance(value, str):
        out[target] = _join_text(out[target].split("\n\n") + [value])
    elif isinstance(out[target], list) and isinstance(value, list):
        out[target] += [v for v in value if v not in out[target]]


def _convert_value(value, key, notes):
    if isinstance(value, list):
        return [_convert_value(v, key, notes) for v in value]
    if isinstance(value, dict):
        return convert_node(value, class_of(value, key), notes)
    return value


def _copy(value, node, rule, notes):
    return _convert_value(value, rule["subject_label"], notes)


def _array(value, node, rule, notes):
    return _as_list(_copy(value, node, rule, notes))


def _join_text(parts):
    """Distinct non-empty strings, in order, separated by a blank line."""
    kept = []
    for part in parts:
        part = part.strip() if isinstance(part, str) else ""
        if part and part not in kept:
            kept.append(part)
    return "\n\n".join(kept)


def _concat_text(value, node, rule, notes):
    """Every text value joined, rather than all but the first dropped."""
    return _join_text(_as_list(value)) or None


def _first_value(value, node, rule, notes):
    return value[0] if isinstance(value, list) and value else value


def _portal_uri(value, node, rule, notes):
    return _portal_url(value)


def _portal_ref(value, node, rule, notes):
    return {"@id": _portal_url(value)}


def _portal_url(portal_id):
    return f"{PORTAL}resources?id={urllib.parse.quote(portal_id)}"


def _source_sameas(value, node, rule, notes):
    return _as_list(value)


def _identifier_values(node):
    return _as_list(node.get("doi")) + _as_list(node.get("identifier"))


def _first_doi(node):
    return next((d for d in map(_bare_doi, _identifier_values(node)) if d), None)


def _bioproject(value):
    match = BIOPROJECT_RE.match(value.strip()) if isinstance(value, str) else None
    return match.group(0) if match else None


def _primary(node):
    """The value the CDIF identifier is made from, as (kind, value): the first DOI, else
    the first BioProject accession, else the first identifier value, else None (the _id
    row then gives the @id)."""
    doi = _first_doi(node)
    if doi:
        return "doi", doi
    acc = next((a for a in map(_bioproject, _identifier_values(node)) if a), None)
    if acc:
        return "bioproject", acc
    first = next((v for v in _as_list(node.get("identifier")) if isinstance(v, str) and v.strip()), None)
    return ("identifier", first) if first else None


def _is_primary(item, primary):
    if not primary:
        return False
    kind, value = primary
    if kind == "identifier":
        return item == value
    return (_bare_doi(item) if kind == "doi" else _bioproject(item)) == value


def _id_as_identifier(value, node, rule, notes):
    return None if _primary(node) else _portal_url(value)


def _primary_identifier(value, node, rule, notes):
    """A DOI, else a BioProject accession, as a PropertyValue naming its registry; else
    the first identifier value as given."""
    primary = _primary(node)
    if not primary:
        return None
    kind, value = primary
    if kind == "identifier":
        return value
    if kind == "doi":
        return {"@type": ["schema:PropertyValue"], "schema:propertyID": {"@id": DOI_REGISTRY},
                "schema:value": value, "schema:url": f"https://doi.org/{value}"}
    return {"@type": ["schema:PropertyValue"], "schema:propertyID": {"@id": BIOPROJECT_REGISTRY},
            "schema:value": value, "schema:url": f"https://www.ncbi.nlm.nih.gov/bioproject/{value}"}


def _extra_identifiers(value, node, rule, notes):
    """Every identifier but the one used as the CDIF identifier."""
    primary = _primary(node)
    extras = []
    for item in _identifier_values(node):
        if isinstance(item, str) and not _is_primary(item, primary) and item not in extras:
            extras.append(item)
    return extras


def _agent(item, notes):
    """An agent with no @type, which CDIF requires: a Person if it has a family name or
    a comma in its name, else an Organization. A string is taken as a name."""
    if isinstance(item, str):
        item = {"name": item}
    if isinstance(item, dict) and not _types(item):
        person = item.get("familyName") or "," in str(item.get("name", ""))
        item = {"@type": "Person" if person else "Organization", **item}
    return _convert_value(item, "author", notes)


def _agents(value):
    """Agents, looking through Role wrappers to the agent they hold."""
    found = []
    for item in _as_list(value):
        if isinstance(item, dict) and any("Role" in str(t) for t in _types(item)):
            found += _agents(item.get("author") or item.get("creator"))
        else:
            found.append(item)
    return found


def _creator_list(value, node, rule, notes):
    if rule["subject_label"] == "author" and node.get("creator"):
        return None  # the creator row adds the authors
    items = _agents(value)
    if rule["subject_label"] == "creator":
        named = {_agent_name(i) for i in items}
        items += [a for a in _agents(node.get("author")) if _agent_name(a) not in named]
    agents = [_agent(item, notes) for item in items]
    return {"@list": [a for a in agents if not _says_nothing(a)]} if any(not _says_nothing(a) for a in agents) else None


def _agent_name(item):
    if isinstance(item, dict):
        return str(item.get("name") or " ".join(filter(None, (item.get("givenName"), item.get("familyName"))))).strip().lower()
    return str(item).strip().lower()


def _author_beside_creator(value, node, rule, notes):
    return _copy(value, node, rule, notes) if node.get("creator") else None


def _agent_array(value, node, rule, notes):
    return [a for a in (_agent(item, notes) for item in _as_list(value)) if not _says_nothing(a)]


def _agent_one(value, node, rule, notes):
    return _agent(value, notes) if not isinstance(value, list) else _agent_array(value, node, rule, notes)


def _first_alternate_name(value, node, rule, notes):
    return (value[0] if value else None) if isinstance(value, list) else value


def _extra_alternate_names(value, node, rule, notes):
    return value[1:] if isinstance(value, list) else None


def _has_name(node):
    """A parsed affiliation can leave a name that is only punctuation."""
    return isinstance(node.get("name"), str) and bool(re.search(r"\w", node["name"]))


def _org_name(value, node, rule, notes):
    return value if not isinstance(value, str) or _has_name(node) else None


def _org_identifier(value, node, rule, notes):
    return value if not _has_name(node) and not node.get("identifier") else None


def _person_name(value, node, rule, notes):
    if node.get("name"):
        return None
    family, given = node.get("familyName", ""), node.get("givenName", "")
    return f"{family}, {given}" if family and given else family or given


def _nonempty_affiliation(value, node, rule, notes):
    """An organization left with nothing naming it says nothing."""
    named = [{"@type": "Organization", "name": v} if isinstance(v, str) else v for v in _as_list(value)]
    kept = [a for a in map(lambda v: _convert_value(v, "affiliation", notes), named)
            if not isinstance(a, dict) or [k for k in a if k != "@type"]]
    return (kept if isinstance(value, list) else kept[0]) if kept else None


def _grant_per_funder(value, node, rule, notes):
    """Drop grants naming no funder, identifier or name, which is what a CDIF grant
    has to say; CDIF's funder is single, so several funders make one grant each."""
    grants = []
    for grant in _as_list(value):
        if not isinstance(grant, dict) or not any(grant.get(k) for k in ("funder", "identifier", "name")):
            continue
        funders = _as_list(grant.get("funder"))
        if len(funders) > 1:
            grants += [{**grant, "funder": funder} for funder in funders]
        else:
            grants.append({**grant, "funder": funders[0]} if funders else grant)
    return _convert_value(grants, "funding", notes)


def _grant_identifier(value, node, rule, notes):
    if isinstance(value, str):
        return {"@type": ["schema:PropertyValue"], "schema:value": value}
    return _copy(value, node, rule, notes)


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


def _work_url(value, node, rule, notes):
    if "CreativeWork" in _types(node) and not node.get("url"):
        return _link_url(node)
    return None


def _link_or_thing(value, node, rule, notes):
    """CDIF reads a schema:CreativeWork as a labelled link, which needs a url; the
    portal also uses the type for things with no address. The data stays and only
    the claim to be a link goes."""
    if "CreativeWork" in _types(node) and not node.get("url") and not _link_url(node):
        return _type_names(["Thing" if t == "CreativeWork" else t for t in _types(node)])
    return _type_names(value)


def _access_statements(node):
    """The name and description of url-less usageInfo, which say how to get access."""
    statements = []
    for info in _as_list(node.get("usageInfo")):
        if isinstance(info, dict) and not info.get("url"):
            text = ": ".join(str(v) for k in ("name", "description")
                             for v in _as_list(info.get(k)) if v)
            if text:
                statements.append(text)
    return statements


def _usage_with_url(value, node, rule, notes):
    return _convert_value([info for info in _as_list(value)
                           if not isinstance(info, dict) or info.get("url")], "usageInfo", notes)


def _usage_to_access(value, node, rule, notes):
    return _access_statements(node)


def _license_or_nil(value, node, rule, notes):
    if value is not None:
        return _array(value, node, rule, notes)
    return None if node.get("conditionsOfAccess") or _access_statements(node) else [OGC_NIL_MISSING]


def _citation_link(value, node, rule, notes):
    """A citation with no address of any kind becomes its text."""
    citations = []
    for cite in _as_list(value):
        if isinstance(cite, dict) and "CreativeWork" in _types(cite) and not cite.get("url") \
                and not _link_url(cite) and (cite.get("citation") or cite.get("name")):
            citations.append(cite.get("citation") or cite.get("name"))
        else:
            citations.append(_convert_value(cite, "citation", notes))
    return citations


def _unaddressed(part):
    return isinstance(part, dict) and "CreativeWork" in _types(part) \
        and not part.get("url") and not _link_url(part)


def _typed_part(part, default):
    if isinstance(part, dict) and not _types(part):
        part = {"@type": default, **part}
    return part


def _linked_parts(value, node, rule, notes):
    """Parts with their own address: the manifest's dataset-level parts, which must be
    typed, so an untyped one is taken as a CreativeWork."""
    parts = [_typed_part(p, "CreativeWork") for p in _as_list(value) if not _unaddressed(p)]
    return _convert_value(parts, "hasPart", notes)


def _single_distribution(node):
    return len(_as_list(node.get("distribution"))) == 1


def _unlinked_parts(value, node, rule, notes):
    """Parts with no address, when there is no single distribution they could belong to."""
    if _single_distribution(node):
        return None
    return _convert_value([p for p in _as_list(value) if _unaddressed(p)], "hasPart", notes)


def _bundle_parts(value, node, rule, notes):
    """Parts with no address, when the record has exactly one distribution: files inside
    that bundle, as the manifest's archive parts (a MediaObject with a name and format;
    a format not given is guessed from the file name, else application/octet-stream)."""
    if not _single_distribution(node):
        return None
    parts = []
    for part in _as_list(value):
        if not (_unaddressed(part) and isinstance(part, dict) and part.get("name")):
            continue
        out = _convert_value(part, "hasPart", notes)
        out["@type"] = ["schema:MediaObject"]
        if not out.get("schema:encodingFormat"):
            out["schema:encodingFormat"] = [
                mimetypes.guess_type(str(part["name"]))[0] or "application/octet-stream"]
        parts.append(out)
    return parts


def _span_dates(span):
    start, end, duration = span.get("startDate"), span.get("endDate"), span.get("duration")
    is_interval = isinstance(duration, str) and re.fullmatch(r"\d{4}[-\d]*/\d{4}[-\d]*", duration)
    return start, end, duration, is_interval


def _iso_interval(value, node, rule, notes):
    """schema:temporalCoverage as ISO 8601 interval strings, written as DCAT's converter
    writes them (start/end, start/.., ../end)."""
    strings = []
    for span in _as_list(value):
        if not isinstance(span, dict):
            strings.append(span)
            continue
        start, end, duration, is_interval = _span_dates(span)
        if start or end:
            strings.append(f"{start or '..'}/{end or '..'}")
        elif is_interval:
            strings.append(duration)
    return strings


def _interval_detail(value, node, rule, notes):
    """The portal's interval also has a temporalType and name, and its duration is
    usually not an interval ("24107 days, 0:00:00"), so an interval holding more than
    its dates is kept whole."""
    detailed = []
    for span in _as_list(value):
        if isinstance(span, dict):
            start, end, duration, is_interval = _span_dates(span)
            if span.get("temporalType") or span.get("name") or (duration and not is_interval) \
                    or not (start or end or is_interval):
                detailed.append(span)
    return _convert_value(detailed, "temporalCoverage", notes)


def _place_shaper(value, node, rule, notes):
    """schema:Place is what CDIF's schemas look for; the portal's more specific type
    (AdministrativeArea) is kept beside it."""
    places = []
    for item in _as_list(value):
        if isinstance(item, str):
            item = {"@type": "Place", "name": item}
        place = _convert_value(item, "spatialCoverage", notes)
        if isinstance(place, dict):
            types = place.get("@type", [])
            place["@type"] = types if "schema:Place" in types else ["schema:Place"] + types
            if isinstance(place.get("schema:geo"), list) and place["schema:geo"]:
                place["schema:geo"] = place["schema:geo"][0]
            if [k for k in place if k != "@type"]:
                places.append(place)
    return _merge_places(places)


def _place_key(place):
    """The identifier a place is known by: its own, or its name term's."""
    name = place.get("schema:name")
    ident = place.get("schema:identifier") or (name.get("schema:identifier")
                                               if isinstance(name, dict) else None)
    return json.dumps(ident, sort_keys=True) if ident else None


def _merge_places(places):
    """Places with the same identifier are one place (Project Tycho lists each country
    twice: once by name, once by ISO code with the country name). The one named from a
    country (its name term has a termCode) is kept, in the first one's position; any
    property only the other has is added to it."""
    merged, by_key = [], {}
    for place in places:
        key = _place_key(place)
        if key is None or key not in by_key:
            if key is not None:
                by_key[key] = len(merged)
            merged.append(place)
            continue
        i = by_key[key]
        kept, other = merged[i], place
        if isinstance(place.get("schema:name"), dict) and place["schema:name"].get("schema:termCode"):
            kept, other = place, merged[i]
        for k, v in other.items():
            kept.setdefault(k, v)
        merged[i] = kept
    return merged


def _property_value(value, node, rule, notes):
    """schema:PropertyValue is what CDIF's schemas look for; the portal's more specific
    type (DefinedTerm) is kept beside it."""
    variables = []
    for var in _as_list(value):
        if isinstance(var, str):
            var = {"name": var}
        out = _convert_value(var, "variableMeasured", notes)
        if isinstance(out, dict):
            types = out.get("@type", [])
            out["@type"] = types if "schema:PropertyValue" in types else ["schema:PropertyValue"] + types
            for key in ("schema:propertyID", "schema:alternateName"):
                if key in out and not isinstance(out[key], list):
                    out[key] = [out[key]]
        variables.append(out)
    return variables


def _term_array(value, node, rule, notes):
    terms = []
    for term in _as_list(value):
        out = _convert_value(term, rule["subject_label"], notes)
        if _says_nothing(out):
            continue
        if isinstance(out, dict) and "@type" not in out:
            out["@type"] = ["schema:DefinedTerm"]
        terms.append(out)
    return terms


def _distribution_array(value, node, rule, notes):
    dists = []
    for dist in _as_list(value):
        out = _convert_value(dist, "distribution", notes)
        if isinstance(out, dict) and "@type" not in out:
            out["@type"] = ["schema:DataDownload"]
        dists.append(out)
    return dists


def _content_url_or_nil(value, node, rule, notes):
    """A file the portal names without an address gets the OGC nil value, so the file
    stays described and the missing URL is said rather than implied."""
    if value:
        return value
    return None if node.get("url") else OGC_NIL_MISSING


def _url_as_content(value, node, rule, notes):
    return None if node.get("contentUrl") else value


def _archive_part(value, node, rule, notes):
    """Parts given as objects. A CDIF archive part needs a name and a format, which a
    bare URL does not give, so those go to part_urls."""
    return [_convert_value(part, "hasPart", notes) for part in _as_list(value) if not isinstance(part, str)]


def _part_urls(value, node, rule, notes):
    return [part for part in _as_list(value) if isinstance(part, str)]


def _link_role(value, node, rule, notes):
    """Each linked value as a schema:relatedLink LinkRole, as dcat_to_cdif writes them.
    linkRelationship is the item's own relationship where the portal gives one, else
    the source property (the row's subject_id). An object's name and description go on
    the LinkRole; an object with no address is left to the row after this one. Some
    sources pack several URLs into one string, separated by commas or semicolons."""
    links, seen = [], set()
    for item in _as_list(value):
        if isinstance(item, dict):
            urls = [item.get("url") or _link_url(item)]
        elif isinstance(item, str):
            urls = re.split(r"[,;]\s*(?=[A-Za-z][A-Za-z0-9+.-]*://)", item)
        else:
            continue
        for url in urls:
            url = url.strip() if isinstance(url, str) else ""
            if not url or url in seen:
                continue
            seen.add(url)
            link = {"@type": ["schema:LinkRole"],
                    "schema:linkRelationship": (item.get("relationship") if isinstance(item, dict)
                                                else None) or rule["subject_id"],
                    "schema:target": {"@type": ["schema:EntryPoint"], "schema:url": url}}
            if isinstance(item, dict):
                for key in ("name", "description"):
                    if isinstance(item.get(key), str):
                        link[f"schema:{key}"] = item[key]
            links.append(link)
    return links


def _unlinked_related(value, node, rule, notes):
    """The related resources link_role could not use: no address of any kind."""
    return _convert_value([item for item in _as_list(value) if isinstance(item, dict)
                           and not (item.get("url") or _link_url(item))], rule["subject_label"], notes)


def _derived_from(value, node, rule, notes):
    """prov:wasDerivedFrom takes a string, an @id or a labelled link: a source with an
    address becomes a CreativeWork link (name, description, url), one without its name."""
    out = []
    for item in _as_list(value):
        if isinstance(item, str) and item.strip():
            out.append(item)
            continue
        if not isinstance(item, dict):
            continue
        url = item.get("url") or _link_url(item)
        if url:
            link = {"@type": ["schema:CreativeWork"], "schema:url": url}
            for key in ("name", "description"):
                if isinstance(item.get(key), str):
                    link[f"schema:{key}"] = item[key]
            out.append(link)
        elif isinstance(item.get("name") or item.get("identifier"), str):
            out.append(item.get("name") or item.get("identifier"))
    return out


KEYWORD_ABOUT = {"topicCategory": "topicCategory", "classification": "NIAID classification"}


def _keyword_about(value, node, rule, notes):
    """Keywords as DefinedTerms whose schema:about says which NIAID property they came
    from (KEYWORD_ABOUT), so they stay distinguishable from the record's own keywords."""
    terms = []
    for item in _as_list(value):
        if isinstance(item, dict):
            term = _convert_value(item, rule["subject_label"], notes)
        elif isinstance(item, str) and item.strip():
            term = {"schema:name": item}
        else:
            continue
        if not isinstance(term, dict) or _says_nothing(term):
            continue
        if not term.get("schema:name"):  # a DefinedTerm needs a name; a bare term URL is an IRI
            if term.get("schema:url"):
                terms.append({"@id": term["schema:url"]})
            continue
        term["@type"] = ["schema:DefinedTerm"]
        term["schema:about"] = KEYWORD_ABOUT.get(rule["subject_label"], rule["subject_label"])
        terms.append(term)
    return terms


def _metadata_source(value, node, rule, notes):
    """A portal provenance flag (fromGPT, fromPMID, ...) as a sentence for the catalog
    record's description; repeats are dropped when the sentences are joined."""
    label = rule["subject_label"][len("from"):]
    return f"Metadata source from {label}." if value is True and label else None


PUBMED_REGISTRY = "https://registry.identifiers.org/registry/pubmed"


def _work_identifier(value, node, rule, notes):
    """A work's doi, pmid and identifier values as its schema:identifier: a DOI or PMID
    as a PropertyValue naming its registry, anything else as given."""
    ids = []
    for item in _as_list(value):
        if not isinstance(item, (str, int)) or isinstance(item, bool):
            continue
        item = str(item).strip()
        doi = _bare_doi(item)
        if doi:
            ids.append({"@type": ["schema:PropertyValue"], "schema:propertyID": {"@id": DOI_REGISTRY},
                        "schema:value": doi, "schema:url": f"https://doi.org/{doi}"})
        elif rule["subject_label"] == "pmid" and item.isdigit():
            ids.append({"@type": ["schema:PropertyValue"], "schema:propertyID": {"@id": PUBMED_REGISTRY},
                        "schema:value": item, "schema:url": f"https://pubmed.ncbi.nlm.nih.gov/{item}/"})
        elif item:
            ids.append(item)
    return ids


def _place_identifier(value, node, rule, notes):
    """An administrative area's identifier, when it has no name for place_name_term to
    carry it in."""
    return None if node.get("name") else _first_value(value, node, rule, notes)


def _work_url_or_given(value, node, rule, notes):
    """The work's url, else one made from its DOI, PMID or identifier."""
    return value if isinstance(value, str) and value else _link_url(node)


def _place_name_term(value, node, rule, notes):
    """An administrative area's name as a DefinedTerm, as the spatialExtent block allows
    for a gazetteer name: with the area's identifier, and its locationType as about
    (except the uninformative "other"). Where the portal gives a country (Project Tycho,
    whose name is then the ISO 3166 code) the country is the name and the code its
    termCode. The identifier, locationType and country rows are consumed here. A name
    with nothing more to say stays a string."""
    if not isinstance(value, str) or not value.strip():
        return None
    country = node.get("country") if isinstance(node.get("country"), str) else None
    about = next((t for t in _as_list(node.get("locationType")) if isinstance(t, str)
                  and t.strip() and t != "other"), None)
    if not node.get("identifier") and not about and not country:
        return value
    term = {"@type": ["schema:DefinedTerm"], "schema:name": country or value}
    if country and country != value:
        term["schema:termCode"] = value
    if node.get("identifier"):
        term["schema:identifier"] = _first_value(node["identifier"], node, rule, notes)
    if about:
        term["schema:about"] = about
    return term


def _place_code_alias(value, node, rule, notes):
    """With a country given, the area's own name (an ISO 3166 code) as an alternateName."""
    name = node.get("name")
    return name if isinstance(value, str) and isinstance(name, str) and name != value else None


def _part_downloads(value, node, rule, notes):
    """File URLs listed on a distribution: each file can be fetched on its own, so each is
    a dataset-level part (row path $.schema:hasPart), a DataDownload at that URL."""
    return [{"@type": ["schema:DataDownload"], "schema:contentUrl": part}
            for part in _as_list(value) if isinstance(part, str)]


def _as_text(value, node, rule, notes):
    return str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value


def _keyword_split(value, node, rule, notes):
    if isinstance(value, str):
        return [k.strip() for k in value.split(",") if k.strip()]
    return _array(value, node, rule, notes)


def _language_name(value, node, rule, notes):
    language = value[0] if isinstance(value, list) and value else value
    return language.get("name") if isinstance(language, dict) else language or None


def _type_array(value, node, rule, notes):
    return _type_names(value)


# The shapers the table's `transform` column names; "" is a plain copy.
SHAPERS = {
    "": _copy, "array": _array, "first_value": _first_value, "concat_text": _concat_text, "type_array": _type_array,
    "portal_uri": _portal_uri, "portal_ref": _portal_ref, "id_as_identifier": _id_as_identifier, "source_sameas": _source_sameas,
    "primary_identifier": _primary_identifier,
    "extra_identifiers": _extra_identifiers,
    "creator_list": _creator_list, "author_beside_creator": _author_beside_creator,
    "agent_array": _agent_array, "first_alternate_name": _first_alternate_name,
    "extra_alternate_names": _extra_alternate_names, "org_name": _org_name,
    "org_identifier": _org_identifier, "person_name": _person_name,
    "nonempty_affiliation": _nonempty_affiliation, "grant_per_funder": _grant_per_funder,
    "grant_identifier": _grant_identifier, "work_url": _work_url, "link_or_thing": _link_or_thing,
    "usage_with_url": _usage_with_url, "usage_to_access": _usage_to_access,
    "license_or_nil": _license_or_nil, "citation_link": _citation_link,
    "linked_parts": _linked_parts, "unlinked_parts": _unlinked_parts,
    "bundle_parts": _bundle_parts, "part_downloads": _part_downloads, "link_role": _link_role, "unlinked_related": _unlinked_related,
    "keyword_about": _keyword_about, "derived_from": _derived_from,
    "metadata_source": _metadata_source, "work_identifier": _work_identifier,
    "work_url_or_given": _work_url_or_given, "place_name_term": _place_name_term, "place_code_alias": _place_code_alias, "place_identifier": _place_identifier,
    "iso_interval": _iso_interval, "interval_detail": _interval_detail, "place": _place_shaper,
    "property_value": _property_value, "term_array": _term_array,
    "distribution_array": _distribution_array, "content_url_or_nil": _content_url_or_nil,
    "url_as_content": _url_as_content, "archive_part": _archive_part, "part_urls": _part_urls,
    "as_text": _as_text, "agent": _agent_one,
    "keyword_split": _keyword_split, "language_name": _language_name, "date_fallback": _copy,
}
# These rows also run when the source has no value, to say that it is missing.
RUNS_WITHOUT_VALUE = {"license_or_nil", "content_url_or_nil", "work_url_or_given"}


def load_rules(path=TABLE):
    """The table's rows by subject_class in file order, the plain row for each name
    for a class with no row of its own, and the names that are dropped."""
    rules, by_name, dropped = {}, {}, set()
    for row in load_table(path):
        rules.setdefault(row["subject_class"], []).append(row)
        if row["object_id"] and not row["object_json_path"].startswith("$.") \
                and not row["transform"] and row["subject_label"] != "@type":
            by_name.setdefault(row["subject_label"], row)
        elif not row["object_id"]:
            dropped.add(row["subject_label"])
    for row in load_table(path):   # a name mapped only on the root, or only by a shaper
        if row["object_id"] and row["subject_label"] not in by_name | {"@type": 0} \
                and row["transform"] in ("", "array", "metadata_source"):
            by_name[row["subject_label"]] = row
    return rules, by_name, dropped


RULES, BY_NAME, DROPPED = load_rules()


def convert_record(hit, build=""):
    """Convert one API hit to a CDIF record. Returns (cdif, changes)."""
    rec = _clean(hit)
    portal_id = hit.get("_id", "")
    if portal_id:
        rec["_id"] = portal_id
    changes = []
    _DEFERRED.clear()
    doc = {"@context": dict(CDIF_CONTEXT), **convert_node(rec, ROOT_CLASS, changes)}
    _place_deferred(doc)
    doc.setdefault("@type", ["schema:Dataset"])

    dataset_id = doc.get("@id", "")
    record = doc.setdefault("schema:subjectOf", {})
    record.update({
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "@id": f"{dataset_id}#metadata" if dataset_id else "urn:cdif:metadata",
        "schema:name": f"Metadata record for: {(doc.get('schema:name') or 'dataset')[:120]}",
        "dcterms:conformsTo": [{"@id": CORE_URI}, {"@id": DISCOVERY_URI}],
    })
    record["schema:description"] = _join_text([
        "CDIF metadata harvested from the NIAID Data Discovery Portal API"
        + (f" (index build {build})" if build else "")
        + f", record {portal_id}, and converted by harvest_niaid_to_cdif.py. The "
          "portal aggregates this record from the repositories named in "
          "schema:includedInDataCatalog; nde: properties, including the portal's "
          "term annotations, are as the portal publishes them.",
        record.get("schema:description")])  # the from<LABEL> rows' sentences
    record["schema:includedInDataCatalog"] = {
        "@type": ["schema:DataCatalog"], "schema:name": "NIAID Data Discovery Portal",
        "schema:url": PORTAL}
    if build:
        record["schema:sdDatePublished"] = build

    if _HAVE_DETECT:
        try:
            uris = detect_cdif_conformance(doc)
            if uris:
                apply_conformance(doc, uris)
        except Exception as exc:
            changes.append(f"detect_cdif_conformance failed ({exc}); kept the default conformsTo")
    # Detection declares cdifNIAID where its registry has the class; a validation
    # checkout without it, or detection off, still gets the claim.
    if {"@id": NIAID_URI} not in record["dcterms:conformsTo"]:
        record["dcterms:conformsTo"].append({"@id": NIAID_URI})

    for prop, why in (("schema:dateModified", "CDIF requires schema:dateModified; none derivable"),):
        if prop not in doc:
            changes.append(f"WARNING: {why}")
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

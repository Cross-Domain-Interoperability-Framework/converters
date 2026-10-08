#!/usr/bin/env python3
"""
fair2_to_cdif.py - Convert a FAIR² data package (fair2.json) to CDIF JSON-LD.

FAIR² (https://fair-squared.github.io/fair2-spec/) is MLCommons Croissant plus
an extension namespace (fair2:) for data articles, archives, method sections,
field units and statistics, contributor roles and changelogs, packaged as a flat
@graph with a file-level _meta block. So the conversion is two passes:

  1. The Croissant core goes through croissant/ConvertFromCroissant.convert(),
     driven by croissant-to-cdif.sssom.tsv.
  2. What FAIR² adds goes through ../mappings/fair2-to-cdif.sssom.tsv. A source
     key that table claims is withheld from pass 1, so for a claimed key the
     FAIR² row wins.

Then the record @id, the catalog record and dcterms:conformsTo are settled
once, for the merged record.

Usage:
    python FAIR2/fair2_to_cdif.py fair2.json -o out.jsonld
    python FAIR2/fair2_to_cdif.py https://example.org/fair2.json -o out/ --validate
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from copy import deepcopy
from pathlib import Path
from urllib.parse import urljoin

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "croissant"))
import sssom_engine as _engine  # noqa: E402
import ConvertFromCroissant as _croissant  # noqa: E402

TABLE = ROOT / "mappings" / "fair2-to-cdif.sssom.tsv"
ALIASES = ROOT / "mappings" / "fair2-aliases.sssom.tsv"
FAIR2_NS = "https://fair2.ai/ns/"
SENSCIENCE_NS = "http://senscience.ai/"
CATALOG = "$.schema:subjectOf."

# Namespaces a FAIR² key can expand into, and the prefix the mapping tables
# write each with (longest match first). Older exports declare the fair2
# prefix as an earlier context URL, with or without a trailing slash; it names
# the same vocabulary, so it folds onto fair2:.
NAMESPACES = [
    ("http://mlcommons.org/croissant/RAI/", "rai"),
    ("http://mlcommons.org/croissant/", "cr"),
    ("https://fair2.ai/spec/fair2_context/", "fair2"),
    ("https://fair2.ai/spec/fair2_context", "fair2"),
    (FAIR2_NS, "fair2"),
    ("https://schema.org/", "sc"),
    ("http://schema.org/", "sc"),
    ("http://purl.org/dc/terms/", "dct"),
    ("http://www.w3.org/ns/prov#", "prov"),
    ("http://www.w3.org/2000/01/rdf-schema#", "rdfs"),
    ("http://www.w3.org/2004/02/skos/core#", "skos"),
    ("http://qudt.org/schema/qudt/", "qudt"),
    (SENSCIENCE_NS, "senscience"),
]


# ---------------------------------------------------------------------------
# Reading the FAIR² graph
# ---------------------------------------------------------------------------

def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _types(node):
    return [str(t).split(":")[-1] for t in _as_list(node.get("@type"))]


class Fair2Doc(object):
    """A fair2.json file: its context, _meta and an index of its nodes."""

    def __init__(self, doc, aliases=None):
        self.context = doc.get("@context") if isinstance(doc.get("@context"), dict) else {}
        self.aliases = aliases or {}
        self.base = self.context.get("@base")
        graph = _as_list(doc.get("@graph")) or [doc]
        datasets = [n for n in graph if isinstance(n, dict) and "Dataset" in _types(n)]
        if len(datasets) != 1:
            raise ValueError("expected exactly one Dataset in the @graph, found %d"
                             % len(datasets))
        self.dataset = datasets[0]
        # Legacy exports put _meta inside the Dataset rather than beside @graph.
        self.meta = doc.get("_meta") or self.dataset.get("_meta") or {}
        # Every identified node anywhere in the file, so a bare {"@id": ...}
        # reference resolves whether its target is a peer in @graph or written
        # out in full somewhere else (legacy files inline authors once and
        # refer to them by @id elsewhere). The fullest copy wins.
        self.nodes = {}
        self._index(doc)

    def _index(self, value):
        if isinstance(value, list):
            for v in value:
                self._index(v)
        elif isinstance(value, dict):
            rid = value.get("@id")
            if isinstance(rid, str) and len(value) > len(self.nodes.get(rid, {})):
                self.nodes[rid] = value
            for k, v in value.items():
                if k != "@context":
                    self._index(v)

    def iri(self, term):
        """A key, type or CURIE expanded the way the file's context says."""
        bound = self.context.get(term)
        if isinstance(bound, dict):
            bound = bound.get("@id")
        if isinstance(bound, str):
            term = bound
        if "://" in term:
            return term
        if ":" in term:
            prefix, local = term.split(":", 1)
            ns = self.context.get(prefix)
            return ns + local if isinstance(ns, str) else term
        vocab = self.context.get("@vocab") or "https://schema.org/"
        return vocab + term

    def compact(self, iri):
        for ns, prefix in NAMESPACES:
            if iri.startswith(ns) and len(iri) > len(ns):
                return "%s:%s" % (prefix, iri[len(ns):].lstrip("/"))
        return iri

    def curie(self, key):
        """A FAIR² key as the mapping table spells it, after aliasing."""
        if key.startswith("@"):
            return key
        term = self.compact(self.iri(key))
        return self.aliases.get(term, term)

    def plain(self, key):
        """A key's compacted IRI before aliasing."""
        return key if key.startswith("@") else self.compact(self.iri(key))

    def cdif_curie(self, key):
        """A key spelled for the CDIF output (schema: = http), not aliased."""
        if key.startswith("@"):
            return key
        term = self.compact(self.iri(key))
        return "schema:" + term[3:] if term.startswith("sc:") else term

    def resolve(self, ref):
        """A bare {"@id": ...} reference or id string -> the fullest node with
        that id. A node written out in place is used as written: exports
        sometimes give one @id different content in different places."""
        if isinstance(ref, dict) and set(ref) - {"@id", "@type"}:
            return ref
        rid = ref.get("@id") if isinstance(ref, dict) else ref
        if isinstance(rid, str) and rid in self.nodes:
            return self.nodes[rid]
        return ref if isinstance(ref, dict) else None

    def absolute(self, iri):
        if isinstance(iri, str) and self.base and ":" not in iri.split("/")[0]:
            return urljoin(self.base, iri)
        return iri

    def expand(self, value):
        """FAIR² JSON with its keys and types written as CDIF-side CURIEs."""
        if isinstance(value, list):
            return [self.expand(v) for v in value]
        if not isinstance(value, dict):
            return value
        out = {}
        for k, v in value.items():
            if k == "@type":
                out[k] = [self.cdif_curie(t) for t in _as_list(v)]
            elif k == "@id":
                out[k] = v
            else:
                out[self.cdif_curie(k)] = self.expand(v)
        return out


# ---------------------------------------------------------------------------
# Shapers (the table's transform column)
#
# Each takes (value, source, rule, ctx) and returns the CDIF value, or None to
# emit nothing. ctx carries the Fair2Doc and the field -> variable id map.
# ---------------------------------------------------------------------------

def _name(node):
    for key in ("name", "schema:name", "headline", "label", "rdfs:label"):
        if isinstance(node.get(key), str) and node[key]:
            return node[key]
    return None


def t_text(value, src, rule, ctx):
    return value if value not in (None, "", []) else None


def t_iri(value, src, rule, ctx):
    got = value.get("@id") or value.get("url") if isinstance(value, dict) else value
    return {"@id": got} if isinstance(got, str) and got else None


def t_date(value, src, rule, ctx):
    return _croissant._normalize_date(value) or value or None


def t_doi(value, src, rule, ctx):
    doi, url = _croissant._extract_doi({"url": value if isinstance(value, str) else ""})
    if not doi:
        return value or None
    return {"@type": ["schema:PropertyValue"],
            "schema:propertyID": {"@id": "https://registry.identifiers.org/registry/doi"},
            "schema:value": doi, "schema:url": url}


def _get(node, key):
    """node[key], also under the schema: prefix some exports write."""
    value = node.get(key)
    return value if value not in (None, "") else node.get("schema:" + key)


def _identifier(value):
    if isinstance(value, dict):          # legacy PropertyValue {propertyID, value}
        value = value.get("value") or value.get("@id") or value.get("url")
    return value if isinstance(value, str) and value else None


def _agent(node, doc):
    node = doc.resolve(node) or {}
    if isinstance(node, str):
        return {"@type": ["schema:Person"], "schema:name": node}
    kind = "schema:Organization" if "Organization" in _types(node) else "schema:Person"
    agent = {"@type": [kind]}
    if node.get("@id"):
        agent["@id"] = doc.absolute(node["@id"])
    if _name(node):
        agent["schema:name"] = _name(node)
    for key in ("givenName", "familyName", "email", "url"):
        if isinstance(_get(node, key), str) and _get(node, key):
            agent["schema:" + key] = _get(node, key)
    ident = _identifier(_get(node, "identifier"))
    if ident:
        agent["schema:identifier"] = ident
    if _get(node, "address") and kind == "schema:Organization":
        agent["schema:address"] = _get(node, "address")
    orgs = [_agent(a, doc) for a in _as_list(_get(node, "affiliation"))]
    if orgs:
        agent["schema:affiliation"] = orgs if len(orgs) > 1 else orgs[0]
    return agent


def t_agent(value, src, rule, ctx):
    return [_agent(v, ctx["doc"]) for v in _as_list(value)] or None


def _role_term(role, doc):
    term = {"@type": ["schema:DefinedTerm"]}
    if isinstance(role, dict):
        if _name(role):
            term["schema:name"] = _name(role)
        if role.get("@id"):
            term["schema:identifier"] = doc.iri(role["@id"])
    elif isinstance(role, str):          # legacy CURIE, e.g. credit:DataCuration
        iri = doc.iri(role)
        term["schema:name"] = role.split(":", 1)[-1] if ":" in role else role
        if "://" in iri:
            term["schema:identifier"] = iri
    return term if len(term) > 1 else None


def t_contributorrole(value, src, rule, ctx):
    """v1.3 Person + hadRole, or legacy Contribution {prov:agent, prov:hadRole}."""
    doc, out = ctx["doc"], []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        agent_ref = item.get("prov:agent") or item.get("agent")
        agent = _agent(agent_ref if agent_ref is not None else item, doc)
        roles = item.get("prov:hadRole") or item.get("hadRole")
        for role in _as_list(roles):
            term = _role_term(role, doc)
            if term:
                out.append({"@type": ["schema:Role"], "schema:roleName": term,
                            "schema:contributor": agent})
    return out or None


def t_grant(value, src, rule, ctx):
    out = []
    for g in _as_list(value):
        g = ctx["doc"].resolve(g) or {}
        grant = {"@type": ["schema:MonetaryGrant"]}
        if g.get("@id"):
            grant["@id"] = ctx["doc"].absolute(g["@id"])
        for key, target in (("name", "schema:name"), ("identifier", "schema:identifier"),
                            ("url", "schema:url")):
            if isinstance(g.get(key), str):
                grant[target] = g[key]
        funders = [_agent(f, ctx["doc"]) for f in _as_list(g.get("funder"))]
        if funders:
            grant["schema:funder"] = funders
        if len(grant) > 1:
            out.append(grant)
    return out or None


def _geo(geo):
    if not isinstance(geo, dict):
        return None
    kind = (_as_list(geo.get("@type")) or [""])[0].split(":")[-1]
    if kind == "GeoCoordinates":
        out = {"@type": ["schema:GeoCoordinates"]}
        for k in ("latitude", "longitude"):
            if geo.get(k) is not None:
                out["schema:" + k] = geo[k]
        return out
    out = {"@type": ["schema:GeoShape"]}
    for k in ("box", "polygon", "line"):
        if geo.get(k):
            out["schema:" + k] = geo[k]
    return out if len(out) > 1 else None


def t_place(value, src, rule, ctx):
    """The nested containsPlace tree, flattened to a list of schema:Place."""
    out = []

    def walk(node):
        node = ctx["doc"].resolve(node)
        if not isinstance(node, dict):
            return
        place = {"@type": ["schema:Place"]}
        for key in ("name", "description", "identifier"):
            if isinstance(node.get(key), str):
                place["schema:" + key] = node[key]
        geo = _geo(node.get("geo"))
        if geo:
            place["schema:geo"] = geo
        if len(place) > 1:
            out.append(place)
        for child in _as_list(node.get("containsPlace")):
            walk(child)

    for v in _as_list(value):
        if isinstance(v, str):
            out.append({"@type": ["schema:Place"], "schema:name": v})
        else:
            walk(v)
    return out or None


def t_accessrights(value, src, rule, ctx):
    out = []
    for v in _as_list(value):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            text = ": ".join(x for x in (_name(v), v.get("definition")) if x)
            if text:
                out.append(text)
            if v.get("url") or v.get("@id"):
                out.append(v.get("url") or v.get("@id"))
    return out or None


def _linkrole(relationship, url, name=None):
    target = {"@type": ["schema:EntryPoint"], "schema:url": url}
    if name:
        target["schema:name"] = name
    return {"@type": ["schema:LinkRole"], "schema:linkRelationship": relationship,
            "schema:target": target}


def _peer_link(relationship, value, ctx):
    out = []
    for v in _as_list(value):
        node = ctx["doc"].resolve(v) or {}
        url = node.get("url") or node.get("@id") if isinstance(node, dict) else v
        if isinstance(url, str) and url.startswith("http"):
            name = _name(node) if isinstance(node, dict) else None
            holder = node.get("holdingArchive") if isinstance(node, dict) else None
            if isinstance(holder, dict) and _name(holder):
                name = "%s (%s)" % (name, _name(holder)) if name else _name(holder)
            out.append(_linkrole(relationship, url, name))
    return out or None


def t_article(value, src, rule, ctx):
    return _peer_link("dataArticle", value, ctx)


def t_archive(value, src, rule, ctx):
    return _peer_link("dataArchive", value, ctx)


def t_portal(value, src, rule, ctx):
    """DataPortal -> LinkRole whose target is also the portal as a CreativeWork."""
    doc, out = ctx["doc"], []
    for v in _as_list(value):
        node = doc.resolve(v) or {}
        if not isinstance(node, dict):
            continue
        url = _get(node, "url") or node.get("@id")
        if not (isinstance(url, str) and url.startswith("http")):
            continue
        link = _linkrole("dataPortal", url, _name(node))
        target = link["schema:target"]
        target["@type"].append("schema:CreativeWork")
        if isinstance(_get(node, "description"), str):
            target["schema:description"] = _get(node, "description")
        ident = _identifier(_get(node, "identifier"))
        if ident:
            target["schema:identifier"] = ident
        if _get(node, "version") is not None:
            target["schema:version"] = str(_get(node, "version"))
        authors = [_agent(a, doc) for a in _as_list(_get(node, "author"))]
        if authors:
            target["schema:author"] = authors
        out.append(link)
    return out or None


_CHANGE_KINDS = (("newFeatures", "New features"), ("improvements", "Improvements"),
                 ("bugFixes", "Bug fixes"), ("otherInformation", "Other information"))


def t_changelog(value, src, rule, ctx):
    """Changelog entries (schema:UpdateAction) -> completed update activities."""
    doc, out = ctx["doc"], []
    for entry in _as_list(value):
        if not isinstance(entry, dict):
            continue
        act = {"@type": ["prov:Activity", "schema:Action"],
               "schema:additionalType": [{"@id": "schema:UpdateAction"}]}
        date = _croissant._normalize_date(_get(entry, "datePublished"))
        version = _get(entry, "version")
        act["schema:name"] = ("Release %s" % version if version is not None
                              else "Update of %s" % date if date else "Update")
        text = _get(entry, "description")
        if isinstance(text, dict):
            text = "\n".join("%s:\n%s" % (label, "\n".join("- %s" % i for i in items))
                             for key, label in _CHANGE_KINDS
                             for items in [_as_list(text.get(key))] if items)
        if isinstance(text, str) and text:
            act["schema:description"] = text
        if date:
            act["schema:endTime"] = date
        act["schema:actionStatus"] = "schema:CompletedActionStatus"
        used = []
        for prev in _as_list(entry.get("wasRevisionOf") or entry.get("prov:wasRevisionOf")):
            if not isinstance(prev, dict):
                continue
            ent = {"@type": ["prov:Entity"]}
            ident = _identifier(_get(prev, "identifier")) or prev.get("@id")
            if ident:
                ent["@id"] = doc.absolute(ident)
            if _get(prev, "version") is not None:
                ent["schema:version"] = str(_get(prev, "version"))
            if len(ent) > 1:
                used.append(ent)
        if used:
            act["prov:used"] = used
        out.append(act)
    return out or None


def t_citation(value, src, rule, ctx):
    article = ctx["doc"].resolve(src.get("dataArticle")) or {}
    seen = {article.get("@id"), article.get("url")}
    urls = [v for v in _as_list(value) if isinstance(v, str) and v not in seen
            and v.startswith("http")]
    return [_linkrole("citation", u) for u in urls] or None


def t_domain(value, src, rule, ctx):
    out = []
    for v in _as_list(value):
        v = ctx["doc"].resolve(v) or {}
        term = {"@type": ["schema:DefinedTerm"]}
        if _name(v):
            term["schema:name"] = _name(v)
        if v.get("@id"):
            term["schema:identifier"] = v["@id"]
        if len(term) > 1:
            out.append(term)
    return out or None


def _ordered(items, doc):
    """Method parts in fair2:next order (array order where there is no chain)."""
    items = [doc.resolve(i) or i for i in _as_list(items)]
    by_id = {i.get("@id"): i for i in items if isinstance(i, dict) and i.get("@id")}
    targets = {(i.get("next") or {}).get("@id") for i in items if isinstance(i, dict)
               and isinstance(i.get("next"), dict)}
    heads = [i for i in items if isinstance(i, dict) and i.get("@id") not in targets]
    out, seen = [], set()
    for head in heads:
        node = head
        while isinstance(node, dict) and id(node) not in seen:
            seen.add(id(node))
            out.append(node)
            nxt = node.get("next")
            node = by_id.get(nxt.get("@id")) if isinstance(nxt, dict) else None
    out.extend(i for i in items if isinstance(i, dict) and id(i) not in seen)
    return out


def _step(step, position, ctx):
    node = {"@type": ["schema:HowToStep"], "schema:position": position}
    if _name(step):
        node["schema:name"] = _name(step)
    if step.get("description"):
        node["schema:description"] = step["description"]
    subs = []
    for sub in _ordered(step.get("substep") or step.get("substeps"), ctx["doc"]):
        d = {"@type": ["schema:HowToDirection"]}
        if _name(sub):
            d["schema:name"] = _name(sub)
        if sub.get("description"):
            d["schema:text"] = sub["description"]
        subs.append(d)
    if subs:
        node["schema:itemListElement"] = subs
    return node


def t_method(value, src, rule, ctx):
    doc, out = ctx["doc"], []
    for section in _ordered(value, doc):
        act = {"@type": ["prov:Activity", "schema:Action"]}
        if section.get("@id"):
            act["@id"] = doc.absolute(section["@id"])
        if _name(section):
            act["schema:name"] = _name(section)
        if section.get("description"):
            act["schema:description"] = section["description"]
        steps = _ordered(section.get("step") or section.get("steps"), doc)
        if steps:
            act["schema:actionProcess"] = {
                "@type": ["schema:HowTo"],
                "schema:name": _name(section) or "Method",
                "schema:step": [_step(s, n, ctx) for n, s in enumerate(steps, 1)]}
        used = []
        for ref in _as_list(section.get("prov:used") or section.get("used")):
            node = doc.resolve(ref)
            if isinstance(node, dict):
                ent = {"@type": ["prov:Entity"], "@id": doc.absolute(node.get("@id"))}
                if _name(node):
                    ent["schema:name"] = _name(node)
                if node.get("encodingFormat"):
                    ent["schema:encodingFormat"] = node["encodingFormat"]
                used.append(ent)
        if used:
            act["prov:used"] = used
        results = []
        for s in [section] + steps:
            for ref in _as_list(s.get("generated")):
                rid = ref.get("@id") if isinstance(ref, dict) else ref
                var = ctx["var_ids"].get(rid)
                if var and {"@id": var} not in results:
                    results.append({"@id": var})
        if results:
            act["schema:result"] = results
        out.append(act)
    return out or None


def t_specref(value, src, rule, ctx):
    urls = [v.get("url") or v.get("@id") for v in _as_list(value) if isinstance(v, dict)]
    return [{"@id": u} for u in urls if isinstance(u, str)] or None


def t_unittext(value, src, rule, ctx):
    """v1.3 {label, symbol, uri} or legacy qudt:Unit {@id, rdfs:label, qudt:symbol}."""
    if isinstance(value, dict):
        for key in ("label", "rdfs:label", "symbol", "qudt:symbol"):
            if isinstance(value.get(key), str) and value[key]:
                return value[key]
        return None
    return value or None


def t_unitcode(value, src, rule, ctx):
    uri = (value.get("uri") or value.get("@id")) if isinstance(value, dict) else None
    return {"@id": uri} if isinstance(uri, str) and "://" in uri else None


def _stat(value, name):
    """A named statistic: from the variableMeasured PropertyValues (also under
    the legacy misspelling varriableMeasured), or a flat legacy key."""
    if not isinstance(value, dict):
        return None
    for key in ("variableMeasured", "varriableMeasured"):
        for pv in _as_list(value.get(key)):
            if isinstance(pv, dict) and pv.get("name") == name \
                    and isinstance(pv.get("value"), (int, float)):
                return pv["value"]
    flat = value.get(name)
    return flat if isinstance(flat, (int, float)) and not isinstance(flat, bool) else None


def t_statmin(value, src, rule, ctx):
    return _stat(value, "min")


def t_statmax(value, src, rule, ctx):
    return _stat(value, "max")


def t_passthrough(value, src, rule, ctx):
    return ctx["doc"].expand(value)


def t_unmapped(value, src, rule, ctx):
    return None


TRANSFORMS = {name[2:]: fn for name, fn in list(globals().items())
              if name.startswith("t_") and callable(fn)}
TRANSFORMS[""] = t_text

TABLE_SET = _engine.MappingSet(
    str(TABLE), transforms=TRANSFORMS,
    arity={"schema:creator": "ordered", "schema:contributor": "array",
           "schema:funding": "array", "schema:spatialCoverage": "array",
           "schema:conditionsOfAccess": "array", "schema:relatedLink": "array",
           "schema:keywords": "array", "prov:wasGeneratedBy": "array",
           "dcterms:conformsTo": "array"})


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def _apply(rows, source, curie_of, ctx, dataset_out, record_out, plain_of=None):
    """Apply table rows to `source`; returns the source keys consumed."""
    plain_of = plain_of or curie_of
    consumed = set()
    # A key reached through an alias gives way to the canonical key when a
    # file carries both (legacy exports repeat creator as author).
    keys = {}
    for k in sorted(source, key=lambda k: curie_of(k) != plain_of(k)):
        keys.setdefault(curie_of(k), k)
    for rule in rows:
        raw = keys.get(rule["subject_id"])
        if raw is None:
            continue
        consumed.add(raw)
        shaper = TRANSFORMS.get(rule.get("transform", ""))
        if shaper is None or not rule.get("object_id"):
            continue
        shaped = shaper(source[raw], source, rule, ctx)
        path = rule.get("object_json_path") or ""
        if path.startswith(CATALOG):
            TABLE_SET.place(record_out, path[len(CATALOG):], shaped)
        else:
            TABLE_SET.place(dataset_out, TABLE_SET.target_key(rule), shaped)
    return consumed


def _field_variable_ids(croissant_in):
    """Field @id -> the CDIF variable @id the Croissant pass gives it.

    Re-runs the Croissant converter's own indexing on the same input, so the
    ids agree with the ones convert() assigned.
    """
    by_file = _croissant._index_record_sets(croissant_in)
    _dist, node_for_file = _croissant._convert_distribution(croissant_in, by_file)
    _vars, _maps, ref_to_var = _croissant._convert_fields_to_cdif(by_file, node_for_file)
    return ref_to_var


def convert(fair2, detect=True, verbose=False):
    doc = Fair2Doc(fair2, _engine.load_aliases(str(ALIASES)))
    ds = doc.dataset
    dataset_rows = TABLE_SET.rows_for(("sc:Dataset",))
    claimed = {r["subject_id"] for r in TABLE_SET.rows
               if r.get("subject_class") == "sc:Dataset"}

    # Pass 1: the Croissant core, without the keys the FAIR² table claims.
    croissant_in = {k: v for k, v in deepcopy(ds).items()
                    if doc.curie(k) not in claimed and k != "_meta"}
    croissant_in["@context"] = doc.context
    # FAIR² writes a field's dataType as a bare schema.org DataType name
    # ('Float'); the Croissant pass keys on 'sc:Float'. Legacy exports whose
    # @vocab is http://senscience.ai/ use the same names.
    for rs in _as_list(croissant_in.get("recordSet")):
        for fld in _as_list(rs.get("field") if isinstance(rs, dict) else None):
            dt = fld.get("dataType") if isinstance(fld, dict) else None
            if isinstance(dt, str) and dt and ":" not in dt:
                fld["dataType"] = "sc:" + dt
    had_detect = _croissant.HAS_DETECT
    _croissant.HAS_DETECT = False            # detect once, on the merged record
    try:
        out = _croissant.convert(deepcopy(croissant_in), verbose=verbose)
    finally:
        _croissant.HAS_DETECT = had_detect
    ctx = {"doc": doc, "var_ids": _field_variable_ids(deepcopy(croissant_in))}

    # Pass 2: what FAIR² adds. A list target extends the Croissant value;
    # anything else replaces it.
    extra, record_extra = {}, {}
    _apply(dataset_rows, ds, doc.curie, ctx, extra, record_extra, doc.plain)
    _apply(TABLE_SET.rows_for(("fair2json:Meta",)), doc.meta,
           lambda k: "fair2json:" + k, ctx, record_extra, record_extra)
    for key, value in extra.items():
        if isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = out[key] + [v for v in value if v not in out[key]]
        else:
            out[key] = value

    # Field-level additions, onto the variable each field became.
    field_rows = TABLE_SET.rows_for(("cr:Field",))
    variables = {v.get("@id"): v for v in out.get("schema:variableMeasured", [])}
    for rs in _as_list(ds.get("recordSet")):
        for fld in _as_list(rs.get("field") if isinstance(rs, dict) else None):
            var = variables.get(ctx["var_ids"].get(fld.get("@id") or fld.get("name")))
            if var is not None:
                _apply(field_rows, fld, doc.curie, ctx, var, var, doc.plain)

    # The record is the FAIR² Dataset, whatever DOI the Croissant pass found.
    dataset_id = doc.absolute(ds.get("@id")) or out.get("@id")
    out["@id"] = dataset_id
    if doc.base:
        out["@context"]["@base"] = doc.base
    out["@context"].setdefault("fair2", FAIR2_NS)
    if '"senscience:' in json.dumps(out):
        out["@context"]["senscience"] = SENSCIENCE_NS
    out["prov:wasDerivedFrom"] = [
        d for d in out.get("prov:wasDerivedFrom", [])
        if d.get("@id") not in _croissant.CROISSANT_SOURCE_URIS
        and d.get("@id") != _croissant.CROISSANT_SOURCE_URI]
    if not out["prov:wasDerivedFrom"]:
        del out["prov:wasDerivedFrom"]

    record = out["schema:subjectOf"]
    record["schema:about"] = {"@id": dataset_id}
    if dataset_id.startswith(("http://", "https://", "urn:")):
        record["@id"] = dataset_id.rstrip("/") + "/metadata"
    record.pop("dcterms:conformsTo", None)
    record.update(record_extra)
    record["schema:description"] = ("Converted from a FAIR² fair2.json package to "
                                    "CDIF by converters/FAIR2/fair2_to_cdif.py.")

    if detect and _croissant.HAS_DETECT:
        uris = _croissant.detect_conformance(out, verbose=verbose)
        _croissant.apply_conformance(out, uris)
    elif not detect:
        claims = list(_croissant.CDIF_CORE_DISCOVERY_CONFORMS_TO)
        if out.get("schema:variableMeasured"):
            claims.append(_croissant.CDIF_DATA_DESCRIPTION_CONFORMS_TO)
        record["dcterms:conformsTo"] = (record.get("dcterms:conformsTo", [])
                                        + [{"@id": u} for u in claims])
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_input(source):
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"Accept": "application/ld+json, application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    with open(source, encoding="utf-8") as fh:
        return json.load(fh)


def validate(path):
    """Frame and validate with the validation submodule's FrameAndValidate."""
    tools = ROOT / "validation"
    cmd = [sys.executable, str(tools / "tools" / "FrameAndValidate.py"), str(path), "-v",
           "--schema", str(tools / "CDIFDataDescriptionSchema.json"),
           "--frame", str(tools / "CDIF-frame-2026.jsonld")]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return res.returncode == 0, (res.stdout + res.stderr).strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert a FAIR² fair2.json package to CDIF JSON-LD")
    ap.add_argument("input", help="fair2.json file or URL")
    ap.add_argument("--output", "-o", help="output file or directory "
                    "(default: <input stem>-cdif.jsonld beside the input)")
    ap.add_argument("--static-conformance", action="store_true",
                    help="declare core + discovery (+ data_description) instead of "
                         "detecting conformance from content")
    ap.add_argument("--validate", action="store_true",
                    help="frame and validate the output against the CDIF Data Description schema")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    doc = convert(load_input(args.input), detect=not args.static_conformance,
                  verbose=args.verbose)
    stem = Path(args.input.rstrip("/").rsplit("/", 1)[-1]).stem
    if args.output and os.path.isdir(args.output):
        out = Path(args.output) / (stem + "-cdif.jsonld")
    elif args.output:
        out = Path(args.output)
    else:
        out = Path(stem + "-cdif.jsonld")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    declared = [c["@id"] for c in doc["schema:subjectOf"].get("dcterms:conformsTo", [])]
    print("%s  %s" % (out, " ".join(declared) or "no conformance"))
    if args.validate:
        ok, report = validate(out)
        print("VALID" if ok else "INVALID")
        if args.verbose or not ok:
            print("\n".join("  " + l for l in report.splitlines()[-25:]))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

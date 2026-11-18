#!/usr/bin/env python3
"""
harvest_format_examples.py - Harvest example metadata records, in each source
format a converter here reads or is planned to read, from many public catalogs.

Formats and where each lands (one XML file per record, plus INDEX.json):

  iso19139     ISO19115/examples/iso19139/      gmd:MD_Metadata, gmi:MI_Metadata
  iso19115-3   ISO19115/examples/iso19115-3/    mdb:MD_Metadata
  datacite     DataCite/examples/               DataCite kernel XML
  eml          EML/examples/                    Ecological Metadata Language
  fgdc         FGDC/examples/                   FGDC CSDGM
  oai_dc       DublinCore/examples/oai_dc/      OAI-PMH simple Dublin Core
  csw_record   DublinCore/examples/csw_record/  OGC csw:Record
  wms / wfs    OGCServices/examples/{wms,wfs}/  GetCapabilities documents

Stages (each writes a JSON file under harvesters/endpoints/, so a later stage
can be rerun alone):

  seed     a random, seeded sample of re3data.org repositories; keeps each one's
           OAI-PMH and CSW API URLs                      -> re3data-sample.json
  probe    asks each endpoint what it serves: OAI-PMH ListMetadataFormats, CSW
           GetCapabilities outputSchema values           -> endpoint-formats.json
  harvest  round-robin over the endpoints serving each format, one record per
           endpoint per round, so the records come from as many repositories
           as possible; then tops up from aggregators that sample at random
           (DataCite random=true, DataONE's Solr index, GBIF and EDI for EML),
           web-accessible folders (NOAA, state surveys) and, for WMS/WFS,
           from service links in the harvested ISO records.

Usage:
    python harvesters/harvest_format_examples.py seed --sample 800
    python harvesters/harvest_format_examples.py probe
    python harvesters/harvest_format_examples.py harvest --per-format 50
    python harvesters/harvest_format_examples.py all
"""

import argparse
import base64
import datetime as _dt
import json
import random
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "harvesters" / "endpoints"
SEED = 20261118
UA = {"User-Agent": "cdif-converters-example-harvester/0.1 (+https://github.com/Cross-Domain-Interoperability-Framework/converters)"}
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False,
                         remove_blank_text=False)
MAX_BYTES = 5_000_000
REPO_CAP = 5            # records per repository per format, so no catalog dominates
CAPABILITIES_MAX = 1_000_000   # WMS/WFS capabilities larger than this are not kept
TODAY = _dt.date.today().isoformat()

OUT = {
    "iso19139": ROOT / "ISO19115" / "examples" / "iso19139",
    "iso19115-3": ROOT / "ISO19115" / "examples" / "iso19115-3",
    "datacite": ROOT / "DataCite" / "examples",
    "eml": ROOT / "EML" / "examples",
    "fgdc": ROOT / "FGDC" / "examples",
    "oai_dc": ROOT / "DublinCore" / "examples" / "oai_dc",
    "csw_record": ROOT / "DublinCore" / "examples" / "csw_record",
    "wms": ROOT / "OGCServices" / "examples" / "wms",
    "wfs": ROOT / "OGCServices" / "examples" / "wfs",
}

# root element (namespace, local name) -> format
ROOT_FORMAT = {
    ("http://www.isotc211.org/2005/gmd", "MD_Metadata"): "iso19139",
    ("http://www.isotc211.org/2005/gmi", "MI_Metadata"): "iso19139",
    ("http://standards.iso.org/iso/19115/-3/mdb/2.0", "MD_Metadata"): "iso19115-3",
    ("http://standards.iso.org/iso/19115/-3/mdb/1.0", "MD_Metadata"): "iso19115-3",
    ("http://www.openarchives.org/OAI/2.0/oai_dc/", "dc"): "oai_dc",
    ("http://www.opengis.net/cat/csw/2.0.2", "Record"): "csw_record",
    ("http://www.opengis.net/wms", "WMS_Capabilities"): "wms",
    (None, "WMT_MS_Capabilities"): "wms",
    ("http://www.opengis.net/wfs", "WFS_Capabilities"): "wfs",
    ("http://www.opengis.net/wfs/2.0", "WFS_Capabilities"): "wfs",
    (None, "metadata"): "fgdc",                      # FGDC CSDGM has no namespace
}


def format_of(el):
    q = etree.QName(el)
    f = ROOT_FORMAT.get((q.namespace, q.localname))
    if f:
        return f
    if q.namespace and "datacite.org/schema/kernel" in q.namespace and q.localname == "resource":
        return "datacite"
    if q.namespace and "ecoinformatics.org/eml" in q.namespace and q.localname == "eml":
        return "eml"
    if q.localname == "eml" and (q.namespace or "").startswith("https://eml.ecoinformatics.org"):
        return "eml"
    if q.localname == "metadata" and el.find("idinfo") is not None:
        return "fgdc"
    return None


# OAI-PMH metadataPrefix / schema / namespace -> format
def oai_format(prefix, schema, namespace):
    s = " ".join(x or "" for x in (prefix, schema, namespace)).lower()
    if "19115/-3" in s or "/mdb/" in s:
        return "iso19115-3"
    if "isotc211.org/2005/gm" in s or prefix.lower() in ("iso19139", "iso19115", "iso", "gmd", "iso19139.mcp"):
        return "iso19139"
    if "datacite" in s:
        return "datacite"
    if "ecoinformatics.org/eml" in s or prefix.lower().startswith("eml"):
        return "eml"
    if "fgdc" in s or "csdgm" in s:
        return "fgdc"
    if prefix == "oai_dc":
        return "oai_dc"
    return None


session = requests.Session()
session.headers.update(UA)


def get(url, params=None, timeout=45, accept=None):
    headers = {"Accept": accept} if accept else None
    r = session.get(url, params=params, timeout=timeout, headers=headers, stream=True)
    r.raise_for_status()
    data = r.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(data) > MAX_BYTES:
        raise ValueError("response larger than %d bytes" % MAX_BYTES)
    return data, r.url


def parse(data):
    return etree.fromstring(data, PARSER)


def slug(text, n=80):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text or "").strip("_")[:n] or "record"


# ---------------------------------------------------------------------------
# seed: re3data sample
# ---------------------------------------------------------------------------

def stage_seed(sample):
    data, _ = get("https://www.re3data.org/api/v1/repositories", timeout=120)
    ids = [e.text for e in parse(data).iter("id")]
    rng = random.Random(SEED)
    chosen = sorted(rng.sample(ids, min(sample, len(ids))))
    print("re3data: %d repositories, sampling %d" % (len(ids), len(chosen)))

    def detail(rid):
        try:
            d, _ = get("https://www.re3data.org/api/v1/repository/%s" % rid, timeout=60)
            x = parse(d)
        except Exception as exc:
            return {"id": rid, "error": str(exc)[:200]}
        t = lambda name: next((e.text.strip() for e in x.iter("{*}" + name) if e.text and e.text.strip()), None)
        apis = [{"type": a.get("apiType"), "url": (a.text or "").strip()}
                for a in x.iter("{*}api") if (a.text or "").strip()]
        return {"id": rid, "name": t("repositoryName"), "url": t("repositoryURL"),
                "software": [e.text for e in x.iter("{*}softwareName") if e.text],
                "metadataStandards": sorted({e.text for e in x.iter("{*}metadataStandardName") if e.text}),
                "apis": apis}

    with ThreadPoolExecutor(max_workers=4) as pool:
        repos = list(pool.map(detail, chosen))
    keep = [r for r in repos if any((a["type"] or "").lower() in ("oai-pmh", "other", "rest")
                                    or "csw" in a["url"].lower()
                                    for a in r.get("apis", []))]
    STATE.mkdir(parents=True, exist_ok=True)
    out = STATE / "re3data-sample.json"
    out.write_text(json.dumps({"seed": SEED, "fetched": TODAY, "sampled": len(chosen),
                               "repositories": keep}, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8", newline="\n")
    n_oai = sum(1 for r in keep for a in r["apis"] if (a["type"] or "").lower() == "oai-pmh")
    print("kept %d repositories with an API (%d OAI-PMH endpoints) -> %s" % (len(keep), n_oai, out))


# ---------------------------------------------------------------------------
# probe: what each endpoint serves
# ---------------------------------------------------------------------------

def _oai_base(url):
    return url.split("?")[0]


def probe_oai(repo, url):
    base = _oai_base(url)
    try:
        data, _ = get(base, {"verb": "ListMetadataFormats"}, timeout=30)
        x = parse(data)
    except Exception as exc:
        return {"kind": "oai-pmh", "url": base, "error": str(exc)[:160]}
    formats = {}
    for mf in x.iter("{*}metadataFormat"):
        prefix = mf.findtext("{*}metadataPrefix") or ""
        f = oai_format(prefix, mf.findtext("{*}schema"), mf.findtext("{*}metadataNamespace"))
        if f and f not in formats:
            formats[f] = prefix
    return {"kind": "oai-pmh", "url": base, "formats": formats}


CSW_SCHEMAS = {
    "iso19139": "http://www.isotc211.org/2005/gmd",
    "iso19115-3": "http://standards.iso.org/iso/19115/-3/mdb/2.0",
    "csw_record": "http://www.opengis.net/cat/csw/2.0.2",
    "fgdc": "http://www.opengis.net/cat/csw/csdgm",
}


def probe_csw(repo, url):
    base = url.split("?")[0]
    try:
        data, _ = get(base, {"service": "CSW", "request": "GetCapabilities", "version": "2.0.2"},
                      timeout=30)
        text = data.decode("utf-8", "replace")
        parse(data)
    except Exception as exc:
        return {"kind": "csw", "url": base, "error": str(exc)[:160]}
    formats = {f: ns for f, ns in CSW_SCHEMAS.items() if ns in text}
    formats.setdefault("csw_record", CSW_SCHEMAS["csw_record"])
    return {"kind": "csw", "url": base, "formats": formats}


# Catalogs added by hand: known public CSWs serving ISO (not all are in re3data)
EXTRA_CSW = [
    ("Geoscience Australia eCat", "https://ecat.ga.gov.au/geonetwork/srv/eng/csw"),
    ("AODN / IMOS", "https://catalogue-imos.aodn.org.au/geonetwork/srv/eng/csw"),
    ("data.gov.au", "https://data.gov.au/geonetwork/srv/eng/csw"),
    ("British Geological Survey", "https://metadata.bgs.ac.uk/geonetwork/srv/eng/csw"),
    ("NOAA NCEI GeoPortal", "https://www.ncei.noaa.gov/metadata/geoportal/csw"),
    ("GeoNetwork demo", "https://demo.georchestra.org/geonetwork/srv/eng/csw"),
    ("PDOK (Netherlands NGR)", "https://nationaalgeoregister.nl/geonetwork/srv/dut/csw"),
    ("INSPIRE Geoportal Ireland", "https://inspire.geohive.ie/geoportal/csw"),
    ("Swiss geocat", "https://www.geocat.ch/geonetwork/srv/eng/csw"),
    ("FAO GeoNetwork", "https://data.apps.fao.org/map/catalog/srv/eng/csw"),
    ("data.gov (US)", "https://catalog.data.gov/csw"),
    ("GeoNetwork Copernicus Land", "https://sdi.eea.europa.eu/catalogue/srv/eng/csw"),
    ("ICES", "https://gis.ices.dk/geonetwork/srv/eng/csw"),
    ("IMAS (University of Tasmania)", "https://metadata.imas.utas.edu.au/geonetwork/srv/eng/csw"),
    ("eAtlas", "https://catalogue.eatlas.org.au/geonetwork/srv/eng/csw"),
    ("TERN", "https://geonetwork.tern.org.au/geonetwork/srv/eng/csw"),
    ("Ifremer Sextant", "https://sextant.ifremer.fr/geonetwork/srv/eng/csw"),
    ("BODC / NERC EDS", "https://csw.edp.nerc.ac.uk/geonetwork/srv/eng/csw"),
]


def stage_probe():
    seed = json.loads((STATE / "re3data-sample.json").read_text(encoding="utf-8"))
    jobs = []
    for r in seed["repositories"]:
        for a in r["apis"]:
            kind = (a["type"] or "").lower()
            if kind == "oai-pmh":
                jobs.append(("oai", r, a["url"]))
            elif "csw" in a["url"].lower() or "geonetwork" in a["url"].lower():
                jobs.append(("csw", r, a["url"]))
    for name, url in EXTRA_CSW:
        jobs.append(("csw", {"id": None, "name": name}, url))
    for name, url in EXTRA_OAI:
        jobs.append(("oai", {"id": None, "name": name}, url))
    print("probing %d endpoints" % len(jobs))

    def run(job):
        kind, r, url = job
        res = probe_oai(r, url) if kind == "oai" else probe_csw(r, url)
        res.update(repository=r.get("name"), re3data=r.get("id"))
        return res

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, jobs))
    seen, eps = set(), []
    for e in results:
        if e["url"] in seen:
            continue
        seen.add(e["url"])
        eps.append(e)
    (STATE / "endpoint-formats.json").write_text(
        json.dumps({"probed": TODAY, "endpoints": eps}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")
    counts = {}
    for e in eps:
        for f in e.get("formats", {}):
            counts[f] = counts.get(f, 0) + 1
    ok = sum(1 for e in eps if "formats" in e)
    print("%d endpoints answered of %d; endpoints per format: %s" % (ok, len(eps), counts))


# ---------------------------------------------------------------------------
# harvest
# ---------------------------------------------------------------------------

class Corpus:
    def __init__(self, fmt, target):
        self.fmt, self.target = fmt, target
        self.dir = OUT[fmt]
        self.dir.mkdir(parents=True, exist_ok=True)
        idx = self.dir / "INDEX.json"
        self.index = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else []
        # No repository gives more than REPO_CAP records: drop any extras an
        # earlier run kept, so a refill brings in other repositories.
        per, keep = {}, []
        for e in self.index:
            r = self.repo_key(e.get("repository"), e.get("endpoint"))
            per[r] = per.get(r, 0) + 1
            if per[r] <= REPO_CAP:
                keep.append(e)
            else:
                (self.dir / e["file"]).unlink(missing_ok=True)
        self.index = keep
        self.files = {e["file"] for e in keep}
        for p in self.dir.glob("*.xml"):
            if p.name not in self.files:
                p.unlink()
        self.sources = {e.get("endpoint") for e in self.index}
        self.per_repo = {}
        for e in keep:
            r = self.repo_key(e.get("repository"), e.get("endpoint"))
            self.per_repo[r] = self.per_repo.get(r, 0) + 1

    @staticmethod
    def repo_key(repository, endpoint):
        return repository or endpoint

    @property
    def full(self):
        return len(self.files) >= self.target

    def add(self, el, label, endpoint, request, repository=None, ident=None, kind=None):
        if self.full or format_of(el) != self.fmt:
            return False
        key = self.repo_key(repository, endpoint)
        if self.per_repo.get(key, 0) >= REPO_CAP:
            return False
        name = "%s-%s.xml" % (slug(label, 40), slug(ident or str(len(self.files)), 60))
        if name in self.files:
            return False
        data = etree.tostring(el, xml_declaration=True, encoding="UTF-8")
        if self.fmt in ("wms", "wfs") and len(data) > CAPABILITIES_MAX:
            return False
        (self.dir / name).write_bytes(data)
        self.files.add(name)
        self.sources.add(endpoint)
        self.per_repo[key] = self.per_repo.get(key, 0) + 1
        self.index.append({"file": name, "format": self.fmt, "repository": repository,
                           "endpoint": endpoint, "request": request, "identifier": ident,
                           "kind": kind, "retrieved": TODAY})
        print("  %-11s %s" % (self.fmt, name))
        return True

    def save(self):
        self.index.sort(key=lambda e: e["file"])
        (self.dir / "INDEX.json").write_text(json.dumps(self.index, indent=1, ensure_ascii=False)
                                             + "\n", encoding="utf-8", newline="\n")


def oai_records(base, prefix, rng):
    params = {"verb": "ListRecords", "metadataPrefix": prefix}
    data, url = get(base, params, timeout=60)
    x = parse(data)
    recs = []
    for rec in x.iter("{http://www.openarchives.org/OAI/2.0/}record"):
        hdr = rec.find("{*}header")
        if hdr is not None and hdr.get("status") == "deleted":
            continue
        md = rec.find("{*}metadata")
        kids = [c for c in md if isinstance(c.tag, str)] if md is not None else []
        if kids:
            recs.append((hdr.findtext("{*}identifier") if hdr is not None else None, kids[0]))
    rng.shuffle(recs)
    return recs, base + "?" + urllib.parse.urlencode(params)


def csw_records(base, schema, rng, start=None):
    start = start or rng.randint(1, 200)
    params = {"service": "CSW", "version": "2.0.2", "request": "GetRecords",
              "typeNames": "csw:Record", "outputSchema": schema, "elementSetName": "full",
              "resultType": "results", "maxRecords": "3", "startPosition": str(start)}
    data, url = get(base, params, timeout=60)
    x = parse(data)
    res = x.find("{*}SearchResults")
    recs = [c for c in res if isinstance(c.tag, str)] if res is not None else []
    return recs, base + "?" + urllib.parse.urlencode(params)


def _ident(el):
    for path in ("{*}fileIdentifier", "{*}metadataIdentifier"):
        e = el.find(path)
        if e is not None:
            t = " ".join(e.itertext()).strip()
            if t:
                return t.split()[0]
    for tag in ("{*}identifier", "{*}packageId"):
        e = el.find(tag)
        if e is not None and (e.text or "").strip():
            return e.text.strip()
    return el.get("packageId")


def harvest_endpoints(corpora, eps, rng):
    """Round-robin: one record per endpoint per round, per format, until the
    format is full or every endpoint has given REPO_CAP records."""
    for fmt, corpus in corpora.items():
        pool = [e for e in eps if fmt in e.get("formats", {})]
        rng.shuffle(pool)
        rounds = 0
        while pool and not corpus.full and rounds < REPO_CAP:
            rounds += 1
            for e in list(pool):
                if corpus.full:
                    break
                try:
                    if e["kind"] == "oai-pmh":
                        recs, req = oai_records(e["url"], e["formats"][fmt], rng)
                        recs = [(i, el) for i, el in recs]
                    else:
                        els, req = csw_records(e["url"], CSW_SCHEMAS[fmt], rng)
                        recs = [(_ident(el), el) for el in els]
                except Exception as exc:
                    print("  skip %s (%s)" % (e["url"], str(exc)[:80]))
                    pool.remove(e)
                    continue
                added = False
                for ident, el in recs:
                    if corpus.add(el, e.get("repository") or e["url"], e["url"], req,
                                  e.get("repository"), _ident(el) or ident, e["kind"]):
                        added = True
                        break
                if not added:
                    pool.remove(e)
                time.sleep(0.3)


def harvest_datacite(corpus, rng):
    """DataCite random sample: each DOI's kernel XML (attributes.xml, base64)."""
    tries = 0
    while not corpus.full and tries < 4:
        tries += 1
        # No fields[] filter: with one, a random page omits attributes.xml
        data, url = get("https://api.datacite.org/dois",
                        {"random": "true", "page[size]": "50"}, timeout=120)
        for item in json.loads(data)["data"]:
            xml = (item.get("attributes") or {}).get("xml")
            if not xml:
                continue
            client = (item.get("relationships", {}).get("client", {}).get("data") or {}).get("id") \
                or item.get("attributes", {}).get("clientId") or "datacite"
            try:
                el = parse(base64.b64decode(xml))
            except Exception:
                continue
            corpus.add(el, "datacite-" + client, "https://api.datacite.org/dois",
                       "https://api.datacite.org/dois/%s" % item["id"], client, item["id"], "datacite")


DATAONE_FORMATS = {
    "eml": "formatId:eml*",
    "fgdc": 'formatId:"FGDC-STD-001-1998" OR formatId:"FGDC-STD-001.1-1999"',
    "iso19139": 'formatId:"http://www.isotc211.org/2005/gmd" OR formatId:"http://www.isotc211.org/2005/gmd-noaa" OR formatId:"http://www.isotc211.org/2005/gmd-pangaea"',
}


def harvest_dataone(corpus, rng):
    """DataONE: a random sort over the Solr index, one record per member node first."""
    q = DATAONE_FORMATS[corpus.fmt]
    params = {"q": "(%s) AND -obsoletedBy:*" % q, "fl": "id,formatId,datasource",
              "rows": "200", "sort": "random_%d desc" % rng.randint(1, 10**6), "wt": "json"}
    data, _ = get("https://cn.dataone.org/cn/v2/query/solr/", params, timeout=90)
    docs = json.loads(data)["response"]["docs"]
    by_node = {}
    for d in docs:
        by_node.setdefault(d.get("datasource"), []).append(d)
    order = []
    while any(by_node.values()):
        for node in list(by_node):
            if by_node[node]:
                order.append(by_node[node].pop(0))
    for d in order:
        if corpus.full:
            break
        obj = "https://cn.dataone.org/cn/v2/object/" + urllib.parse.quote(d["id"], safe="")
        try:
            el = parse(get(obj, timeout=60)[0])
        except Exception as exc:
            print("  skip %s (%s)" % (d["id"][:60], str(exc)[:60]))
            continue
        node = (d.get("datasource") or "dataone").split(":")[-1]
        corpus.add(el, "dataone-" + node, "https://cn.dataone.org/cn/v2/object/", obj,
                   d.get("datasource"), d["id"], "dataone")
        time.sleep(0.2)


# Endpoints added by hand: catalogs known to serve a format re3data's sample
# lacks. OAI-PMH entries are probed like the re3data ones.
EXTRA_OAI = [
    ("PANGAEA", "https://ws.pangaea.de/oai/provider"),
    ("GFZ Data Services", "https://doidb.wdc-terra.org/oaip/oai"),
    ("NERC EDS CEDA", "https://catalogue.ceda.ac.uk/oai/provider"),
    ("Edmond (Max Planck)", "https://edmond.mpg.de/oai"),
]

# Web-accessible folders of metadata files (from geoscience_live_wafs.csv):
# the root element decides the format (NOAA: ISO 19115-2; the state surveys: FGDC)
WAF_SOURCES = [
    ("ngdc-geology", "https://data.noaa.gov/waf/NOAA/NESDIS/NGDC/MGG/Geology/iso/xml/"),
    ("ngdc-geophysics", "https://data.noaa.gov/waf/NOAA/NESDIS/NGDC/MGG/Geophysics/iso/xml/"),
    ("ngdc-dem", "https://data.noaa.gov/waf/NOAA/NESDIS/NGDC/MGG/DEM/iso/xml/"),
    ("ngdc-multibeam", "https://data.noaa.gov/waf/NOAA/NESDIS/NGDC/MGG/Multibeam/iso/xml/"),
    ("ngdc-seismic", "https://data.noaa.gov/waf/NOAA/NESDIS/NGDC/MGG/Seismic/iso/xml/"),
    ("ngdc-geomag", "https://data.noaa.gov/waf/NOAA/NESDIS/NGDC/STP/Geomag/iso/xml/"),
    ("ngdc-hazard-photos", "https://data.noaa.gov/waf/NOAA/NESDIS/NGDC/MGG/Hazard_Photos/iso/xml/"),
    ("ngdc-sonar-water-column", "https://data.noaa.gov/waf/NOAA/NESDIS/NGDC/MGG/Sonar_Water_Column/iso/xml/"),
    ("ncei-paleo", "https://data.noaa.gov/waf/NOAA/NESDIS/ncei/paleo/iso/xml/"),
    ("ncei-accessions", "https://data.noaa.gov/waf/NOAA/NESDIS/ncei/accessions/iso/xml/"),
    ("ncei-gis", "https://data.noaa.gov/waf/NOAA/NESDIS/ncei/gis/iso/xml/"),
    ("ncei-oer", "https://data.noaa.gov/waf/NOAA/NESDIS/ncei/oer/iso/xml/"),
    ("ngdc-collection", "https://data.noaa.gov/waf/NOAA/NESDIS/NGDC/Collection/iso/xml/"),
    ("akdggs", "https://dggs.alaska.gov/webpubs/metadata/"),
    ("arkansas-gis", "https://gis.arkansas.gov/metadata/XML/"),
]

# Local records, copied as they are.
LOCAL = [
    ("usgin", Path("C:/GithubC/USGIN/metadataTransforms/iso-19139-to-HTMLwSDO/examples/"
                   "CDI_ISO19139_full_example_12.2.0.xml")),
]


def datagov_wafs(rng, n=40):
    """A random sample of data.gov's harvest-source WAFs (endpoints/datagov-wafs.json,
    from data.gov's harvest source list): many agencies, ISO and FGDC alike."""
    path = STATE / "datagov-wafs.json"
    if not path.exists():
        return []
    wafs = json.loads(path.read_text(encoding="utf-8"))["wafs"]
    rng.shuffle(wafs)
    return [(slug(w["name"], 40), w["url"].rstrip("/") + "/", w["organization"]) for w in wafs[:n]]


def harvest_wafs(corpora, rng, per_waf=2):
    """A few files from each WAF, chosen at random from its listing."""
    sources = [(label, folder, None) for label, folder in WAF_SOURCES] + datagov_wafs(rng)
    for label, folder, org in sources:
        try:
            listing = get(folder, timeout=60)[0].decode("utf-8", "replace")
        except Exception as exc:
            print("  skip %s (%s)" % (folder, str(exc)[:80]))
            continue
        names = sorted(set(re.findall(r'href="([^"/?]+\.xml)"', listing, re.I)))
        rng.shuffle(names)
        got = 0
        for name in names[:per_waf * 3]:
            if got >= per_waf:
                break
            try:
                el = parse(get(folder + name, timeout=60)[0])
            except Exception:
                continue
            c = corpora.get(format_of(el))
            if c is not None and c.add(el, label, folder, folder + name, org or label,
                                       Path(name).stem, "waf"):
                got += 1
            time.sleep(0.3)


def harvest_gbif(corpus, rng):
    """GBIF registry datasets at random offsets -> each one's EML document."""
    data, _ = get("https://api.gbif.org/v1/dataset", {"limit": "1"}, timeout=60)
    total = json.loads(data)["count"]
    seen = set()
    for _ in range(corpus.target * 3):
        if corpus.full:
            break
        off = rng.randrange(total)
        try:
            ds = json.loads(get("https://api.gbif.org/v1/dataset",
                                {"limit": "1", "offset": str(off)}, timeout=60)[0])["results"][0]
            pub = ds.get("publishingOrganizationKey")
            if pub in seen:
                continue
            url = "https://api.gbif.org/v1/dataset/%s/document" % ds["key"]
            el = parse(get(url, timeout=60)[0])
        except Exception:
            continue
        if corpus.add(el, "gbif-" + (ds.get("type") or "dataset").lower(), "https://api.gbif.org/v1/dataset",
                      url, ds.get("publishingOrganizationKey"), ds["key"], "gbif"):
            seen.add(pub)
        time.sleep(0.3)


def harvest_edi(corpus, rng, n=8):
    """EDI / LTER PASTA: the newest revision of random packages' EML."""
    scopes = get("https://pasta.lternet.edu/package/eml", timeout=60)[0].decode().split()
    rng.shuffle(scopes)
    for scope in scopes[:n * 2]:
        if corpus.full or n <= 0:
            break
        try:
            ids = get("https://pasta.lternet.edu/package/eml/%s" % scope, timeout=60)[0].decode().split()
            ident = rng.choice(ids)
            rev = get("https://pasta.lternet.edu/package/eml/%s/%s?filter=newest" % (scope, ident),
                      timeout=60)[0].decode().strip()
            url = "https://pasta.lternet.edu/package/metadata/eml/%s/%s/%s" % (scope, ident, rev)
            el = parse(get(url, timeout=60)[0])
        except Exception:
            continue
        if corpus.add(el, "edi-" + scope, "https://pasta.lternet.edu/package/metadata/eml/",
                      url, "EDI (%s)" % scope, "%s.%s.%s" % (scope, ident, rev), "pasta"):
            n -= 1
        time.sleep(0.3)


def harvest_local(corpora):
    for label, path in LOCAL:
        if path.exists():
            el = etree.parse(str(path), PARSER).getroot()
            c = corpora.get(format_of(el))
            if c is not None:
                c.add(el, label, str(path.parent), path.name, "USGIN metadataTransforms",
                      path.stem, "local")


OGC_LINK = re.compile(r"(WMS|WFS)", re.I)


def _ogc_links(root, src):
    out = []
    for o in root.iter("{*}CI_OnlineResource"):
        lk, pr = o.find("{*}linkage"), o.find("{*}protocol")
        url = " ".join(lk.itertext()).strip() if lk is not None else ""
        proto = " ".join(pr.itertext()).strip() if pr is not None else ""
        m = OGC_LINK.search(proto + " " + url)
        if url.startswith("http") and m:
            out.append((m.group(1).lower(), url, src))
    return out


def harvest_ogc(corpora, rng, eps=()):
    """GetCapabilities of WMS/WFS services, one per host: services linked from the
    harvested ISO records, and from the service records of each CSW."""
    links = []
    for d in (OUT["iso19139"], OUT["iso19115-3"]):
        for f in d.glob("*.xml"):
            try:
                links += _ogc_links(etree.parse(str(f), PARSER).getroot(), f.name)
            except Exception:
                continue
    for e in eps:
        if e.get("kind") != "csw" or "iso19139" not in e.get("formats", {}):
            continue
        params = {"service": "CSW", "version": "2.0.2", "request": "GetRecords",
                  "typeNames": "csw:Record", "outputSchema": CSW_SCHEMAS["iso19139"],
                  "elementSetName": "full", "resultType": "results", "maxRecords": "25",
                  "constraintLanguage": "CQL_TEXT", "constraint_language_version": "1.1.0",
                  "constraint": "type = 'service'"}
        try:
            links += _ogc_links(parse(get(e["url"], params, timeout=60)[0]),
                                "service records of " + (e.get("repository") or e["url"]))
        except Exception as exc:
            print("  no service records from %s (%s)" % (e["url"], str(exc)[:60]))
    rng.shuffle(links)
    hosts = {"wms": set(), "wfs": set()}
    for kind, url, src in links:
        corpus = corpora[kind]
        host = urllib.parse.urlparse(url).netloc
        if corpus.full or host in hosts[kind]:
            continue
        base = url.split("?")[0]
        params = {"service": kind.upper(), "request": "GetCapabilities"}
        try:
            data, final = get(base, params, timeout=45)
            el = parse(data)
        except Exception as exc:
            continue
        if corpus.add(el, host, base, base + "?" + urllib.parse.urlencode(params), host,
                      slug(urllib.parse.urlparse(base).path, 50), "linked from " + src):
            hosts[kind].add(host)
        time.sleep(0.3)


def stage_harvest(per_format, only=None):
    rng = random.Random(SEED)
    eps = json.loads((STATE / "endpoint-formats.json").read_text(encoding="utf-8"))["endpoints"]
    fmts = only or list(OUT)
    corpora = {f: Corpus(f, per_format) for f in fmts}
    harvest_local(corpora)
    harvest_wafs(corpora, rng)
    harvest_endpoints({f: c for f, c in corpora.items() if f not in ("wms", "wfs")}, eps, rng)
    if "eml" in corpora:
        for fn in (harvest_edi, harvest_gbif):
            try:
                fn(corpora["eml"], rng)
            except Exception as exc:
                print("  eml %s failed: %s" % (fn.__name__, exc))
    for f, fn in (("datacite", harvest_datacite), ("eml", harvest_dataone),
                  ("fgdc", harvest_dataone), ("iso19139", harvest_dataone)):
        if f in corpora and not corpora[f].full:
            try:
                fn(corpora[f], rng)
            except Exception as exc:
                print("  %s top-up failed: %s" % (f, exc))
    if "wms" in corpora or "wfs" in corpora:
        harvest_ogc({k: corpora.get(k) or Corpus(k, per_format) for k in ("wms", "wfs")}, rng, eps)
    for c in corpora.values():
        c.save()
    print("\n".join("%-11s %3d records from %d endpoints" % (f, len(c.files), len(c.sources))
                    for f, c in corpora.items()))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("stage", choices=["seed", "probe", "harvest", "all"])
    ap.add_argument("--sample", type=int, default=800, help="re3data repositories to sample")
    ap.add_argument("--per-format", type=int, default=50)
    ap.add_argument("--only", nargs="*", choices=list(OUT), help="formats to harvest")
    args = ap.parse_args()
    if args.stage in ("seed", "all"):
        stage_seed(args.sample)
    if args.stage in ("probe", "all"):
        stage_probe()
    if args.stage in ("harvest", "all"):
        stage_harvest(args.per_format, args.only)
    return 0


if __name__ == "__main__":
    sys.exit(main())

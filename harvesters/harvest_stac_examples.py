#!/usr/bin/env python3
"""
harvest_stac_examples.py - Harvest example STAC Collections from the public
catalogs listed in STAC Index (stacindex.org), into STAC/examples/ (one JSON
file per Collection, plus INDEX.json).

The catalogs are visited in a seeded random order. From each, up to PER_CATALOG
Collections are taken at random, so the examples come from as many catalogs as
possible:
  - a STAC API: its /collections list (the data link of its landing page);
  - a static catalog: its child links, followed up to MAX_DEPTH levels down,
    keeping the children whose type is Collection.
Each Collection is fetched from its own self link when it has one, since some
APIs trim the list entries.

Usage:
    python harvesters/harvest_stac_examples.py --target 50
"""

import argparse
import datetime as _dt
import json
import random
import re
import urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "STAC" / "examples"
SEED = 20261122
INDEX_URL = "https://stacindex.org/api/catalogs"
UA = {"User-Agent": "cdif-converters-example-harvester/0.1 (+https://github.com/Cross-Domain-Interoperability-Framework/converters)",
      "Accept": "application/json, application/geo+json;q=0.9"}
PER_CATALOG = 2
MAX_DEPTH = 2
MAX_BYTES = 1_000_000
TODAY = _dt.date.today().isoformat()


def get_json(url, timeout=40):
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    if len(r.content) > MAX_BYTES:
        raise ValueError("larger than %d bytes" % MAX_BYTES)
    return r.json(), r.url


def slug(text, n=60):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text or "").strip("_")[:n] or "x"


def link(doc, rel, base):
    for ln in doc.get("links") or []:
        if ln.get("rel") == rel and ln.get("href"):
            return urllib.parse.urljoin(base, ln["href"])
    return None


def api_collections(url, rng):
    landing, base = get_json(url)
    data = link(landing, "data", base) or base.rstrip("/") + "/collections"
    page, base = get_json(data)
    cols = [c for c in page.get("collections") or [] if c.get("id")]
    rng.shuffle(cols)
    return [(c, link(c, "self", base)) for c in cols]


def static_collections(url, rng):
    found, frontier = [], [(url, 0)]
    seen = set()
    while frontier and len(found) < PER_CATALOG * 3:
        href, depth = frontier.pop(0)
        if href in seen:
            continue
        seen.add(href)
        try:
            doc, base = get_json(href)
        except Exception:
            continue
        if doc.get("type") == "Collection" and doc.get("id"):
            found.append((doc, base))
            continue
        if depth >= MAX_DEPTH:
            continue
        kids = [urllib.parse.urljoin(base, ln["href"]) for ln in doc.get("links") or []
                if ln.get("rel") == "child" and ln.get("href")]
        rng.shuffle(kids)
        frontier.extend((k, depth + 1) for k in kids[:6])
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--target", type=int, default=50)
    args = ap.parse_args()
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    for p in OUT.glob("*.json"):
        p.unlink()
    catalogs = [c for c in requests.get(INDEX_URL, headers=UA, timeout=60).json()
                if not c.get("isPrivate") and c.get("url")]
    catalogs.sort(key=lambda c: c["url"])
    rng.shuffle(catalogs)
    index = []
    for cat in catalogs:
        if len(index) >= args.target:
            break
        kind = "stac-api" if cat.get("isApi") else "static"
        try:
            cands = (api_collections if cat.get("isApi") else static_collections)(cat["url"], rng)
        except Exception as exc:
            print("  %s: %s" % (cat["url"], str(exc)[:80]))
            continue
        got = 0
        for doc, self_url in cands:
            if got >= PER_CATALOG or len(index) >= args.target:
                break
            request = self_url
            if kind == "stac-api" and self_url:
                try:
                    doc, request = get_json(self_url)
                except Exception:
                    request = self_url
            if doc.get("type") != "Collection" or not doc.get("extent"):
                continue
            name = "%s-%s.json" % (slug(cat.get("slug") or cat["title"], 30), slug(doc["id"]))
            if any(e["file"] == name for e in index):
                continue
            (OUT / name).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="\n")
            index.append({"file": name, "format": "stac", "repository": cat.get("title"),
                          "endpoint": cat["url"], "request": request, "identifier": doc["id"],
                          "kind": kind, "stac_version": doc.get("stac_version"),
                          "retrieved": TODAY})
            got += 1
            print("  %s" % name)
    index.sort(key=lambda e: e["file"])
    (OUT / "INDEX.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="\n")
    print("%d collections from %d catalogs" % (len(index), len({e["endpoint"] for e in index})))


if __name__ == "__main__":
    main()

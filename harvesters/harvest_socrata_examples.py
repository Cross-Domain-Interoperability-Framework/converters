#!/usr/bin/env python3
"""
harvest_socrata_examples.py - Fetch example Socrata views for Socrata/examples/.

For each portal in PORTALS, asks the Socrata Discovery API for the portal's most
viewed assets of each kind in KINDS, and saves each one's native metadata
(https://{host}/api/views/{id}.json) as Socrata/examples/{host}-{id}.json.
Socrata/examples/INDEX.json records where each file came from and when.

A portal or a view that does not answer is skipped, with a note.

Usage:
    python harvesters/harvest_socrata_examples.py
"""

import datetime
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "Socrata" / "examples"
DISCOVERY = "https://api.us.socrata.com/api/catalog/v1"
# (Discovery API `only` value, how many per portal)
KINDS = [("datasets", 3), ("files", 1), ("links", 1)]
PORTALS = [
    ("data.cityofnewyork.us", "NYC Open Data"),
    ("data.cityofchicago.org", "Chicago Data Portal"),
    ("data.cdc.gov", "Centers for Disease Control and Prevention"),
    ("healthdata.gov", "HealthData.gov"),
    ("data.transportation.gov", "U.S. Department of Transportation"),
    ("data.wa.gov", "Washington State Open Data Portal"),
    ("data.ny.gov", "Open NY"),
    ("data.texas.gov", "Texas Open Data Portal"),
    ("data.ct.gov", "Connecticut Open Data"),
    ("data.seattle.gov", "City of Seattle Open Data"),
    ("data.austintexas.gov", "City of Austin Open Data"),
    ("data.montgomerycountymd.gov", "dataMontgomery"),
    ("www.datos.gov.co", "Datos Abiertos Colombia"),
    ("data.edmonton.ca", "City of Edmonton Open Data"),
]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "cdif-converters/0.1",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    index = []
    for host, name in PORTALS:
        for kind, count in KINDS:
            query = urllib.parse.urlencode({"domains": host, "search_context": host, "only": kind,
                                            "order": "page_views_total", "limit": count})
            try:
                results = get("%s?%s" % (DISCOVERY, query)).get("results", [])
            except Exception as exc:
                print("%s %s: %s" % (host, kind, exc), file=sys.stderr)
                continue
            for item in results:
                view_id = item["resource"]["id"]
                request = "https://%s/api/views/%s.json" % (host, view_id)
                try:
                    view = get(request)
                except Exception as exc:
                    print("%s: %s" % (request, exc), file=sys.stderr)
                    continue
                file = "%s-%s.json" % (host, view_id)
                (OUT / file).write_text(json.dumps(view, indent=1, ensure_ascii=False) + "\n",
                                        encoding="utf-8", newline="\n")
                index.append({"file": file, "request": request, "retrieved": today,
                              "repository": name, "endpoint": "https://%s/" % host})
                print(file)
    (OUT / "INDEX.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="\n")
    print("%d views written to %s" % (len(index), OUT))


if __name__ == "__main__":
    main()

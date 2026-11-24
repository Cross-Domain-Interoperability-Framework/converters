#!/usr/bin/env python3
"""
harvest_ckan_examples.py - Fetch example CKAN packages for CKAN/examples/.

For each portal in PORTALS, calls the CKAN Action API package_search and saves
each returned package (the `result` of package_show) as
CKAN/examples/{host}-{name}.json. CKAN/examples/INDEX.json records where each
file came from and when.

The portals are chosen for variety: a plain CKAN demo, two ckanext-dcat /
scheming portals that promote DCAT fields to the top level (bilingual Canada,
INSPIRE-style Ireland with a GeoJSON `spatial`), and a humanitarian portal.

A portal that does not answer, or whose API is behind a WAF, is skipped.

Usage:
    python harvesters/harvest_ckan_examples.py
"""

import datetime
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "CKAN" / "examples"
# (host, repository name, search query to vary the sample, how many)
PORTALS = [
    ("demo.ckan.org", "CKAN Demo", "", 4),
    ("open.canada.ca/data", "Open Government Canada", "spatial", 5),
    ("data.gov.ie", "data.gov.ie", "boundary", 5),
    ("data.humdata.org", "Humanitarian Data Exchange", "population", 4),
    ("ckan.govdata.de", "GovData Deutschland", "grenzen", 4),
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
    for host, name, query, count in PORTALS:
        base = "https://%s/api/3/action/package_search" % host
        q = urllib.parse.urlencode({"q": query, "rows": count})
        try:
            results = get("%s?%s" % (base, q)).get("result", {}).get("results", [])
        except Exception as exc:
            print("%s: %s" % (host, exc), file=sys.stderr)
            continue
        domain = host.split("/")[0]
        for pkg in results:
            slug = pkg.get("name") or pkg.get("id")
            if not slug:
                continue
            file = "%s-%s.json" % (domain, slug)
            (OUT / file).write_text(json.dumps(pkg, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="\n")
            index.append({"file": file, "request": "https://%s/api/3/action/package_show?id=%s"
                          % (host, slug), "host": domain, "retrieved": today,
                          "repository": name, "endpoint": "https://%s/" % host})
            print(file)
    (OUT / "INDEX.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="\n")
    print("%d packages written to %s" % (len(index), OUT))


if __name__ == "__main__":
    main()

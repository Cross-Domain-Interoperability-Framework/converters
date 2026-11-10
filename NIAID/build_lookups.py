"""Build the two lookup tables the converter reads, both keyed by lookup_key() in
harvest_niaid_to_cdif.py and matched exactly:

org_ror.json -- organization names -> ROR ids, for funders and organization creators
  the portal names without one:
  1. organizations in niaidExamples/ that carry a ROR id: their name and alternateNames;
  2. the ROR API (https://api.ror.org/v2), for the funder and creator organization
     names in niaidExamples/ that
     (1) leaves unmatched: a ROR record is taken only when one of its names equals ours.
  A name that leads to two ROR ids is dropped.

iso3166.json -- country names -> ISO 3166-1 alpha-3 codes (the portal's form), from the
  Unicode CLDR data in babel: each country's English name, its alpha-3 code itself, and
  ISO's own short names where CLDR's differ (ALIASES).

Rerun after adding examples (needs network access and `pip install babel`):

    python NIAID/build_lookups.py
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from harvest_niaid_to_cdif import lookup_key  # noqa: E402

EXAMPLES = HERE / "niaidExamples"

# ISO 3166 short names (and one common form) that CLDR's English names don't match.
ALIASES = {"Korea, Republic of": "KOR", "United States of America": "USA",
           "Russian Federation": "RUS", "Iran, Islamic Republic of": "IRN",
           "Viet Nam": "VNM", "Tanzania, United Republic of": "TZA"}


def as_list(v):
    return v if isinstance(v, list) else [] if v is None else [v]


def walk(node, visit):
    if isinstance(node, dict):
        visit(node)
        for value in node.values():
            walk(value, visit)
    elif isinstance(node, list):
        for value in node:
            walk(value, visit)


def ror_of(node):
    ids = [i for i in as_list(node.get("identifier")) if isinstance(i, str) and "ror.org/" in i]
    return "https://ror.org/" + ids[0].rstrip("/").rsplit("/", 1)[-1] if len(ids) == 1 else None


def ror_api(name):
    url = "https://api.ror.org/v2/organizations?query=" + urllib.parse.quote(name)
    request = urllib.request.Request(url, headers={"User-Agent": "cdif-converters"})
    with urllib.request.urlopen(request, timeout=30) as resp:
        items = json.load(resp).get("items", [])
    hits = {item["id"] for item in items
            if lookup_key(name) in {lookup_key(n["value"]) for n in item.get("names", [])}}
    return hits.pop() if len(hits) == 1 else None


def funder_table():
    found, funders = defaultdict(set), set()

    def visit(node):
        ror = ror_of(node)
        if ror:
            for name in [node.get("name")] + as_list(node.get("alternateName")):
                if isinstance(name, str):
                    found[lookup_key(name)].add(ror)
        orgs = as_list(node.get("funder")) + [a for k in ("creator", "author") for a in as_list(node.get(k))
                                             if isinstance(a, dict) and a.get("@type") == "Organization"]
        for org in orgs:
            if isinstance(org, dict) and not ror_of(org) and isinstance(org.get("name"), str):
                funders.add(org["name"])

    for path in sorted(EXAMPLES.glob("*.json")):
        if path.name != "INDEX.json":
            walk(json.loads(path.read_text(encoding="utf-8")), visit)
    for name in sorted(funders):
        if lookup_key(name) not in found and len(name) <= 120:
            ror = ror_api(name)
            if ror:
                found[lookup_key(name)].add(ror)
            time.sleep(0.5)
    return {key: rors.pop() for key, rors in sorted(found.items()) if key and len(rors) == 1}


def country_table():
    from babel import Locale
    from babel.core import get_global
    alpha3 = {two[0]: three for three, two in get_global("territory_aliases").items()
              if len(three) == 3 and three.isalpha() and len(two) == 1}
    table = {}
    for two, name in Locale("en").territories.items():
        if two in alpha3:
            table[lookup_key(name)] = alpha3[two]
            table[lookup_key(alpha3[two])] = alpha3[two]
    for name, code in ALIASES.items():
        table[lookup_key(name)] = code
    return dict(sorted(table.items()))


def write(name, table):
    (HERE / name).write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n",
                             encoding="utf-8", newline="\n")
    print(f"{name}: {len(table)} names")


if __name__ == "__main__":
    write("iso3166.json", country_table())
    write("org_ror.json", funder_table())

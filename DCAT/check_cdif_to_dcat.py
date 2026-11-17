#!/usr/bin/env python3
"""Round-trip check for cdif_to_dcat.py: CDIF -> DCAT -> CDIF.

Each CDIF record is converted to DCAT by cdif_to_dcat.py, parsed as RDF, and
converted back by dcat_to_cdif.py. Key fields of the original are compared with
the round trip; a field that does not survive points at a gap in one of the two
mapping tables (cdif-to-dcat.sssom.tsv, dcat-to-cdif.sssom.tsv).

    python DCAT/check_cdif_to_dcat.py                     # NIAID and DCAT corpora
    python DCAT/check_cdif_to_dcat.py path/to/cdif/ --show 5

Exits 1 if any record fails the structure check or a field survives in under
--min-rate of the records that have it (default 0.9).
"""
import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cdif_to_dcat as c2d  # noqa: E402
import dcat_to_cdif as d2c  # noqa: E402

DEFAULT = [HERE.parent / "NIAID" / "cdifJSON-LD", HERE / "cdifOK"]
as_list = c2d.as_list


def root(doc):
    return c2d.Converter(doc).root


def texts(v):
    out = set()
    for x in as_list(v.get("@list") if isinstance(v, dict) and "@list" in v else v):
        t = x if isinstance(x, str) else c2d.text_of(x) if isinstance(x, dict) else None
        if isinstance(t, str) and t.strip():
            out.add(t.strip())
    return out


def urls(v):
    out = set()
    for x in as_list(v):
        if isinstance(x, str):
            out.add(x)
        elif isinstance(x, dict):
            for k in ("schema:url", "@id", "schema:contentUrl"):
                if isinstance(x.get(k), str) and c2d.is_iri(x[k]):
                    out.add(x[k])
                    break
    return {u for u in out if u != c2d.OGC_NIL}


def dates(v):
    return {str(x)[:10] for x in as_list(v) if isinstance(x, str) and not x.startswith(c2d.DATE_SENTINEL)}


def creators(r):
    v = r.get("schema:creator")
    items = v.get("@list") if isinstance(v, dict) and "@list" in v else as_list(v)
    return {a.get("schema:name") for a in items if isinstance(a, dict) and a.get("schema:name")}


FIELDS = {
    "name": lambda r: texts(r.get("schema:name")),
    "description": lambda r: {t[:200] for t in texts(r.get("schema:description"))},
    "identifier": lambda r: {c2d.identifier_text(r.get("schema:identifier"))} - {None},
    "url": lambda r: urls(r.get("schema:url")),
    "datePublished": lambda r: dates(r.get("schema:datePublished")),
    "dateModified": lambda r: dates(r.get("schema:dateModified")),
    "license": lambda r: urls(r.get("schema:license")),
    "keywords": lambda r: texts(r.get("schema:keywords")),
    "creator": creators,
    "distribution": lambda r: {u for d in as_list(r.get("schema:distribution")) if isinstance(d, dict)
                               for u in urls(d.get("schema:contentUrl"))},
}


def round_trip(doc):
    dcat = c2d.convert(doc)
    n, problems = c2d.check(dcat)
    graph = d2c.build_node_index(dcat)
    datasets = [d for d in d2c.find_datasets(dcat)]
    if not datasets:
        return dcat, None, problems + ["dcat_to_cdif found no dcat:Dataset"]
    with redirect_stdout(io.StringIO()):
        back = d2c.convert_dcat_to_cdif(datasets[0], detect=False, graph=graph)
    return dcat, back, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="*", default=[str(p) for p in DEFAULT])
    ap.add_argument("--show", type=int, default=3, help="Examples of each lost field to print")
    ap.add_argument("--min-rate", type=float, default=0.9)
    args = ap.parse_args()
    files = [f for p in map(Path, args.inputs)
             for f in (sorted(p.rglob("*.jsonld")) if p.is_dir() else [p]) if f.name != "INDEX.json"]
    have = {k: 0 for k in FIELDS}
    kept = {k: 0 for k in FIELDS}
    lost = {k: [] for k in FIELDS}
    broken = []
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        try:
            _dcat, back, problems = round_trip(doc)
        except Exception as e:
            broken.append((f, f"{type(e).__name__}: {e}"))
            continue
        if problems or back is None:
            broken.append((f, "; ".join(problems)))
            continue
        orig = root(doc)
        for k, get in FIELDS.items():
            a, b = get(orig), get(back)
            if not a:
                continue
            have[k] += 1
            if a <= b:
                kept[k] += 1
            else:
                lost[k].append((f.name, sorted(a - b)[:2]))
    print(f"{len(files)} records; {len(files) - len(broken)} round-tripped\n")
    print(f"{'field':14s} {'records':>8s} {'kept':>6s}  rate")
    worst = 1.0
    for k in FIELDS:
        rate = kept[k] / have[k] if have[k] else 1.0
        worst = min(worst, rate)
        print(f"{k:14s} {have[k]:8d} {kept[k]:6d}  {rate:5.1%}")
    for k in FIELDS:
        for name, missing in lost[k][: args.show]:
            print(f"  lost {k}: {name}: {missing}")
    for f, why in broken[: args.show]:
        print(f"  BROKEN {f.name}: {why[:200]}")
    return 1 if broken or worst < args.min_rate else 0


if __name__ == "__main__":
    sys.exit(main())

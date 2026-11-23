#!/usr/bin/env python3
"""
build_iso_examples.py - Convert a sample of this repository's CDIF records to
ISO 19115-3 with cdif_to_iso.py, validate them, and convert them back.

From each CDIF corpus (SAMPLE_FROM below) it takes PER_CORPUS records spread
evenly over the sorted file list, and writes each one's ISO record to
isoFromCDIF/<corpus>/. Each output is:

  * validated against the ISO 19115-3 XML Schema: schema.xsd from the
    GeoNetwork checkout beside this repository (the iso19115-3.2018 schema
    plugin), or the --xsd given; without one the check reports "skipped";
  * converted back to CDIF by iso_to_cdif.py, and compared with the source
    record: for each property in COMPARED, whether a source record that has it
    still has it after the round trip.

isoFromCDIF/INDEX.json records both per record; the summary prints the
round-trip counts per property. The output is deterministic.

Usage:
    python ISO19115/build_iso_examples.py
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import cdif_to_iso as c2i  # noqa: E402
import iso_to_cdif as i2c  # noqa: E402

OUT = HERE / "isoFromCDIF"
PER_CORPUS = 6
SAMPLE_FROM = ["DataCite/cdifJSON-LD", "DublinCore/cdifJSON-LD", "EML/cdifJSON-LD",
               "FGDC/cdifJSON-LD", "ISO19115/cdifJSON-LD", "NIAID/cdifJSON-LD",
               "OGCServices/cdifJSON-LD", "SDMX/cdifJSON-LD", "STAC/cdifJSON-LD", "DCAT/cdifOK"]
COMPARED = ["schema:name", "schema:description", "schema:identifier", "schema:datePublished",
            "schema:dateModified", "schema:version", "schema:creator", "schema:contributor",
            "schema:publisher", "schema:keywords", "schema:license", "schema:conditionsOfAccess",
            "schema:spatialCoverage", "schema:temporalCoverage", "schema:distribution",
            "schema:variableMeasured", "schema:relatedLink", "schema:funding",
            "prov:wasGeneratedBy", "prov:wasDerivedFrom"]


def sample(folder):
    files = sorted(p for p in (ROOT / folder).rglob("*.json*") if p.name != "INDEX.json")
    if len(files) <= PER_CORPUS:
        return files
    step = len(files) / PER_CORPUS
    return [files[int(i * step)] for i in range(PER_CORPUS)]


def has(doc, key):
    v = doc.get(key)
    if isinstance(v, dict) and "@list" in v:
        v = v["@list"]
    if key == "schema:dateModified" and isinstance(v, str) and v.startswith(c2i.DATE_SENTINEL):
        return False
    if key == "schema:license" and v == [{"@id": c2i.NIL}]:
        return False
    return v not in (None, "", [], {})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--xsd", help="ISO 19115-3 schema.xsd (default: the GeoNetwork checkout beside this repository)")
    args = ap.parse_args()
    warnings.filterwarnings("ignore")
    xsd = c2i.load_xsd(args.xsd or c2i.DEFAULT_XSD)
    rows = c2i.load_table()
    back_rows = i2c.load_table(i2c.TABLES["iso19115-3"])
    index, valid = [], 0
    kept = {k: [0, 0] for k in COMPARED}
    for folder in SAMPLE_FROM:
        corpus = folder.split("/")[0]
        out_dir = OUT / corpus
        out_dir.mkdir(parents=True, exist_ok=True)
        for src in sample(folder):
            record = json.loads(src.read_text(encoding="utf-8"))
            md, _ = c2i.convert_cdif_to_iso(record, rows)
            data = c2i.serialize(md)
            name = src.stem + ".xml"
            (out_dir / name).write_bytes(data)
            entry = {"file": corpus + "/" + name, "source": str(src.relative_to(ROOT)).replace("\\", "/")}
            xml = etree.fromstring(data)
            if xsd is None:
                entry["xsd"] = "skipped"
            elif xsd.validate(xml):
                entry["xsd"] = "valid"
                valid += 1
            else:
                entry["xsd"] = "invalid"
                entry["xsd_errors"] = sorted({str(e).split(":", 5)[-1].strip()[:200] for e in xsd.error_log})[:5]
            back, _ = i2c.convert_iso_to_cdif(xml, back_rows, detect=False)
            src_doc = c2i.Doc(record).root
            lost = []
            for key in COMPARED:
                if has(src_doc, key):
                    kept[key][1] += 1
                    if has(back, key):
                        kept[key][0] += 1
                    else:
                        lost.append(key)
            entry["round_trip_lost"] = lost
            index.append(entry)
    (OUT / "INDEX.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="\n")
    print("%d ISO records written to %s; %s" % (
        len(index), OUT, "XSD not found, not validated" if xsd is None
        else "%d valid against the ISO 19115-3 XML Schema" % valid))
    for e in index:
        if e["xsd"] == "invalid":
            print("  %s: %s" % (e["file"], e["xsd_errors"][0]))
    print("round trip CDIF -> ISO -> CDIF (records that keep the property / records that have it):")
    for key, (k, n) in kept.items():
        if n:
            print("  %-26s %3d / %d" % (key, k, n))


if __name__ == "__main__":
    main()

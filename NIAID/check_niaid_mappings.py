#!/usr/bin/env python3
"""Check mappings/niaid-to-cdif.sssom.tsv against the NIAID converter.

harvest_niaid_to_cdif.py reads the table at run time, so the table cannot drift
from what the converter does the way a documenting table can. What can go wrong
is the table itself, or a record the table has no row for. This fails loudly on:

  shape      a row without exactly the header's columns
  transform  a transform the converter does not define
  profile    a profile other than cdifCore, cdifDiscovery or cdifNIAID
  prefixes   a prefix in subject_id, object_id or subject_class that the
             sidecar's curie_map does not declare
  coverage   a name in the committed examples (niaidExamples/) that the table
             has no row for, which the converter keeps under cdifniaid: with a
             note: every such name needs a row
  convert    a committed example that fails to convert

Standard library only, with conformance detection off.

    python NIAID/check_niaid_mappings.py
"""

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TABLE = os.path.join(ROOT, "mappings", "niaid-to-cdif.sssom.tsv")
SIDECAR = os.path.join(ROOT, "mappings", "niaid-to-cdif.sssom.yml")
sys.path.insert(0, HERE)
import harvest_niaid_to_cdif as converter  # noqa: E402

converter._HAVE_DETECT = False
PROFILES = {"", "cdifCore", "cdifDiscovery", "cdifNIAID"}
CURIE_COLUMNS = ("subject_id", "object_id", "subject_class")


def main():
    problems = []
    with open(TABLE, encoding="utf-8") as fh:
        lines = fh.read().replace("\r\n", "\n").strip("\n").split("\n")
    width = len(lines[0].split("\t"))
    for n, line in enumerate(lines[1:], start=2):
        if len(line.split("\t")) != width:
            problems.append(f"shape: line {n} has {len(line.split(chr(9)))} columns, the header {width}")
    rows = converter.load_table(TABLE)

    for row in rows:
        if row["transform"] not in converter.SHAPERS:
            problems.append(f"transform: {row['subject_label']} on {row['subject_class']} names "
                            f"'{row['transform']}', which the converter does not define")
        if row.get("profile", "") not in PROFILES:
            problems.append(f"profile: {row['subject_label']} on {row['subject_class']} has '{row['profile']}'")

    with open(SIDECAR, encoding="utf-8") as fh:
        sidecar = fh.read()
    curie_map = sidecar.split("curie_map:", 1)[1].split("\n\n", 1)[0]
    declared = set(re.findall(r"^  ([A-Za-z][\w.-]*):", curie_map, re.M))
    used = {row[c].split(":", 1)[0] for row in rows for c in CURIE_COLUMNS
            if ":" in row[c] and not row[c].startswith("@")}
    for prefix in sorted(used - declared):
        problems.append(f"prefixes: '{prefix}' is used but not in the sidecar's curie_map")

    examples = sorted(p for p in glob.glob(os.path.join(HERE, "niaidExamples", "*.json"))
                      if not p.endswith("INDEX.json"))
    uncovered = {}
    for path in examples:
        with open(path, encoding="utf-8") as fh:
            hit = json.load(fh)
        try:
            _doc, notes = converter.convert_record(hit)
        except Exception as exc:
            problems.append(f"convert: {os.path.basename(path)}: {type(exc).__name__}: {exc}")
            continue
        for note in notes:
            if "is not in the mapping table" in note:
                uncovered.setdefault(note.split()[0], os.path.basename(path))
    for name, where in sorted(uncovered.items()):
        problems.append(f"coverage: '{name}' (e.g. in {where}) has no row")

    print(f"{len(rows)} rows, {len(examples)} examples converted")
    for p in problems:
        print("  " + p)
    if problems:
        print(f"{len(problems)} problem(s)")
        return 1
    print("all checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())

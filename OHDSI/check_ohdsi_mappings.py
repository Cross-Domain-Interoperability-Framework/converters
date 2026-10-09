#!/usr/bin/env python3
"""Check mappings/ohdsi-to-cdif.sssom.tsv against the OHDSI converter.

harvest_ohdsi_to_cdif.py is hand-coded and does not read the table, so the
table documents the converter and can drift from it silently. This converts
the corpus in OHDSIMetadata/ and fails loudly when they disagree:

  source    every property on a source Dataset, and on the ETL potentialAction
            the converter uses, is named by a row (or is a JSON-LD keyword)
  target    whenever a source record has a property, the output carries that
            row's target -- except for the transforms that legitimately drop
            empty or unusable values (CONDITIONAL), which must still produce
            their target in at least one record
  output    every top-level output property is a row's target or one of the
            synthesised fields the sidecar documents (SYNTHESISED)

Standard library only.

    python OHDSI/check_ohdsi_mappings.py
"""

import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TABLE = os.path.join(ROOT, "mappings", "ohdsi-to-cdif.sssom.tsv")
sys.path.insert(0, HERE)
import harvest_ohdsi_to_cdif as converter  # noqa: E402

KEYWORDS = {"@id", "@type", "@context", "@language"}
# transforms that may emit nothing for a record that has the source property
CONDITIONAL = {"variable", "datadownload", "idref", "etl"}
# output properties no row maps (the sidecar's "Structural" and "Synthesised")
SYNTHESISED = {"@context", "@id", "@type", "schema:identifier",
               "schema:subjectOf", "schema:inLanguage"}


def load_rows():
    with open(TABLE, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def leaf(path, head):
    """$.prov:wasGeneratedBy[*].schema:name -> ["schema:name"] under head."""
    body = path[2:] if path.startswith("$.") else path
    if not body.startswith(head):
        return None
    rest = body[len(head):].lstrip("[*]").lstrip(".")
    return [k.replace("[*]", "") for k in rest.split(".")] if rest else []


def has_path(node, keys):
    for k in keys:
        if isinstance(node, list):
            node = node[0] if node else None
        if not isinstance(node, dict) or k not in node:
            return False
        node = node[k]
    return node not in (None, "", [], {})


def main():
    rows = load_rows()
    ds_rows = [r for r in rows if r["subject_class"] == "sc:Dataset"]
    act_rows = [r for r in rows if r["subject_class"] == "sc:Action"]
    ds_subjects = {r["subject_id"] for r in ds_rows}
    act_subjects = {r["subject_id"] for r in act_rows}
    targets = {r["object_json_path"][2:].split("[")[0].split(".")[0]
               for r in ds_rows if r["object_json_path"]}

    problems, exercised = [], set()
    files = sorted(glob.glob(os.path.join(HERE, "OHDSIMetadata", "*.json")))
    for path in files:
        name = os.path.basename(path)
        with open(path, encoding="utf-8") as fh:
            src = json.load(fh)
        out = converter.convert_document(src, name, "2000-01-01", provenance=True)

        # source: every property is named
        for key in src:
            if key not in KEYWORDS and "sc:" + key not in ds_subjects:
                problems.append("%s: source property %r has no row" % (name, key))
        action = converter.find_etl_action(src)
        if action:
            for key in action:
                if key not in KEYWORDS and "sc:" + key not in act_subjects:
                    problems.append("%s: ETL action property %r has no row" % (name, key))

        # target: each present source property reaches its target
        for r in ds_rows:
            key = r["subject_id"][3:]
            if not src.get(key):
                continue
            tgt = r["object_json_path"][2:]
            if tgt in out:
                exercised.add(r["subject_id"])
            elif r["transform"] not in CONDITIONAL:
                problems.append("%s: %s present but %s missing from output"
                                % (name, r["subject_id"], tgt))
        activities = out.get("prov:wasGeneratedBy") or []
        if action and activities:
            for r in act_rows:
                key = r["subject_id"][3:]
                keys = leaf(r["object_json_path"], "prov:wasGeneratedBy")
                if action.get(key) and not has_path(activities[0], keys):
                    problems.append("%s: action %s present but %s missing"
                                    % (name, r["subject_id"], r["object_json_path"]))

        # output: every property is accounted for
        for key in out:
            if key not in targets and key not in SYNTHESISED:
                problems.append("%s: output property %r is in no row" % (name, key))

    for r in ds_rows:
        if r["transform"] in CONDITIONAL and r["subject_id"] not in exercised:
            problems.append("row %s (%s) never produced its target in the corpus"
                            % (r["subject_id"], r["transform"]))

    print("checked %d record(s) against %d row(s)" % (len(files), len(rows)))
    for p in problems:
        print("  DRIFT  " + p)
    print("OK" if not problems else "%d problem(s)" % len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

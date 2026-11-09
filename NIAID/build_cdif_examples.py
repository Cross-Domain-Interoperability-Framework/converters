#!/usr/bin/env python3
"""build_cdif_examples.py - convert niaidExamples/ to CDIF JSON-LD in cdifJSON-LD/.

Each record in niaidExamples/ is converted by harvest_niaid_to_cdif.convert_record,
which writes the record's conformance claims: the CDIF profiles detected from its
content, plus the cdifNIAID profile (https://w3id.org/cdif/niaid/1.0). Each output is
then framed and validated against the JSON Schema of every profile it claims, read
from the metadataBuildingBlocks checkout beside this repository: a profile's
resolvedSchema.json in _sources/profiles/, found by the conformsTo URI in its
conformance.json. For cdifNIAID that is
_sources/profiles/cdifCompositeProfile/cdifNIAID/resolvedSchema.json (cdifCore +
niaidDataset). Each profile is also checked with SHACL: the rules of every block its
schema references, merged by metadataBuildingBlocks' tools/validate_shacl.py
--emit-shapes (the bundle a release repo publishes as its *Rules.shacl). Only
sh:Violation results fail a record; warnings and info do not. The w3id copies are not
used, as cdifNIAID is not published there yet.

Writes cdifJSON-LD/<example name>.jsonld and cdifJSON-LD/INDEX.json, which lists
each record's claims and the result for each. Run from anywhere:
    python NIAID/build_cdif_examples.py
"""
import json
import re
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "validation")]
import harvest_niaid_to_cdif as h  # noqa: E402
import ConformanceValidate as cv  # noqa: E402

SOURCES = HERE / "niaidExamples"
OUT = HERE / "cdifJSON-LD"
BB = ROOT.parent / "metadataBuildingBlocks" / "_sources"
FRAME = ROOT / "validation" / "CDIF-frame-2026.jsonld"
BUILD = "2026-10-28"  # the index build niaidExamples/ was fetched from (its README)


class BuildingBlockResolver:
    """conformsTo URI -> the profile's resolvedSchema.json and SHACL bundle in the
    local _sources."""

    def __init__(self, bb):
        self.bb = bb
        self.profiles = {}
        self.bundles = {}
        for decl in bb.glob("profiles/*/*/conformance.json"):
            uri = json.loads(decl.read_text(encoding="utf-8"))["conformsTo"]
            self.profiles[cv.normalize_uri(uri)] = decl.parent

    def schema(self, uri):
        profile = self.profiles.get(cv.normalize_uri(uri))
        if not profile:
            return None
        return json.loads((profile / "resolvedSchema.json").read_text(encoding="utf-8"))

    def shacl(self, uri):
        profile = self.profiles.get(cv.normalize_uri(uri))
        if not profile:
            return None
        if profile.name not in self.bundles:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "rules.ttl"
                subprocess.run([sys.executable, str(self.bb.parent / "tools" / "validate_shacl.py"),
                                profile.name, "--emit-shapes", str(out)],
                               check=True, capture_output=True, cwd=self.bb.parent)
                self.bundles[profile.name] = out.read_text(encoding="utf-8")
        return self.bundles[profile.name]


def error_text(error):
    """A JSON Schema error (path, message) or a SHACL result (Result Path, Message)."""
    where = error.get("path") or error.get("Result Path") or ""
    message = str(error.get("message") or error.get("Message"))[:200]
    value = error.get("Value")
    return (f"{where}: " if where else "") + message + (f" (value: {str(value)[:80]})" if value else "")


def profile_name(uri):
    return re.sub(r"^https://w3id\.org/cdif/", "", uri)


def main():
    warnings.filterwarnings("ignore")
    if not BB.is_dir():
        sys.exit(f"metadataBuildingBlocks not found at {BB}")
    resolver = BuildingBlockResolver(BB)
    frame = json.loads(FRAME.read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    index, passed = [], 0
    for src in sorted(SOURCES.glob("*.json")):
        record = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or "@type" not in record:
            continue  # INDEX.json
        doc, _ = h.convert_record(record, BUILD)
        name = src.stem + ".jsonld"
        (OUT / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8", newline="\n")
        result = cv.run_conformance(doc, resolver, do_schema=True, do_shacl=True, frame=frame)
        profiles = {}
        for p in result["profiles"]:
            entry = {}
            for check in ("schema", "shacl"):
                res = p.get(check) or {}
                entry[check] = {"status": res.get("status")}
                if res.get("errors"):
                    # sorted: pyshacl reports results in no fixed order
                    entry[check]["errors"] = sorted(error_text(e) for e in res["errors"])[:5]
            profiles[profile_name(p["uri"])] = entry
        ok = bool(profiles) and all(c["status"] == "passed"
                                    for p in profiles.values() for c in p.values())
        passed += ok
        index.append({"file": name, "source": src.name, "@id": doc.get("@id"),
                      "passed": ok, "profiles": profiles})
    (OUT / "INDEX.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="\n")
    print(f"{len(index)} records written to {OUT}; {passed} pass every claimed profile "
          "(JSON Schema and SHACL)")
    for entry in index:
        if not entry["passed"]:
            bad = [f"{k} {check}: {res.get('errors', [res['status']])[0]}"
                   for k, v in entry["profiles"].items() for check, res in v.items()
                   if res["status"] != "passed"]
            print(f"  {entry['file']}: {'; '.join(bad)[:220]}")


if __name__ == "__main__":
    main()

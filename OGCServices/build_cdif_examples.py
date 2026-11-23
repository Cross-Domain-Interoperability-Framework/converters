#!/usr/bin/env python3
"""
build_cdif_examples.py - Convert OGCServices/examples/ to CDIF and validate them.

Converts every capabilities document in examples/wms/ and examples/wfs/ with
ogc_services_to_cdif.py, writes one CDIF record per service to
cdifJSON-LD/<service>/, and checks each against every profile it claims: the
profile's resolvedSchema.json and its SHACL bundle, both from the
metadataBuildingBlocks checkout beside this repository (the same check as
NIAID/build_cdif_examples.py, whose resolver it reuses).

Each folder's INDEX.json gives a document's source: its request URL is the
record's @id (the GetCapabilities URL), its retrieval date dates the catalog
record, and the catalogue whose service records linked to it is the catalog.
cdifJSON-LD/INDEX.json records the claims and the results.

Usage:
    python OGCServices/build_cdif_examples.py
"""

import importlib.util
import json
import re
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "NIAID"))       # NIAID's build imports its converter
sys.path.insert(0, str(ROOT / "validation"))

import ogc_services_to_cdif as ogc  # noqa: E402
import ConformanceValidate as cv  # noqa: E402

# NIAID's build has the same module name as this one, so load it by path.
_spec = importlib.util.spec_from_file_location("niaid_build", ROOT / "NIAID" / "build_cdif_examples.py")
_niaid = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_niaid)
BB, FRAME, BuildingBlockResolver = _niaid.BB, _niaid.FRAME, _niaid.BuildingBlockResolver
error_text, profile_name = _niaid.error_text, _niaid.profile_name

EXAMPLES = HERE / "examples"
OUT = HERE / "cdifJSON-LD"


def main():
    warnings.filterwarnings("ignore")
    if not BB.is_dir():
        sys.exit(f"metadataBuildingBlocks not found at {BB}")
    resolver = BuildingBlockResolver(BB)
    frame = json.loads(FRAME.read_text(encoding="utf-8"))
    rows = ogc.load_table()
    index, passed = [], 0
    for encoding in ("wms", "wfs"):
        folder = EXAMPLES / encoding
        sources = {e["file"]: e for e in
                   json.loads((folder / "INDEX.json").read_text(encoding="utf-8"))}
        out_dir = OUT / encoding
        out_dir.mkdir(parents=True, exist_ok=True)
        for src in sorted(folder.glob("*.xml")):
            meta = sources.get(src.name, {})
            m = re.match(r"^linked from service records of (.+)$", meta.get("kind", ""))
            catalog = {"@type": ["schema:DataCatalog"], "schema:name": m.group(1)} if m else None
            root = ogc.load_xml(str(src))
            if ogc.kind_of(root) is None:
                index.append({"file": None, "source": encoding + "/" + src.name, "passed": False,
                              "error": "not a WMS or WFS capabilities document"})
                continue
            doc, _ = ogc.convert_capabilities_to_cdif(root, rows, catalog=catalog,
                                                      capabilities=meta.get("request"),
                                                      retrieved=meta.get("retrieved"))
            name = src.stem + ".jsonld"
            (out_dir / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8", newline="\n")
            result = cv.run_conformance(doc, resolver, do_schema=True, do_shacl=True, frame=frame)
            profiles = {}
            for p in result["profiles"]:
                entry = {}
                for check in ("schema", "shacl"):
                    res = p.get(check) or {}
                    entry[check] = {"status": res.get("status")}
                    if res.get("errors"):
                        entry[check]["errors"] = sorted(error_text(e) for e in res["errors"])[:5]
                profiles[profile_name(p["uri"])] = entry
            ok = bool(profiles) and all(c["status"] == "passed"
                                        for p in profiles.values() for c in p.values())
            passed += ok
            index.append({"file": encoding + "/" + name, "source": encoding + "/" + src.name,
                          "@id": doc.get("@id"), "passed": ok, "profiles": profiles})
    (OUT / "INDEX.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="\n")
    print(f"{len(index)} records written to {OUT}; {passed} pass every claimed profile "
          "(JSON Schema and SHACL)")
    for entry in index:
        if not entry["passed"]:
            if "error" in entry:
                print(f"  {entry['source']}: {entry['error']}")
                continue
            bad = [f"{k} {check}: {res.get('errors', [res['status']])[0]}"
                   for k, v in entry["profiles"].items() for check, res in v.items()
                   if res["status"] != "passed"]
            print(f"  {entry['file']}: {'; '.join(bad)[:400]}")


if __name__ == "__main__":
    main()

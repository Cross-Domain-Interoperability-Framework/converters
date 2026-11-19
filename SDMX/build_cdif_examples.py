#!/usr/bin/env python3
"""
build_cdif_examples.py - Convert SDMX/examples/ to CDIF and validate them.

Converts every structure message in examples/ with sdmx_to_cdif.py, writes one
record per dataflow to cdifJSON-LD/, and checks each against every profile it
claims: the profile's resolvedSchema.json and its SHACL bundle, both from the
metadataBuildingBlocks checkout beside this repository (the same check as
NIAID/build_cdif_examples.py, whose resolver it reuses).

examples/INDEX.json gives each message's SDMX-REST endpoint and request. The
endpoint makes the record's addresses; a structure-only registry (REGISTRIES)
gets no data distribution; a request with detail=referencepartial means the
codelists hold only the codes the dataflow uses. cdifJSON-LD/INDEX.json
records the claims and the results.

Usage:
    python SDMX/build_cdif_examples.py
"""

import importlib.util
import json
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "NIAID"))       # NIAID's build imports its converter
sys.path.insert(0, str(ROOT / "validation"))

import sdmx_to_cdif as sdmx  # noqa: E402
import ConformanceValidate as cv  # noqa: E402

# NIAID's build has the same module name as this one, so load it by path.
_spec = importlib.util.spec_from_file_location("niaid_build", ROOT / "NIAID" / "build_cdif_examples.py")
_niaid = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_niaid)
BB, FRAME, BuildingBlockResolver = _niaid.BB, _niaid.FRAME, _niaid.BuildingBlockResolver
error_text, profile_name = _niaid.error_text, _niaid.profile_name

EXAMPLES = HERE / "examples"
OUT = HERE / "cdifJSON-LD"
# SDMX-REST services that hold structures but no data
REGISTRIES = ("registry.sdmx.org", "sdmxcentral.imf.org")


def main():
    warnings.filterwarnings("ignore")
    if not BB.is_dir():
        sys.exit(f"metadataBuildingBlocks not found at {BB}")
    resolver = BuildingBlockResolver(BB)
    frame = json.loads(FRAME.read_text(encoding="utf-8"))
    rows = sdmx.load_table()
    sources = {e["file"]: e for e in json.loads((EXAMPLES / "INDEX.json").read_text(encoding="utf-8"))}
    OUT.mkdir(parents=True, exist_ok=True)
    index, passed = [], 0
    for src in sorted(EXAMPLES.glob("*.xml")):
        meta = sources.get(src.name, {})
        endpoint = meta.get("endpoint")
        has_data = not any(host in (endpoint or "") for host in REGISTRIES)
        partial = "detail=referencepartial" in meta.get("request", "")
        root = sdmx.load_xml(str(src))
        flows = sdmx.dataflows(root) if sdmx.is_structure_message(root) else []
        if not flows:
            index.append({"file": None, "source": src.name, "passed": False,
                          "error": "no dataflow in an SDMX-ML 2.1 structure message"})
            continue
        msg = sdmx.Message(root)
        # the message answers a query for one dataflow; others it holds are references
        wanted = meta.get("identifier", "")
        df = next((f for f in flows if "%s-%s-%s" % (f.get("agencyID"), f.get("id"),
                                                     f.get("version")) == wanted), flows[0])
        doc, _ = sdmx.convert_sdmx_to_cdif(df, msg, rows, endpoint, has_data, partial)
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
            if "error" in entry:
                print(f"  {entry['source']}: {entry['error']}")
                continue
            bad = [f"{k} {check}: {res.get('errors', [res['status']])[0]}"
                   for k, v in entry["profiles"].items() for check, res in v.items()
                   if res["status"] != "passed"]
            print(f"  {entry['file']}: {'; '.join(bad)[:400]}")


if __name__ == "__main__":
    main()

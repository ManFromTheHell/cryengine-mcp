#!/usr/bin/env python3
"""Self-test of object_catalog.py against the catalog the editor wrote (engine_data/object_catalog.json). Editor-free, about 1 s. (The capture itself is live: python catalogs/object_catalog.py --capture.)

    test_object_catalog.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import object_catalog as C

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-110s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))


def main():
    rows = {r["type"]: r for r in C.load()}
    made = [r for r in rows.values() if "attributes" in r]
    check("all 36 of the editor's object types are in the catalog, 35 with their properties and 1 (Entity) skipped with the reason", len(rows) == 36 and len(made) == 35 and "skipped" in rows["Entity"], (len(rows), len(made)))
    check("no type failed to be created from a script", not [t for t, r in rows.items() if "error" in r], [t for t, r in rows.items() if "error" in r])
    check("the engine's defaults are there (Road Width 4 and StepSize 4, River FogDensity 0.5, VisArea Height 5, WaterVolume VolumeDepth 10 and PhysDensity 1000)",
          rows["Road"]["attributes"]["Width"] == "4" and rows["Road"]["attributes"]["StepSize"] == "4" and rows["River"]["attributes"]["FogDensity"] == "0.5" and rows["VisArea"]["attributes"]["Height"] == "5"
          and rows["WaterVolume"]["attributes"]["VolumeDepth"] == "10" and rows["WaterVolume"]["attributes"]["PhysDensity"] == "1000", "")
    check("a Portal is a VisArea with more (UseDeepness, DoubleSide, LightBlending) and a WaterVolume has its Advanced child", set(rows["VisArea"]["attributes"]) < set(rows["Portal"]["attributes"]) and {"UseDeepness", "DoubleSide", "LightBlending"} <= set(rows["Portal"]["attributes"]) and "Advanced" in rows["WaterVolume"]["children"], "")
    check("the common attributes (Name, Pos, Id, Layer ...) are left out: only what is specific to the type is listed", all("Pos" not in r.get("attributes", {}) and "Id" not in r.get("attributes", {}) for r in made), "")
    hits = C.search("fog")
    check("search finds types by a property name ('fog': River, WaterVolume ...)", {"River", "WaterVolume"} <= {r["type"] for r in hits}, [r["type"] for r in hits])
    check("search finds a category ('audio': 4 audio types)", len(C.search("audio")) >= 4, len(C.search("audio")))
    bad = results.count(False)
    print("object catalog test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

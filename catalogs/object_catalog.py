#!/usr/bin/env python3
"""Every editor object type with the properties the engine gives a fresh one (ENGINE_LEARNING_PLAN A9): each of the editor's own object types is created once as a scratch object, its XML
(the attributes the object's own Serialize writes, and its child nodes) is read back and the object is deleted again; the answer is saved in engine_data/object_catalog.json.

    python catalogs/object_catalog.py --capture         needs the editor (about 1 minute; creates and deletes ~36 scratch objects, checks the object count at the end)
    python catalogs/object_catalog.py <text>            types / properties containing the text
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import sys
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
DATA = os.path.join(HERE, "engine_data", "object_catalog.json")
COMMON = {"Type", "Layer", "LayerGUID", "Id", "Name", "Pos", "Rotate", "Scale", "ColorRGB", "UseCustomLevelLayerColor", "MinSpec", "MatLayersMask", "OutdoorOnly"}
GEOMETRY = {"Brush": "objects/sphere.cgf", "EntityWithStaticMeshComponent": "objects/sphere.cgf"}      # types that need a model to be created
SKIP = {"Entity": "needs an entity class: try object_props.create('Entity', name, pos, '<class>')"}
POSITION = [487, 392, 40]


def capture(path=DATA):
    import object_props as O
    import xml.etree.ElementTree as ET
    types = O.object_types()
    before = O.object_count()
    rows = []
    for t in types:
        name = "zz_oc_%s" % t["type"]
        row = {"type": t["type"], "category": t["category"], "file_spec": t["fileSpec"]}
        if t["type"] in SKIP:
            row["skipped"] = SKIP[t["type"]]
            rows.append(row)
            continue
        geometry = GEOMETRY.get(t["type"], "" if "*" in (t["fileSpec"] or "*") else t["fileSpec"])
        try:
            with O.scratch(name):
                O.create(t["type"], name, POSITION, geometry)
                x = O.get_xml(name)
                row["attributes"] = {k: v for k, v in x.attrib.items() if k not in COMMON}
                row["children"] = {c.tag: dict(c.attrib) for c in x}
                row["entity_class"] = x.attrib.get("EntityClass", "")
        except Exception as exc:                                  # noqa: BLE001 - a type that cannot be created from a script is a finding
            row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            for leftover in O.names_like(name):
                O.delete([leftover])
        rows.append(row)
        time.sleep(0.2)
    after = O.object_count()
    if after != before:
        raise RuntimeError("the level holds %d objects, it held %d: delete the zz_oc_ objects by hand" % (after, before))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(rows, open(path, "w", encoding="utf-8"), indent=1)
    return rows


def load(path=DATA):
    if not os.path.exists(path):
        raise RuntimeError("no catalog at %s: run  python catalogs/object_catalog.py --capture  with the editor open" % path)
    return json.load(open(path, encoding="utf-8"))


def search(text="", path=DATA):
    words = [w for w in text.lower().split() if w]
    out = []
    for r in load(path):
        hay = (r["type"] + " " + r["category"] + " " + " ".join(r.get("attributes", {})) + " " + " ".join(r.get("children", {}))).lower()
        if all(w in hay for w in words):
            out.append(r)
    return out


def main():
    args = sys.argv[1:]
    if "--capture" in args:
        rows = capture()
        print("%d types: %d with properties, %d skipped, %d errors" % (len(rows), sum(1 for r in rows if "attributes" in r), sum(1 for r in rows if "skipped" in r), sum(1 for r in rows if "error" in r)))
        return
    for r in search(" ".join(args)):
        print("%-30s %-16s %s %s" % (r["type"], r["category"], len(r.get("attributes", {})), r.get("error", r.get("skipped", ""))))


if __name__ == "__main__":
    main()

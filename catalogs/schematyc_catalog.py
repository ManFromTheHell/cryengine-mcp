#!/usr/bin/env python3
"""The Schematyc environment as a catalog (ENGINE_LEARNING_PLAN E3 and the start of E1).

Schematyc is the engine's own gameplay-scripting layer and the registry every entity component is registered in. The editor's plugin (our bridge verb
`bridge.schematyc.dump <file>`) walks `gEnv->pSchematyc->GetEnvRegistry()` and writes everything the engine and the game registered: 83 entity components (name, GUID,
editor category, flags, every member with its label, description and type), 520 functions with their inputs and outputs, 46 signals, 42 data types, the modules, the actions
and the classes, each with the file and line it was registered from. This module reads that file; nothing here scrapes source text.

    python catalogs/schematyc_catalog.py --capture          needs the editor (writes engine_data/schematyc_env.json)
    python catalogs/schematyc_catalog.py <text>             search   (--type Component|Function|Signal|DataType|Module|Action|Class, --category Physics)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
DUMP = os.path.join(HERE, "engine_data", "schematyc_env.json")
SOURCE = _paths.ENGINE_SOURCE   # env CRYENGINE_SOURCE; "" when unset (the source-reading steps then skip)

_CACHE = {}


def capture(path=DUMP):
    import bridge_py
    os.makedirs(os.path.dirname(path), exist_ok=True)
    reply = bridge_py.send("bridge.schematyc.dump " + path.replace(chr(92), "/"), 120)
    if not reply.get("ok"):
        raise RuntimeError("the editor refused: %s (is the bridge plugin new enough? deploy it with bridge_devloop.py cycle)" % reply.get("error"))
    return json.loads(reply["output"])


def source_of(file_text):
    """The registration file as a path in a source tree we have: the engine's build machine wrote D:/jk/gamesdk_release/Code/CryEngine/..., ours is Code/... in the project."""
    f = (file_text or "").replace(chr(92), "/")
    i = f.find("/Code/")
    return f[i + 1:] if i >= 0 else f


def load(path=DUMP):
    mt = os.path.getmtime(path) if os.path.exists(path) else 0
    if _CACHE.get("path") != path or _CACHE.get("mt") != mt:
        if not mt:
            raise RuntimeError("no catalog at %s: run  python catalogs/schematyc_catalog.py --capture  with the editor open" % path)
        data = json.load(open(path, encoding="utf-8"))
        for it in data["items"]:
            it["source"] = source_of(it.get("file"))
            it["category"] = it.get("component", {}).get("editor_category", "") if it["type"] == "Component" else ""
        _CACHE.update(path=path, mt=mt, data=data)
    return _CACHE["data"]


def search(text="", type_="", category="", path_prefix="", limit=25, path=DUMP):
    words = [w for w in re.split(r"\s+", text.lower().strip()) if w]
    rows = []
    for it in load(path)["items"]:
        if type_ and it["type"].lower() != type_.lower():
            continue
        if category and it["category"].lower() != category.lower():
            continue
        if path_prefix and not it["path"].lower().startswith(path_prefix.lower()):
            continue
        hay = (it["name"] + " " + it["path"] + " " + it["description"] + " " + it.get("component", {}).get("label", "")).lower()
        if all(w in hay for w in words):
            name_hit = all(w in it["name"].lower() for w in words)
            rows.append((0 if name_hit else 1, it["name"].lower(), it))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in rows[:limit]], len(rows)


def get(name_or_guid, type_="", path=DUMP):
    """One element by GUID, or by exact name (case-insensitive; `type_` narrows an ambiguous name). -> element, or a list when several match."""
    key = name_or_guid.lower().strip()
    hits = []
    for it in load(path)["items"]:
        label = it.get("component", {}).get("label", "").lower()
        if (it["guid"].lower() == key or it["name"].lower() == key or label == key) and (not type_ or it["type"].lower() == type_.lower()):
            hits.append(it)
    return hits[0] if len(hits) == 1 else hits


def summary(path=DUMP):
    d = load(path)
    cats = {}
    for it in d["items"]:
        if it["type"] == "Component":
            cats[it["category"] or "(none)"] = cats.get(it["category"] or "(none)", 0) + 1
    return {"elements": d["elements"], "counts": d["counts"], "component_categories": dict(sorted(cats.items(), key=lambda kv: -kv[1]))}


def main():
    args = sys.argv[1:]
    if "--capture" in args:
        print(capture())
        return
    opt = lambda name: args[args.index(name) + 1] if name in args else ""
    text = " ".join(a for a in args if not a.startswith("--") and a not in (opt("--type"), opt("--category")))
    rows, total = search(text, opt("--type"), opt("--category"))
    for it in rows:
        extra = ""
        if it["type"] == "Component":
            extra = " [%s] %d members" % (it["category"], len(it["component"]["members"]))
        elif it["type"] == "Function":
            extra = " (%s) -> %s" % (", ".join(i["name"] for i in it["function"]["inputs"]), ", ".join(o["name"] for o in it["function"]["outputs"]))
        print("%-10s %s/%s%s" % (it["type"], it["path"], it["name"], extra))
        if it["description"]:
            print("           " + it["description"][:140])
    print("%d of %d shown" % (len(rows), total))


if __name__ == "__main__":
    main()

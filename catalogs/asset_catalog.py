#!/usr/bin/env python3
"""The engine's own asset metadata as a catalog (ENGINE_LEARNING_PLAN A10).

Next to every asset the engine's asset system writes `<file>.cryasset`: an XML with the asset's type, GUID, the files that make it up (a mesh's LOD chain), details it measured
(triangle and vertex counts, material and texture counts, width/height/mips) and what it depends on, with usage counts. The editor's Asset Browser and Dependency Graph show this data;
this module reads it without the editor, so "what uses this texture?", "what are the heaviest meshes?" and "which assets does the engine not know about yet?" are one call.

What it is NOT: the project's own lint (asset_lint.py) walks the real .cgf/.mtl files and says whether references RESOLVE; this reads what the engine RECORDED, which is complete only
for the assets it has generated metadata for (asset.generate_and_repair_all_metadata in the editor writes the missing ones).

    python catalogs/asset_catalog.py                       summary: types, coverage
    python catalogs/asset_catalog.py <path>                one asset and what uses it
    python catalogs/asset_catalog.py --heaviest [type]     biggest by triangles (meshes) or pixels (textures)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")

# the source extensions the asset system makes an asset of, in this project (coverage is measured against these)
ASSET_EXTS = (".cgf", ".mtl", ".dds", ".cdf", ".chr", ".skin", ".caf", ".level", ".env", ".pfx", ".cga", ".abc", ".cag", ".adb", ".bspace")

_CACHE = {}


def norm(path):
    return (path or "").replace(chr(92), "/").strip().lower()


def _resolve(dep, owner_dir):
    """A dependency string as a comparable key: './x.mtl' is relative to the owner's folder; '%engine%/...' stays an engine path; anything else is relative to Assets."""
    d = norm(dep)
    if d.startswith("./"):
        d = norm(owner_dir + "/" + d[2:])
    elif d.startswith("../"):
        parts = owner_dir.split("/")
        while d.startswith("../") and parts:
            parts.pop()
            d = d[3:]
        d = norm("/".join(parts + [d]))
    return d.lstrip("/")


def scan(assets=ASSETS):
    """Every `.cryasset` under the assets folder -> [{path, type, guid, files, details, deps, timestamp, source, meta}], keyed lookups in index()."""
    key = ("scan", assets, int(os.path.getmtime(assets)) if os.path.isdir(assets) else 0)
    if _CACHE.get("scan_key") == key:
        return _CACHE["scan"]
    out = []
    for root, _dirs, names in os.walk(assets):
        for n in names:
            if not n.endswith(".cryasset"):
                continue
            meta = os.path.join(root, n)
            try:
                el = ET.parse(meta).getroot()
            except (ET.ParseError, OSError):
                continue
            folder = norm(os.path.relpath(root, assets))
            folder = "" if folder == "." else folder
            main = norm((folder + "/" if folder else "") + n[:-len(".cryasset")])
            files = [norm((folder + "/" if folder else "") + f.get("path", "")) for f in el.findall("Files/File")]
            details = {d.get("name"): d.text for d in el.findall("Details/Detail")}
            deps = [{"path": _resolve(p.text, folder), "raw": (p.text or "").strip(), "uses": int(p.get("usageCount") or 1)} for p in el.findall("Dependencies/Path") if p.text]
            out.append({"path": main, "type": el.get("type", ""), "guid": el.get("guid", ""), "files": files, "details": details, "deps": deps,
                        "timestamp": el.get("timestamp", ""), "source": el.get("source", ""), "meta": os.path.relpath(meta, assets).replace(chr(92), "/")})
    _CACHE["scan_key"] = key
    _CACHE["scan"] = out
    _CACHE.pop("index", None)
    return out


def index(assets=ASSETS):
    """(by main path, by guid, reverse: dependency key -> [assets that depend on it])."""
    rows = scan(assets)
    if "index" not in _CACHE:
        by_path = {r["path"]: r for r in rows}
        by_guid = {r["guid"].lower(): r for r in rows}
        reverse = {}
        for r in rows:
            for d in r["deps"]:
                reverse.setdefault(d["path"], []).append(r["path"])
                stem = os.path.splitext(d["path"])[0]                     # a dependency on x.tif is a dependency on x.dds and the other way round
                reverse.setdefault(stem, []).append(r["path"])
        _CACHE["index"] = (by_path, by_guid, reverse)
    return _CACHE["index"]


def get(path_or_guid, assets=ASSETS):
    by_path, by_guid, _ = index(assets)
    key = norm(path_or_guid)
    if key in by_guid:
        return by_guid[key]
    key = key.replace("assets/", "", 1) if key.startswith("assets/") else key
    if key in by_path:
        return by_path[key]
    for r in by_path.values():                                          # a file of a multi-file asset (a LOD), or the source name of a texture
        if key in r["files"] or os.path.splitext(key)[0] == os.path.splitext(r["path"])[0]:
            return r
    return None


def users(path, assets=ASSETS):
    """Assets whose recorded dependencies name this one. -> sorted list of asset paths."""
    _, _, reverse = index(assets)
    a = get(path, assets)
    keys = {norm(path)}
    if a:
        keys |= {a["path"], os.path.splitext(a["path"])[0]} | set(a["files"])
    keys |= {os.path.splitext(k)[0] for k in keys}
    found = set()
    for k in keys:
        found.update(reverse.get(k, []))
    found.discard(a["path"] if a else None)
    return sorted(found)


def summary(assets=ASSETS):
    rows = scan(assets)
    types = {}
    for r in rows:
        types[r["type"]] = types.get(r["type"], 0) + 1
    return {"assets_with_metadata": len(rows), "by_type": dict(sorted(types.items(), key=lambda kv: -kv[1])), "dependencies_recorded": sum(len(r["deps"]) for r in rows)}


def coverage(assets=ASSETS):
    """Real asset files under Assets that no metadata lists: {extension: [with, without]} and the list of those without (first 200)."""
    covered = set()
    for r in scan(assets):
        covered.update(r["files"])
        covered.add(r["path"])
    per, missing = {}, []
    for root, _dirs, names in os.walk(assets):
        for n in names:
            ext = os.path.splitext(n)[1].lower()
            if ext not in ASSET_EXTS:
                continue
            rel = norm(os.path.relpath(os.path.join(root, n), assets))
            ok = rel in covered
            per.setdefault(ext, [0, 0])[0 if ok else 1] += 1
            if not ok and len(missing) < 200:
                missing.append(rel)
    return {"by_extension": {k: {"with_metadata": v[0], "without": v[1]} for k, v in sorted(per.items())}, "without_examples": missing}


def heaviest(type_="Mesh", limit=15, assets=ASSETS):
    """The biggest assets of a type by what the engine measured: triangles for Mesh/Character, width x height for Texture. -> [{path, value, details}]"""
    rows = [r for r in scan(assets) if r["type"].lower() == type_.lower()]

    def weight(r):
        d = r["details"]
        try:
            if "triangleCount" in d:
                return int(d["triangleCount"])
            if "width" in d and "height" in d:
                return int(d["width"]) * int(d["height"])
        except (TypeError, ValueError):
            pass
        return 0
    rows.sort(key=weight, reverse=True)
    return [{"path": r["path"], "weight": weight(r), "details": r["details"], "files": len(r["files"])} for r in rows[:limit]]


def unused(type_="Material", assets=ASSETS, limit=100):
    """Assets of a type that no other recorded asset depends on (a level or a layer may still use them: the level lists its objects in layer files this does not read)."""
    out = []
    for r in scan(assets):
        if r["type"].lower() == type_.lower() and not users(r["path"], assets):
            out.append(r["path"])
    return sorted(out)[:limit], len(out)


def main():
    args = sys.argv[1:]
    if "--heaviest" in args:
        t = next((a for a in args if not a.startswith("--")), "Mesh")
        for h in heaviest(t):
            print("%10d  %s  (%d file(s))" % (h["weight"], h["path"], h["files"]))
        return
    if args:
        a = get(args[0])
        if not a:
            raise SystemExit("no asset metadata for %r" % args[0])
        print("%s  [%s]  guid %s" % (a["path"], a["type"], a["guid"]))
        print("  files:", ", ".join(a["files"]))
        print("  details:", a["details"])
        for d in a["deps"]:
            print("  depends on %s (x%d)" % (d["path"], d["uses"]))
        for u in users(args[0]):
            print("  used by   %s" % u)
        return
    print(summary())
    print(coverage()["by_extension"])


if __name__ == "__main__":
    main()
